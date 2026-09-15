"""The gateway agent: the watch, the resync, and what it writes back."""

from __future__ import annotations

import json
import os

import pytest

from ..csi.node_mount_gateway import NODE_MOUNT_GATEWAY_VOLUME_NAME, STATE_READY, NodeMountGateway, PodRef
from ..csi.node_mount_gateway_agent import NodeMountGatewayAgent
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
        (root / "pods" / uid / "volumes" / "kubernetes.io~empty-dir" / NODE_MOUNT_GATEWAY_VOLUME_NAME).mkdir(parents=True)
    return root


@pytest.fixture
def gateway(tmp_path, shared, kubelet) -> NodeMountGateway:
    return NodeMountGateway(
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
    agent = NodeMountGatewayAgent(gateway, pods)

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
    agent = NodeMountGatewayAgent(gateway, pods)

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
    agent = NodeMountGatewayAgent(gateway, pods)
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
    agent = NodeMountGatewayAgent(gateway, pods)
    agent.resync()
    assert os.path.isdir(gateway.pod_tree(POD_A))

    pods.pods = []
    agent.resync()

    assert not os.path.isdir(gateway.pod_tree(POD_A))


def test_a_terminating_pod_is_released_and_not_counted_as_live(gateway):
    subject = pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))
    pods = FakePods([subject])
    agent = NodeMountGatewayAgent(gateway, pods)
    agent.resync()

    subject.terminating = True
    agent.resync()

    assert not os.path.isdir(gateway.pod_tree(POD_A))


def test_the_watch_reconciles_what_it_delivers(gateway):
    quiet = pod(POD_A)
    pods = FakePods([quiet], events=[pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = NodeMountGatewayAgent(gateway, pods)

    agent.run_once()

    assert pods.watches == 1
    assert json.loads(pods.written[-1][1])["mounted"] == ["eric"]


def test_a_watch_that_breaks_does_not_stop_the_agent(gateway):
    class Broken(FakePods):
        def watch_pods(self, timeout: int):
            raise RuntimeError("connection reset")

    pods = Broken([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = NodeMountGatewayAgent(gateway, pods)

    agent.run_once()  # must not raise: a watch is allowed to break

    assert json.loads(pods.written[-1][1])["mounted"] == ["eric"]


def test_an_api_that_refuses_the_answer_does_not_lose_the_mount(gateway):
    class Refusing(FakePods):
        def set_ready(self, pod: PodRef, value: str) -> None:
            raise RuntimeError("403")

    pods = Refusing([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))])
    agent = NodeMountGatewayAgent(gateway, pods)

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
    NodeMountGatewayAgent(gateway, pods).resync()

    snapshot = gateway.snapshot()["pods"]
    assert set(snapshot) == {POD_A, POD_B}
    assert list(snapshot[POD_A]["mounts"]) == ["eric"]
    assert list(snapshot[POD_B]["mounts"]) == ["nina"]


def test_a_pod_the_agent_cannot_list_is_not_a_crash(gateway):
    class Blind(FakePods):
        def list_pods(self):
            raise RuntimeError("the API server is not answering")

    agent = NodeMountGatewayAgent(gateway, Blind([]))

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
    NodeMountGatewayAgent(gateway, pods).resync()
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


# ---------------------------------------------------------------------------
# Reading a Secret from Kubernetes
# ---------------------------------------------------------------------------


class _Meta:
    def __init__(self, owners):
        self.owner_references = owners


class _Secret:
    def __init__(self, data, owners):
        self.data = data
        self.metadata = _Meta(owners)


class _Owner:
    def __init__(self, uid):
        self.uid = uid


class _Api:
    def __init__(self, secret=None, error=None):
        self.secret = secret
        self.error = error
        self.asked: list[tuple[str, str]] = []

    def read_namespaced_secret(self, name, namespace):
        self.asked.append((namespace, name))
        if self.error:
            raise self.error
        return self.secret


def _credentials(api, namespace="datalayer-runtimes"):
    from ..csi.node_mount_gateway_agent import KubernetesCredentials

    return KubernetesCredentials(api, namespace)


def test_a_secret_the_pod_owns_is_read():
    import base64

    from ..csi.node_mount_gateway import NodeMountGatewayError  # noqa: F401 - asserted by absence

    api = _Api(_Secret({"key": base64.b64encode(b"token").decode()}, [_Owner(POD_A)]))

    data = _credentials(api).read_secret("datalayer-runtimes", "mount-1", POD_A)

    assert data == {"key": b"token"}
    assert api.asked == [("datalayer-runtimes", "mount-1")]


def test_a_secret_the_pod_does_not_own_is_refused():
    from ..csi.node_mount_gateway import ERROR_SECRET_REFUSED, NodeMountGatewayError

    api = _Api(_Secret({}, [_Owner("some-other-pod")]))

    with pytest.raises(NodeMountGatewayError) as raised:
        _credentials(api).read_secret("datalayer-runtimes", "companion-secret", POD_A)

    # This is what stops a grant from naming the companion's key or another
    # tenant's bridge token: RBAC cannot narrow to one Secret, so ownership does.
    assert raised.value.code == ERROR_SECRET_REFUSED


def test_a_secret_nothing_owns_is_refused():
    from ..csi.node_mount_gateway import NodeMountGatewayError

    api = _Api(_Secret({}, []))

    with pytest.raises(NodeMountGatewayError):
        _credentials(api).read_secret("datalayer-runtimes", "platform-key", POD_A)


def test_a_secret_outside_the_watched_namespace_is_refused_without_asking():
    from ..csi.node_mount_gateway import NodeMountGatewayError

    api = _Api(_Secret({}, [_Owner(POD_A)]))

    with pytest.raises(NodeMountGatewayError):
        _credentials(api).read_secret("kube-system", "anything", POD_A)

    assert api.asked == []


def test_a_forbidden_read_and_a_missing_secret_are_the_same_answer():
    from ..csi.node_mount_gateway import ERROR_SECRET_REFUSED, NodeMountGatewayError

    api = _Api(error=RuntimeError("403 Forbidden"))

    with pytest.raises(NodeMountGatewayError) as raised:
        _credentials(api).read_secret("datalayer-runtimes", "mount-1", POD_A)

    # Telling the two apart would say whether a Secret exists to somebody who
    # may not read it.
    assert raised.value.code == ERROR_SECRET_REFUSED


def test_the_default_agent_reads_nothing():
    from ..csi.node_mount_gateway import ERROR_SECRET_REFUSED, NodeMountGatewayError, NoCredentials

    with pytest.raises(NodeMountGatewayError) as raised:
        NoCredentials().read_secret("datalayer-runtimes", "mount-1", POD_A)

    assert raised.value.code == ERROR_SECRET_REFUSED


# ---------------------------------------------------------------------------
# Sending each grant to the runner for its kind
# ---------------------------------------------------------------------------


class _Runner:
    def __init__(self, name, pid):
        self.name = name
        self.pid = pid
        self.started: list[str] = []
        self.stopped: list[int] = []
        self.living: set[int] = set()

    def start(self, *, kind, source, target, read_only, credential):
        self.started.append(kind)
        self.living.add(self.pid)
        return self.pid

    def alive(self, pid):
        return pid in self.living

    def stop(self, pid, target):
        self.stopped.append(pid)
        self.living.discard(pid)


def _router():
    from ..csi.node_mount_gateway_agent import ProcessRouter

    bridges = _Runner("local-bridge", 101)
    buckets = _Runner("cloud-storage", 202)
    return ProcessRouter({"local-bridge": bridges, "cloud-storage": buckets}), bridges, buckets


def _start(router, kind):
    return router.start(kind=kind, source="s", target="/t", read_only=False, credential={})


def test_each_kind_goes_to_its_own_runner():
    router, bridges, buckets = _router()

    _start(router, "local-bridge")
    _start(router, "cloud-storage")

    # Two kinds with nothing in common beyond being processes: one dials a
    # relay, the other mounts a bucket. Two runners behind one protocol beats
    # one runner with a branch in it.
    assert bridges.started == ["local-bridge"]
    assert buckets.started == ["cloud-storage"]


def test_a_kind_nobody_serves_is_refused_by_name():
    from ..csi.node_mount_gateway import ERROR_PROCESS_UNSUPPORTED, NodeMountGatewayError

    router, _bridges, _buckets = _router()

    with pytest.raises(NodeMountGatewayError) as raised:
        _start(router, "dataset")

    # Which tells an operator to turn a switch on, rather than to go looking
    # for a bug.
    assert raised.value.code == ERROR_PROCESS_UNSUPPORTED
    assert "dataset" in str(raised.value)


def test_a_pid_is_stopped_by_the_runner_that_started_it():
    router, bridges, buckets = _router()
    pid = _start(router, "cloud-storage")

    router.stop(pid, "/t")

    assert buckets.stopped == [pid]
    assert bridges.stopped == []


def test_a_pid_inherited_from_a_gone_agent_is_still_alive():
    router, _bridges, buckets = _router()
    buckets.living.add(999)

    # Saying "dead" because the router was replaced would take a working
    # mount away from a sandbox.
    assert router.alive(999) is True
    assert router.alive(12345) is False


def test_the_watch_is_closed_even_when_the_agent_stops_mid_stream(gateway):
    """Walking away from the generator leaves its HTTP stream open.

    Prompt collection under refcounting usually hides this. A process that
    opens a watch every few minutes for months should not be relying on that,
    and stopping mid-stream is exactly when it happens.
    """
    closed: list[bool] = []

    class _Watch:
        def __init__(self, pods):
            self._pods = pods

        def __iter__(self):
            return iter(self._pods)

        def close(self):
            closed.append(True)

    class Pods(FakePods):
        def watch_pods(self, timeout: int):
            return _Watch([pod(POD_A, annotation(mount("home/users/01H-eric", "eric")))] * 5)

    agent = NodeMountGatewayAgent(gateway, Pods([]))
    agent._stop.set()

    agent.run_once()

    assert closed == [True]


def test_the_watch_is_closed_when_it_breaks(gateway):
    closed: list[bool] = []

    class _Watch:
        def __iter__(self):
            raise RuntimeError("connection reset")

        def close(self):
            closed.append(True)

    class Pods(FakePods):
        def watch_pods(self, timeout: int):
            return _Watch()

    NodeMountGatewayAgent(gateway, Pods([])).run_once()

    assert closed == [True]
