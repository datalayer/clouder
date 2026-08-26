"""The Local CSI Node semantics, independent of gRPC.

:class:`LocalCsiDriver` implements what ``NodePublishVolume``,
``NodeUnpublishVolume`` and ``NodeGetVolumeStats`` mean for
``local.csi.datalayer.io``; :mod:`clouder.csi.server` only translates
between protobuf messages and these calls.

The contract the driver consumes is the pod's ephemeral inline volume::

    csi:
      driver: local.csi.datalayer.io
      readOnly: true                      # == (mount-mode == "ro")
      volumeAttributes:
        bridge-uid: <bridge uid>
        sandbox-uid: <sandbox uid>
        mount-mode: ro | rw
        relay-url: wss://<relay>/bridges/<bridge uid>
      nodePublishSecretRef:
        name: bridge-<bridge uid>         # key: mount-token

Nothing in the pod names the user's folder: the local root is known to the
local client only, and the driver refuses an attribute that would carry it.
"""

from __future__ import annotations

import enum
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

from .mounter import MountError, Mounter, MountHandle

log = logging.getLogger("clouder.csi.driver")

DRIVER_NAME = "local.csi.datalayer.io"

ATTR_BRIDGE_UID = "bridge-uid"
ATTR_SANDBOX_UID = "sandbox-uid"
ATTR_MOUNT_MODE = "mount-mode"
ATTR_RELAY_URL = "relay-url"
SECRET_MOUNT_TOKEN = "mount-token"

MODE_RO = "ro"
MODE_RW = "rw"
MODES = (MODE_RO, MODE_RW)

#: Set by kubelet on every inline volume when ``podInfoOnMount`` is true.
EPHEMERAL_CONTEXT_KEY = "csi.storage.k8s.io/ephemeral"
POD_NAME_KEY = "csi.storage.k8s.io/pod.name"
POD_NAMESPACE_KEY = "csi.storage.k8s.io/pod.namespace"
POD_UID_KEY = "csi.storage.k8s.io/pod.uid"

#: Attributes that would put the user's folder on the pod. Refused outright.
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "host-path",
        "hostpath",
        "host_path",
        "local-root",
        "localroot",
        "local_root",
        "local-path",
        "localpath",
        "local_path",
        "source-path",
        "sourcepath",
        "source_path",
    }
)

_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Stable messages: the tests, the Operator and the docs rely on their prefixes.
MSG_NOT_EPHEMERAL = f"{DRIVER_NAME} serves ephemeral inline volumes only"
MSG_MISSING_SECRET = f"nodePublishSecretRef secret has no '{SECRET_MOUNT_TOKEN}' key"
MSG_HOST_PATH_ATTRIBUTE = "volumeAttributes must not carry a host path: the local root is known to the local client only"
MSG_MODE_MISMATCH = "mount-mode and the volume's readOnly flag disagree"
MSG_BLOCK_UNSUPPORTED = "block volumes are not supported"
MSG_DISCONNECTED = "bridge disconnected"
MSG_NOT_MOUNTED = "target path is not mounted"
MSG_BRIDGE_CONFLICT = "bridge is already mounted on this node with different attributes"


class Code(enum.Enum):
    """The subset of gRPC status codes the driver answers with."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    FAILED_PRECONDITION = "FAILED_PRECONDITION"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"
    UNIMPLEMENTED = "UNIMPLEMENTED"


class CsiError(Exception):
    """An error with the gRPC code to answer it with."""

    def __init__(self, code: Code, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PublishRequest:
    """What ``NodePublishVolume`` carries, reduced to what the driver reads."""

    volume_id: str
    target_path: str
    volume_context: Mapping[str, str]
    secrets: Mapping[str, str]
    readonly: bool = False
    block: bool = False


@dataclass(frozen=True)
class BridgeSpec:
    bridge_uid: str
    sandbox_uid: str
    mode: str
    relay_url: str

    @property
    def read_only(self) -> bool:
        return self.mode == MODE_RO


@dataclass(frozen=True)
class VolumeStats:
    abnormal: bool
    message: str
    total: int | None = None
    available: int | None = None
    used: int | None = None


@dataclass
class BridgeState:
    spec: BridgeSpec
    mount_path: str
    handle: MountHandle | None
    volumes: set[str] = field(default_factory=set)
    connected: bool = True
    reason: str = ""
    started_at: float = field(default_factory=time.time)


@dataclass
class VolumeState:
    volume_id: str
    bridge_uid: str
    targets: set[str]
    pod: dict[str, str]
    published_at: float = field(default_factory=time.time)


class LocalCsiDriver:
    """Publishes bridge mounts on one node.

    One bridge filesystem process runs per ``bridge-uid``; volumes (one per
    pod) bind-mount its mount point. The bridge is stopped when the last
    volume is unpublished. A watcher (``reap``) notices a bridge whose process
    has exited — the relay refused or revoked the token, the laptop
    disconnected — unmounts every target it served so that nothing stale is
    ever read, and keeps the record so that ``volume_stats`` reports the
    volume abnormal until the pod is gone.
    """

    def __init__(
        self,
        *,
        mounter: Mounter,
        node_id: str,
        state_dir: str,
        relay_host: str | None = None,
        allow_insecure_relay: bool = False,
        watch_interval: float = 2.0,
    ):
        self.mounter = mounter
        self.node_id = node_id
        self.state_dir = os.path.abspath(state_dir)
        self.relay_host = (relay_host or "").strip().lower() or None
        self.allow_insecure_relay = allow_insecure_relay
        self.watch_interval = watch_interval
        self._bridges: dict[str, BridgeState] = {}
        self._volumes: dict[str, VolumeState] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Request parsing
    # ------------------------------------------------------------------

    def parse_bridge_spec(self, request: PublishRequest) -> BridgeSpec:
        """Read the bridge attributes; refuse anything that is not the contract."""
        context = dict(request.volume_context or {})

        if context.get(EPHEMERAL_CONTEXT_KEY, "").lower() != "true":
            raise CsiError(Code.FAILED_PRECONDITION, MSG_NOT_EPHEMERAL)

        for key in context:
            if key.lower() in FORBIDDEN_ATTRIBUTES:
                raise CsiError(Code.INVALID_ARGUMENT, MSG_HOST_PATH_ATTRIBUTE)

        bridge_uid = (context.get(ATTR_BRIDGE_UID) or "").strip()
        if not bridge_uid:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_BRIDGE_UID} is required")
        if not _UID_RE.match(bridge_uid):
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_BRIDGE_UID} is not a valid identifier")

        sandbox_uid = (context.get(ATTR_SANDBOX_UID) or "").strip()
        if not sandbox_uid:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_SANDBOX_UID} is required")
        if not _UID_RE.match(sandbox_uid):
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_SANDBOX_UID} is not a valid identifier")

        mode = (context.get(ATTR_MOUNT_MODE) or "").strip().lower()
        if mode not in MODES:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_MOUNT_MODE} must be one of {', '.join(MODES)}")

        relay_url = (context.get(ATTR_RELAY_URL) or "").strip()
        if not relay_url:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_RELAY_URL} is required")
        self._validate_relay_url(relay_url, bridge_uid)

        return BridgeSpec(bridge_uid=bridge_uid, sandbox_uid=sandbox_uid, mode=mode, relay_url=relay_url)

    def _validate_relay_url(self, relay_url: str, bridge_uid: str) -> None:
        parts = urlsplit(relay_url)
        allowed = ("wss",) if not self.allow_insecure_relay else ("wss", "ws")
        if parts.scheme not in allowed:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_RELAY_URL} must be a wss:// URL")
        if not parts.hostname:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_RELAY_URL} has no host")
        if parts.username or parts.password:
            raise CsiError(Code.INVALID_ARGUMENT, f"volumeAttributes.{ATTR_RELAY_URL} must not carry credentials")
        if self.relay_host and parts.hostname.lower() != self.relay_host:
            raise CsiError(
                Code.INVALID_ARGUMENT,
                f"volumeAttributes.{ATTR_RELAY_URL} host '{parts.hostname}' is not the configured relay host",
            )
        if not parts.path.rstrip("/").endswith(f"/bridges/{bridge_uid}"):
            raise CsiError(
                Code.INVALID_ARGUMENT,
                f"volumeAttributes.{ATTR_RELAY_URL} does not name volumeAttributes.{ATTR_BRIDGE_UID}",
            )

    @staticmethod
    def read_mount_token(request: PublishRequest) -> str:
        token = (request.secrets or {}).get(SECRET_MOUNT_TOKEN, "")
        if not token or not token.strip():
            raise CsiError(Code.INVALID_ARGUMENT, MSG_MISSING_SECRET)
        return token.strip()

    @staticmethod
    def _pod_info(context: Mapping[str, str]) -> dict[str, str]:
        return {
            "name": context.get(POD_NAME_KEY, ""),
            "namespace": context.get(POD_NAMESPACE_KEY, ""),
            "uid": context.get(POD_UID_KEY, ""),
        }

    def bridge_mount_path(self, bridge_uid: str) -> str:
        return os.path.join(self.state_dir, bridge_uid, "mnt")

    # ------------------------------------------------------------------
    # NodePublishVolume
    # ------------------------------------------------------------------

    def publish(self, request: PublishRequest) -> None:
        if not request.volume_id:
            raise CsiError(Code.INVALID_ARGUMENT, "volume_id is required")
        if not request.target_path:
            raise CsiError(Code.INVALID_ARGUMENT, "target_path is required")
        if request.block:
            raise CsiError(Code.INVALID_ARGUMENT, MSG_BLOCK_UNSUPPORTED)

        spec = self.parse_bridge_spec(request)
        token = self.read_mount_token(request)

        # Kubernetes readOnly enforcement: the pod's flag and the bridge's
        # mode must agree. The Operator renders both from the attachment.
        if request.readonly != spec.read_only:
            raise CsiError(Code.INVALID_ARGUMENT, MSG_MODE_MISMATCH)

        target = os.path.normpath(request.target_path)
        pod = self._pod_info(request.volume_context)

        with self._lock:
            bridge = self._bridges.get(spec.bridge_uid)
            if bridge is not None and bridge.spec != spec:
                raise CsiError(Code.INVALID_ARGUMENT, MSG_BRIDGE_CONFLICT)

            volume = self._volumes.get(request.volume_id)
            if volume is not None and volume.bridge_uid != spec.bridge_uid:
                raise CsiError(Code.ALREADY_EXISTS, "volume is already published for another bridge")

            bridge = self._ensure_bridge(spec, token)

            if volume is not None and target in volume.targets and self.mounter.is_mount_point(target):
                log.info("volume %s already published at %s", request.volume_id, target)
                return

            os.makedirs(target, exist_ok=True)
            if self.mounter.is_mount_point(target):
                # Left behind by a previous driver incarnation: never bind
                # over it, never trust it.
                log.warning("volume %s: clearing stale mount at %s", request.volume_id, target)
                self._unmount(target)

            try:
                self.mounter.bind(bridge.mount_path, target, spec.read_only)
            except MountError as exc:
                raise CsiError(Code.INTERNAL, f"bind mount failed: {exc}") from exc

            if volume is None:
                volume = VolumeState(
                    volume_id=request.volume_id,
                    bridge_uid=spec.bridge_uid,
                    targets=set(),
                    pod=pod,
                )
                self._volumes[request.volume_id] = volume
            volume.targets.add(target)
            bridge.volumes.add(request.volume_id)
            log.info(
                "volume %s published at %s (bridge %s, %s, pod %s/%s)",
                request.volume_id,
                target,
                spec.bridge_uid,
                spec.mode,
                pod["namespace"],
                pod["name"],
            )

    def _ensure_bridge(self, spec: BridgeSpec, token: str) -> BridgeState:
        bridge = self._bridges.get(spec.bridge_uid)
        if bridge is not None and bridge.connected and bridge.handle is not None and self.mounter.alive(bridge.handle):
            return bridge

        if bridge is not None:
            # Disconnected, or dead without the watcher noticing yet: clean
            # up before starting again. A revoked token makes the start fail
            # and the pod stays pending, which is the intended outcome.
            self._disconnect_bridge(bridge, bridge.reason or self._exit_reason(bridge))

        mount_path = self.bridge_mount_path(spec.bridge_uid)
        os.makedirs(mount_path, exist_ok=True)
        if self.mounter.is_mount_point(mount_path):
            log.warning("bridge %s: clearing stale mount at %s", spec.bridge_uid, mount_path)
            self._unmount(mount_path)
        try:
            handle = self.mounter.start(
                bridge_uid=spec.bridge_uid,
                relay_url=spec.relay_url,
                mount_token=token,
                mount_path=mount_path,
                mode=spec.mode,
            )
        except MountError as exc:
            raise CsiError(Code.UNAVAILABLE, f"bridge mount failed: {exc}") from exc

        if bridge is None:
            bridge = BridgeState(spec=spec, mount_path=mount_path, handle=handle)
            self._bridges[spec.bridge_uid] = bridge
        else:
            bridge.handle = handle
            bridge.mount_path = mount_path
            bridge.connected = True
            bridge.reason = ""
            bridge.started_at = time.time()
        log.info("bridge %s mounted at %s (%s)", spec.bridge_uid, mount_path, spec.mode)
        return bridge

    # ------------------------------------------------------------------
    # NodeUnpublishVolume
    # ------------------------------------------------------------------

    def unpublish(self, volume_id: str, target_path: str) -> None:
        if not volume_id:
            raise CsiError(Code.INVALID_ARGUMENT, "volume_id is required")
        if not target_path:
            raise CsiError(Code.INVALID_ARGUMENT, "target_path is required")
        target = os.path.normpath(target_path)

        with self._lock:
            volume = self._volumes.get(volume_id)
            if volume is None:
                # Unknown to this incarnation of the driver: still never
                # leave a mount behind.
                if self.mounter.is_mount_point(target):
                    log.warning("unpublish of unknown volume %s: unmounting %s", volume_id, target)
                    self._unmount(target, strict=True)
                self._remove_dir(target)
                return

            if self.mounter.is_mount_point(target):
                self._unmount(target, strict=True)
            self._remove_dir(target)
            volume.targets.discard(target)
            if volume.targets:
                return

            self._volumes.pop(volume_id, None)
            bridge = self._bridges.get(volume.bridge_uid)
            if bridge is None:
                return
            bridge.volumes.discard(volume_id)
            if not bridge.volumes:
                self._stop_bridge(bridge)
                self._bridges.pop(volume.bridge_uid, None)
            log.info("volume %s unpublished from %s", volume_id, target)

    def _stop_bridge(self, bridge: BridgeState) -> None:
        if bridge.handle is not None:
            try:
                self.mounter.stop(bridge.handle)
            except MountError as exc:
                log.warning("bridge %s: stop failed: %s", bridge.spec.bridge_uid, exc)
            bridge.handle = None
        if self.mounter.is_mount_point(bridge.mount_path):
            self._unmount(bridge.mount_path)
        bridge_dir = os.path.dirname(bridge.mount_path)
        # Only ever rmdir: never recurse into what might still be a mount.
        self._remove_dir(bridge.mount_path)
        for name in ("mounter.log",):
            try:
                os.remove(os.path.join(bridge_dir, name))
            except OSError:
                pass
        self._remove_dir(bridge_dir)
        log.info("bridge %s stopped", bridge.spec.bridge_uid)

    # ------------------------------------------------------------------
    # NodeGetVolumeStats
    # ------------------------------------------------------------------

    def volume_stats(self, volume_id: str, volume_path: str) -> VolumeStats:
        if not volume_id:
            raise CsiError(Code.INVALID_ARGUMENT, "volume_id is required")
        if not volume_path:
            raise CsiError(Code.INVALID_ARGUMENT, "volume_path is required")
        target = os.path.normpath(volume_path)

        with self._lock:
            volume = self._volumes.get(volume_id)
            if volume is None:
                raise CsiError(Code.NOT_FOUND, f"volume {volume_id} is not published on this node")
            bridge = self._bridges.get(volume.bridge_uid)
            if bridge is None or not bridge.connected:
                reason = (bridge.reason if bridge is not None else "") or "bridge is gone"
                return VolumeStats(abnormal=True, message=f"{MSG_DISCONNECTED}: {reason}")
            if bridge.handle is None or not self.mounter.alive(bridge.handle):
                # Exited between two watcher passes: report it now, reap next.
                return VolumeStats(abnormal=True, message=f"{MSG_DISCONNECTED}: {self._exit_reason(bridge)}")
            if target not in volume.targets or not self.mounter.is_mount_point(target):
                return VolumeStats(abnormal=True, message=MSG_NOT_MOUNTED)

        total = available = used = None
        try:
            stat = os.statvfs(target)
            total = stat.f_blocks * stat.f_frsize
            available = stat.f_bavail * stat.f_frsize
            used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
        except OSError as exc:
            return VolumeStats(abnormal=True, message=f"{MSG_DISCONNECTED}: {exc.strerror or exc}")
        return VolumeStats(abnormal=False, message="", total=total, available=available, used=used)

    # ------------------------------------------------------------------
    # Watching the bridge processes
    # ------------------------------------------------------------------

    def reap(self) -> list[str]:
        """One watcher pass: disconnect every bridge whose process has exited."""
        reaped: list[str] = []
        with self._lock:
            for bridge_uid, bridge in list(self._bridges.items()):
                if not bridge.connected or bridge.handle is None:
                    continue
                if self.mounter.alive(bridge.handle):
                    continue
                self._disconnect_bridge(bridge, self._exit_reason(bridge))
                reaped.append(bridge_uid)
        return reaped

    def _exit_reason(self, bridge: BridgeState) -> str:
        if bridge.handle is None:
            return bridge.reason or "mounter is gone"
        try:
            return self.mounter.exit_reason(bridge.handle) or "mounter exited"
        except Exception as exc:  # noqa: BLE001
            return f"mounter exited ({exc})"

    def _disconnect_bridge(self, bridge: BridgeState, reason: str) -> None:
        """Unmount everything the bridge served; keep the record for stats."""
        log.warning("bridge %s disconnected: %s", bridge.spec.bridge_uid, reason)
        for volume_id in list(bridge.volumes):
            volume = self._volumes.get(volume_id)
            if volume is None:
                continue
            for target in list(volume.targets):
                self._unmount(target)
        self._stop_bridge(bridge)
        bridge.connected = False
        bridge.reason = reason

    def start(self) -> None:
        """Start the background watcher."""
        if self._watcher is not None:
            return
        self._stop.clear()
        self._watcher = threading.Thread(target=self._watch, name="local-csi-watcher", daemon=True)
        self._watcher.start()

    def close(self) -> None:
        """Stop the watcher. Mounts are left as they are: kubelet owns them."""
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=self.watch_interval * 2 + 1)
            self._watcher = None

    def _watch(self) -> None:
        while not self._stop.wait(self.watch_interval):
            try:
                self.reap()
            except Exception:  # noqa: BLE001
                log.exception("watcher pass failed")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """What the health endpoint and ``clouder local-csi status`` show."""
        with self._lock:
            bridges = {
                uid: {
                    "sandbox_uid": bridge.spec.sandbox_uid,
                    "mode": bridge.spec.mode,
                    "relay_host": urlsplit(bridge.spec.relay_url).hostname,
                    "mount_path": bridge.mount_path,
                    "connected": bridge.connected,
                    "reason": bridge.reason,
                    "pid": bridge.handle.pid if bridge.handle else None,
                    "volumes": sorted(bridge.volumes),
                    "started_at": bridge.started_at,
                }
                for uid, bridge in self._bridges.items()
            }
            volumes = {
                volume_id: {
                    "bridge_uid": volume.bridge_uid,
                    "targets": sorted(volume.targets),
                    "pod": dict(volume.pod),
                    "published_at": volume.published_at,
                }
                for volume_id, volume in self._volumes.items()
            }
        return {"driver": DRIVER_NAME, "node_id": self.node_id, "bridges": bridges, "volumes": volumes}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _unmount(self, path: str, strict: bool = False) -> None:
        try:
            self.mounter.unmount(path)
        except MountError as exc:
            if strict:
                raise CsiError(Code.INTERNAL, f"unmount of {path} failed: {exc}") from exc
            log.warning("unmount of %s failed: %s", path, exc)

    @staticmethod
    def _remove_dir(path: str) -> None:
        try:
            os.rmdir(path)
        except OSError:
            pass
