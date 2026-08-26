"""The kubeadm install hook and the `clouder local-csi status` command."""

from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ..cli import local_csi as local_csi_cli
from ..cli.kubeadm import local_csi as hook

runner = CliRunner()


@pytest.fixture
def chart_dir(tmp_path) -> Path:
    chart = tmp_path / "datalayer-local-csi"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: datalayer-local-csi\nversion: 0.1.0\n")
    (chart / "values.yaml").write_text("relay:\n  host: ''\n")
    (chart / "templates" / "csidriver.yaml").write_text("kind: CSIDriver\n")
    return chart


def test_resolve_chart_dir_prefers_explicit_then_env(tmp_path, chart_dir, monkeypatch):
    monkeypatch.delenv("PLANE_HOME", raising=False)
    monkeypatch.delenv("DATALAYER_HOME", raising=False)
    assert hook.resolve_chart_dir(str(chart_dir)) == chart_dir

    plane_home = tmp_path / "plane"
    target = plane_home / "etc" / "helm" / "charts" / "datalayer-local-csi"
    target.mkdir(parents=True)
    (target / "Chart.yaml").write_text("apiVersion: v2\n")
    monkeypatch.setenv("PLANE_HOME", str(plane_home))
    assert hook.resolve_chart_dir() == target

    assert hook.resolve_chart_dir(str(tmp_path / "missing")) == target


def test_install_script_embeds_the_chart_and_values(chart_dir):
    script = hook.build_local_csi_install_script(
        chart_dir,
        image="registry.example.com/local-csi:0.1.0",
        relay_host="r1.datalayer.run",
        relay_port=443,
        relay_cidr="203.0.113.0/24",
    )
    assert "helm upgrade --install datalayer-local-csi" in script
    assert "--namespace datalayer-runtimes" in script
    assert "--set driver.image=registry.example.com/local-csi:0.1.0" in script
    assert "--set relay.host=r1.datalayer.run" in script
    assert "--set relay.port=443" in script
    assert "--set relay.cidr=203.0.113.0/24" in script
    assert "kubectl get csidriver local.csi.datalayer.io" in script

    blob = script.split("echo '", 1)[1].split("'", 1)[0]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(blob)), mode="r:gz") as archive:
        names = set(archive.getnames())
    assert "datalayer-local-csi/Chart.yaml" in names
    assert "datalayer-local-csi/templates/csidriver.yaml" in names


def test_install_script_quotes_values(chart_dir):
    script = hook.build_local_csi_install_script(chart_dir, relay_host="host; rm -rf /")
    assert "relay.host='host; rm -rf /'" in script
    assert "relay.cidr" not in script


def test_install_local_csi_skips_without_chart(monkeypatch, tmp_path):
    monkeypatch.delenv("PLANE_HOME", raising=False)
    monkeypatch.delenv("DATALAYER_HOME", raising=False)
    monkeypatch.setattr(hook, "resolve_chart_dir", lambda explicit=None: None)
    assert hook.install_local_csi(master={"ip": "1.2.3.4"}, resolved_user="ubuntu", key_path="/k") is False


def test_install_local_csi_runs_the_script_on_the_master(monkeypatch, chart_dir):
    calls = []

    def fake_stream(ip, user, key_path, command):
        calls.append((ip, user, key_path, command))
        return 0

    monkeypatch.setattr("clouder.cli.kubeadm._helpers._ssh_cmd_stream", fake_stream)
    ok = hook.install_local_csi(
        master={"ip": "1.2.3.4"},
        resolved_user="ubuntu",
        key_path="/k",
        relay_host="r1.datalayer.run",
        chart_path=str(chart_dir),
    )
    assert ok is True
    assert calls[0][:3] == ("1.2.3.4", "ubuntu", "/k")
    assert "helm upgrade --install datalayer-local-csi" in calls[0][3]


class _Result:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def test_status_command_reports_nodes(monkeypatch):
    # Rich wraps the table at the terminal width; keep the cells on one line.
    monkeypatch.setenv("COLUMNS", "200")
    mounts = {
        "driver": "local.csi.datalayer.io",
        "node_id": "worker-1",
        "bridges": {
            "brd-1": {"connected": True, "reason": "", "volumes": ["csi-1"]},
            "brd-2": {"connected": False, "reason": "mounter exited with status 4: relay refused the mount token", "volumes": ["csi-2"]},
        },
        "volumes": {"csi-1": {}, "csi-2": {}},
    }
    pods = {
        "items": [
            {
                "metadata": {"name": "datalayer-local-csi-abcde"},
                "spec": {"nodeName": "worker-1"},
                "status": {"phase": "Running", "containerStatuses": [{"ready": True}, {"ready": True}]},
            }
        ]
    }

    def fake_ssh(ip, user, key_path, command, check=True):
        if "get csidriver" in command:
            return _Result("local.csi.datalayer.io")
        if "get daemonset" in command:
            return _Result(json.dumps({"status": {"desiredNumberScheduled": 1, "numberReady": 1}}))
        if "get pods" in command:
            return _Result(json.dumps(pods))
        if "/mounts" in command:
            return _Result(json.dumps(mounts))
        raise AssertionError(command)

    monkeypatch.setattr(local_csi_cli, "_infer_cluster_name", lambda cluster: "demo")
    monkeypatch.setattr(local_csi_cli, "resolve_kubeadm_cloud_context", lambda cloud, cluster_name: ("aws", "acc"))
    monkeypatch.setattr(local_csi_cli, "_resolve_cluster_vms", lambda name, cloud, context_id: {"master": {"ip": "1.2.3.4"}, "workers": []})
    monkeypatch.setattr(local_csi_cli, "_resolve_ssh_key_for_cluster", lambda name: "/k")
    monkeypatch.setattr(local_csi_cli, "_ssh_cmd", fake_ssh)

    result = runner.invoke(local_csi_cli.local_csi_app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["csidriver"] is True
    assert payload["daemonset"] == {"desired": 1, "ready": 1}
    assert payload["pods"][0]["mounts"]["bridges"]["brd-2"]["connected"] is False

    result = runner.invoke(local_csi_cli.local_csi_app, ["status"])
    assert result.exit_code == 0, result.output
    assert "present" in result.stdout
    assert "1/2" in result.stdout
    assert "relay refused" in result.stdout
