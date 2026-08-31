"""The node agent: the Kubernetes half of the mount gateway.

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
import threading
import time
from typing import Any, Iterable, Iterator, Protocol

from .gateway import (
    GATEWAY_MOUNTS_ANNOTATION,
    GATEWAY_READY_ANNOTATION,
    MountGateway,
    PodRef,
    Report,
)

log = logging.getLogger("clouder.csi.gateway.agent")

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


class GatewayAgent:
    """Reconciles this node's pods, on a watch and on a timer."""

    def __init__(
        self,
        gateway: MountGateway,
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

    # -- one pod -----------------------------------------------------------

    def reconcile_pod(self, pod: PodRef) -> Report | None:
        """Apply one pod's annotation and write the answer back.

        The answer is only written when it changed. A pod whose mounts are
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

    # -- every pod ---------------------------------------------------------

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
        """One pass: resync, then follow the watch until it times out."""
        self.resync()
        try:
            for pod in self.pods.watch_pods(self.watch_timeout):
                if self._stop.is_set():
                    return
                self.reconcile_pod(pod)
                if time.monotonic() - self._last_resync >= self.resync_interval:
                    self.resync()
        except Exception as exc:  # noqa: BLE001 - a watch is allowed to break
            log.warning("pod watch ended: %s", exc)

    def run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(1.0)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="mount-gateway", daemon=True)
        self._thread.start()
        log.info("mount gateway agent started (root %s)", self.gateway.gateway_root)

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
            annotation=annotations.get(GATEWAY_MOUNTS_ANNOTATION, "") or "",
            ready_annotation=annotations.get(GATEWAY_READY_ANNOTATION, "") or "",
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
        body = {"metadata": {"annotations": {GATEWAY_READY_ANNOTATION: value}}}
        if pod.namespace:
            self.api.patch_namespaced_pod(name=pod.name, namespace=pod.namespace, body=body)


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
) -> GatewayAgent:
    gateway = MountGateway(
        mounter,
        shared_root=shared_root,
        gateway_root=gateway_root,
        kubelet_dir=kubelet_dir,
        max_mounts_per_pod=max_mounts_per_pod,
        max_mounts_per_node=max_mounts_per_node,
    )
    return GatewayAgent(
        gateway,
        KubernetesPods(node_name, namespace),
        resync_interval=resync_interval,
    )


def released_summary(released: Iterable[str]) -> str:
    names = sorted(released)
    return ", ".join(names) if names else "nothing"
