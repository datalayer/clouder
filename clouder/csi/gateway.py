"""The mount gateway: binding folders into a pod that is already running.

A Pod's volumes are fixed when it is created, so a Datalayer Runtime that
mounts content cannot be served from the prewarmed pool by adding a volume to
it. Every pooled pod instead carries one memory-backed ``emptyDir`` that its
runtime container mounts ``HostToContainer``, and this — running in the
privileged node DaemonSet, outside the tenant pod — binds real filesystems
into it afterwards.

The shape, and why it is this shape:

- the agent owns a tree per pod under ``<gateway-root>/pods/<pod-uid>``, makes
  it a shared mount, and binds that one tree into the pod's gateway volume
  directory. Every folder it grants is then a mount *inside its own tree*
  that propagates into the pod. Nothing the agent mounts is ever a mount
  kubelet sees directly beneath the ``emptyDir``;
- the gateway volume is memory-backed on purpose. kubelet must ``umount`` a
  tmpfs before it removes the directory, and ``umount`` of a mount that still
  has children fails. A leaked mount therefore sticks the Pod in
  ``Terminating`` — visible and recoverable — instead of letting kubelet's
  recursive delete walk into a user's home folder on the shared claim;
- the desired state is a pod annotation, so a restarted agent converges by
  reading the API rather than by anyone replaying a call, and there is no
  gateway API to authenticate and nothing inside the tenant pod can reach.

`clouder` cannot import `datalayer_common`, so the wire format is mirrored
here from :mod:`datalayer_common.mount_gateway`. ``test_csi_gateway.py`` holds
the mirror to the original.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Iterable

from .linux import resolve_beneath
from .mounter import MountError, Mounter

log = logging.getLogger("clouder.csi.gateway")

#: Mirrored from `datalayer_common.mount_gateway`.
GATEWAY_MOUNTS_ANNOTATION = "runtime-pools.datalayer.io/gateway-mounts"
GATEWAY_READY_ANNOTATION = "runtime-pools.datalayer.io/gateway-mounts-ready"
GATEWAY_VOLUME_NAME = "mount-gateway"

STATE_READY = "ready"
STATE_DEGRADED = "degraded"
STATE_FAILED = "failed"

ERROR_INVALID_TARGET = "GATEWAY_INVALID_TARGET"
ERROR_INVALID_SOURCE = "GATEWAY_INVALID_SOURCE"
ERROR_TOO_MANY_MOUNTS = "GATEWAY_TOO_MANY_MOUNTS"
ERROR_MOUNT_FAILED = "GATEWAY_MOUNT_FAILED"
ERROR_NOT_READY = "GATEWAY_NOT_READY"

DEFAULT_GATEWAY_ROOT = "/var/lib/datalayer/mount-gateway"
DEFAULT_SHARED_ROOT = "/mnt/shared-fs"
DEFAULT_KUBELET_DIR = "/var/lib/kubelet"
DEFAULT_MAX_MOUNTS_PER_POD = 32
DEFAULT_MAX_MOUNTS_PER_NODE = 512

_TARGET_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,126}$")
_POD_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class GatewayError(RuntimeError):
    """A grant that must not be applied, with the code to report."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Grant:
    """One folder a pod is granted, as the annotation names it."""

    source: str
    target: str
    mode: str = "rw"
    allow_exec: bool = True
    uid: str = ""
    kind: str = ""

    @property
    def read_only(self) -> bool:
        return self.mode == "ro"

    def key(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "mode": self.mode,
            "allow_exec": self.allow_exec,
        }


@dataclass
class PodRef:
    """What the agent needs to know about a pod, and nothing more."""

    uid: str
    name: str = ""
    namespace: str = ""
    terminating: bool = False
    annotation: str = ""
    ready_annotation: str = ""


@dataclass
class Report:
    """What the agent writes back to the pod."""

    applied_hash: str
    state: str
    mounted: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def encode(self) -> str:
        return json.dumps(
            {
                "hash": self.applied_hash,
                "state": self.state,
                "mounted": sorted(self.mounted),
                "failed": dict(sorted(self.failed.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


# ---------------------------------------------------------------------------
# The wire format
# ---------------------------------------------------------------------------


def _clean_source(value: Any) -> str:
    raw = str(value or "").strip().strip("/")
    if not raw or "\\" in raw or "\x00" in raw:
        raise GatewayError(ERROR_INVALID_SOURCE, f"source '{value}' is not a relative path")
    parts = [part for part in raw.split("/") if part]
    for part in parts:
        if part in (".", ".."):
            raise GatewayError(ERROR_INVALID_SOURCE, f"source '{raw}' walks outside the shared filesystem")
    return "/".join(parts)


def _clean_target(value: Any) -> str:
    raw = str(value or "").strip()
    if not _TARGET_RE.match(raw):
        raise GatewayError(ERROR_INVALID_TARGET, f"target '{raw}' is not one path segment")
    return raw


def parse_grants(annotation: Any) -> list[Grant]:
    """Read the mount set from a pod annotation.

    An entry that cannot be read is dropped and logged rather than failing the
    whole set: one malformed grant must not cost a user the folders that were
    written correctly beside it. An annotation that cannot be parsed at all is
    an empty set, which unmounts everything — the safe reading of "I do not
    know what this pod may reach".
    """
    payload: Any = annotation
    if isinstance(annotation, (str, bytes)) or annotation is None:
        if not annotation:
            return []
        try:
            payload = json.loads(annotation)
        except (TypeError, ValueError):
            log.warning("gateway annotation is not JSON; treating it as an empty mount set")
            return []
    if isinstance(payload, dict):
        payload = payload.get("mounts")
    if not isinstance(payload, (list, tuple)):
        return []

    seen: set[str] = set()
    grants: list[Grant] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            target = _clean_target(item.get("target"))
            source = _clean_source(item.get("source"))
        except GatewayError as exc:
            log.warning("dropping a grant: %s", exc)
            continue
        if target in seen:
            continue
        seen.add(target)
        mode = str(item.get("mode") or "rw").strip().lower()
        grants.append(
            Grant(
                source=source,
                target=target,
                mode=mode if mode in ("ro", "rw") else "rw",
                allow_exec=bool(item.get("allow_exec", True)),
                uid=str(item.get("uid") or ""),
                kind=str(item.get("kind") or ""),
            )
        )
    return sorted(grants, key=lambda grant: grant.target)


def grants_hash(grants: Iterable[Grant]) -> str:
    """A stable name for one mount set: what tells applied from asked for."""
    canonical = json.dumps([grant.key() for grant in grants], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


class MountGateway:
    """Reconciles one node's pods to the mount sets their annotations ask for.

    Everything here is idempotent, because a watch redelivers and a resync
    repeats: mounting a grant that is already mounted, releasing a pod that
    was already released and reconciling a pod whose annotation did not change
    all do nothing and say so.
    """

    def __init__(
        self,
        mounter: Mounter,
        *,
        shared_root: str = DEFAULT_SHARED_ROOT,
        gateway_root: str = DEFAULT_GATEWAY_ROOT,
        kubelet_dir: str = DEFAULT_KUBELET_DIR,
        max_mounts_per_pod: int = DEFAULT_MAX_MOUNTS_PER_POD,
        max_mounts_per_node: int = DEFAULT_MAX_MOUNTS_PER_NODE,
    ) -> None:
        self.mounter = mounter
        self.shared_root = os.path.normpath(shared_root)
        self.gateway_root = os.path.normpath(gateway_root)
        self.kubelet_dir = os.path.normpath(kubelet_dir)
        self.max_mounts_per_pod = max_mounts_per_pod
        self.max_mounts_per_node = max_mounts_per_node
        self.counters = {
            "granted": 0,
            "revoked": 0,
            "failed": 0,
            "released": 0,
            "leaked": 0,
        }

    # -- where things live -------------------------------------------------

    def pods_dir(self) -> str:
        return os.path.join(self.gateway_root, "pods")

    def state_dir(self) -> str:
        """State lives beside the trees, never inside one: a pod must not see it."""
        return os.path.join(self.gateway_root, "state")

    def pod_tree(self, pod_uid: str) -> str:
        return os.path.join(self.pods_dir(), _pod_uid(pod_uid))

    def target_path(self, pod_uid: str, target: str) -> str:
        return os.path.join(self.pod_tree(pod_uid), _clean_target(target))

    def pod_volume_dir(self, pod_uid: str) -> str:
        """The node directory kubelet made for the pod's gateway ``emptyDir``."""
        return os.path.join(
            self.kubelet_dir,
            "pods",
            _pod_uid(pod_uid),
            "volumes",
            "kubernetes.io~empty-dir",
            GATEWAY_VOLUME_NAME,
        )

    # -- reconciliation ----------------------------------------------------

    def reconcile(self, pod: PodRef) -> Report:
        """Make the pod's tree match its annotation, and say what happened."""
        if pod.terminating:
            self.release(pod.uid)
            return Report(applied_hash="", state=STATE_READY)

        grants = parse_grants(pod.annotation)
        wanted_hash = grants_hash(grants)

        if not grants:
            self.release(pod.uid)
            return Report(applied_hash=wanted_hash, state=STATE_READY)

        if len(grants) > self.max_mounts_per_pod:
            self.counters["failed"] += 1
            return Report(
                applied_hash=wanted_hash,
                state=STATE_FAILED,
                failed={"*": ERROR_TOO_MANY_MOUNTS},
            )
        if self.mounted_count() + len(grants) > self.max_mounts_per_node:
            self.counters["failed"] += 1
            return Report(
                applied_hash=wanted_hash,
                state=STATE_FAILED,
                failed={"*": ERROR_TOO_MANY_MOUNTS},
            )

        volume_dir = self.pod_volume_dir(pod.uid)
        if not os.path.isdir(volume_dir):
            # kubelet has not made the pod's volume yet, or this pod has no
            # gateway at all. Either way there is nowhere to propagate to, and
            # a resync will come back to it.
            return Report(
                applied_hash=wanted_hash,
                state=STATE_FAILED,
                failed={"*": ERROR_NOT_READY},
            )

        try:
            self._ensure_tree(pod.uid, volume_dir)
        except (MountError, OSError) as exc:
            log.error("gateway tree for pod %s could not be prepared: %s", pod.uid, exc)
            self.counters["failed"] += 1
            return Report(applied_hash=wanted_hash, state=STATE_FAILED, failed={"*": ERROR_MOUNT_FAILED})

        applied = self._read_state(pod.uid)
        desired = {grant.target: grant for grant in grants}

        for target in sorted(set(applied) - set(desired)):
            self._revoke(pod.uid, target)
            applied.pop(target, None)

        mounted: list[str] = []
        failed: dict[str, str] = {}
        for target, grant in sorted(desired.items()):
            path = self.target_path(pod.uid, target)
            unchanged = applied.get(target) == grant.key() and self.mounter.is_mount_point(path)
            if unchanged:
                mounted.append(target)
                continue
            if self.mounter.is_mount_point(path):
                # The grant changed under the same name: take the old one down
                # before the new one goes up, so nobody reads the previous
                # folder through the new name.
                self._revoke(pod.uid, target)
                applied.pop(target, None)
            try:
                self._grant(pod.uid, grant)
            except GatewayError as exc:
                log.warning("pod %s: refusing grant '%s': %s", pod.uid, target, exc)
                failed[target] = exc.code
                self.counters["failed"] += 1
                continue
            except (MountError, OSError) as exc:
                log.error("pod %s: grant '%s' failed: %s", pod.uid, target, exc)
                failed[target] = ERROR_MOUNT_FAILED
                self.counters["failed"] += 1
                continue
            applied[target] = grant.key()
            mounted.append(target)
            self.counters["granted"] += 1

        self._write_state(pod.uid, applied)
        state = STATE_READY if not failed else (STATE_DEGRADED if mounted else STATE_FAILED)
        return Report(applied_hash=wanted_hash, state=state, mounted=mounted, failed=failed)

    def release(self, pod_uid: str) -> None:
        """Take down everything this pod was granted, in the only safe order.

        The pod's own copy of the tree goes first: while it stands, kubelet
        cannot unmount the gateway tmpfs, and a pod that cannot unmount is a
        pod stuck in ``Terminating``. The grants come down next, then the tree
        itself, then the directories.
        """
        tree = self.pod_tree(pod_uid)
        volume_dir = self.pod_volume_dir(pod_uid)
        applied = self._read_state(pod_uid)
        if not applied and not os.path.isdir(tree) and not self.mounter.is_mount_point(volume_dir):
            return

        errors: list[str] = []
        for path in (volume_dir,):
            try:
                self.mounter.unmount(path)
            except MountError as exc:
                errors.append(f"{path}: {exc}")

        for target in sorted(applied) + sorted(_entries(tree) - set(applied)):
            path = os.path.join(tree, target)
            try:
                self.mounter.unmount(path)
                self.counters["revoked"] += 1
            except MountError as exc:
                errors.append(f"{path}: {exc}")

        try:
            self.mounter.unmount(tree)
        except MountError as exc:
            errors.append(f"{tree}: {exc}")

        if errors:
            # A mount that would not come down is the one failure mode that
            # must never be quiet: kubelet is about to try the same thing.
            self.counters["leaked"] += 1
            log.error("pod %s: gateway mounts left behind: %s", pod_uid, "; ".join(errors))
        else:
            self._remove_tree(tree)
            self._forget_state(pod_uid)
            self.counters["released"] += 1

    def release_unknown(self, live_pod_uids: Iterable[str]) -> list[str]:
        """Release every tree whose pod is gone. Returns what it released."""
        live = {_pod_uid(uid) for uid in live_pod_uids}
        released: list[str] = []
        for entry in sorted(_entries(self.pods_dir())):
            if entry in live:
                continue
            log.info("releasing gateway tree of pod %s, which is gone", entry)
            self.release(entry)
            released.append(entry)
        return released

    # -- the mount table ---------------------------------------------------

    def _ensure_tree(self, pod_uid: str, volume_dir: str) -> None:
        """The per-pod tree, shared, and bound once into the pod's volume."""
        tree = self.pod_tree(pod_uid)
        os.makedirs(tree, mode=0o755, exist_ok=True)
        if not self.mounter.is_mount_point(tree):
            # A directory cannot be a peer group on its own: bind it to itself
            # first, then share it, so what is mounted inside it afterwards
            # propagates to the pod's copy.
            self.mounter.bind_dir(tree, tree)
            self.mounter.make_shared(tree)
        if not self.mounter.is_mount_point(volume_dir):
            self.mounter.bind_dir(tree, volume_dir, recursive=True)

    def _grant(self, pod_uid: str, grant: Grant) -> None:
        try:
            source = resolve_beneath(self.shared_root, grant.source)
        except OSError as exc:
            # A source that will not resolve beneath the claim — missing, a
            # symlink, an escape — is a bad grant, not a failed mount. The
            # difference is what the Operator reports to the user.
            raise GatewayError(
                ERROR_INVALID_SOURCE, f"source '{grant.source}' is not reachable beneath the shared filesystem: {exc}"
            ) from exc
        if not os.path.isdir(source):
            raise GatewayError(ERROR_INVALID_SOURCE, f"source '{grant.source}' is not a directory")
        path = self.target_path(pod_uid, grant.target)
        os.makedirs(path, mode=0o755, exist_ok=True)
        self.mounter.bind_dir(source, path)
        try:
            self.mounter.set_attrs(
                path,
                read_only=grant.read_only,
                noexec=not grant.allow_exec,
            )
        except (MountError, OSError):
            self.mounter.unmount(path)
            raise

    def _revoke(self, pod_uid: str, target: str) -> None:
        path = self.target_path(pod_uid, target)
        try:
            self.mounter.unmount(path)
            self.counters["revoked"] += 1
        except MountError as exc:
            self.counters["leaked"] += 1
            log.error("pod %s: '%s' would not unmount: %s", pod_uid, target, exc)
            return
        try:
            os.rmdir(path)
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EBUSY):
                log.warning("pod %s: '%s' could not be removed: %s", pod_uid, target, exc)

    # -- state -------------------------------------------------------------

    def _state_file(self, pod_uid: str) -> str:
        return os.path.join(self.state_dir(), f"{_pod_uid(pod_uid)}.json")

    def _read_state(self, pod_uid: str) -> dict[str, dict[str, Any]]:
        try:
            with open(self._state_file(pod_uid), "r", encoding="utf-8") as handle:
                parsed = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): value for key, value in parsed.items() if isinstance(value, dict)}

    def _write_state(self, pod_uid: str, applied: dict[str, dict[str, Any]]) -> None:
        os.makedirs(self.state_dir(), mode=0o700, exist_ok=True)
        path = self._state_file(pod_uid)
        with open(f"{path}.tmp", "w", encoding="utf-8") as handle:
            json.dump(applied, handle, sort_keys=True)
        os.replace(f"{path}.tmp", path)

    def _forget_state(self, pod_uid: str) -> None:
        try:
            os.remove(self._state_file(pod_uid))
        except OSError:
            pass

    def _remove_tree(self, tree: str) -> None:
        try:
            shutil.rmtree(tree)
        except OSError as exc:
            log.debug("gateway tree %s not removed: %s", tree, exc)

    # -- what an operator reads -------------------------------------------

    def mounted_count(self) -> int:
        return sum(len(self._read_state(entry)) for entry in _entries(self.pods_dir()))

    def snapshot(self) -> dict[str, Any]:
        pods = {}
        for entry in sorted(_entries(self.pods_dir())):
            applied = self._read_state(entry)
            pods[entry] = {
                "mounts": {
                    target: {
                        **spec,
                        "mounted": self.mounter.is_mount_point(os.path.join(self.pod_tree(entry), target)),
                    }
                    for target, spec in sorted(applied.items())
                },
                "published": self.mounter.is_mount_point(self.pod_volume_dir(entry)),
            }
        return {
            "gateway_root": self.gateway_root,
            "shared_root": self.shared_root,
            "counters": dict(self.counters),
            "pods": pods,
        }


def _pod_uid(value: str) -> str:
    raw = str(value or "").strip()
    if not _POD_UID_RE.match(raw):
        raise GatewayError(ERROR_INVALID_TARGET, f"'{value}' is not a pod uid")
    return raw


def _entries(path: str) -> set[str]:
    try:
        return {entry.name for entry in os.scandir(path)}
    except OSError:
        return set()
