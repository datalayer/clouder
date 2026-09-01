"""Render the datalayer-node-mounts chart and check what kubelet depends on."""

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
    / "datalayer-node-mounts"
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
            "datalayer-node-mounts",
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


@pytest.fixture(scope="module")
def with_gateway() -> dict[tuple[str, str], dict]:
    if not CHART.is_dir():
        pytest.skip(f"chart not found at {CHART}")
    result = subprocess.run(
        [
            "helm",
            "template",
            "datalayer-node-mounts",
            str(CHART),
            "--namespace",
            "datalayer-runtimes",
            "--set",
            "relay.host=r1.datalayer.run",
            "--set",
            "nodeMountGateway.enabled=true",
            "--set",
            "nodeMountGateway.sharedFilesystemClaim=datalayer-shared-fs",
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
    daemonset = rendered[("DaemonSet", "datalayer-node-mounts")]
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
    policy = rendered[("NetworkPolicy", "datalayer-node-mounts")]
    assert policy["spec"]["policyTypes"] == ["Egress"]
    egress = policy["spec"]["egress"]
    ports = {(p["protocol"], p["port"]) for rule in egress for p in rule.get("ports", [])}
    assert ("TCP", 443) in ports
    assert ("UDP", 53) in ports and ("TCP", 53) in ports
    relay_rules = [rule for rule in egress if any(p["port"] == 443 for p in rule["ports"])]
    assert relay_rules and relay_rules[0]["to"][0]["ipBlock"]["cidr"] == "203.0.113.0/24"


def test_rbac_and_service_account(rendered):
    assert ("ServiceAccount", "datalayer-node-mounts") in rendered
    binding = rendered[("ClusterRoleBinding", "datalayer-node-mounts")]
    assert binding["subjects"][0]["namespace"] == "datalayer-runtimes"
    role = rendered[("ClusterRole", "datalayer-node-mounts")]
    verbs = {verb for rule in role["rules"] for verb in rule["verbs"]}
    assert verbs <= {"get", "create", "patch"}, "nodes: get and events: create, patch, nothing more"
    assert not any("secrets" in rule["resources"] for rule in role["rules"]), "the token arrives in the request"


# ---------------------------------------------------------------------------
# The Node Mount Gateway
# ---------------------------------------------------------------------------


def test_the_gateway_is_off_by_default(rendered):
    driver = rendered[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]["containers"][0]
    assert "--node-mount-gateway" not in driver["args"]
    assert not any(m["mountPath"] == "/mnt/shared-fs" for m in driver["volumeMounts"])
    role = rendered[("ClusterRole", "datalayer-node-mounts")]
    assert not any("pods" in rule["resources"] for rule in role["rules"])


def test_the_gateway_mounts_the_claim_and_its_own_tree(with_gateway):
    pod = with_gateway[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]
    driver = pod["containers"][0]
    assert "--node-mount-gateway" in driver["args"]

    mounts = {m["mountPath"]: m for m in driver["volumeMounts"]}
    # Bidirectional, or a bind made inside the tree never reaches the host,
    # and a mount that does not reach the host never reaches a pod.
    assert mounts["/var/lib/datalayer/node-mount-gateway"]["mountPropagation"] == "Bidirectional"
    assert "/mnt/shared-fs" in mounts

    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["gateway-root"]["hostPath"]["type"] == "DirectoryOrCreate"
    assert volumes["shared-fs"]["persistentVolumeClaim"]["claimName"] == "datalayer-shared-fs"


def test_the_gateway_needs_a_claim_named(tmp_path):
    if not CHART.is_dir():
        pytest.skip(f"chart not found at {CHART}")
    result = subprocess.run(
        [
            "helm",
            "template",
            "datalayer-node-mounts",
            str(CHART),
            "--set",
            "nodeMountGateway.enabled=true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # Turning the gateway on without the claim would deploy an agent with
    # nothing to bind, and the failure would surface as launches that never
    # become ready. Fail at install instead.
    assert result.returncode != 0
    assert "sharedFilesystemClaim is required" in (result.stderr + result.stdout)


def test_the_gateway_may_read_pods_and_write_one_annotation(with_gateway):
    role = with_gateway[("ClusterRole", "datalayer-node-mounts")]
    pods = [rule for rule in role["rules"] if "pods" in rule["resources"]]
    assert pods and set(pods[0]["verbs"]) == {"get", "list", "watch", "patch"}
    # Not delete, not create, not eviction, and still no Secret: the pod
    # annotation is the whole of the gateway's interface.
    assert not {"delete", "create", "update"} & set(pods[0]["verbs"])
    assert not any("pods/eviction" in rule["resources"] for rule in role["rules"])
    assert not any("secrets" in rule["resources"] for rule in role["rules"])


def test_the_gateway_may_reach_the_api_server(with_gateway):
    egress = with_gateway[("NetworkPolicy", "datalayer-node-mounts")]["spec"]["egress"]
    # The first rule is the API server, and it carries no `to`: with no CIDR
    # configured the port is open to any destination, which is what an
    # unconfigured control-plane address has to mean.
    assert egress[0]["ports"] == [{"protocol": "TCP", "port": 443}]
    assert "to" not in egress[0]


def test_the_gateway_runs_in_the_driver_that_already_has_the_privilege(with_gateway):
    containers = with_gateway[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]["containers"]
    # One node component, not two: a node has one mount table, and two things
    # pretending to own it is how a leak goes unnoticed.
    assert {c["name"] for c in containers} == {"driver", "registrar"}


def test_the_metrics_are_scraped_and_the_leak_is_alerted(tmp_path):
    if not CHART.is_dir():
        pytest.skip(f"chart not found at {CHART}")
    result = subprocess.run(
        [
            "helm", "template", "datalayer-node-mounts", str(CHART),
            "--set", "nodeMountGateway.enabled=true",
            "--set", "nodeMountGateway.sharedFilesystemClaim=datalayer-shared-fs",
            "--set", "monitoring.prometheusRule=true",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    documents = {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc
    }

    pod = documents[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["metadata"]
    assert pod["annotations"]["prometheus.io/path"] == "/metrics"

    rules = documents[("PrometheusRule", "datalayer-node-mounts")]["spec"]["groups"][0]["rules"]
    leak = next(rule for rule in rules if rule["alert"] == "DatalayerNodeMountGatewayLeakedMount")
    # A leaked mount is the failure that ends in a Pod stuck Terminating. It
    # must not depend on somebody running a CLI to notice it.
    assert leak["labels"]["severity"] == "critical"
    assert "datalayer_mount_gateway_leaked_total" in leak["expr"]


def test_there_is_no_rule_where_there_is_no_prometheus_operator(rendered):
    # An unappliable manifest fails the whole release, so the CRD-dependent
    # object is opt-in.
    assert not any(kind == "PrometheusRule" for kind, _ in rendered)


def _render(**settings):
    if not CHART.is_dir():
        pytest.skip(f"chart not found at {CHART}")
    args = ["helm", "template", "datalayer-node-mounts", str(CHART)]
    for key, value in settings.items():
        args += ["--set", f"{key.replace('__', '.')}={value}"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc
    }


def test_the_gateway_reads_no_secret_by_default(with_gateway):
    # A Home Folder is a sub-path of a claim the agent already holds, so the
    # default deployment needs no Secret and is not given one.
    assert not any(kind == "Role" for kind, _ in with_gateway)
    role = with_gateway[("ClusterRole", "datalayer-node-mounts")]
    assert not any("secrets" in rule["resources"] for rule in role["rules"])
    driver = with_gateway[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]["containers"][0]
    assert "--node-mount-gateway-credentials" not in driver["args"]


def test_credentials_are_a_namespaced_role_and_never_the_cluster_role():
    rendered = _render(
        nodeMountGateway__enabled="true",
        nodeMountGateway__sharedFilesystemClaim="datalayer-shared-fs",
        nodeMountGateway__credentials="true",
    )

    role = rendered[("Role", "datalayer-node-mounts-credentials")]
    # In the runtimes namespace and nowhere else: a ClusterRole rule would let
    # the agent read every Secret in the cluster, which is not what a mount
    # needs and is not what a compromised agent should be able to take.
    assert role["metadata"]["namespace"] == "datalayer-runtimes"
    assert role["rules"] == [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}]

    cluster_role = rendered[("ClusterRole", "datalayer-node-mounts")]
    assert not any("secrets" in rule["resources"] for rule in cluster_role["rules"])

    binding = rendered[("RoleBinding", "datalayer-node-mounts-credentials")]
    assert binding["roleRef"]["kind"] == "Role"
    assert binding["subjects"][0]["name"] == "datalayer-node-mounts"


def test_credentials_cannot_be_turned_on_without_the_gateway():
    # The switch is inside the gateway's own: an agent that mounts nothing has
    # no mount to read a credential for.
    rendered = _render(nodeMountGateway__credentials="true")
    assert not any(kind == "Role" for kind, _ in rendered)


def test_local_bridges_need_credentials_to_be_useful():
    rendered = _render(
        nodeMountGateway__enabled="true",
        nodeMountGateway__sharedFilesystemClaim="datalayer-shared-fs",
        nodeMountGateway__credentials="true",
        nodeMountGateway__localBridges="true",
    )
    args = rendered[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]["containers"][0]["args"]

    assert "--node-mount-gateway-local-bridges" in args
    # The mount token lives in the pod's Secret, so the two switches go
    # together; the agent refuses the grant otherwise, which is a mount a user
    # asked for and did not get.
    assert "--node-mount-gateway-credentials" in args


def test_local_bridges_are_off_by_default(with_gateway):
    args = with_gateway[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--node-mount-gateway-local-bridges" not in args


def test_buckets_are_a_switch_of_their_own():
    rendered = _render(
        nodeMountGateway__enabled="true",
        nodeMountGateway__sharedFilesystemClaim="datalayer-shared-fs",
        nodeMountGateway__credentials="true",
        nodeMountGateway__buckets="true",
    )
    args = rendered[("DaemonSet", "datalayer-node-mounts")]["spec"]["template"]["spec"]["containers"][0]["args"]

    assert "--node-mount-gateway-buckets" in args
    # Serving a bucket and serving a local bridge are separate decisions: a
    # cluster may want one without the other.
    assert "--node-mount-gateway-local-bridges" not in args
