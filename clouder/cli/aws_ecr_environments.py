"""Clouder CLI - the Environments registry in AWS ECR.

`clouder aws ecr-environments` is how clouder sets up what PLAN_ENV.md (E0-12) asks of
it, never by hand in the AWS console:

- `plan` and `apply` run the Terraform root under terraform/environments-registry;
- `outputs` shows what it created;
- `rotate-keys` creates the access keys of the builder, puller and reader principals,
  writing each to a file only its owner can read, never to the terminal or to state;
- `check` proves the registry does what the plan relies on: a push, a pull by digest, a
  signature made and verified, a scan read back, and two refusals.
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

from ..cloud.aws.api import _client

ecr_environments_app = typer.Typer(no_args_is_help=True)

#: The principals of PLAN_ENV.md, D-17, as Terraform names their users.
PRINCIPALS = ("builder", "puller", "reader")

#: What AWS answers when a policy refuses a call.
DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}

DEFAULT_KEYS_DIR = Path("ecr-environments-keys")
DEFAULT_PROBE_IMAGE = "public.ecr.aws/docker/library/busybox:1.36"


def default_terraform_dir() -> Path:
    """The Terraform root, beside this package in a checkout, or where the environment says."""
    configured = os.getenv("CLOUDER_ECR_ENVIRONMENTS_TERRAFORM_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "terraform" / "environments-registry"


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


def _root(terraform_dir: Optional[Path]) -> Path:
    root = terraform_dir or default_terraform_dir()
    if not (root / "main.tf").is_file():
        print(f"[red]No Terraform root at {root}; pass --terraform-dir.[/red]")
        raise typer.Exit(1)
    return root


def _require(*tools: str) -> None:
    missing = [tool for tool in tools if _which(tool) is None]
    if missing:
        print(f"[red]Required on PATH: {', '.join(missing)}.[/red]")
        raise typer.Exit(1)


def _terraform(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    result = _run(["terraform", *arguments], cwd=root)
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(f"[red]terraform {arguments[0]} failed[/red]\n{result.stderr}")
        raise typer.Exit(result.returncode)
    return result


def _ensure_tfvars(root: Path) -> None:
    tfvars = root / "terraform.tfvars"
    if not tfvars.exists():
        shutil.copy(root / "terraform.tfvars.example", tfvars)
        print(f"Created {tfvars} from the example; review it before applying.")


def outputs(root: Path) -> dict[str, Any]:
    """The root's Terraform outputs, as name to value."""
    result = _terraform(root, "output", "-json")
    return {name: item.get("value") for name, item in json.loads(result.stdout or "{}").items()}


TerraformDirOption = typer.Option(
    None, "--terraform-dir", help="The environments-registry Terraform root."
)


@ecr_environments_app.command("plan")
def plan(terraform_dir: Optional[Path] = TerraformDirOption):
    """Plan the Environments registry: ECR, KMS and IAM, into tfplan."""
    _require("terraform")
    root = _root(terraform_dir)
    _ensure_tfvars(root)
    _terraform(root, "init", "-input=false")
    _terraform(root, "plan", "-input=false", "-var-file=terraform.tfvars", "-out=tfplan")
    print("[green]Plan written to tfplan. Apply it with `clouder aws ecr-environments apply`.[/green]")


@ecr_environments_app.command("apply")
def apply(
    terraform_dir: Optional[Path] = TerraformDirOption,
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Plan and apply in one step instead of applying tfplan."
    ),
):
    """Apply the plan, or plan and apply at once with --auto-approve."""
    _require("terraform")
    root = _root(terraform_dir)
    if auto_approve:
        _ensure_tfvars(root)
        arguments = ["apply", "-input=false", "-var-file=terraform.tfvars", "-auto-approve"]
    elif (root / "tfplan").is_file():
        arguments = ["apply", "-input=false", "tfplan"]
    else:
        print("[red]No tfplan: run `clouder aws ecr-environments plan` first, or pass --auto-approve.[/red]")
        raise typer.Exit(1)
    _terraform(root, "init", "-input=false")
    _terraform(root, *arguments)


@ecr_environments_app.command("outputs")
def show_outputs(terraform_dir: Optional[Path] = TerraformDirOption):
    """Show what the registry root created."""
    _require("terraform")
    values = outputs(_root(terraform_dir))
    table = Table(title="Environments registry")
    table.add_column("Output", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    for name in sorted(values):
        value = values[name]
        table.add_row(name, value if isinstance(value, str) else json.dumps(value))
    print(table)


def _write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read, created that way rather than narrowed afterwards."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


@ecr_environments_app.command("rotate-keys")
def rotate_keys(
    principal: str = typer.Option("all", "--principal", help="builder, puller, reader or all."),
    output_dir: Path = typer.Option(
        DEFAULT_KEYS_DIR, "--output-dir", help="Where each principal's key file is written."
    ),
    retire_old: bool = typer.Option(
        False,
        "--retire-old",
        help="Delete every key but the newest, once the Secrets carry it; creates nothing.",
    ),
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Create a new access key per principal, or retire the old ones.

    A rotation is two runs: `rotate-keys` writes new key files, the Secrets are updated
    from them, then `rotate-keys --retire-old` deletes the keys nothing uses anymore.
    """
    if principal != "all" and principal not in PRINCIPALS:
        print(f"[red]--principal is one of {', '.join(PRINCIPALS)} or all.[/red]")
        raise typer.Exit(1)
    names = PRINCIPALS if principal == "all" else (principal,)
    _require("terraform")
    values = outputs(_root(terraform_dir))
    iam = _client("iam")
    table = Table(title="Environments registry keys")
    table.add_column("Principal", style="cyan")
    table.add_column("IAM user")
    table.add_column("Access key id")
    table.add_column("Result", style="green")
    if not retire_old:
        output_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(output_dir, 0o700)
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
                f"[red]{user} already has two keys, the most IAM allows. Update the Secrets from "
                "the newest, then run `rotate-keys --retire-old`.[/red]"
            )
            raise typer.Exit(1)
        created = iam.create_access_key(UserName=user)["AccessKey"]
        path = output_dir / f"{name}.env"
        _write_private(
            path,
            f"AWS_ACCESS_KEY_ID={created['AccessKeyId']}\n"
            f"AWS_SECRET_ACCESS_KEY={created['SecretAccessKey']}\n"
            f"AWS_REGION={values['region']}\n",
        )
        table.add_row(name, user, created["AccessKeyId"], f"written to {path}")
    print(table)


# --- check -------------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def _key_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        print(f"[red]{path} is missing: run `clouder aws ecr-environments rotate-keys` first.[/red]")
        raise typer.Exit(1)
    return dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#")
    )


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


def run_check(
    values: dict[str, Any], keys_dir: Path, probe_image: str, scan_timeout: int
) -> list[Step]:
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


@ecr_environments_app.command("check")
def check(
    keys_dir: Path = typer.Option(
        DEFAULT_KEYS_DIR, "--keys-dir", help="The key files `rotate-keys` wrote."
    ),
    probe_image: str = typer.Option(DEFAULT_PROBE_IMAGE, "--probe-image", help="A small public image to push."),
    scan_timeout: int = typer.Option(600, "--scan-timeout", help="Seconds to wait for the scan."),
    terraform_dir: Optional[Path] = TerraformDirOption,
):
    """Prove the registry: push, pull, sign, verify, scan, and two refusals."""
    _require("terraform", "docker", "cosign")
    values = outputs(_root(terraform_dir))
    steps = run_check(values, keys_dir, probe_image, scan_timeout)
    table = Table(title="Environments registry check")
    table.add_column("Step", style="cyan")
    table.add_column("Result")
    table.add_column("Detail", style="dim")
    for step in steps:
        table.add_row(step.name, "[green]ok[/green]" if step.ok else "[red]failed[/red]", step.detail)
    print(table)
    if any(not step.ok for step in steps):
        raise typer.Exit(1)
