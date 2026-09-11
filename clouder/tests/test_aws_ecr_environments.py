"""`clouder aws ecr-environments`, against stand-ins for Terraform, AWS, kubectl, docker and cosign."""

from __future__ import annotations

import base64
import datetime
import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from typer.testing import CliRunner

from ..cli import aws_ecr_environments as cli
from ..cloud.aws import ecr_environments_refresher as refresher

runner = CliRunner()

DIGEST = "sha256:" + "a" * 64
OUTPUTS = {
    "region": "us-east-1",
    "registry": "123456789012.dkr.ecr.us-east-1.amazonaws.com",
    "repository_prefix": "environments",
    "encryption_key_arn": "arn:aws:kms:us-east-1:123456789012:key/enc",
    "signing_key_alias": "alias/datalayer-environments-signing",
    "base_reader_role_arn": "arn:aws:iam::123456789012:role/datalayer-environments-base-reader",
    "builder_user": "datalayer-environments-builder",
    "puller_user": "datalayer-environments-puller",
    "reader_user": "datalayer-environments-reader",
}


def denied(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, operation)


def said(result) -> str:
    """The output with Rich's line wrapping undone."""
    return " ".join(result.output.split())


class FakeRegistry:
    """An account's ECR registry, as far as its scanning configuration goes; fresh by default."""

    def __init__(self, scan_type: str = "BASIC", filters: tuple[str, ...] = ()) -> None:
        self.configuration = {
            "scanType": scan_type,
            "rules": [
                {"scanFrequency": "CONTINUOUS_SCAN", "repositoryFilters": [{"filter": item, "filterType": "WILDCARD"}]}
                for item in filters
            ],
        }
        self.reads = 0

    def get_registry_scanning_configuration(self):
        self.reads += 1
        return {"registryId": "123456789012", "scanningConfiguration": self.configuration}


class Recorder:
    """Every command the CLI runs, answered as if it succeeded."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.plan_code = 2

    def __call__(self, command, *, cwd=None, input_text=None, env=None):
        self.commands.append(list(command))
        self.inputs.append(input_text)
        code, stdout = 0, ""
        if "plan" in command and "-detailed-exitcode" in command:
            code, stdout = self.plan_code, "Plan: 14 to add, 0 to change, 0 to destroy."
        elif command[-2:] == ["output", "-json"]:
            stdout = json.dumps({name: {"value": value} for name, value in OUTPUTS.items()})
        elif command[-2:] == ["config", "current-context"]:
            stdout = "r1\n"
        return subprocess.CompletedProcess(command, code, stdout, "")

    def terraform(self) -> list[list[str]]:
        return [command[1:] for command in self.commands if command[0] == "terraform"]

    def applied(self) -> list[dict]:
        documents = []
        for command, text in zip(self.commands, self.inputs, strict=True):
            if "apply" in command and "--server-side" in command:
                documents += [json.loads(part) for part in text.split("\n---\n")]
        return documents


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "terraform" / "environments-registry"
    directory.mkdir(parents=True)
    (directory / "main.tf").write_text('module "environments_registry" {}\n')
    return directory


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recording = Recorder()
    monkeypatch.setattr(cli, "_run", recording)
    monkeypatch.setattr(cli, "_which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(cli, "_client", lambda service, region=None: FakeRegistry())
    monkeypatch.delenv("DATALAYER_ECR_ENVIRONMENTS_REGION", raising=False)
    monkeypatch.delenv("DATALAYER_ECR_ENVIRONMENTS_REGISTRY", raising=False)
    monkeypatch.delenv("DATALAYER_DURABLE_NAMESPACE", raising=False)
    return recording


def write_keys(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in cli.PRINCIPALS:
        (directory / f"{name}.env").write_text(
            f"AWS_ACCESS_KEY_ID=AKIA{name.upper()}\nAWS_SECRET_ACCESS_KEY=secret-of-{name}\nAWS_REGION=us-east-1\n"
        )
    return directory


# --- Terraform ---------------------------------------------------------------------------


def test_plan_writes_the_variables_from_its_options_and_a_plan(root: Path, recorder: Recorder) -> None:
    result = runner.invoke(
        cli.ecr_environments_app,
        ["plan", "--region", "eu-west-3", "--base-channel", "python-cpu", "--json", "--terraform-dir", str(root)],
    )
    assert result.exit_code == 0, result.output
    tfvars = (root / cli.TFVARS).read_text()
    assert 'aws_region = "eu-west-3"' in tfvars
    assert 'base_channels = ["python-cpu"]' in tfvars
    assert "manage_registry_scanning = true" in tfvars
    assert "extra_scan_filters = []" in tfvars
    assert recorder.terraform() == [
        ["init", "-input=false", "-no-color"],
        ["plan", "-input=false", "-no-color", "-detailed-exitcode", "-out=tfplan"],
        ["show", "-json", "tfplan"],
    ]
    assert (root / "tfplan.json").exists()


def test_the_region_comes_from_the_rc_when_no_option_sets_it(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATALAYER_ECR_ENVIRONMENTS_REGION", "ca-central-1")
    assert runner.invoke(cli.ecr_environments_app, ["plan", "--terraform-dir", str(root)]).exit_code == 0
    assert 'aws_region = "ca-central-1"' in (root / cli.TFVARS).read_text()


def test_a_plan_without_changes_says_so(root: Path, recorder: Recorder) -> None:
    recorder.plan_code = 0
    result = runner.invoke(cli.ecr_environments_app, ["plan", "--terraform-dir", str(root)])
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_apply_needs_a_saved_plan(root: Path, recorder: Recorder) -> None:
    refused = runner.invoke(cli.ecr_environments_app, ["apply", "--terraform-dir", str(root)])
    assert refused.exit_code == 1 and recorder.commands == []
    (root / "tfplan").write_text("plan")
    applied = runner.invoke(cli.ecr_environments_app, ["apply", "--terraform-dir", str(root)])
    assert applied.exit_code == 0
    assert recorder.terraform()[-1] == ["apply", "-input=false", "-no-color", "tfplan"]


def test_terraform_runs_in_its_pinned_image_when_it_is_not_installed(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_which", lambda tool: None if tool == "terraform" else f"/usr/bin/{tool}")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator-secret")
    result = runner.invoke(cli.ecr_environments_app, ["outputs", "--terraform-dir", str(root)])
    assert result.exit_code == 0, result.output
    command = recorder.commands[0]
    assert command[:2] == ["docker", "run"]
    assert f"{root.parent.resolve()}:/workspace" in command
    assert command[command.index("-w") + 1] == "/workspace/environments-registry"
    assert command[command.index(cli.TERRAFORM_IMAGE) + 1 :] == ["output", "-json"]
    assert "AWS_SECRET_ACCESS_KEY" in command
    assert not any("operator-secret" in part for part in command)


def test_without_terraform_or_docker_the_command_says_so(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_which", lambda tool: None)
    result = runner.invoke(cli.ecr_environments_app, ["plan", "--terraform-dir", str(root)])
    assert result.exit_code == 1
    assert "docker" in result.output
    assert recorder.commands == []


def use_registry(monkeypatch: pytest.MonkeyPatch, registry: FakeRegistry) -> None:
    monkeypatch.setattr(cli, "_client", lambda service, region=None: registry)


def test_scanning_that_already_covers_the_prefix_is_left_as_it_is(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account this was first run against scanned `*` continuously; replacing it would have stopped that."""
    use_registry(monkeypatch, FakeRegistry("ENHANCED", ("*",)))
    result = runner.invoke(cli.ecr_environments_app, ["plan", "--terraform-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert "manage_registry_scanning = false" in (root / cli.TFVARS).read_text()
    assert "left as it is" in said(result)


def test_other_scanning_rules_are_replaced_only_when_they_are_named_again(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_registry(monkeypatch, FakeRegistry("ENHANCED", ("services/*",)))
    refused = runner.invoke(cli.ecr_environments_app, ["plan", "--terraform-dir", str(root)])
    assert refused.exit_code == 1
    assert "services/*" in said(refused) and recorder.commands == []
    kept = runner.invoke(
        cli.ecr_environments_app, ["plan", "--extra-scan-filter", "services/*", "--terraform-dir", str(root)]
    )
    assert kept.exit_code == 0, kept.output
    tfvars = (root / cli.TFVARS).read_text()
    assert 'extra_scan_filters = ["services/*"]' in tfvars
    assert "manage_registry_scanning = true" in tfvars


def test_a_fresh_account_gets_enhanced_scanning_and_an_opt_out_reads_nothing(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = FakeRegistry()
    use_registry(monkeypatch, registry)
    assert runner.invoke(cli.ecr_environments_app, ["plan", "--terraform-dir", str(root)]).exit_code == 0
    assert "manage_registry_scanning = true" in (root / cli.TFVARS).read_text()
    registry.reads = 0
    arguments = ["plan", "--no-manage-registry-scanning", "--terraform-dir", str(root)]
    assert runner.invoke(cli.ecr_environments_app, arguments).exit_code == 0
    assert registry.reads == 0
    assert "manage_registry_scanning = false" in (root / cli.TFVARS).read_text()


@pytest.mark.parametrize(
    ("repository_filter", "covered"),
    [("*", True), ("environments/*", True), ("env*", True), ("environments*", True), ("services/*", False), ("*/base", False), ("environments/u/*", False)],
)
def test_a_filter_covers_the_prefix_only_when_it_matches_everything_under_it(repository_filter: str, covered: bool) -> None:
    assert cli._covers(repository_filter, "environments") is covered


# --- Keys --------------------------------------------------------------------------------


class FakeIAM:
    def __init__(self, keys: dict[str, list[dict]] | None = None) -> None:
        self.keys = keys or {}
        self.created: list[str] = []
        self.deleted: list[str] = []

    def list_access_keys(self, UserName: str):  # noqa: N803 - boto3's own names
        return {"AccessKeyMetadata": list(self.keys.get(UserName, []))}

    def create_access_key(self, UserName: str):  # noqa: N803
        self.created.append(UserName)
        key = {"AccessKeyId": f"AKIA{UserName[-6:].upper()}", "SecretAccessKey": f"secret-of-{UserName}"}
        return {"AccessKey": key}

    def delete_access_key(self, UserName: str, AccessKeyId: str):  # noqa: N803
        self.deleted.append(AccessKeyId)


def test_rotate_keys_writes_private_files_and_never_prints_a_secret(
    root: Path, tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_client", lambda service, region=None: FakeIAM())
    keys = tmp_path / "keys"
    result = runner.invoke(
        cli.ecr_environments_app, ["rotate-keys", "--keys-dir", str(keys), "--terraform-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert "secret-of" not in result.output
    for name in cli.PRINCIPALS:
        path = keys / f"{name}.env"
        assert path.stat().st_mode & 0o777 == 0o600
        assert f"AWS_SECRET_ACCESS_KEY=secret-of-datalayer-environments-{name}" in path.read_text()
        assert "AWS_REGION=us-east-1" in path.read_text()
    assert keys.stat().st_mode & 0o777 == 0o700


def test_a_third_key_is_refused_and_old_keys_are_retired_on_request(
    root: Path, tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    moment = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)
    two = [
        {"AccessKeyId": "AKIAOLD", "CreateDate": moment},
        {"AccessKeyId": "AKIANEW", "CreateDate": moment + datetime.timedelta(days=1)},
    ]
    iam = FakeIAM({"datalayer-environments-builder": two})
    monkeypatch.setattr(cli, "_client", lambda service, region=None: iam)
    arguments = ["rotate-keys", "--principal", "builder", "--keys-dir", str(tmp_path), "--terraform-dir", str(root)]
    refused = runner.invoke(cli.ecr_environments_app, arguments)
    assert refused.exit_code == 1
    assert "retire-old" in refused.output
    retired = runner.invoke(cli.ecr_environments_app, [*arguments, "--retire-old"])
    assert retired.exit_code == 0
    assert iam.deleted == ["AKIAOLD"]


def test_an_unknown_principal_is_refused(root: Path, recorder: Recorder) -> None:
    result = runner.invoke(
        cli.ecr_environments_app, ["rotate-keys", "--principal", "admin", "--terraform-dir", str(root)]
    )
    assert result.exit_code == 1


# --- Kubernetes --------------------------------------------------------------------------


def test_secrets_go_to_their_namespaces_by_server_side_apply(tmp_path: Path, recorder: Recorder) -> None:
    keys = write_keys(tmp_path / "keys")
    result = runner.invoke(cli.ecr_environments_app, ["secrets", "--keys-dir", str(keys), "--yes"])
    assert result.exit_code == 0, result.output
    apply = next(command for command in recorder.commands if "apply" in command)
    assert apply[:6] == ["kubectl", "apply", "--server-side", "--field-manager=clouder-ecr-environments", "--force-conflicts", "-f"]
    documents = recorder.applied()
    placed = {(item["metadata"]["name"], item["metadata"]["namespace"]) for item in documents if item["kind"] == "Secret"}
    assert placed == {
        ("ecr-environments-builder", "datalayer-durable"),
        ("ecr-environments-reader", "datalayer-api"),
        ("ecr-environments-puller", "datalayer-runtimes"),
    }
    kinds = [item["kind"] for item in documents]
    assert {item["metadata"]["name"] for item in documents if item["kind"] == "Namespace"} == {
        "datalayer-durable",
        "datalayer-api",
        "datalayer-runtimes",
    }
    assert max(index for index, kind in enumerate(kinds) if kind == "Namespace") < kinds.index("Secret")
    builder = next(item for item in documents if item["metadata"]["name"] == "ecr-environments-builder")
    assert "stringData" not in builder
    assert base64.b64decode(builder["data"]["AWS_SECRET_ACCESS_KEY"]).decode() == "secret-of-builder"
    assert not any("secret-of" in part for command in recorder.commands for part in command)
    assert "secret-of" not in result.output


def test_the_builder_secret_follows_the_durable_namespace_of_the_rc(
    tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATALAYER_DURABLE_NAMESPACE", "durable-r1")
    keys = write_keys(tmp_path / "keys")
    arguments = ["secrets", "--principal", "builder", "--keys-dir", str(keys), "--yes"]
    assert runner.invoke(cli.ecr_environments_app, arguments).exit_code == 0
    secrets = [item for item in recorder.applied() if item["kind"] == "Secret"]
    assert [(item["metadata"]["name"], item["metadata"]["namespace"]) for item in secrets] == [
        ("ecr-environments-builder", "durable-r1")
    ]


def test_secrets_need_the_key_files_before_touching_the_cluster(tmp_path: Path, recorder: Recorder) -> None:
    result = runner.invoke(cli.ecr_environments_app, ["secrets", "--keys-dir", str(tmp_path / "none"), "--yes"])
    assert result.exit_code == 1
    assert "rotate-keys" in result.output
    assert recorder.commands == []


def test_secrets_ask_before_writing_into_a_context(tmp_path: Path, recorder: Recorder) -> None:
    keys = write_keys(tmp_path / "keys")
    result = runner.invoke(
        cli.ecr_environments_app, ["secrets", "--keys-dir", str(keys), "--context", "r1"], input="n\n"
    )
    assert result.exit_code == 1
    assert recorder.applied() == []


def test_the_refresher_is_a_hardened_cronjob_scoped_to_its_secret() -> None:
    documents = {item["kind"]: item for item in cli.refresher_manifests("datalayer-runtimes", OUTPUTS["registry"])}
    cronjob = documents["CronJob"]
    assert cronjob["spec"]["schedule"] == "0 */6 * * *"
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    images = [container["image"] for container in pod["initContainers"] + pod["containers"]]
    assert images == [cli.AWS_CLI_IMAGE, cli.PYTHON_IMAGE]
    assert all(":" in image and not image.endswith(":latest") for image in images)
    assert pod["initContainers"][0]["envFrom"] == [{"secretRef": {"name": "ecr-environments-puller"}}]
    environment = {item["name"]: item["value"] for item in pod["containers"][0]["env"]}
    assert environment["REGISTRY"] == OUTPUTS["registry"]
    assert environment["SECRET_NAME"] == "ecr-environments"
    rules = documents["Role"]["rules"]
    assert rules[0] == {"apiGroups": [""], "resources": ["secrets"], "resourceNames": ["ecr-environments"], "verbs": ["get", "update"]}
    assert documents["ConfigMap"]["data"]["refresh.py"] == Path(refresher.__file__).read_text()


def test_the_refresher_installs_then_runs_once(recorder: Recorder) -> None:
    arguments = ["refresher", "--registry", OUTPUTS["registry"], "--runtime-namespace", "runtimes-a", "--yes"]
    result = runner.invoke(cli.ecr_environments_app, arguments)
    assert result.exit_code == 0, result.output
    steps = [command[1:5] for command in recorder.commands]
    assert ["-n", "runtimes-a", "get", "secret"] in steps
    assert ["-n", "runtimes-a", "create", "job"] in steps
    assert ["-n", "runtimes-a", "wait", "--for=condition=complete"] in steps
    assert {item["kind"] for item in recorder.applied()} >= {"CronJob", "Role", "RoleBinding", "ConfigMap"}


def test_the_refresher_needs_the_puller_secret(recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command, **kwargs):
        if "get" in command and "secret" in command:
            recorder.commands.append(list(command))
            recorder.inputs.append(None)
            return subprocess.CompletedProcess(command, 1, "", "NotFound")
        return recorder(command, **kwargs)

    monkeypatch.setattr(cli, "_run", run)
    result = runner.invoke(cli.ecr_environments_app, ["refresher", "--registry", "r", "--yes"])
    assert result.exit_code == 1
    assert "--principal puller" in said(result)
    assert recorder.applied() == []


# --- deploy ------------------------------------------------------------------------------


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> FakeIAM:
    iam = FakeIAM()
    monkeypatch.setattr(cli, "_client", lambda service, region=None: iam if service == "iam" else FakeRegistry())
    monkeypatch.setattr(cli, "get_aws_identity", lambda: {"account_id": "123456789012", "arn": "arn:aws:iam::123456789012:user/owner"})
    monkeypatch.setattr(cli, "run_check", lambda values, keys_dir, probe_image, scan_timeout: [cli.Step("push by the builder", True)])
    return iam


def test_deploy_runs_every_step_in_order(root: Path, tmp_path: Path, recorder: Recorder, aws: FakeIAM) -> None:
    keys = tmp_path / "keys"
    result = runner.invoke(cli.ecr_environments_app, ["deploy", "--keys-dir", str(keys), "--yes", "--terraform-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert [command[0] for command in recorder.terraform()] == ["init", "plan", "init", "apply", "output"]
    assert aws.created == [OUTPUTS[f"{name}_user"] for name in cli.PRINCIPALS]
    kinds = [item["kind"] for item in recorder.applied()]
    assert kinds.index("Secret") < kinds.index("CronJob")
    assert any(command[1:5] == ["-n", "datalayer-runtimes", "create", "job"] for command in recorder.commands)
    assert f"export DATALAYER_ECR_ENVIRONMENTS_REGISTRY={OUTPUTS['registry']}" in result.output
    assert "push by the builder" in result.output


def test_deploy_run_again_changes_nothing_and_keeps_the_keys(
    root: Path, tmp_path: Path, recorder: Recorder, aws: FakeIAM
) -> None:
    keys = write_keys(tmp_path / "keys")
    recorder.plan_code = 0
    result = runner.invoke(cli.ecr_environments_app, ["deploy", "--keys-dir", str(keys), "--yes", "--terraform-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert "No changes" in result.output
    assert "apply" not in [command[0] for command in recorder.terraform()]
    assert aws.created == []


def test_deploy_stops_when_a_principal_has_a_key_nobody_kept(
    root: Path, tmp_path: Path, recorder: Recorder, aws: FakeIAM
) -> None:
    aws.keys = {OUTPUTS["puller_user"]: [{"AccessKeyId": "AKIAOLD"}]}
    result = runner.invoke(
        cli.ecr_environments_app,
        ["deploy", "--keys-dir", str(tmp_path / "keys"), "--yes", "--skip-check", "--terraform-dir", str(root)],
    )
    assert result.exit_code == 1
    assert "rotate-keys --principal puller" in said(result)
    assert recorder.applied() == []


def test_deploy_asks_before_applying(root: Path, tmp_path: Path, recorder: Recorder, aws: FakeIAM) -> None:
    arguments = ["deploy", "--keys-dir", str(tmp_path / "keys"), "--skip-kubernetes", "--skip-check", "--terraform-dir", str(root)]
    result = runner.invoke(cli.ecr_environments_app, arguments, input="n\n")
    assert result.exit_code == 1
    assert "apply" not in [command[0] for command in recorder.terraform()]


# --- check -------------------------------------------------------------------------------


class FakeECR:
    def __init__(self, *, allow_outside: bool = False, allow_base_reader: bool = False) -> None:
        self.allow_outside = allow_outside
        self.allow_base_reader = allow_base_reader
        self.created: list[str] = []
        self.deleted_images: list[dict] = []

    def get_authorization_token(self):
        return {"authorizationData": [{"authorizationToken": base64.b64encode(b"AWS:registry-password").decode()}]}

    def create_repository(self, repositoryName: str, **_):  # noqa: N803
        if not repositoryName.startswith("environments/") and not self.allow_outside:
            raise denied("CreateRepository")
        self.created.append(repositoryName)

    def delete_repository(self, **_):
        pass

    def describe_images(self, **_):
        return {"imageDetails": [{"imageDigest": DIGEST}]}

    def describe_image_scan_findings(self, **_):
        return {"imageScanStatus": {"status": "COMPLETE"}, "imageScanFindings": {"findingSeverityCounts": {"LOW": 2}}}

    def batch_get_image(self, **_):
        if not self.allow_base_reader:
            raise denied("BatchGetImage")
        return {"images": [{}]}

    def list_images(self, **_):
        return {"imageIds": [{"imageDigest": DIGEST}]}

    def batch_delete_image(self, **kwargs):
        self.deleted_images.append(kwargs)


class FakeSession:
    def __init__(self, ecr: FakeECR) -> None:
        self.ecr = ecr

    def client(self, service: str):
        if service == "sts":
            return SimpleNamespace(
                assume_role=lambda **_: {"Credentials": {"AccessKeyId": "ASIA", "SecretAccessKey": "s", "SessionToken": "t"}}
            )
        return self.ecr

    def get_credentials(self):
        frozen = SimpleNamespace(access_key="AKIA", secret_key="principal-secret", token=None)
        return SimpleNamespace(get_frozen_credentials=lambda: frozen)


def stub_sessions(monkeypatch: pytest.MonkeyPatch, principal: FakeECR, base_reader: FakeECR) -> None:
    monkeypatch.setattr(cli, "_principal_session", lambda path, region: FakeSession(principal))
    monkeypatch.setattr(cli, "_assumed_session", lambda credentials, region: FakeSession(base_reader))


def test_the_check_passes_every_step_on_a_registry_that_behaves(
    root: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    ecr = FakeECR()
    stub_sessions(monkeypatch, ecr, FakeECR())
    result = runner.invoke(cli.ecr_environments_app, ["check", "--terraform-dir", str(root)])
    assert result.exit_code == 0, result.output
    names = [step.name for step in cli.run_check(OUTPUTS, Path("keys"), cli.DEFAULT_PROBE_IMAGE, 1)]
    assert names == [
        "log in as the builder",
        "create environments/u/clouder-check/probe",
        "pull the probe image",
        "tag the probe",
        "push by the builder",
        "sign with the KMS key",
        "log in as the puller",
        "pull by digest as the puller",
        "verify the signature as the puller",
        "read the scan as the reader",
        "the builder is refused outside the prefix",
        "the base-reader is refused a user image",
    ]
    pinned = f"{OUTPUTS['registry']}/environments/u/clouder-check/probe@{DIGEST}"
    commands = recorder.commands
    assert ["cosign", "sign", "--yes", "--key", "awskms:///alias/datalayer-environments-signing", pinned] in commands
    assert ["cosign", "verify", "--key", "awskms:///alias/datalayer-environments-signing", pinned] in commands
    assert ecr.deleted_images, "the probe image is cleaned up"


@pytest.mark.parametrize(
    ("principal", "base_reader", "step"),
    [
        (FakeECR(allow_outside=True), FakeECR(), "the builder is refused outside the prefix"),
        (FakeECR(), FakeECR(allow_base_reader=True), "the base-reader is refused a user image"),
    ],
)
def test_the_check_fails_when_a_refusal_is_allowed(
    root: Path,
    recorder: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    principal: FakeECR,
    base_reader: FakeECR,
    step: str,
) -> None:
    stub_sessions(monkeypatch, principal, base_reader)
    steps = {item.name: item for item in cli.run_check(OUTPUTS, Path("keys"), cli.DEFAULT_PROBE_IMAGE, 1)}
    assert not steps[step].ok
    assert all(item.ok for name, item in steps.items() if name != step)
    result = runner.invoke(cli.ecr_environments_app, ["check", "--terraform-dir", str(root)])
    assert result.exit_code == 1


def test_the_check_needs_the_key_files(root: Path, tmp_path: Path, recorder: Recorder) -> None:
    result = runner.invoke(
        cli.ecr_environments_app, ["check", "--keys-dir", str(tmp_path / "none"), "--terraform-dir", str(root)]
    )
    assert result.exit_code == 1
    assert "rotate-keys" in result.output


# --- the refresher's own program ---------------------------------------------------------


def test_the_pull_secret_carries_a_docker_config_for_the_registry() -> None:
    secret = refresher.pull_secret("ecr-environments", "datalayer-runtimes", "r.example", "pw")
    assert secret["type"] == "kubernetes.io/dockerconfigjson"
    config = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
    assert config["auths"]["r.example"]["username"] == "AWS"
    assert base64.b64decode(config["auths"]["r.example"]["auth"]).decode() == "AWS:pw"


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://k8s", code, "status", {}, io.BytesIO())


def test_refresh_replaces_the_secret_or_creates_it_the_first_time() -> None:
    calls: list[tuple[str, str]] = []

    def existing(method: str, url: str, body: bytes) -> None:
        calls.append((method, url))

    assert refresher.refresh(existing, "https://k8s", "ns", "ecr-environments", "r", "pw") == "replaced"
    assert calls == [("PUT", "https://k8s/api/v1/namespaces/ns/secrets/ecr-environments")]

    calls.clear()

    def missing(method: str, url: str, body: bytes) -> None:
        calls.append((method, url))
        if method == "PUT":
            raise http_error(404)

    assert refresher.refresh(missing, "https://k8s", "ns", "ecr-environments", "r", "pw") == "created"
    assert calls[-1] == ("POST", "https://k8s/api/v1/namespaces/ns/secrets")


def test_refresh_does_not_hide_a_refusal() -> None:
    def forbidden(method: str, url: str, body: bytes) -> None:
        raise http_error(403)

    with pytest.raises(urllib.error.HTTPError):
        refresher.refresh(forbidden, "https://k8s", "ns", "ecr-environments", "r", "pw")


def test_the_refresher_program_fails_without_a_password(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    environ = {"REGISTRY": "r", "KUBERNETES_SERVICE_HOST": "10.0.0.1", "PASSWORD_FILE": str(tmp_path / "none")}
    assert refresher.main(environ, service_account=str(tmp_path)) == 1
    assert "cannot read" in capsys.readouterr().err
