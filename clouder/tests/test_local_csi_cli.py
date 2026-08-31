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
        if "/gateway" in command:
            # 404 body: the gateway is not enabled on this node.
            return _Result('{"error": "the mount gateway is not enabled on this node"}')
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


def test_status_reports_the_mount_gateway_when_a_node_runs_one(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    pod_uid = "3f2b1a0c-1111-2222-3333-444455556666"
    gateway = {
        "gateway_root": "/var/lib/datalayer/mount-gateway",
        "shared_root": "/mnt/shared-fs",
        "counters": {"granted": 2, "revoked": 0, "failed": 0, "released": 0, "leaked": 1},
        "pods": {
            pod_uid: {
                "published": True,
                "mounts": {
                    "eric": {"source": "home/users/01H", "mode": "rw", "mounted": True},
                    "datalayer": {"source": "home/organizations/01J", "mode": "ro", "mounted": True},
                },
            }
        },
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
            return _Result(json.dumps({"driver": "local.csi.datalayer.io", "bridges": {}, "volumes": {}}))
        if "/gateway" in command:
            return _Result(json.dumps(gateway))
        raise AssertionError(command)

    monkeypatch.setattr(local_csi_cli, "_infer_cluster_name", lambda cluster: "demo")
    monkeypatch.setattr(local_csi_cli, "resolve_kubeadm_cloud_context", lambda cloud, cluster_name: ("aws", "acc"))
    monkeypatch.setattr(
        local_csi_cli,
        "_resolve_cluster_vms",
        lambda name, cloud, context_id: {"master": {"ip": "1.2.3.4"}, "workers": []},
    )
    monkeypatch.setattr(local_csi_cli, "_resolve_ssh_key_for_cluster", lambda name: "/k")
    monkeypatch.setattr(local_csi_cli, "_ssh_cmd", fake_ssh)

    result = runner.invoke(local_csi_cli.local_csi_app, ["status"])

    assert result.exit_code == 0, result.output
    assert "Mount gateway" in result.stdout
    assert "eric" in result.stdout and "datalayer" in result.stdout
    # A leaked mount is the one failure that must not be a log line nobody
    # reads: it is why a Pod sticks in Terminating.
    assert "could not be unmounted" in result.stdout


# ---------------------------------------------------------------------------
# `clouder local-csi verify`
# ---------------------------------------------------------------------------


def _cluster(monkeypatch, answers: dict):
    """Point the CLI at a fake master that answers kubectl by substring."""
    def fake_ssh(ip, user, key_path, command, check=True):
        # Longest needle first: a kubectl line matches several of these, and
        # the most specific one is the question actually being asked.
        for needle in sorted(answers, key=len, reverse=True):
            if needle in command:
                return _Result(answers[needle])
        return _Result("")

    monkeypatch.setattr(local_csi_cli, "_infer_cluster_name", lambda cluster: "demo")
    monkeypatch.setattr(local_csi_cli, "resolve_kubeadm_cloud_context", lambda cloud, cluster_name: ("aws", "acc"))
    monkeypatch.setattr(
        local_csi_cli,
        "_resolve_cluster_vms",
        lambda name, cloud, context_id: {"master": {"ip": "1.2.3.4"}, "workers": []},
    )
    monkeypatch.setattr(local_csi_cli, "_resolve_ssh_key_for_cluster", lambda name: "/k")
    monkeypatch.setattr(local_csi_cli, "_ssh_cmd", fake_ssh)


HEALTHY = {
    "get daemonset": "3/3",
    "findmnt": "shared",
    "/gateway": '{"pods": {}, "counters": {"leaked": 0}}',
    "DATALAYER_MOUNT_GATEWAY_ENABLED": "true",
    "persistentVolumeClaim.claimName": "datalayer-shared-fs",
    "can-i patch pods -n datalayer-runtimes --as=system:serviceaccount:datalayer-runtimes:datalayer-local-csi": "yes",
    "can-i patch pods -n datalayer-runtimes --as=system:serviceaccount:datalayer-runtimes:datalayer-runtimes-sa": "no",
    "can-i get secrets": "no",
    "containers[0].args": "[--endpoint,--mount-gateway]",
    "uname -r": "6.8.0-138-generic",
}


def _verify(monkeypatch, **overrides):
    answers = {**HEALTHY, **overrides}
    _cluster(monkeypatch, answers)
    result = runner.invoke(local_csi_cli.local_csi_app, ["verify", "--json"])
    return result, {check["name"]: check for check in json.loads(result.stdout)}


def test_a_healthy_gateway_passes(monkeypatch):
    result, checks = _verify(monkeypatch)

    assert result.exit_code == 0
    assert all(check["ok"] is not False for check in checks.values())


def test_propagation_that_is_not_shared_fails_and_says_what_to_run(monkeypatch):
    result, checks = _verify(monkeypatch, findmnt="private")

    # On a private node every grant succeeds and is invisible in the sandbox,
    # which is the worst way for this to fail.
    assert result.exit_code == 1
    assert checks["Mount propagation (rshared)"]["ok"] is False
    assert "make-rshared" in checks["Mount propagation (rshared)"]["fix"]


def test_the_operator_granting_with_no_agent_is_a_failure(monkeypatch):
    result, checks = _verify(monkeypatch, **{"/gateway": ""})

    # Pods would carry the volume and wait for mounts nobody makes. The
    # deployment order exists to prevent exactly this.
    assert result.exit_code == 1
    assert checks["Operator and agent agree"]["ok"] is False
    assert "FIRST" in checks["Operator and agent agree"]["fix"]


def test_a_runtime_that_could_grant_itself_a_mount_is_a_failure(monkeypatch):
    result, checks = _verify(
        monkeypatch,
        **{
            "can-i patch pods -n datalayer-runtimes --as=system:serviceaccount:datalayer-runtimes:datalayer-runtimes-sa": "yes"
        },
    )

    # A sandbox that can patch its own pod can mount any folder on the claim.
    # This is the exit gate's security claim, as a command rather than an
    # argument.
    assert result.exit_code == 1
    assert checks["A runtime may NOT grant itself a mount"]["ok"] is False


def test_secret_access_without_the_switch_is_a_failure(monkeypatch):
    result, checks = _verify(monkeypatch, **{"can-i get secrets": "yes"})

    # A permission nothing uses is a permission somebody else can.
    assert result.exit_code == 1
    assert checks["Agent Secret access matches its configuration"]["ok"] is False


def test_a_leaked_mount_is_reported(monkeypatch):
    result, checks = _verify(
        monkeypatch, **{"/gateway": '{"pods": {}, "counters": {"leaked": 2}}'}
    )

    assert result.exit_code == 1
    assert checks["No leaked mounts"]["ok"] is False
    assert "Terminating" in checks["No leaked mounts"]["fix"]


def test_a_gateway_that_is_simply_off_is_not_a_failure(monkeypatch):
    result, checks = _verify(
        monkeypatch,
        **{"/gateway": "", "DATALAYER_MOUNT_GATEWAY_ENABLED": "false"},
    )

    # Off is a deployment choice, not a fault. Reporting it as broken would
    # teach an operator to ignore this command.
    assert result.exit_code == 0
    assert checks["Operator and agent agree"]["ok"] is None


def test_a_kernel_without_mount_setattr_fails(monkeypatch):
    result, checks = _verify(monkeypatch, **{"uname -r": "5.4.0-generic"})

    # Without mount_setattr a read-only grant cannot be made read-only in the
    # sandbox at all, so the agent refuses the mount. Better to know before
    # a user asks for one.
    assert result.exit_code == 1
    assert checks["Kernel supports mount_setattr (5.12+)"]["ok"] is False
