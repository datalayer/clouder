"""The Node Mount Gateway: binding folders into a pod that is already running.

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

The wire format is **imported**, from
:mod:`datalayer_core.contents_node_mount_gateway`, which both this and
`datalayer_common` depend on. It used to be copied here and held to the
original by a test; that worked and was the wrong shape. A format the Operator
writes and this reads, byte for byte, should be one implementation — a test
that two copies agree can only fail after somebody has already changed one.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import time
import re
import shutil
import stat
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .git_materializer import NoMaterialize
from .linux import make_beneath, resolve_beneath
from .mounter import MountError, Mounter

log = logging.getLogger("clouder.csi.node_mount_gateway")

from datalayer_core.contents_node_mount_gateway import (  # noqa: F401
    TARGET_MAX_SEGMENTS,
    CLOUD_STORAGE_KIND,
    DELIVERY_BIND,
    DELIVERY_FILESYSTEM,
    DELIVERY_MATERIALIZE,
    DELIVERY_PROCESS,
    ERROR_INVALID_SOURCE,
    ERROR_INVALID_TARGET,
    ERROR_MOUNT_FAILED,
    ERROR_NOT_READY,
    ERROR_SECRET_REFUSED,
    ERROR_TOO_MANY_MOUNTS,
    ERROR_UNSUPPORTED_KIND,
    GIT_KIND,
    NFS_KIND,
    NodeMountGatewayError,
    NODE_MOUNT_GATEWAY_MOUNTS_ANNOTATION,
    NODE_MOUNT_GATEWAY_READY_ANNOTATION,
    NODE_MOUNT_GATEWAY_VOLUME_NAME,
    STATE_DEGRADED,
    STATE_FAILED,
    STATE_READY,
    clean_revision,
    clean_secret,
    clean_source,
    clean_target,
    delivery_known,
    delivery_of,
    grants_hash as _canonical_hash,
)
#: The filesystem behind a mount stopped. The mount stays and returns errors:
#: a sandbox reading `EIO` knows something is wrong, and one reading stale
#: bytes does not.
ERROR_MOUNT_DEAD = "NODE_MOUNT_GATEWAY_MOUNT_DEAD"
#: A grant whose kind needs a process this agent has no way to start.
ERROR_PROCESS_UNSUPPORTED = "NODE_MOUNT_GATEWAY_PROCESS_UNSUPPORTED"

DEFAULT_NODE_MOUNT_GATEWAY_ROOT = "/var/lib/datalayer/node-mount-gateway"
DEFAULT_SHARED_ROOT = "/mnt/shared-fs"
DEFAULT_KUBELET_DIR = "/var/lib/kubelet"
DEFAULT_MAX_MOUNTS_PER_POD = 32
DEFAULT_MAX_MOUNTS_PER_NODE = 512

#: The Contents source kind whose folder is provisioned lazily: it exists the
#: first time a sandbox mounts it or something is uploaded into it, whichever
#: comes first. Mounting is one of the two, so the agent is one of the two
#: that create it. Nothing else has a folder invented for it.
HOME_FOLDER_KIND = "files"

#: Mount options every kernel mount the gateway makes carries. A tenant's
#: mount is data, and data does not need to carry setuid bits or device nodes
#: into a sandbox for the sandbox to read it.
KERNEL_MOUNT_OPTIONS = ("nosuid", "nodev")

#: Kinds whose mount is a **process** — a userspace filesystem — rather than a
#: bind of a directory the agent already reaches. A bucket and a person's own
#: folder are each one of these: something has to be running for the mount to
#: answer, so it has to be started, watched and stopped, and its death is a
#: mount that returns errors rather than one that disappears.
PROCESS_KINDS = ("cloud-storage", "local-bridge")

#: Mirrored from `datalayer_common.home_folders`. Every sandbox runs as
#: `jovyan` (1000:100) and a home folder is created for that identity: a
#: folder owned by anyone else reaches the sandbox read-only, or not at all.
HOME_FOLDER_OWNER_UID = 1000
HOME_FOLDER_OWNER_GID = 100
HOME_FOLDER_DIRECTORY_MODE = 0o775

_POD_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Grant:
    """One folder a pod is granted, as the annotation names it."""

    source: str
    target: str
    mode: str = "rw"
    allow_exec: bool = True
    uid: str = ""
    kind: str = ""
    #: The NAME of a Secret holding the credential this mount needs — never
    #: the value. A Home Folder needs none.
    secret: str = ""
    #: The revision a materialized mount is pinned to. Empty for every kind
    #: that has nothing to pin.
    revision: str = ""

    @property
    def read_only(self) -> bool:
        return self.mode == "ro"

    @property
    def delivery(self) -> str:
        """What the agent has to do to produce this mount."""
        return delivery_of(self.kind)

    @property
    def is_process(self) -> bool:
        """Whether this mount is a filesystem to run, not a directory to bind.

        Deliberately not `self.delivery == DELIVERY_PROCESS`: this is read on
        the liveness path, for every mount, every pass, and a kind nobody
        serves must answer "no" there rather than raise. The refusal belongs
        where the mount is made, once, with a code to report.
        """
        return self.kind in PROCESS_KINDS

    def key(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "mode": self.mode,
            "allow_exec": self.allow_exec,
            "secret": self.secret,
            "revision": self.revision,
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


#: Which kernel filesystem each `DELIVERY_FILESYSTEM` kind mounts as.
_FILESYSTEM_TYPES = {NFS_KIND: "nfs"}


def _filesystem_type(kind: str) -> str:
    try:
        return _FILESYSTEM_TYPES[kind]
    except KeyError:  # pragma: no cover - delivery_of refuses this first
        raise NodeMountGatewayError(
            ERROR_UNSUPPORTED_KIND, f"no filesystem is known for kind '{kind}'"
        ) from None


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
            target = clean_target(item.get("target"))
            kind = str(item.get("kind") or "")
            # The kind decides how the source is read, so it is read first. An
            # unknown kind is *not* dropped here: a grant nobody serves is a
            # thing the user should be told about, and a dropped grant is a
            # missing folder with no reason attached. It is parsed under the
            # default rule and refused, with its code, when it is applied.
            try:
                delivery = delivery_of(kind)
            except NodeMountGatewayError:
                delivery = DELIVERY_BIND
                kind = kind or ""
            source = clean_source(item.get("source"), kind if delivery_known(kind) else "")
            revision = (
                clean_revision(item.get("revision"))
                if delivery == DELIVERY_MATERIALIZE
                else ""
            )
        except NodeMountGatewayError as exc:
            log.warning("dropping a grant: %s", exc)
            continue
        if target in seen:
            continue
        seen.add(target)
        secret = str(item.get("secret") or "").strip()
        if secret:
            try:
                secret = clean_secret(secret)
            except NodeMountGatewayError as exc:
                log.warning("dropping a grant: %s", exc)
                continue
        mode = str(item.get("mode") or "rw").strip().lower()
        grants.append(
            Grant(
                source=source,
                target=target,
                mode=mode if mode in ("ro", "rw") else "rw",
                allow_exec=bool(item.get("allow_exec", True)),
                uid=str(item.get("uid") or ""),
                kind=kind,
                secret=secret,
                revision=revision,
            )
        )
    return sorted(grants, key=lambda grant: grant.target)


def grants_hash(grants: Iterable[Grant]) -> str:
    """A stable name for one mount set: what tells applied from asked for.

    Delegated to the shared implementation rather than repeated here. The
    Operator asks for a set by its hash and this answers about the set it
    applied by the same hash; two implementations of that would be two
    processes agreeing on nothing while appearing to agree on everything.
    """
    return _canonical_hash([grant.key() for grant in grants])


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


class MountProcesses(Protocol):
    """How the agent runs the filesystem behind a process-backed mount."""

    def start(
        self, *, kind: str, source: str, target: str, read_only: bool, credential: dict[str, bytes]
    ) -> int:
        """Mount ``source`` at ``target`` and return the pid serving it."""

    def alive(self, pid: int) -> bool:
        """Whether the process is still serving its mount."""

    def stop(self, pid: int, target: str) -> None:
        """Stop the process and make sure its mount point is gone."""


class NoProcessMounts:
    """The default: the agent binds directories and runs nothing.

    A deployment that has not been given a way to run a bucket or a bridge
    filesystem refuses a grant that needs one, rather than reporting a mount
    it never made.
    """

    def start(self, *, kind, source, target, read_only, credential) -> int:
        raise NodeMountGatewayError(
            ERROR_PROCESS_UNSUPPORTED,
            f"this node agent cannot run the filesystem a '{kind}' mount needs",
        )

    def alive(self, pid: int) -> bool:
        return False

    def stop(self, pid: int, target: str) -> None:
        return None


class Credentials(Protocol):
    """How the agent reads a Secret a grant names, if it may read one at all."""

    def read_secret(self, namespace: str, name: str, pod_uid: str) -> dict[str, bytes]:
        """The Secret's data, or raise if this pod may not have it."""


class NoCredentials:
    """The default: the agent reads no Secret, and says so when asked to.

    A deployment that has not turned credentials on cannot be talked into
    reading one by a grant that names a Secret. That is the point of it being
    a separate switch from the gateway itself.
    """

    def read_secret(self, namespace: str, name: str, pod_uid: str) -> dict[str, bytes]:
        raise NodeMountGatewayError(
            ERROR_SECRET_REFUSED,
            "this node agent reads no Secrets; the mount needs a credential it cannot have",
        )


class NodeMountGateway:
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
        gateway_root: str = DEFAULT_NODE_MOUNT_GATEWAY_ROOT,
        kubelet_dir: str = DEFAULT_KUBELET_DIR,
        max_mounts_per_pod: int = DEFAULT_MAX_MOUNTS_PER_POD,
        max_mounts_per_node: int = DEFAULT_MAX_MOUNTS_PER_NODE,
        credentials: Credentials | None = None,
        processes: MountProcesses | None = None,
        materializer: Any | None = None,
    ) -> None:
        self.mounter = mounter
        self.credentials = credentials or NoCredentials()
        self.processes = processes or NoProcessMounts()
        self.materializer = materializer or NoMaterialize()
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
        return os.path.join(self.pod_tree(pod_uid), clean_target(target))

    def pod_volume_dir(self, pod_uid: str) -> str:
        """The node directory kubelet made for the pod's gateway ``emptyDir``."""
        return os.path.join(
            self.kubelet_dir,
            "pods",
            _pod_uid(pod_uid),
            "volumes",
            "kubernetes.io~empty-dir",
            NODE_MOUNT_GATEWAY_VOLUME_NAME,
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
            # kubelet has not made the Pod's volume yet, or this Pod has no
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
            self._revoke(pod.uid, target, _pid_of(applied.get(target)))
            applied.pop(target, None)

        mounted: list[str] = []
        failed: dict[str, str] = {}
        for target, grant in sorted(desired.items()):
            path = self.target_path(pod.uid, target)
            recorded = applied.get(target) or {}
            unchanged = _same_grant(recorded, grant) and self.mounter.is_mount_point(path)
            if unchanged:
                if grant.is_process and not self.processes.alive(int(recorded.get("pid") or 0)):
                    # The mount is still there and returns errors, which is
                    # what the sandbox should see. Saying `ready` about it is
                    # what would make somebody trust the bytes.
                    log.warning("pod %s: the filesystem behind '%s' has stopped", pod.uid, target)
                    failed[target] = ERROR_MOUNT_DEAD
                    continue
                mounted.append(target)
                continue
            if self.mounter.is_mount_point(path):
                # The grant changed under the same name: take the old one down
                # before the new one goes up, so nobody reads the previous
                # folder through the new name.
                self._revoke(pod.uid, target, _pid_of(recorded))
                applied.pop(target, None)
            try:
                pid = self._grant(pod, grant)
            except NodeMountGatewayError as exc:
                log.warning("pod %s: refusing grant '%s': %s", pod.uid, target, exc)
                failed[target] = exc.code
                self.counters["failed"] += 1
                continue
            except (MountError, OSError) as exc:
                log.error("pod %s: grant '%s' failed: %s", pod.uid, target, exc)
                failed[target] = ERROR_MOUNT_FAILED
                self.counters["failed"] += 1
                continue
            applied[target] = {**grant.key(), **({"pid": pid} if pid else {})}
            mounted.append(target)
            self.counters["granted"] += 1

        self._write_state(pod.uid, applied)
        state = STATE_READY if not failed else (STATE_DEGRADED if mounted else STATE_FAILED)
        return Report(applied_hash=wanted_hash, state=state, mounted=mounted, failed=failed)

    def release(self, pod_uid: str) -> None:
        """Take down everything this pod was granted, in the only order that works.

        The grants go first, then the pod's copy of the tree, then the tree.
        Not because it reads well — because the kernel refuses anything else.
        Our tree is bound onto kubelet's `emptyDir` and each grant propagates
        into that copy as a **child** of it, so unmounting the pod's copy while
        a grant stands fails with `EBUSY`. Unmounting a grant inside the tree
        propagates the unmount out to the copy, which is what leaves the copy
        childless and removable.

        What that order buys is the thing that matters: by the time kubelet
        comes to unmount its tmpfs, nothing of ours is left inside it. If any
        of this fails the tree stays and `leaked` counts it, because a mount
        that will not come down is what makes a Pod stick in `Terminating`,
        and quietly forcing it is how that becomes a mystery.
        """
        tree = self.pod_tree(pod_uid)
        volume_dir = self.pod_volume_dir(pod_uid)
        applied = self._read_state(pod_uid)
        if not applied and not os.path.isdir(tree) and not self._is_published(pod_uid):
            return

        errors: list[str] = []
        for target in sorted(applied) + sorted(_stray_mounts(tree, set(applied), self.mounter)):
            path = os.path.join(tree, target)
            pid = _pid_of(applied.get(target))
            if pid:
                try:
                    self.processes.stop(pid, path)
                except Exception as exc:  # noqa: BLE001 - a dead process is fine
                    log.warning("pod %s: '%s' filesystem would not stop: %s", pod_uid, target, exc)
            try:
                self.mounter.unmount(path)
                self.counters["revoked"] += 1
            except MountError as exc:
                errors.append(f"{path}: {exc}")

        if self._is_published(pod_uid):
            # Exactly ours, and exactly one: the tree is stacked ON kubelet's
            # `emptyDir` tmpfs, so unmounting until the path is clear would
            # take kubelet's own volume out from under a running pod.
            try:
                self.mounter.unmount_once(volume_dir)
            except MountError as exc:
                errors.append(f"{volume_dir}: {exc}")

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

    def _is_published(self, pod_uid: str) -> bool:
        """Whether the pod's gateway volume is showing THIS agent's tree.

        Not "is something mounted there": kubelet's memory-backed `emptyDir`
        is a mount point too, and reading it as an already-published tree is a
        gateway that binds nothing, reports `ready`, and delivers an empty
        directory to the sandbox. That is the failure this compares against
        the tree's own identity to avoid.
        """
        identity = self.mounter.mount_identity(self.pod_volume_dir(pod_uid))
        return identity is not None and identity == self.mounter.mount_identity(
            self.pod_tree(pod_uid)
        )

    def _ensure_tree(self, pod_uid: str, volume_dir: str) -> None:
        """The per-pod tree, shared, and bound once into the pod's volume."""
        tree = self.pod_tree(pod_uid)
        os.makedirs(tree, mode=0o755, exist_ok=True)
        if not self.mounter.is_mount_point(tree):
            # A directory cannot be a peer group on its own: bind it to itself
            # first, then share it, so what is mounted inside it afterwards
            # propagates to the Pod's copy.
            self.mounter.bind_dir(tree, tree)
            self.mounter.make_shared(tree)
        if not self._is_published(pod_uid):
            # Stacked on kubelet's tmpfs rather than replacing it: the tmpfs
            # stays underneath, which is what refuses to unmount while a grant
            # is standing and turns a leak into a stuck Pod.
            self.mounter.bind_dir(tree, volume_dir, recursive=True)

    def _source_of(self, grant: Grant) -> str:
        """The folder to bind, created if this is a home folder that has none.

        A Home Folder exists the first time a sandbox mounts it or something
        is uploaded into it, whichever comes first — mounting used to be an
        init container's `mkdir`, and with the gateway it is this. A brand-new
        user, or an organization nobody has written to yet, would otherwise
        get `NODE_MOUNT_GATEWAY_INVALID_SOURCE` for a folder that is simply new.

        Only a home folder is created. A cloud bucket or a Volume that is not
        there is a mistake, and inventing a directory for it would turn a
        clear failure into an empty folder somebody debugs later.
        """
        try:
            return resolve_beneath(self.shared_root, grant.source)
        except FileNotFoundError:
            if grant.kind != HOME_FOLDER_KIND:
                raise
            created = make_beneath(self.shared_root, grant.source, HOME_FOLDER_DIRECTORY_MODE)
            try:
                os.chown(created, HOME_FOLDER_OWNER_UID, HOME_FOLDER_OWNER_GID)
                os.chmod(created, HOME_FOLDER_DIRECTORY_MODE)
            except OSError as exc:
                # Some backends decide ownership themselves — an EFS access
                # point, an NFS export that squashes root — and the folder is
                # already right. The creation-time init container tolerated
                # this with `|| true`; this checks instead, because a folder
                # the sandbox cannot write arrives read-only with no
                # explanation anywhere.
                if not _writable_by_sandbox_user(created):
                    raise NodeMountGatewayError(
                        ERROR_INVALID_SOURCE,
                        f"home folder '{grant.source}' was created but the sandbox "
                        f"user cannot write it: {exc}",
                    ) from exc
                log.warning(
                    "Could not set ownership on %s (%s); it is writable by the sandbox user anyway",
                    grant.source,
                    exc,
                )
            log.info("Created home folder %s for the first mount of it", grant.source)
            return created

    def _credential_for(self, pod: PodRef, grant: Grant) -> dict[str, bytes]:
        """The Secret a grant names, checked against the pod that asked for it.

        A grant is written by the Operator, which is the only identity that can
        patch a runtime pod — but a Secret name in it is still a name the agent
        will look up, so it is checked rather than trusted: the Secret must be
        in the pod's own namespace, and the reader must confirm the pod owns
        it. That is what stops a grant from naming the companion's key, another
        tenant's bridge token, or anything else in the namespace.
        """
        if not grant.secret:
            return {}
        return self.credentials.read_secret(pod.namespace, grant.secret, pod.uid)

    def _grant(self, pod: PodRef, grant: Grant) -> int | None:
        # Raises for a kind nobody here serves. First thing, before a Secret is
        # read or a directory is made: a grant that cannot be applied should
        # leave nothing behind that says it nearly was.
        delivery = grant.delivery
        # Read and check the credential before anything is mounted: a mount
        # made and then abandoned because its Secret was refused is a mount
        # that briefly existed. A bind of a directory needs none; a process
        # mount is handed it.
        credential = self._credential_for(pod, grant)
        pod_uid = pod.uid
        if delivery == DELIVERY_PROCESS:
            # A bucket or a bridge names something the agent does not reach
            # through the shared claim — a bucket and prefix, a bridge uid —
            # so there is no path here to resolve beneath anything.
            source = grant.source
        elif delivery == DELIVERY_FILESYSTEM:
            # An export the node mounts itself. The host is not this agent's
            # to resolve either.
            source = grant.source
        elif delivery == DELIVERY_MATERIALIZE:
            # The content does not exist yet. Producing it is the slow part —
            # a clone over the network — and it happens before the target
            # directory is made, so a checkout that fails leaves no empty
            # folder at a path a sandbox would then mount.
            source = self.materializer.materialize(
                kind=grant.kind,
                source=grant.source,
                revision=grant.revision,
                credential=credential,
            )
            if not os.path.isdir(source):
                raise NodeMountGatewayError(
                    ERROR_MOUNT_FAILED,
                    f"the content for '{grant.target}' was produced at no directory",
                )
        else:
            try:
                source = self._source_of(grant)
            except NodeMountGatewayError:
                raise
            except OSError as exc:
                # A source that will not resolve beneath the claim — missing,
                # a symlink, an escape — is a bad grant, not a failed mount.
                # The difference is what the Operator reports to the user.
                raise NodeMountGatewayError(
                    ERROR_INVALID_SOURCE,
                    f"source '{grant.source}' is not reachable beneath the shared filesystem: {exc}",
                ) from exc
            if not os.path.isdir(source):
                raise NodeMountGatewayError(
                    ERROR_INVALID_SOURCE, f"source '{grant.source}' is not a directory"
                )
        path = self.target_path(pod_uid, grant.target)
        os.makedirs(path, mode=0o755, exist_ok=True)
        if delivery == DELIVERY_FILESYSTEM:
            # The kernel holds this one: nothing runs, nothing is watched, and
            # it ends when it is unmounted. The flags go on at mount time
            # because attributes do not propagate to the sandbox's copy —
            # the same reason a bind is attached rather than bound and fixed.
            options = list(KERNEL_MOUNT_OPTIONS)
            options.append("ro" if grant.read_only else "rw")
            if not grant.allow_exec:
                options.append("noexec")
            self.mounter.mount_filesystem(
                _filesystem_type(grant.kind), source, path, options=options
            )
            if not self.mounter.is_mount_point(path):
                raise NodeMountGatewayError(
                    ERROR_MOUNT_FAILED,
                    f"the filesystem for '{grant.target}' mounted nothing",
                )
            return None
        if delivery == DELIVERY_PROCESS:
            # A bucket or a person's own folder: something has to be running
            # for the mount to answer at all. The process mounts at the target
            # itself, so the mount it makes is the one that propagates.
            pid = self.processes.start(
                kind=grant.kind,
                source=source,
                target=path,
                read_only=grant.read_only,
                credential=credential,
            )
            # A filesystem process mounts a moment after it starts —
            # Mountpoint in about a tenth of a second — and a check made in
            # the same millisecond found nothing, stopped it, and reported a
            # bucket the sandbox could read as `MOUNT_FAILED`. Wait for the
            # mount point, briefly; a process that dies first is failed at
            # once.
            if not _wait_for_mount_point(
                self.mounter,
                path,
                alive=lambda: self.processes.alive(pid),
                timeout=PROCESS_MOUNT_PATIENCE_SECONDS,
            ):
                # Started and mounted nothing: a directory reported as a mount
                # is the failure that lets somebody read an empty bucket and
                # believe it.
                self.processes.stop(pid, path)
                raise NodeMountGatewayError(
                    ERROR_MOUNT_FAILED,
                    f"the filesystem for '{grant.target}' started but mounted nothing",
                )
            return pid
        # One call: the mount is built detached, given its attributes, and
        # only then attached, so every copy propagation makes — the sandbox's
        # among them — is created from a mount that is already read-only,
        # `nosuid` and `nodev`. Binding first and setting the flags after
        # leaves the sandbox's copy with the flags it had at attach time,
        # which for a `ro` grant means no read-only mount at all.
        self.mounter.attach(
            source,
            path,
            # A materialized checkout is shared between every Pod on this node
            # that asked for the same revision, so it is read-only whatever
            # the grant says: one sandbox writing into it is every other
            # sandbox's files changing under them.
            read_only=grant.read_only or delivery == DELIVERY_MATERIALIZE,
            noexec=not grant.allow_exec,
        )
        return None

    def _revoke(self, pod_uid: str, target: str, pid: int | None = None) -> None:
        path = self.target_path(pod_uid, target)
        if pid:
            # Stop the filesystem before taking its mount point away, or the
            # process is left serving a path nothing can reach.
            try:
                self.processes.stop(pid, path)
            except Exception as exc:  # noqa: BLE001 - a dead process is fine
                log.warning("pod %s: '%s' filesystem would not stop: %s", pod_uid, target, exc)
        try:
            self.mounter.unmount(path)
            self.counters["revoked"] += 1
        except MountError as exc:
            self.counters["leaked"] += 1
            log.error("pod %s: '%s' would not unmount: %s", pod_uid, target, exc)
            return
        # The leaf, then any parent `makedirs` made for a nested target, as
        # far up as the pod's tree and only while empty.
        tree = self.pod_tree(pod_uid)
        while path != tree and path.startswith(tree + os.sep):
            try:
                os.rmdir(path)
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EBUSY):
                    log.warning("pod %s: '%s' could not be removed: %s", pod_uid, target, exc)
                break
            path = os.path.dirname(path)

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
                # Showing THIS agent's tree, not merely "something is mounted".
                "published": self._is_published(entry),
            }
        return {
            "gateway_root": self.gateway_root,
            "shared_root": self.shared_root,
            "counters": dict(self.counters),
            "pods": pods,
        }


def _same_grant(recorded: dict[str, Any] | None, grant: Grant) -> bool:
    """Whether what is recorded as applied is this grant.

    The pid a process mount is served by is recorded beside the grant and is
    deliberately not part of it: a restarted filesystem is the same grant, and
    a mount set whose hash changed because a process was restarted would be
    re-applied for no reason.
    """
    if not recorded:
        return False
    return {key: recorded.get(key) for key in grant.key()} == grant.key()


def _pid_of(recorded: dict[str, Any] | None) -> int | None:
    try:
        return int((recorded or {}).get("pid") or 0) or None
    except (TypeError, ValueError):
        return None


def _writable_by_sandbox_user(path: str) -> bool:
    """Whether `1000:100` can write this directory, however it came to be."""
    try:
        info = os.stat(path)
    except OSError:
        return False
    if info.st_uid == HOME_FOLDER_OWNER_UID and info.st_mode & stat.S_IWUSR:
        return True
    if info.st_gid == HOME_FOLDER_OWNER_GID and info.st_mode & stat.S_IWGRP:
        return True
    return bool(info.st_mode & stat.S_IWOTH)


def _pod_uid(value: str) -> str:
    raw = str(value or "").strip()
    if not _POD_UID_RE.match(raw):
        raise NodeMountGatewayError(ERROR_INVALID_TARGET, f"'{value}' is not a pod uid")
    return raw


#: How long a filesystem process is given to bring its mount up. Mountpoint
#: takes a tenth of a second; a bridge over a relay a little more; anything
#: past this is a process that is not going to mount.
PROCESS_MOUNT_PATIENCE_SECONDS = 15.0


def _wait_for_mount_point(mounter: Any, path: str, *, alive: Any, timeout: float, poll: float = 0.05) -> bool:
    """True once `path` is a mount point; False if the process dies or time runs out."""
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while True:
        if mounter.is_mount_point(path):
            return True
        if not alive() or time.monotonic() >= deadline:
            return mounter.is_mount_point(path)
        time.sleep(poll)


def _stray_mounts(tree: str, applied: set[str], mounter: Any) -> set[str]:
    """Mount points beneath a pod's tree that the state does not know about.

    Once the entries of the tree; wrong since a target may be `datasets/<name>`,
    whose parent `datasets` is a plain directory the leaf's `makedirs` made —
    an entry, not a mount, and unmounting it fails, which counted as a leak
    and kept the tree from being removed. So the tree is walked to the depth
    a target may have, and only mount points are returned, by the relative
    path a target would have. Nothing under an applied target is entered: a
    mount's own contents are the source's, not ours.
    """
    stray: set[str] = set()

    def walk(directory: str, prefix: str, depth: int) -> None:
        for name in sorted(_entries(directory)):
            relative = f"{prefix}/{name}" if prefix else name
            path = os.path.join(directory, name)
            if relative in applied:
                continue
            if mounter.is_mount_point(path):
                stray.add(relative)
            elif depth > 1 and os.path.isdir(path) and not os.path.islink(path):
                walk(path, relative, depth - 1)

    walk(tree, "", TARGET_MAX_SEGMENTS)
    return stray


def _entries(path: str) -> set[str]:
    try:
        return {entry.name for entry in os.scandir(path)}
    except OSError:
        return set()
