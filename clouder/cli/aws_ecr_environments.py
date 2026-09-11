"""Clouder CLI - the Environments registry in AWS ECR.

`clouder aws ecr-environments` is the only way the registry of PLAN_ENV.md (E0-12) is
deployed: nothing is created by hand in the AWS console, and nobody runs `terraform` in its
root. `deploy` does all of it:

1. confirms the AWS account and the Kubernetes context it is about to change;
2. writes the Terraform variables from its options, plans, and applies after a confirmation;
3. creates the access key of any principal that has none, in a file only its owner can read;
4. creates the Kubernetes Secrets of the builder, the reader and the puller;
5. installs the refresher that keeps the `ecr-environments` pull secret fresh;
6. runs `check`, and prints the rc exports.

Each step is also a command of its own, for CI, for rotations and for a second plane.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import boto3
import typer
from botocore.exceptions import ClientError
from rich import print
from rich.table import Table

from ..cloud.aws import ecr_environments_refresher
from ..cloud.aws.api import _client, get_aws_identity

ecr_environments_app = typer.Typer(no_args_is_help=True)

#: The principals of PLAN_ENV.md, D-17, as Terraform names their users.
PRINCIPALS = ("builder", "puller", "reader")

#: What AWS answers when a policy refuses a call.
DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}

DEFAULT_REGION = "us-east-1"
DEFAULT_KEYS_DIR = Path.home() / ".clouder" / "ecr-environments" / "keys"
DEFAULT_PROBE_IMAGE = "public.ecr.aws/docker/library/busybox:1.36"

#: Terraform, when no binary is installed, pinned like every image these commands run.
TERRAFORM_IMAGE = "hashicorp/terraform:1.9.8"
#: What a Terraform container inherits by name, so no value is on its command line.
AWS_ENVIRONMENT = (
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
#: Written by every plan from the command's options; Terraform loads it by its name.
TFVARS = "clouder.auto.tfvars"
PLAN_FILE = "tfplan"

KEY_NAMES = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")
FIELD_MANAGER = "clouder-ecr-environments"
LABELS = ecr_environments_refresher.LABELS
PULL_SECRET = "ecr-environments"  # noqa: S105 - the Secret's name, not a secret
REFRESHER = "ecr-environments-refresher"
#: Every 6 hours, so a failed run leaves another attempt before a 12-hour token expires.
REFRESH_SCHEDULE = "0 */6 * * *"
AWS_CLI_IMAGE = "public.ecr.aws/aws-cli/aws-cli:2.36.43"
PYTHON_IMAGE = "python:3.13-alpine3.22"
DEFAULT_READER_NAMESPACE = "datalayer-api"
DEFAULT_RUNTIME_NAMESPACES = ("datalayer-runtimes",)


def default_terraform_dir() -> Path:
    """The Terraform root, beside this package in a checkout, or where the environment says."""
    configured = os.getenv("CLOUDER_ECR_ENVIRONMENTS_TERRAFORM_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "terraform" / "environments-registry"


def _durable_namespace() -> str:
    """Where the durable worker runs builds, as `up.sh` decides it."""
    return os.getenv("DATALAYER_DURABLE_NAMESPACE") or "datalayer-durable"


def _which(tool: str) -> Optional[str]:
    return shutil.which(tool)


def _run(
    command: list[str],
    *,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """The one place a command runs, so a test can stand in for all of them."""
    return subprocess.run(
        command, cwd=cwd, input=input_text, env=env, text=True, capture_output=True, check=False
    )


def _require(*tools: str) -> None:
    missing = [tool for tool in tools if _which(tool) is None]
    if missing:
        print(f"[red]Required on PATH: {', '.join(missing)}.[/red]")
        raise typer.Exit(1)


# --- Terraform ---------------------------------------------------------------------------


@dataclass
class Settings:
    """The registry's Terraform variables, as the command's options set them."""

    region: str
    project_name: str
    repository_prefix: str
    base_channels: list[str]
    manage_registry_scanning: bool
    extra_scan_filters: list[str]

    def tfvars(self) -> dict[str, Any]:
        return {
            "aws_region": self.region,
            "project_name": self.project_name,
            "repository_prefix": self.repository_prefix,
            "base_channels": self.base_channels,
            "manage_registry_scanning": self.manage_registry_scanning,
            "extra_scan_filters": self.extra_scan_filters,
        }


def _settings(
    region: Optional[str],
    project_name: str,
    repository_prefix: str,
    base_channel: Optional[list[str]],
    manage_registry_scanning: bool,
    extra_scan_filter: Optional[list[str]],
) -> Settings:
    return Settings(
        region=region or os.getenv("DATALAYER_ECR_ENVIRONMENTS_REGION") or DEFAULT_REGION,
        project_name=project_name,
        repository_prefix=repository_prefix,
        base_channels=list(base_channel or ("python-cpu", "python-cuda")),
        manage_registry_scanning=manage_registry_scanning,
        extra_scan_filters=list(extra_scan_filter or ()),
    )


def _hcl(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_hcl(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


def write_tfvars(root: Path, settings: Settings) -> Path:
    path = root / TFVARS
    lines = ["# Written by `clouder aws ecr-environments`: change its options, not this file."]
    lines += [f"{name} = {_hcl(value)}" for name, value in settings.tfvars().items()]
    path.write_text("\n".join(lines) + "\n")
    return path


def _root(terraform_dir: Optional[Path]) -> Path:
    root = terraform_dir or default_terraform_dir()
    if not (root / "main.tf").is_file():
        print(f"[red]No Terraform root at {root}; pass --terraform-dir.[/red]")
        raise typer.Exit(1)
    return root


def terraform_command(root: Path) -> list[str]:
    """Terraform from PATH, or from its pinned image when only docker is installed."""
    if _which("terraform"):
        return ["terraform"]
    if _which("docker"):
        home = "/tmp/terraform-home"  # noqa: S108 - inside the container
        command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}"]
        # The root reaches its module through `../modules`, so its parent is mounted.
        command += ["-v", f"{root.parent.resolve()}:/workspace", "-w", f"/workspace/{root.name}"]
        command += ["-e", f"HOME={home}"]
        aws = Path.home() / ".aws"
        if aws.is_dir():
            command += ["-v", f"{aws}:{home}/.aws:ro"]
        for name in AWS_ENVIRONMENT:
            if os.environ.get(name):
                command += ["-e", name]
        return [*command, TERRAFORM_IMAGE]
    print("[red]Terraform is needed: install it, or install docker to run the pinned image.[/red]")
    raise typer.Exit(1)


def _terraform(root: Path, *arguments: str, codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    result = _run([*terraform_command(root), *arguments], cwd=root)
    if result.returncode not in codes:
        print(f"[red]terraform {arguments[0]} failed[/red]")
        typer.echo(result.stderr or result.stdout)
        raise typer.Exit(result.returncode or 1)
    return result


def plan_registry(root: Path, settings: Settings) -> bool:
    """Plan into tfplan, and say whether the plan changes anything."""
    write_tfvars(root, settings)
    _terraform(root, "init", "-input=false", "-no-color")
    result = _terraform(
        root, "plan", "-input=false", "-no-color", "-detailed-exitcode", f"-out={PLAN_FILE}", codes=(0, 2)
    )
    typer.echo(result.stdout)
    return result.returncode == 2


def apply_registry(root: Path) -> None:
    _terraform(root, "init", "-input=false", "-no-color")
    typer.echo(_terraform(root, "apply", "-input=false", "-no-color", PLAN_FILE).stdout)


def outputs(root: Path) -> dict[str, Any]:
    """The root's Terraform outputs, as name to value."""
    result = _terraform(root, "output", "-json")
    return {name: item.get("value") for name, item in json.loads(result.stdout or "{}").items()}


TerraformDirOption = typer.Option(None, "--terraform-dir", help="The environments-registry Terraform root.")
RegionOption = typer.Option(
    None, "--region", help="AWS region; DATALAYER_ECR_ENVIRONMENTS_REGION, then us-east-1, by default."
)
ProjectOption = typer.Option("datalayer", "--project-name", help="The first part of every IAM and KMS name.")
PrefixOption = typer.Option("environments", "--repository-prefix", help="The prefix every policy is scoped to.")
ChannelOption = typer.Option(
    None, "--base-channel", help="A base channel repository, repeated; python-cpu and python-cuda by default."
)
ScanningOption = typer.Option(
    True,
    "--manage-registry-scanning/--no-manage-registry-scanning",
    help="Own the account's registry scanning configuration, which is one per region.",
)
ScanFilterOption = typer.Option(
    None, "--extra-scan-filter", help="Another repository wildcard to keep scanned, repeated."
)
KeysDirOption = typer.Option(DEFAULT_KEYS_DIR, "--keys-dir", help="Where the principals' key files live.")
KubeconfigOption = typer.Option(None, "--kubeconfig", help="The kubeconfig of the plane.")
ContextOption = typer.Option(None, "--context", help="The kubeconfig context of the plane.")
BuilderNamespaceOption = typer.Option(
    None, "--builder-namespace", help="Where the durable worker runs; DATALAYER_DURABLE_NAMESPACE by default."
)
ReaderNamespaceOption = typer.Option(
    DEFAULT_READER_NAMESPACE, "--reader-namespace", help="Where the Runtimes service runs."
)
RuntimeNamespaceOption = typer.Option(
    None, "--runtime-namespace", help="A namespace runtimes launch in, repeated; datalayer-runtimes by default."
)
ProbeImageOption = typer.Option(DEFAULT_PROBE_IMAGE, "--probe-image", help="A small public image to push.")
ScanTimeoutOption = typer.Option(600, "--scan-timeout", help="Seconds to wait for the probe's scan.")
YesOption = typer.Option(False, "--yes", "-y", help="Do not ask before changing AWS or Kubernetes.")


@ecr_environments_app.command("plan")
def plan(
    region: Optional[str] = RegionOption,
    project_name: str = ProjectOption,
    repository_prefix: str = PrefixOption,
    base_channel: Optional[list[str]] = ChannelOption,
    manage_registry_scanning: bool = ScanningOption,
    extra_scan_filter: Optional[list[str]] = ScanFilterOption,
    json_output: bool = typer.Option(False, "--json", help="Also write tfplan.json, for review in CI."),
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Plan the Environments registry into tfplan, changing nothing."""
    root = _root(terraform_dir)
    settings = _settings(
        region, project_name, repository_prefix, base_channel, manage_registry_scanning, extra_scan_filter
    )
    changed = plan_registry(root, settings)
    if json_output:
        (root / "tfplan.json").write_text(_terraform(root, "show", "-json", PLAN_FILE).stdout)
    if changed:
        print("[green]Plan written to tfplan; `clouder aws ecr-environments apply` applies it.[/green]")
    else:
        print("[green]No changes: the registry already matches.[/green]")


@ecr_environments_app.command("apply")
def apply(terraform_dir: Optional[Path] = TerraformDirOption):
    """Apply the plan `plan` saved. A saved plan applies without asking: review it first."""
    root = _root(terraform_dir)
    if not (root / PLAN_FILE).is_file():
        print("[red]No tfplan: run `clouder aws ecr-environments plan` first.[/red]")
        raise typer.Exit(1)
    apply_registry(root)


@ecr_environments_app.command("outputs")
def show_outputs(terraform_dir: Optional[Path] = TerraformDirOption):
    """Show what the registry root created."""
    values = outputs(_root(terraform_dir))
    table = Table(title="Environments registry")
    table.add_column("Output", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    for name in sorted(values):
        value = values[name]
        table.add_row(name, value if isinstance(value, str) else json.dumps(value))
    print(table)


# --- Keys --------------------------------------------------------------------------------


def _write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read, created that way rather than narrowed afterwards."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _keys_dir(keys_dir: Path) -> None:
    keys_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(keys_dir, 0o700)


def _create_key(iam: Any, name: str, values: dict[str, Any], keys_dir: Path) -> str:
    created = iam.create_access_key(UserName=values[f"{name}_user"])["AccessKey"]
    _write_private(
        keys_dir / f"{name}.env",
        f"AWS_ACCESS_KEY_ID={created['AccessKeyId']}\n"
        f"AWS_SECRET_ACCESS_KEY={created['SecretAccessKey']}\n"
        f"AWS_REGION={values['region']}\n",
    )
    return created["AccessKeyId"]


def ensure_keys(values: dict[str, Any], keys_dir: Path) -> list[tuple[str, str]]:
    """Create a key for every principal that has none, and keep the key files already written."""
    iam = _client("iam")
    _keys_dir(keys_dir)
    results = []
    for name in PRINCIPALS:
        path = keys_dir / f"{name}.env"
        if path.is_file():
            results.append((name, f"kept {path}"))
            continue
        user = values[f"{name}_user"]
        if iam.list_access_keys(UserName=user)["AccessKeyMetadata"]:
            print(
                f"[red]{user} has a key, but {path} is missing and AWS never shows a secret twice. "
                f"`clouder aws ecr-environments rotate-keys --principal {name}` makes a new one.[/red]"
            )
            raise typer.Exit(1)
        results.append((name, f"created {_create_key(iam, name, values, keys_dir)} in {path}"))
    return results


@ecr_environments_app.command("rotate-keys")
def rotate_keys(
    principal: str = typer.Option("all", "--principal", help="builder, puller, reader or all."),
    keys_dir: Path = KeysDirOption,
    retire_old: bool = typer.Option(
        False,
        "--retire-old",
        help="Delete every key but the newest, once the Secrets carry it; creates nothing.",
    ),
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Create a new access key per principal, or retire the old ones.

    A rotation is three runs: `rotate-keys` writes new key files, `secrets` puts them in the
    Secrets, and `rotate-keys --retire-old` deletes the keys nothing uses anymore.
    """
    if principal != "all" and principal not in PRINCIPALS:
        print(f"[red]--principal is one of {', '.join(PRINCIPALS)} or all.[/red]")
        raise typer.Exit(1)
    names = PRINCIPALS if principal == "all" else (principal,)
    values = outputs(_root(terraform_dir))
    iam = _client("iam")
    table = Table(title="Environments registry keys")
    table.add_column("Principal", style="cyan")
    table.add_column("IAM user")
    table.add_column("Access key id")
    table.add_column("Result", style="green")
    if not retire_old:
        _keys_dir(keys_dir)
    for name in names:
        user = values[f"{name}_user"]
        keys = sorted(
            iam.list_access_keys(UserName=user)["AccessKeyMetadata"], key=lambda key: key["CreateDate"]
        )
        if retire_old:
            for key in keys[:-1]:
                iam.delete_access_key(UserName=user, AccessKeyId=key["AccessKeyId"])
                table.add_row(name, user, key["AccessKeyId"], "retired")
            continue
        if len(keys) >= 2:
            print(
                f"[red]{user} already has two keys, the most IAM allows. Put the newest in the "
                "Secrets, then run `rotate-keys --retire-old`.[/red]"
            )
            raise typer.Exit(1)
        created = _create_key(iam, name, values, keys_dir)
        table.add_row(name, user, created, f"written to {keys_dir / f'{name}.env'}")
    print(table)


def _key_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        print(f"[red]{path} is missing: run `clouder aws ecr-environments rotate-keys` first.[/red]")
        raise typer.Exit(1)
    values = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    missing = [name for name in KEY_NAMES if not values.get(name)]
    if missing:
        print(f"[red]{path} has no {', '.join(missing)}.[/red]")
        raise typer.Exit(1)
    return values


# --- Kubernetes --------------------------------------------------------------------------


def _kube(kubeconfig: Optional[Path], context: Optional[str]) -> list[str]:
    command = ["kubectl"]
    if kubeconfig:
        command += ["--kubeconfig", str(kubeconfig)]
    if context:
        command += ["--context", context]
    return command


def _kubectl(kube: list[str], *arguments: str, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    result = _run([*kube, *arguments], input_text=input_text)
    if result.returncode != 0:
        print(f"[red]kubectl {' '.join(arguments[:3])} failed[/red]")
        typer.echo(result.stderr or result.stdout)
        raise typer.Exit(result.returncode)
    return result


def _confirm_context(kube: list[str], context: Optional[str], yes: bool) -> None:
    current = context or _kubectl(kube, "config", "current-context").stdout.strip()
    print(f"Kubernetes context: [bold]{current}[/bold]")
    if not yes and not typer.confirm(f"Write the Environments registry's Secrets and refresher into {current}?"):
        raise typer.Exit(1)


def apply_manifests(kube: list[str], manifests: list[dict]) -> None:
    """Server-side apply, which keeps no copy of a Secret's data in an annotation."""
    text = "\n---\n".join(json.dumps(manifest) for manifest in manifests)
    arguments = ("apply", "--server-side", f"--field-manager={FIELD_MANAGER}", "--force-conflicts", "-f", "-")
    typer.echo(_kubectl(kube, *arguments, input_text=text).stdout)


def _namespace(name: str) -> dict:
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}}


def placements(
    principals: tuple[str, ...] | list[str],
    builder_namespace: Optional[str],
    reader_namespace: str,
    runtime_namespaces: Optional[list[str]],
) -> dict[str, list[str]]:
    """Where each principal's Secret goes: D-17's builder, reader and puller."""
    every = {
        "builder": [builder_namespace or _durable_namespace()],
        "reader": [reader_namespace],
        "puller": list(runtime_namespaces or DEFAULT_RUNTIME_NAMESPACES),
    }
    return {name: every[name] for name in principals}


def secret_manifests(keys_dir: Path, where: dict[str, list[str]]) -> list[dict]:
    namespaces = dict.fromkeys(namespace for names in where.values() for namespace in names)
    manifests = [_namespace(namespace) for namespace in namespaces]
    for name, targets in where.items():
        values = _key_file(keys_dir / f"{name}.env")
        data = {key: base64.b64encode(values[key].encode()).decode() for key in KEY_NAMES}
        for namespace in dict.fromkeys(targets):
            manifests.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {"name": f"{PULL_SECRET}-{name}", "namespace": namespace, "labels": dict(LABELS)},
                    "data": data,
                }
            )
    return manifests


def _principals(requested: Optional[list[str]]) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(requested or PRINCIPALS))
    unknown = [name for name in names if name not in PRINCIPALS]
    if unknown:
        print(f"[red]--principal is one of {', '.join(PRINCIPALS)}, not {', '.join(unknown)}.[/red]")
        raise typer.Exit(1)
    return names


@ecr_environments_app.command("secrets")
def secrets(
    principal: Optional[list[str]] = typer.Option(
        None, "--principal", help="builder, reader or puller, repeated; all three by default."
    ),
    keys_dir: Path = KeysDirOption,
    builder_namespace: Optional[str] = BuilderNamespaceOption,
    reader_namespace: str = ReaderNamespaceOption,
    runtime_namespace: Optional[list[str]] = RuntimeNamespaceOption,
    kubeconfig: Optional[Path] = KubeconfigOption,
    context: Optional[str] = ContextOption,
    yes: bool = YesOption,
):
    """Create the principals' Kubernetes Secrets from their key files."""
    _require("kubectl")
    where = placements(_principals(principal), builder_namespace, reader_namespace, runtime_namespace)
    manifests = secret_manifests(keys_dir, where)
    kube = _kube(kubeconfig, context)
    _confirm_context(kube, context, yes)
    apply_manifests(kube, manifests)


def refresher_manifests(namespace: str, registry: str) -> list[dict]:
    """The refresher of D-17: an ECR password from the puller's key, written as a pull secret."""

    def metadata(name: str) -> dict:
        return {"name": name, "namespace": namespace, "labels": dict(LABELS)}

    hardened = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    pod = {
        "serviceAccountName": REFRESHER,
        "restartPolicy": "OnFailure",
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 65534,
            "runAsGroup": 65534,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "initContainers": [
            {
                "name": "password",
                "image": AWS_CLI_IMAGE,
                "command": ["sh", "-c", 'aws ecr get-login-password --region "$AWS_REGION" > /work/password'],
                "envFrom": [{"secretRef": {"name": f"{PULL_SECRET}-puller"}}],
                "env": [{"name": "HOME", "value": "/work"}],
                "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                "securityContext": hardened,
                "resources": {"requests": {"cpu": "10m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
            }
        ],
        "containers": [
            {
                "name": "secret",
                "image": PYTHON_IMAGE,
                "command": ["python3", "/refresher/refresh.py"],
                "env": [
                    {"name": "REGISTRY", "value": registry},
                    {"name": "SECRET_NAME", "value": PULL_SECRET},
                    {"name": "PASSWORD_FILE", "value": "/work/password"},
                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                ],
                "volumeMounts": [
                    {"name": "work", "mountPath": "/work", "readOnly": True},
                    {"name": "refresher", "mountPath": "/refresher", "readOnly": True},
                ],
                "securityContext": hardened,
                "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "128Mi"}},
            }
        ],
        "volumes": [
            {"name": "work", "emptyDir": {"medium": "Memory", "sizeLimit": "4Mi"}},
            {"name": "refresher", "configMap": {"name": REFRESHER}},
        ],
    }
    return [
        _namespace(namespace),
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": metadata(REFRESHER)},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": metadata(REFRESHER),
            "rules": [
                {"apiGroups": [""], "resources": ["secrets"], "resourceNames": [PULL_SECRET], "verbs": ["get", "update"]},
                {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": metadata(REFRESHER),
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": REFRESHER},
            "subjects": [{"kind": "ServiceAccount", "name": REFRESHER, "namespace": namespace}],
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata(REFRESHER),
            "data": {"refresh.py": Path(ecr_environments_refresher.__file__).read_text()},
        },
        {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": metadata(REFRESHER),
            "spec": {
                "schedule": REFRESH_SCHEDULE,
                "concurrencyPolicy": "Forbid",
                "startingDeadlineSeconds": 3600,
                "successfulJobsHistoryLimit": 1,
                "failedJobsHistoryLimit": 3,
                "jobTemplate": {
                    "spec": {
                        "backoffLimit": 4,
                        "activeDeadlineSeconds": 600,
                        "ttlSecondsAfterFinished": 86400,
                        "template": {"metadata": {"labels": dict(LABELS)}, "spec": pod},
                    }
                },
            },
        },
    ]


def install_refresher(kube: list[str], namespaces: list[str], registry: str) -> None:
    """Install the refresher in each namespace and run it once, so the pull secret exists now."""
    for namespace in dict.fromkeys(namespaces):
        found = _run([*kube, "-n", namespace, "get", "secret", f"{PULL_SECRET}-puller"])
        if found.returncode != 0:
            print(
                f"[red]No {PULL_SECRET}-puller Secret in {namespace}: run "
                f"`clouder aws ecr-environments secrets --principal puller --runtime-namespace {namespace}`.[/red]"
            )
            raise typer.Exit(1)
        apply_manifests(kube, refresher_manifests(namespace, registry))
        job = f"{REFRESHER}-{int(time.time())}"
        _kubectl(kube, "-n", namespace, "create", "job", f"--from=cronjob/{REFRESHER}", job)
        waited = _run([*kube, "-n", namespace, "wait", "--for=condition=complete", f"job/{job}", "--timeout=180s"])
        if waited.returncode != 0:
            print(
                f"[red]The first refresh in {namespace} did not complete: "
                f"kubectl -n {namespace} logs job/{job} --all-containers[/red]"
            )
            raise typer.Exit(1)
        print(f"[green]{PULL_SECRET} refreshed in {namespace}.[/green]")


@ecr_environments_app.command("refresher")
def refresher(
    registry: Optional[str] = typer.Option(
        None,
        "--registry",
        help="The registry host; DATALAYER_ECR_ENVIRONMENTS_REGISTRY, then the Terraform outputs, by default.",
    ),
    runtime_namespace: Optional[list[str]] = RuntimeNamespaceOption,
    kubeconfig: Optional[Path] = KubeconfigOption,
    context: Optional[str] = ContextOption,
    yes: bool = YesOption,
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Install the CronJob that refreshes the `ecr-environments` pull secret every 6 hours."""
    _require("kubectl")
    host = registry or os.getenv("DATALAYER_ECR_ENVIRONMENTS_REGISTRY") or outputs(_root(terraform_dir))["registry"]
    kube = _kube(kubeconfig, context)
    _confirm_context(kube, context, yes)
    install_refresher(kube, list(runtime_namespace or DEFAULT_RUNTIME_NAMESPACES), host)


# --- check -------------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def _principal_session(path: Path, region: str) -> Any:
    values = _key_file(path)
    return boto3.Session(
        aws_access_key_id=values["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=values["AWS_SECRET_ACCESS_KEY"],
        region_name=region,
    )


def _assumed_session(credentials: dict[str, str], region: str) -> Any:
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def _denied(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in DENIED


def _environment(session: Any, region: str) -> dict[str, str]:
    """A process environment carrying a principal's credentials, for docker and cosign."""
    frozen = session.get_credentials().get_frozen_credentials()
    environment = dict(os.environ)
    environment.update(
        {"AWS_ACCESS_KEY_ID": frozen.access_key, "AWS_SECRET_ACCESS_KEY": frozen.secret_key, "AWS_REGION": region}
    )
    environment.pop("AWS_SESSION_TOKEN", None)
    environment.pop("AWS_PROFILE", None)
    if frozen.token:
        environment["AWS_SESSION_TOKEN"] = frozen.token
    return environment


def _login(ecr: Any, registry: str) -> Step:
    token = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
    password = base64.b64decode(token).decode().split(":", 1)[1]
    result = _run(["docker", "login", "--username", "AWS", "--password-stdin", registry], input_text=password)
    return Step("log in", result.returncode == 0, result.stderr.strip()[-200:])


def _command(name: str, command: list[str], env: Optional[dict[str, str]] = None) -> Step:
    result = _run(command, env=env)
    return Step(name, result.returncode == 0, (result.stderr or result.stdout).strip()[-200:])


def run_check(values: dict[str, Any], keys_dir: Path, probe_image: str, scan_timeout: int) -> list[Step]:
    """Every step of the check, in order; a step whose prerequisite failed is skipped."""
    region, registry, prefix = values["region"], values["registry"], values["repository_prefix"]
    sessions = {name: _principal_session(keys_dir / f"{name}.env", region) for name in PRINCIPALS}
    builder, puller, reader = (sessions[name].client("ecr") for name in PRINCIPALS)
    repository = f"{prefix}/u/clouder-check/probe"
    tag = f"check-{uuid.uuid4().hex[:12]}"
    steps: list[Step] = []

    def failed() -> bool:
        return any(not step.ok for step in steps)

    login = _login(builder, registry)
    steps.append(Step("log in as the builder", login.ok, login.detail))
    try:
        builder.create_repository(
            repositoryName=repository,
            imageTagMutability="IMMUTABLE",
            encryptionConfiguration={"encryptionType": "KMS", "kmsKey": values["encryption_key_arn"]},
        )
        steps.append(Step(f"create {repository}", True))
    except ClientError as error:
        exists = error.response.get("Error", {}).get("Code") == "RepositoryAlreadyExistsException"
        steps.append(Step(f"create {repository}", exists, "" if exists else str(error)))
    digest = ""
    if not failed():
        reference = f"{registry}/{repository}:{tag}"
        for step in (
            _command("pull the probe image", ["docker", "pull", probe_image]),
            _command("tag the probe", ["docker", "tag", probe_image, reference]),
            _command("push by the builder", ["docker", "push", reference]),
        ):
            steps.append(step)
            if not step.ok:
                break
    if not failed():
        details = builder.describe_images(repositoryName=repository, imageIds=[{"imageTag": tag}])
        digest = details["imageDetails"][0]["imageDigest"]
        pinned = f"{registry}/{repository}@{digest}"
        signing = f"awskms:///{values['signing_key_alias']}"
        steps.append(
            _command(
                "sign with the KMS key",
                ["cosign", "sign", "--yes", "--key", signing, pinned],
                env=_environment(sessions["builder"], region),
            )
        )
        login = _login(puller, registry)
        steps.append(Step("log in as the puller", login.ok, login.detail))
        steps.append(_command("pull by digest as the puller", ["docker", "pull", pinned]))
        steps.append(
            _command(
                "verify the signature as the puller",
                ["cosign", "verify", "--key", signing, pinned],
                env=_environment(sessions["puller"], region),
            )
        )
    if digest:
        steps.append(_scan(reader, repository, digest, scan_timeout))
    try:
        outside = f"clouder-check-outside-{uuid.uuid4().hex[:8]}"
        builder.create_repository(repositoryName=outside)
        builder.delete_repository(repositoryName=outside, force=True)
        steps.append(Step("the builder is refused outside the prefix", False, f"{outside} was created"))
    except ClientError as error:
        steps.append(Step("the builder is refused outside the prefix", _denied(error), str(error)))
    if digest:
        steps.append(_base_reader_refusal(sessions["builder"], values, repository, digest))
        ids = builder.list_images(repositoryName=repository).get("imageIds", [])
        if ids:
            builder.batch_delete_image(repositoryName=repository, imageIds=ids)
    return steps


def _scan(reader: Any, repository: str, digest: str, timeout: int) -> Step:
    deadline = time.monotonic() + timeout
    while True:
        try:
            findings = reader.describe_image_scan_findings(
                repositoryName=repository, imageId={"imageDigest": digest}
            )
            status = findings.get("imageScanStatus", {}).get("status", "")
            if status in {"COMPLETE", "ACTIVE"}:
                counts = findings.get("imageScanFindings", {}).get("findingSeverityCounts", {})
                return Step("read the scan as the reader", True, json.dumps(counts))
            if status in {"FAILED", "UNSUPPORTED_IMAGE"}:
                return Step("read the scan as the reader", False, status)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ScanNotFoundException":
                return Step("read the scan as the reader", False, str(error))
        if time.monotonic() >= deadline:
            return Step("read the scan as the reader", False, f"no scan result after {timeout} s")
        time.sleep(10)


def _base_reader_refusal(builder_session: Any, values: dict[str, Any], repository: str, digest: str) -> Step:
    name = "the base-reader is refused a user image"
    credentials = builder_session.client("sts").assume_role(
        RoleArn=values["base_reader_role_arn"], RoleSessionName="clouder-check", DurationSeconds=900
    )["Credentials"]
    ecr = _assumed_session(credentials, values["region"]).client("ecr")
    try:
        ecr.batch_get_image(repositoryName=repository, imageIds=[{"imageDigest": digest}])
    except ClientError as error:
        return Step(name, _denied(error), str(error))
    return Step(name, False, f"the base-reader read {repository}")


def _print_steps(steps: list[Step]) -> None:
    table = Table(title="Environments registry check")
    table.add_column("Step", style="cyan")
    table.add_column("Result")
    table.add_column("Detail", style="dim")
    for step in steps:
        table.add_row(step.name, "[green]ok[/green]" if step.ok else "[red]failed[/red]", step.detail)
    print(table)
    if any(not step.ok for step in steps):
        raise typer.Exit(1)


@ecr_environments_app.command("check")
def check(
    keys_dir: Path = KeysDirOption,
    probe_image: str = ProbeImageOption,
    scan_timeout: int = ScanTimeoutOption,
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Prove the registry: push, pull, sign, verify, scan, and two refusals."""
    _require("docker", "cosign")
    values = outputs(_root(terraform_dir))
    _print_steps(run_check(values, keys_dir, probe_image, scan_timeout))


# --- deploy ------------------------------------------------------------------------------


@ecr_environments_app.command("deploy")
def deploy(
    region: Optional[str] = RegionOption,
    project_name: str = ProjectOption,
    repository_prefix: str = PrefixOption,
    base_channel: Optional[list[str]] = ChannelOption,
    manage_registry_scanning: bool = ScanningOption,
    extra_scan_filter: Optional[list[str]] = ScanFilterOption,
    keys_dir: Path = KeysDirOption,
    builder_namespace: Optional[str] = BuilderNamespaceOption,
    reader_namespace: str = ReaderNamespaceOption,
    runtime_namespace: Optional[list[str]] = RuntimeNamespaceOption,
    kubeconfig: Optional[Path] = KubeconfigOption,
    context: Optional[str] = ContextOption,
    skip_kubernetes: bool = typer.Option(False, "--skip-kubernetes", help="Stop after the keys: no Secrets, no refresher."),
    skip_check: bool = typer.Option(False, "--skip-check", help="Leave out the final check."),
    probe_image: str = ProbeImageOption,
    scan_timeout: int = ScanTimeoutOption,
    yes: bool = YesOption,
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Deploy the Environments registry end to end: Terraform, keys, Secrets, refresher, check."""
    root = _root(terraform_dir)
    _require(*(() if skip_kubernetes else ("kubectl",)), *(() if skip_check else ("docker", "cosign")))
    terraform_command(root)
    try:
        identity = get_aws_identity()
    except Exception as error:  # noqa: BLE001 - whatever boto3 raises, the answer is the same
        print(f"[red]No usable AWS credentials: {error}. `clouder aws configure` shows the ways.[/red]")
        raise typer.Exit(1) from error
    print(f"AWS account [bold]{identity.get('account_id', '?')}[/bold] as {identity.get('arn', '?')}")
    kube = _kube(kubeconfig, context)
    if not skip_kubernetes:
        _confirm_context(kube, context, yes)
    settings = _settings(
        region, project_name, repository_prefix, base_channel, manage_registry_scanning, extra_scan_filter
    )
    if plan_registry(root, settings):
        if not yes and not typer.confirm("Apply this plan?"):
            raise typer.Exit(1)
        apply_registry(root)
    else:
        print("[green]No changes: the registry already matches.[/green]")
    values = outputs(root)
    for name, result in ensure_keys(values, keys_dir):
        print(f"{name}: {result}")
    if not skip_kubernetes:
        where = placements(PRINCIPALS, builder_namespace, reader_namespace, runtime_namespace)
        apply_manifests(kube, secret_manifests(keys_dir, where))
        install_refresher(kube, where["puller"], values["registry"])
    if not skip_check:
        _print_steps(run_check(values, keys_dir, probe_image, scan_timeout))
    print("[green]The Environments registry is deployed.[/green] Add to the datalayerrc of each plane:")
    typer.echo(f"export DATALAYER_ECR_ENVIRONMENTS_REGION={values['region']}")
    typer.echo(f"export DATALAYER_ECR_ENVIRONMENTS_REGISTRY={values['registry']}")
