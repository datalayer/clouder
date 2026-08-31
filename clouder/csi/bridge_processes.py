"""Running a local bridge as a gateway mount.

A `local-bridge` grant mounts a folder of a person's own computer into a
sandbox that is already running. The filesystem behind it is the one the CSI
driver already runs — `clouder.csi.bridge_mount` over the Contents relay — so
this is an adapter rather than a second implementation: the same mounter, the
same process, the same failure modes, reached through the gateway's
`MountProcesses` protocol instead of through a CSI volume.

What it adds over the driver's path is what the gateway needs and a CSI volume
never did: the mount is made **after** the pod is running, and the agent may
be restarted while it stands.
"""

from __future__ import annotations

import logging
import os
import signal
from urllib.parse import urlsplit

from .gateway import ERROR_PROCESS_UNSUPPORTED, GatewayError
from .mounter import MountError, MountHandle, Mounter

log = logging.getLogger("clouder.csi.gateway.bridge")

#: The grant kind this runs. Anything else is somebody else's to serve.
LOCAL_BRIDGE_KIND = "local-bridge"

#: What the pod-owned Secret carries. The Operator writes both: the token is
#: the credential, and the relay URL travels with it rather than in the grant
#: so a deployment detail never has to be right in two places.
SECRET_TOKEN_KEY = "mount-token"
SECRET_RELAY_KEY = "relay-url"


class BridgeProcesses:
    """The gateway's runner for `local-bridge` grants."""

    def __init__(
        self,
        mounter: Mounter,
        *,
        relay_host: str = "",
        allow_insecure_relay: bool = False,
    ) -> None:
        self._mounter = mounter
        self._relay_host = (relay_host or "").strip().lower()
        self._allow_insecure_relay = allow_insecure_relay
        self._handles: dict[int, MountHandle] = {}

    # -- MountProcesses ----------------------------------------------------

    def start(
        self,
        *,
        kind: str,
        source: str,
        target: str,
        read_only: bool,
        credential: dict[str, bytes],
    ) -> int:
        if kind != LOCAL_BRIDGE_KIND:
            raise GatewayError(
                ERROR_PROCESS_UNSUPPORTED,
                f"this runner serves '{LOCAL_BRIDGE_KIND}' grants, not '{kind}'",
            )
        token = _text(credential.get(SECRET_TOKEN_KEY))
        relay_url = _text(credential.get(SECRET_RELAY_KEY))
        if not token or not relay_url:
            raise GatewayError(
                ERROR_PROCESS_UNSUPPORTED,
                f"the Secret for bridge '{source}' carries no "
                f"{SECRET_TOKEN_KEY} and {SECRET_RELAY_KEY}",
            )
        self._check_relay(relay_url, source)
        handle = self._mounter.start(
            bridge_uid=source,
            relay_url=relay_url,
            mount_token=token,
            mount_path=target,
            mode="ro" if read_only else "rw",
        )
        pid = int(handle.pid or 0)
        if not pid:
            raise MountError(f"the bridge filesystem for '{source}' reported no pid")
        self._handles[pid] = handle
        return pid

    def alive(self, pid: int) -> bool:
        """Whether the filesystem is still serving, including after a restart.

        A restarted agent did not start these processes and has no handle for
        them, but they are still running and still serving their mounts.
        Reporting them dead would take a working folder away from a sandbox
        because the agent was replaced, so an unknown pid is asked of the
        kernel instead.
        """
        handle = self._handles.get(pid)
        if handle is not None:
            return self._mounter.alive(handle)
        return _running(pid)

    def stop(self, pid: int, target: str) -> None:
        handle = self._handles.pop(pid, None)
        if handle is not None:
            self._mounter.stop(handle)
            return
        # Adopted after a restart: signal it by pid, then make sure its mount
        # point is gone — a bridge left mounted is a sandbox reading a folder
        # nobody granted any more.
        if _running(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                log.warning("bridge filesystem pid %s would not stop: %s", pid, exc)
        try:
            self._mounter.unmount(target)
        except MountError as exc:
            log.warning("bridge mount %s would not unmount: %s", target, exc)

    # -- the relay ---------------------------------------------------------

    def _check_relay(self, relay_url: str, bridge_uid: str) -> None:
        """The same rules the CSI driver applies, for the same reasons."""
        parts = urlsplit(relay_url)
        allowed = ("wss", "ws") if self._allow_insecure_relay else ("wss",)
        if parts.scheme not in allowed:
            raise GatewayError(ERROR_PROCESS_UNSUPPORTED, "the relay URL must be wss://")
        if not parts.hostname:
            raise GatewayError(ERROR_PROCESS_UNSUPPORTED, "the relay URL has no host")
        if parts.username or parts.password:
            raise GatewayError(
                ERROR_PROCESS_UNSUPPORTED, "the relay URL must not carry credentials"
            )
        if self._relay_host and parts.hostname.lower() != self._relay_host:
            raise GatewayError(
                ERROR_PROCESS_UNSUPPORTED,
                f"relay host '{parts.hostname}' is not the configured relay host",
            )
        if not parts.path.rstrip("/").endswith(f"/bridges/{bridge_uid}"):
            # The URL must name the bridge it is for, or a grant could point a
            # sandbox's mount at somebody else's session.
            raise GatewayError(
                ERROR_PROCESS_UNSUPPORTED, f"the relay URL does not name bridge '{bridge_uid}'"
            )


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value or "").strip()


def _running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, and not ours to signal. Still alive.
        return True
    return True
