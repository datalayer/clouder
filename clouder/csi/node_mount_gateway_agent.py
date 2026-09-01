"""The node agent: the Kubernetes half of the Node Mount Gateway.

It watches the pods scheduled to its own node, reconciles each one's tree to
the mount set its ``gateway-mounts`` annotation asks for, and answers on
``gateway-mounts-ready`` with the hash of what it actually mounted. That hash
is what makes the answer unambiguous: an agent that answered for the previous
mount set has not answered for this one, however recently it did.

The Kubernetes client sits behind :class:`PodSource` so the reconciliation
loop can be tested without a cluster — the same reason the CSI driver takes a
:class:`~clouder.csi.mounter.Mounter`. :class:`KubernetesPods` is the real one;
``FakePods`` in the tests is the other.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Iterable, Iterator, Protocol

from .git_materializer import DEFAULT_CLONE_TIMEOUT_SECONDS
from .node_mount_gateway import (
    ERROR_PROCESS_UNSUPPORTED,
    ERROR_SECRET_REFUSED,
    NODE_MOUNT_GATEWAY_MOUNTS_ANNOTATION,
    NODE_MOUNT_GATEWAY_READY_ANNOTATION,
    NodeMountGatewayError,
    NodeMountGateway,
    NoCredentials,
    PodRef,
    Report,
)

log = logging.getLogger("clouder.csi.node_mount_gateway.agent")

#: How often the agent reconciles everything it can see, whatever the watch
#: did or did not deliver. A watch that silently stopped is the failure this
#: catches, and it is the reason the agent is not watch-only.
DEFAULT_RESYNC_INTERVAL = 60.0

#: How long one watch call runs before the agent resyncs and watches again.
DEFAULT_WATCH_TIMEOUT = 300


class PodSource(Protocol):
    """The pod operations the agent needs, and nothing else."""

    def list_pods(self) -> list[PodRef]:
        """Every pod on this node."""

    def watch_pods(self, timeout: int) -> Iterator[PodRef]:
        """Pods as they change, until ``timeout`` seconds have passed."""

    def set_ready(self, pod: PodRef, value: str) -> None:
        """Write the agent's answer to the pod's ready annotation."""


class NodeMountGatewayAgent:
    """Reconciles this node's pods, on a watch and on a timer."""

    def __init__(
        self,
        gateway: NodeMountGateway,
        pods: PodSource,
        *,
        resync_interval: float = DEFAULT_RESYNC_INTERVAL,
        watch_timeout: int = DEFAULT_WATCH_TIMEOUT,
    ) -> None:
        self.gateway = gateway
        self.pods = pods
        self.resync_interval = resync_interval
        self.watch_timeout = watch_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_resync = 0.0

    # -- one Pod -----------------------------------------------------------

    def reconcile_pod(self, pod: PodRef) -> Report | None:
        """Apply one pod's annotation and write the answer back.

        The answer is only written when it changed. A Pod whose mounts are
        already what they should be must not produce an API write on every
        resync: a runtime lives for hours, and a write per pod per minute is
        how a controller becomes the thing that overloads the API server.
        """
        report = self.gateway.reconcile(pod)
        value = report.encode()
        if value == (pod.ready_annotation or ""):
            return report
        try:
            self.pods.set_ready(pod, value)
        except Exception as exc:  # noqa: BLE001 - the API is allowed to be unavailable
            log.warning("pod %s: could not write the gateway answer: %s", pod.name or pod.uid, exc)
        return report

    # -- every Pod ---------------------------------------------------------

    def resync(self) -> list[Report]:
        """Reconcile every pod on the node, then release the trees of pods that are gone."""
        try:
            pods = self.pods.list_pods()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not list pods: %s", exc)
            return []
        reports: list[Report] = []
        for pod in pods:
            report = self.reconcile_pod(pod)
            if report is not None:
                reports.append(report)
        self.gateway.release_unknown(pod.uid for pod in pods if not pod.terminating)
        self._last_resync = time.monotonic()
        return reports

    def run_once(self) -> None:
        """One pass: resync, then follow the watch until it times out.

        The watch is closed on the way out, whichever way that is. Walking
        away from a generator leaves the HTTP stream behind it open until the
        interpreter collects it — prompt under refcounting, and not something
        a process that does this every few minutes for months should be
        relying on.
        """
        self.resync()
        watch = None
        try:
            # Inside the try: a watch that cannot even be opened — the API
            # server refusing, a resource version the server has forgotten —
            # is a thing to log and come back from, not to end the agent on.
            watch = self.pods.watch_pods(self.watch_timeout)
            for pod in watch:
                if self._stop.is_set():
                    return
                self.reconcile_pod(pod)
                if time.monotonic() - self._last_resync >= self.resync_interval:
                    self.resync()
        except Exception as exc:  # noqa: BLE001 - a watch is allowed to break
            log.warning("pod watch ended: %s", exc)
        finally:
            close = getattr(watch, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - closing must not raise
                    log.debug("pod watch would not close: %s", exc)

    def run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(1.0)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="mount-gateway", daemon=True)
        self._thread.start()
        log.info("Node Mount Gateway agent started (root %s)", self.gateway.gateway_root)

    def close(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def snapshot(self) -> dict[str, Any]:
        return self.gateway.snapshot()


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


class KubernetesPods:
    """The real pod source: the API server, scoped to one node.

    The field selector is the whole of the agent's authority in practice — it
    only ever sees pods kubelet placed here — and its RBAC is `watch` on pods
    plus `patch` on their annotations, which is what a role can be checked
    against.
    """

    def __init__(self, node_name: str, namespace: str | None = None) -> None:
        from kubernetes import client, config  # imported here: the tests never need it

        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001 - a laptop running against a kubeconfig
            config.load_kube_config()
        self.node_name = node_name
        self.namespace = namespace
        self.api = client.CoreV1Api()
        self._resource_version = ""

    def _selector(self) -> str:
        return f"spec.nodeName={self.node_name}"

    def _list(self, **kwargs):
        if self.namespace:
            return self.api.list_namespaced_pod(self.namespace, field_selector=self._selector(), **kwargs)
        return self.api.list_pod_for_all_namespaces(field_selector=self._selector(), **kwargs)

    @staticmethod
    def _ref(pod: Any) -> PodRef:
        metadata = pod.metadata
        annotations = metadata.annotations or {}
        return PodRef(
            uid=str(metadata.uid or ""),
            name=str(metadata.name or ""),
            namespace=str(metadata.namespace or ""),
            terminating=metadata.deletion_timestamp is not None,
            annotation=annotations.get(NODE_MOUNT_GATEWAY_MOUNTS_ANNOTATION, "") or "",
            ready_annotation=annotations.get(NODE_MOUNT_GATEWAY_READY_ANNOTATION, "") or "",
        )

    def list_pods(self) -> list[PodRef]:
        response = self._list()
        self._resource_version = response.metadata.resource_version or ""
        return [self._ref(pod) for pod in response.items if pod.metadata and pod.metadata.uid]

    def watch_pods(self, timeout: int) -> Iterator[PodRef]:
        from kubernetes import watch as k8s_watch

        watcher = k8s_watch.Watch()
        kwargs: dict[str, Any] = {"timeout_seconds": timeout, "field_selector": self._selector()}
        if self._resource_version:
            kwargs["resource_version"] = self._resource_version
        stream = (
            watcher.stream(self.api.list_namespaced_pod, self.namespace, **kwargs)
            if self.namespace
            else watcher.stream(self.api.list_pod_for_all_namespaces, **kwargs)
        )
        for event in stream:
            pod = event.get("object")
            if pod is None or not getattr(pod, "metadata", None) or not pod.metadata.uid:
                continue
            self._resource_version = pod.metadata.resource_version or self._resource_version
            ref = self._ref(pod)
            if event.get("type") == "DELETED":
                ref.terminating = True
            yield ref

    def set_ready(self, pod: PodRef, value: str) -> None:
        body = {"metadata": {"annotations": {NODE_MOUNT_GATEWAY_READY_ANNOTATION: value}}}
        if pod.namespace:
            self.api.patch_namespaced_pod(name=pod.name, namespace=pod.namespace, body=body)


class ProcessRouter:
    """Sends each grant to the runner for its kind, and refuses the rest.

    Two kinds are process-backed and they have nothing in common beyond that
    — one dials a relay, the other mounts a bucket — so they are two runners
    behind one protocol rather than one runner with a branch in it. A kind
    nobody serves is refused by name, which is what tells an operator to turn
    a switch on rather than to go looking for a bug.
    """

    def __init__(self, runners: dict[str, Any]) -> None:
        self._runners = dict(runners)
        self._by_pid: dict[int, Any] = {}

    def start(self, *, kind, source, target, read_only, credential) -> int:
        runner = self._runners.get(kind)
        if runner is None:
            raise NodeMountGatewayError(
                ERROR_PROCESS_UNSUPPORTED,
                f"this node agent does not serve '{kind}' mounts",
            )
        pid = runner.start(
            kind=kind, source=source, target=target, read_only=read_only, credential=credential
        )
        self._by_pid[pid] = runner
        return pid

    def alive(self, pid: int) -> bool:
        runner = self._by_pid.get(pid)
        if runner is not None:
            return runner.alive(pid)
        # Inherited from an agent that is gone: any runner can answer whether
        # a pid is alive, and saying "dead" because the router was replaced
        # would take a working mount away from a sandbox.
        return any(runner.alive(pid) for runner in self._runners.values())

    def stop(self, pid: int, target: str) -> None:
        runner = self._by_pid.pop(pid, None)
        for candidate in [runner] if runner is not None else list(self._runners.values()):
            candidate.stop(pid, target)


class KubernetesCredentials:
    """Reads a Secret a grant names, and only one the pod itself owns.

    The RBAC behind this is a **Role**, not a ClusterRole rule: the agent can
    `get` a Secret in the runtimes namespace and nowhere else. That is still
    broader than the one Secret a mount needs, because a name cannot be known
    in advance and `resourceNames` cannot be written for it — so the narrowing
    is done here, against the Pod: a Secret the Pod does not own is refused
    before its value is read into anything.

    Which is what stops a grant from naming the companion's API key, another
    tenant's bridge token, or a platform Secret that happens to live in the
    same namespace.
    """

    def __init__(self, api, namespace: str | None = None) -> None:
        self.api = api
        self.namespace = namespace

    def read_secret(self, namespace: str, name: str, pod_uid: str) -> dict[str, bytes]:
        import base64

        target = namespace or self.namespace or ""
        if self.namespace and target != self.namespace:
            raise NodeMountGatewayError(
                ERROR_SECRET_REFUSED,
                f"Secret '{name}' is outside the namespace this agent reads",
            )
        try:
            secret = self.api.read_namespaced_secret(name=name, namespace=target)
        except Exception as exc:  # noqa: BLE001 - a 403 and a 404 are the same answer here
            raise NodeMountGatewayError(
                ERROR_SECRET_REFUSED, f"Secret '{name}' could not be read: {exc}"
            ) from exc

        owners = (getattr(secret.metadata, "owner_references", None) or []) if secret.metadata else []
        if not any(str(getattr(owner, "uid", "")) == pod_uid for owner in owners):
            # The Operator creates a mount's Secret owned by the Pod, so its
            # lifetime is the Pod's and its reader is this Pod's agent. A
            # Secret owned by anything else is not this mount's to read.
            raise NodeMountGatewayError(
                ERROR_SECRET_REFUSED,
                f"Secret '{name}' is not owned by the pod it was granted to",
            )
        return {
            str(key): base64.b64decode(value)
            for key, value in (secret.data or {}).items()
        }


def build_agent(
    *,
    mounter,
    node_name: str,
    namespace: str | None,
    shared_root: str,
    gateway_root: str,
    kubelet_dir: str,
    max_mounts_per_pod: int,
    max_mounts_per_node: int,
    resync_interval: float = DEFAULT_RESYNC_INTERVAL,
    credentials: bool = False,
    local_bridges: bool = False,
    buckets: bool = False,
    repositories: bool = False,
    clone_timeout: int = DEFAULT_CLONE_TIMEOUT_SECONDS,
    relay_host: str = "",
    allow_insecure_relay: bool = False,
) -> NodeMountGatewayAgent:
    pods = KubernetesPods(node_name, namespace)
    runners = {}
    if local_bridges:
        from .bridge_processes import LOCAL_BRIDGE_KIND, BridgeProcesses

        # The CSI driver's filesystem, reached through the gateway instead of
        # a CSI volume: one implementation of the bridge, two ways in.
        runners[LOCAL_BRIDGE_KIND] = BridgeProcesses(
            mounter, relay_host=relay_host, allow_insecure_relay=allow_insecure_relay
        )
    if buckets:
        from .bucket_processes import CLOUD_STORAGE_KIND, BucketProcesses

        runners[CLOUD_STORAGE_KIND] = BucketProcesses(mounter)
    processes = ProcessRouter(runners) if runners else None
    materializer = None
    if repositories:
        from .git_materializer import GitMaterializer

        # Checkouts live beside the mounts rather than under them: they are
        # shared between Pods, and one Pod's directory being released must not
        # take away the repository another is reading.
        materializer = GitMaterializer(
            os.path.join(gateway_root, "content"), timeout=clone_timeout
        )
    gateway = NodeMountGateway(
        mounter,
        shared_root=shared_root,
        gateway_root=gateway_root,
        kubelet_dir=kubelet_dir,
        max_mounts_per_pod=max_mounts_per_pod,
        max_mounts_per_node=max_mounts_per_node,
        # Off unless the deployment says otherwise, and separate from the
        # gateway's own switch: reading Secrets is the one thing this agent
        # does that its RBAC would otherwise forbid outright.
        credentials=KubernetesCredentials(pods.api, namespace) if credentials else NoCredentials(),
        processes=processes,
        materializer=materializer,
    )
    return NodeMountGatewayAgent(gateway, pods, resync_interval=resync_interval)


def released_summary(released: Iterable[str]) -> str:
    names = sorted(released)
    return ", ".join(names) if names else "nothing"
