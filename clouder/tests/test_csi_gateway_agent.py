"""The gateway agent: the watch, the resync, and what it writes back."""

from __future__ import annotations

import json
import os

import pytest

from ..csi.gateway import GATEWAY_VOLUME_NAME, STATE_READY, MountGateway, PodRef
from ..csi.gateway_agent import GatewayAgent
from ..csi.mounter import FakeMounter

POD_A = "aaaa1111-1111-1111-1111-111111111111"
POD_B = "bbbb2222-2222-2222-2222-222222222222"


def annotation(*mounts) -> str:
    return json.dumps({"mounts": list(mounts)})


def mount(source: str, target: str, mode: str = "rw") -> dict:
    return {"source": source, "target": target, "mode": mode, "allow_exec": True}


class FakePods:
    """A pod source that answers from a dict and records what was written."""

    def __init__(self, pods: list[PodRef], events: list[PodRef] | None = None):
        self.pods = pods
        self.events = events or []
        self.written: list[tuple[str, str]] = []
        self.watches = 0

    def list_pods(self) -> list[PodRef]:
        return list(self.pods)

    def watch_pods(self, timeout: int):
        self.watches += 1
        for pod in self.events:
            yield pod

    def set_ready(self, pod: PodRef, value: str) -> None:
        self.written.append((pod.uid, value))
        pod.ready_annotation = value


@pytest.fixture
def shared(tmp_path):
    root = tmp_path / "shared-fs"
    (root / "home/users/01H-eric").mkdir(parents=True)
    (root / "home/users/01H-nina").mkdir(parents=True)
    return root


@pytest.fixture
def kubelet(tmp_path):
    root = tmp_path / "kubelet"
    for uid in (POD_A, POD_B):
        (root / "pods" / uid / "volumes" / "kubernetes.io~empty-dir" / GATEWAY_VOLUME_NAME).mkdir(parents=True)
    return root


@pytest.fixture
def gateway(tmp_path, shared, kubelet) -> MountGateway:
    return MountGateway(
        FakeMounter(),
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(kubelet),
    )


def pod(uid: str, mounts: str = "", *, terminating: bool = False, ready: str = "") -> PodRef:
    return PodRef(
        uid=uid,
        name=f"jupyter-{uid[:4]}",
        namespace="datalayer-runtimes",
        terminating=terminating,
        annotation=mounts,
        ready_annotation=ready,
    )


def test_the_agent_answers_with_the_hash_of_what_it_mounted(gateway):
    pods = FakePods([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = GatewayAgent(gateway, pods)

    agent.resync()

    assert len(pods.written) == 1
    uid, value = pods.written[0]
    answer = json.loads(value)
    assert uid == POD_A
    assert answer["state"] == STATE_READY
    assert answer["mounted"] == ["eric"]
    assert answer["hash"]


def test_an_unchanged_pod_is_not_written_again(gateway):
    pods = FakePods([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = GatewayAgent(gateway, pods)

    agent.resync()
    agent.resync()
    agent.resync()

    # A runtime lives for hours and the agent resyncs every minute. One write
    # per pod per resync is how a controller becomes the thing that overloads
    # the API server.
    assert len(pods.written) == 1


def test_a_changed_annotation_is_answered_again(gateway):
    subject = pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))
    pods = FakePods([subject])
    agent = GatewayAgent(gateway, pods)
    agent.resync()

    subject.annotation = annotation(
        mount("home/users/01H-eric", "eric"), mount("home/users/01H-nina", "nina")
    )
    agent.resync()

    assert len(pods.written) == 2
    assert json.loads(pods.written[-1][1])["mounted"] == ["eric", "nina"]


def test_a_pod_that_is_gone_has_its_tree_released(gateway):
    subject = pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))
    pods = FakePods([subject])
    agent = GatewayAgent(gateway, pods)
    agent.resync()
    assert os.path.isdir(gateway.pod_tree(POD_A))

    pods.pods = []
    agent.resync()

    assert not os.path.isdir(gateway.pod_tree(POD_A))


def test_a_terminating_pod_is_released_and_not_counted_as_live(gateway):
    subject = pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))
    pods = FakePods([subject])
    agent = GatewayAgent(gateway, pods)
    agent.resync()

    subject.terminating = True
    agent.resync()

    assert not os.path.isdir(gateway.pod_tree(POD_A))


def test_the_watch_reconciles_what_it_delivers(gateway):
    quiet = pod(POD_A)
    pods = FakePods([quiet], events=[pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = GatewayAgent(gateway, pods)

    agent.run_once()

    assert pods.watches == 1
    assert json.loads(pods.written[-1][1])["mounted"] == ["eric"]


def test_a_watch_that_breaks_does_not_stop_the_agent(gateway):
    class Broken(FakePods):
        def watch_pods(self, timeout: int):
            raise RuntimeError("connection reset")

    pods = Broken([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = GatewayAgent(gateway, pods)

    agent.run_once()  # must not raise: a watch is allowed to break

    assert json.loads(pods.written[-1][1])["mounted"] == ["eric"]


def test_an_api_that_refuses_the_answer_does_not_lose_the_mount(gateway):
    class Refusing(FakePods):
        def set_ready(self, pod: PodRef, value: str) -> None:
            raise RuntimeError("403")

    pods = Refusing([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = GatewayAgent(gateway, pods)

    reports = agent.resync()

    # The folder is mounted whatever the API said; the answer is retried on
    # the next resync, and a pod that is mounted but unanswered is better
    # than one that is answered but unmounted.
    assert reports[0].mounted == ["eric"]
    assert gateway.snapshot()["pods"][POD_A]["mounts"]["eric"]["mounted"] is True


def test_two_pods_do_not_share_a_tree(gateway):
    pods = FakePods(
        [
            pod(POD_A, annotation(mount("home/users/01H-eric", "eric"))),
            pod(POD_B, annotation(mount("home/users/01H-nina", "nina"))),
        ]
    )
    GatewayAgent(gateway, pods).resync()

    snapshot = gateway.snapshot()["pods"]
    assert set(snapshot) == {POD_A, POD_B}
    assert list(snapshot[POD_A]["mounts"]) == ["eric"]
    assert list(snapshot[POD_B]["mounts"]) == ["nina"]


def test_a_pod_the_agent_cannot_list_is_not_a_crash(gateway):
    class Blind(FakePods):
        def list_pods(self):
            raise RuntimeError("the API server is not answering")

    agent = GatewayAgent(gateway, Blind([]))

    assert agent.resync() == []


# ---------------------------------------------------------------------------
# What an observer scrapes
# ---------------------------------------------------------------------------


def test_the_metrics_name_the_leak(gateway):
    from ..csi.health import HealthServer

    class _Driver:
        node_id = "worker-1"

        def snapshot(self):
            return {"driver": "local.csi.datalayer.io", "bridges": {"b1": {"connected": False}}}

    pods = FakePods([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    GatewayAgent(gateway, pods).resync()
    gateway.counters["leaked"] = 2

    text = HealthServer(_Driver(), gateway=gateway).metrics()

    assert 'datalayer_mount_gateway_leaked_total{node="worker-1"} 2' in text
    assert 'datalayer_mount_gateway_mounts{node="worker-1"} 1' in text
    assert 'datalayer_mount_gateway_pods{node="worker-1"} 1' in text
    assert 'datalayer_local_csi_bridges_disconnected{node="worker-1"} 1' in text
    # Prometheus refuses a body whose HELP and TYPE do not precede the sample.
    for line in text.splitlines():
        assert line.startswith("#") or "{" in line


def test_a_broken_snapshot_does_not_break_the_probe(gateway):
    from ..csi.health import HealthServer

    class _Driver:
        node_id = 'a "quoted" node'

        def snapshot(self):
            raise RuntimeError("no")

    # The metrics endpoint shares a process with the liveness probe. It may
    # report nothing; it may not take the driver down with it.
    text = HealthServer(_Driver(), gateway=gateway).metrics()
    assert r'node="a \"quoted\" node"' in text
