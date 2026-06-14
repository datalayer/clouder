from pathlib import Path

import yaml
from typer.testing import CliRunner

from ..cli import ctx as ctx_cli


runner = CliRunner()


def _read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_ctx_sync_creates_context_file_with_discovered_azure_and_aws(monkeypatch, tmp_path):
    config_folder = tmp_path / ".clouder"
    context_file = config_folder / "clouder.yaml"

    monkeypatch.setattr(ctx_cli, "CLOUDER_CONFIG_FOLDER", config_folder)
    monkeypatch.setattr(ctx_cli, "CLOUDER_CONTEXT_FILE", context_file)

    monkeypatch.setattr(
        "clouder.cloud.azure.api.list_azure_subscriptions",
        lambda: [{"id": "sub-1", "name": "Subscription One"}],
    )
    monkeypatch.setattr(
        "clouder.cloud.aws.api.list_aws_accounts",
        lambda: [{"id": "acc-1", "name": "Account One"}],
    )

    result = runner.invoke(ctx_cli.ctx_app, ["sync"])

    assert result.exit_code == 0
    assert "Synced azure: 1 created, 0 updated, 0 unchanged." in result.stdout
    assert "Synced aws: 1 created, 0 updated, 0 unchanged." in result.stdout

    payload = _read_yaml(context_file)
    contexts = payload["clouder"]["contexts"]
    assert contexts["azure"] == {"sub-1": {"name": "Subscription One"}}
    assert contexts["aws"] == {"acc-1": {"name": "Account One"}}
    assert contexts["ovh"] == {}


def test_ctx_sync_updates_existing_entries_without_removing_other_clouds(monkeypatch, tmp_path):
    config_folder = tmp_path / ".clouder"
    context_file = config_folder / "clouder.yaml"
    config_folder.mkdir(parents=True, exist_ok=True)

    initial = {
        "clouder": {
            "version": "1.0.0",
            "default_context": "",
            "current_context": "",
            "contexts": {
                "ovh": {"ovh-1": {"name": "OVH Project"}},
                "azure": {},
                "aws": {"acc-1": {"name": "Old Name"}},
            },
        }
    }
    with open(context_file, "w", encoding="utf-8") as handle:
        yaml.safe_dump(initial, handle, sort_keys=False)

    monkeypatch.setattr(ctx_cli, "CLOUDER_CONFIG_FOLDER", config_folder)
    monkeypatch.setattr(ctx_cli, "CLOUDER_CONTEXT_FILE", context_file)

    monkeypatch.setattr("clouder.cloud.azure.api.list_azure_subscriptions", lambda: [])
    monkeypatch.setattr(
        "clouder.cloud.aws.api.list_aws_accounts",
        lambda: [{"id": "acc-1", "name": "New Name"}],
    )

    result = runner.invoke(ctx_cli.ctx_app, ["sync"])

    assert result.exit_code == 0
    assert "Synced aws: 0 created, 1 updated, 0 unchanged." in result.stdout

    payload = _read_yaml(context_file)
    contexts = payload["clouder"]["contexts"]
    assert contexts["ovh"] == {"ovh-1": {"name": "OVH Project"}}
    assert contexts["aws"] == {"acc-1": {"name": "New Name"}}


def test_ctx_sync_handles_missing_cloud_credentials(monkeypatch, tmp_path):
    config_folder = tmp_path / ".clouder"
    context_file = config_folder / "clouder.yaml"

    monkeypatch.setattr(ctx_cli, "CLOUDER_CONFIG_FOLDER", config_folder)
    monkeypatch.setattr(ctx_cli, "CLOUDER_CONTEXT_FILE", context_file)

    def raise_azure():
        raise RuntimeError("azure unavailable")

    def raise_aws():
        raise RuntimeError("aws unavailable")

    monkeypatch.setattr("clouder.cloud.azure.api.list_azure_subscriptions", raise_azure)
    monkeypatch.setattr("clouder.cloud.aws.api.list_aws_accounts", raise_aws)

    result = runner.invoke(ctx_cli.ctx_app, ["sync"])

    assert result.exit_code == 0
    assert "Could not fetch Azure subscriptions (skipping)." in result.stderr
    assert "Could not fetch AWS account (skipping)." in result.stderr
    assert "No Azure or AWS contexts discovered." in result.stderr

    payload = _read_yaml(context_file)
    contexts = payload["clouder"]["contexts"]
    assert contexts["azure"] == {}
    assert contexts["aws"] == {}
    assert contexts["ovh"] == {}
