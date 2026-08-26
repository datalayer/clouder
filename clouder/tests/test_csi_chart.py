"""Render the datalayer-local-csi chart and check what kubelet depends on."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "plane"
    / "etc"
    / "helm"
    / "charts"
    / "datalayer-local-csi"
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


@pytest.fixture(scope="module")
def rendered() -> dict[tuple[str, str], dict]:
    if not CHART.is_dir():
        pytest.skip(f"chart not found at {CHART}")
    result = subprocess.run(
        [
            "helm",
            "template",
            "datalayer-local-csi",
            str(CHART),
            "--namespace",
            "datalayer-runtimes",
            "--set",
            "relay.host=r1.datalayer.run",
            "--set",
            "relay.cidr=203.0.113.0/24",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in documents}


def test_csidriver(rendered):
    csidriver = rendered[("CSIDriver", "local.csi.datalayer.io")]
    spec = csidriver["spec"]
    assert spec["volumeLifecycleModes"] == ["Ephemeral"]
    assert spec["podInfoOnMount"] is True
    assert spec["attachRequired"] is False
    assert spec["fsGroupPolicy"] == "None"


def test_daemonset_mounts_and_sidecar(rendered):
    daemonset = rendered[("DaemonSet", "datalayer-local-csi")]
    assert daemonset["metadata"]["namespace"] == "datalayer-runtimes"
    pod = daemonset["spec"]["template"]["spec"]
    containers = {c["name"]: c for c in pod["containers"]}
    assert set(containers) == {"driver", "registrar"}

    driver = containers["driver"]
    assert driver["securityContext"]["privileged"] is True
    mounts = {m["mountPath"]: m for m in driver["volumeMounts"]}
    assert mounts["/csi"]["mountPropagation"] == "Bidirectional"
    assert mounts["/var/lib/kubelet/pods"]["mountPropagation"] == "Bidirectional"
    assert "/dev/fuse" in mounts
    assert "--endpoint" in " ".join(driver["args"]) or "CSI_ENDPOINT" in {e["name"] for e in driver["env"]}
    assert any(e["name"] == "NODE_ID" for e in driver["env"])

    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["plugin-dir"]["hostPath"]["path"] == "/var/lib/kubelet/plugins/local.csi.datalayer.io"
    assert volumes["pods-dir"]["hostPath"]["path"] == "/var/lib/kubelet/pods"
    assert volumes["fuse"]["hostPath"]["path"] == "/dev/fuse"
    assert volumes["registration-dir"]["hostPath"]["path"] == "/var/lib/kubelet/plugins_registry"

    registrar = containers["registrar"]
    assert "csi-node-driver-registrar" in registrar["image"]
    assert any("/var/lib/kubelet/plugins/local.csi.datalayer.io/csi.sock" in a for a in registrar["args"])

    probe = driver["livenessProbe"]["httpGet"]
    assert probe["path"] == "/healthz"


def test_network_policy_egress_is_the_relay_only(rendered):
    policy = rendered[("NetworkPolicy", "datalayer-local-csi")]
    assert policy["spec"]["policyTypes"] == ["Egress"]
    egress = policy["spec"]["egress"]
    ports = {(p["protocol"], p["port"]) for rule in egress for p in rule.get("ports", [])}
    assert ("TCP", 443) in ports
    assert ("UDP", 53) in ports and ("TCP", 53) in ports
    relay_rules = [rule for rule in egress if any(p["port"] == 443 for p in rule["ports"])]
    assert relay_rules and relay_rules[0]["to"][0]["ipBlock"]["cidr"] == "203.0.113.0/24"


def test_rbac_and_service_account(rendered):
    assert ("ServiceAccount", "datalayer-local-csi") in rendered
    binding = rendered[("ClusterRoleBinding", "datalayer-local-csi")]
    assert binding["subjects"][0]["namespace"] == "datalayer-runtimes"
    role = rendered[("ClusterRole", "datalayer-local-csi")]
    verbs = {verb for rule in role["rules"] for verb in rule["verbs"]}
    assert verbs <= {"get", "create", "patch"}, "nodes: get and events: create, patch, nothing more"
    assert not any("secrets" in rule["resources"] for rule in role["rules"]), "the token arrives in the request"
