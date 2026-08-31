"""Mounters: how the Local CSI driver makes and unmakes mounts.

The driver never shells out itself. Everything that touches the node's mount
table goes through a :class:`Mounter`:

- :class:`ProcessMounter` runs on a node. It starts the bridge filesystem
  process (``python -m clouder.csi.bridge_mount``, which runs
  ``code_sandboxes.bridge_mount.run_bridge_mount``), bind-mounts its mount
  point at the pod's target path with ``mount --bind`` — remounted read-only
  when the bridge is — and unmounts with ``umount``.
- :class:`FakeMounter` records the same calls in memory for the tests.

The mount gateway uses the same mounter for a different job: ``bind_dir``,
``make_shared`` and ``set_attrs`` bind a directory of the shared filesystem
into a running pod. One mounter, because there is one mount table on a node
and two things pretending to own it is how a leak goes unnoticed.
"""

from __future__ import annotations

import abc
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

from .linux import UnsupportedKernel, mount_setattr

log = logging.getLogger("clouder.csi.mounter")

#: Environment variable the bridge filesystem process reads its token from.
#: The token travels in the environment, never on the command line, so it is
#: not visible in ``ps`` output.
MOUNT_TOKEN_ENV = "DATALAYER_BRIDGE_MOUNT_TOKEN"


class MountError(RuntimeError):
    """A mount, unmount or bridge process operation failed."""


@dataclass
class MountHandle:
    """What the driver keeps about a running bridge filesystem."""

    bridge_uid: str
    mount_path: str
    pid: int | None = None


class Mounter(abc.ABC):
    """The mount operations the driver needs, and nothing else."""

    @abc.abstractmethod
    def start(self, *, bridge_uid: str, relay_url: str, mount_token: str, mount_path: str, mode: str) -> MountHandle:
        """Start the bridge filesystem for ``bridge_uid`` and wait for it to be mounted at ``mount_path``."""

    @abc.abstractmethod
    def alive(self, handle: MountHandle) -> bool:
        """Whether the bridge filesystem process is still running."""

    @abc.abstractmethod
    def exit_reason(self, handle: MountHandle) -> str:
        """A short, stable description of why the process exited."""

    @abc.abstractmethod
    def stop(self, handle: MountHandle) -> None:
        """Stop the bridge filesystem process and make sure its mount point is unmounted."""

    @abc.abstractmethod
    def bind(self, source: str, target: str, read_only: bool) -> None:
        """Bind-mount ``source`` at ``target``; read-only when asked."""

    @abc.abstractmethod
    def unmount(self, path: str) -> None:
        """Unmount ``path`` if it is mounted; a no-op otherwise."""

    @abc.abstractmethod
    def is_mount_point(self, path: str) -> bool:
        """Whether ``path`` is a mount point, including a dead FUSE one."""

    # -- what the mount gateway needs on top of a bridge bind ---------------

    @abc.abstractmethod
    def bind_dir(self, source: str, target: str, *, recursive: bool = False) -> None:
        """Bind the directory ``source`` at ``target``.

        Unlike :meth:`bind`, ``source`` is a directory on the node rather than
        a bridge filesystem this mounter started, and the read-only decision
        is :meth:`set_attrs`' — a remount is not recursive, and the gateway
        needs one that is.
        """

    @abc.abstractmethod
    def make_shared(self, path: str) -> None:
        """Put ``path`` in a shared peer group, so submounts propagate out of it."""

    @abc.abstractmethod
    def set_attrs(self, path: str, *, read_only: bool, noexec: bool, recursive: bool = True) -> None:
        """Apply mount attributes to ``path``, recursively by default."""


# ---------------------------------------------------------------------------
# Node implementation
# ---------------------------------------------------------------------------


def _read_mount_table(path: str = "/proc/self/mounts") -> set[str]:
    """Return the mount points listed in the kernel mount table."""
    mounts: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 2:
                    continue
                # Octal escapes for space, tab, newline and backslash.
                mount_point = (
                    parts[1]
                    .replace("\\040", " ")
                    .replace("\\011", "\t")
                    .replace("\\012", "\n")
                    .replace("\\134", "\\")
                )
                mounts.add(mount_point)
    except OSError:
        pass
    return mounts


def _tail(path: str, lines: int = 3) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = [line.rstrip() for line in handle.read().splitlines() if line.strip()]
    except OSError:
        return ""
    return " | ".join(content[-lines:])


class ProcessMounter(Mounter):
    """Runs the bridge filesystem as a child process and uses ``mount``/``umount``."""

    def __init__(
        self,
        *,
        python: str | None = None,
        mount_timeout: float = 30.0,
        stop_timeout: float = 10.0,
        mount_table: str = "/proc/self/mounts",
    ):
        self._python = python or sys.executable
        self._mount_timeout = mount_timeout
        self._stop_timeout = stop_timeout
        self._mount_table = mount_table
        self._procs: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, str] = {}

    # -- process -----------------------------------------------------------

    def start(self, *, bridge_uid: str, relay_url: str, mount_token: str, mount_path: str, mode: str) -> MountHandle:
        os.makedirs(mount_path, exist_ok=True)
        log_path = os.path.join(os.path.dirname(mount_path), "mounter.log")
        env = dict(os.environ)
        env[MOUNT_TOKEN_ENV] = mount_token
        command = [
            self._python,
            "-m",
            "clouder.csi.bridge_mount",
            "--relay-url",
            relay_url,
            "--mount-path",
            mount_path,
            "--mode",
            mode,
        ]
        with open(log_path, "ab") as log_file:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, token in env
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self._procs[bridge_uid] = proc
        self._logs[bridge_uid] = log_path
        handle = MountHandle(bridge_uid=bridge_uid, mount_path=mount_path, pid=proc.pid)
        log.info("bridge %s: mounter pid %s started for %s", bridge_uid, proc.pid, mount_path)

        deadline = time.monotonic() + self._mount_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                reason = self.exit_reason(handle)
                self._procs.pop(bridge_uid, None)
                raise MountError(f"mounter exited before mounting: {reason}")
            if self.is_mount_point(mount_path):
                return handle
            time.sleep(0.2)
        self._terminate(proc)
        self._procs.pop(bridge_uid, None)
        raise MountError(f"mounter did not mount {mount_path} within {self._mount_timeout:.0f}s")

    def alive(self, handle: MountHandle) -> bool:
        proc = self._procs.get(handle.bridge_uid)
        return proc is not None and proc.poll() is None

    def exit_reason(self, handle: MountHandle) -> str:
        proc = self._procs.get(handle.bridge_uid)
        code = proc.returncode if proc is not None else None
        detail = _tail(self._logs.get(handle.bridge_uid, ""))
        if code is None:
            return "mounter is gone"
        reason = f"mounter exited with status {code}"
        return f"{reason}: {detail}" if detail else reason

    def stop(self, handle: MountHandle) -> None:
        proc = self._procs.pop(handle.bridge_uid, None)
        if proc is not None and proc.poll() is None:
            self._terminate(proc)
        # A crashed FUSE daemon leaves its mount point behind, in the
        # "transport endpoint is not connected" state. Clear it.
        self.unmount(handle.mount_path)
        self._logs.pop(handle.bridge_uid, None)

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            proc.terminate()
        try:
            proc.wait(timeout=self._stop_timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            proc.kill()
        try:
            proc.wait(timeout=self._stop_timeout)
        except subprocess.TimeoutExpired:
            log.warning("mounter pid %s did not exit after SIGKILL", proc.pid)

    # -- mount table -------------------------------------------------------

    def bind(self, source: str, target: str, read_only: bool) -> None:
        self._run(["mount", "--bind", source, target])
        if read_only:
            try:
                self._run(["mount", "-o", "remount,bind,ro", target])
            except MountError:
                self.unmount(target)
                raise

    def bind_dir(self, source: str, target: str, *, recursive: bool = False) -> None:
        self._run(["mount", "--rbind" if recursive else "--bind", source, target])

    def make_shared(self, path: str) -> None:
        self._run(["mount", "--make-rshared", path])

    def set_attrs(self, path: str, *, read_only: bool, noexec: bool, recursive: bool = True) -> None:
        try:
            mount_setattr(
                path,
                read_only=read_only,
                nosuid=True,
                nodev=True,
                noexec=noexec,
                recursive=recursive,
            )
            return
        except UnsupportedKernel as exc:
            # A kernel without `mount_setattr` can still be told read-only,
            # but only for this mount: a nested mount underneath it stays
            # writable, so say so rather than letting the caller believe the
            # subtree is protected.
            log.warning("mount_setattr unavailable (%s); falling back to remount for %s", exc, path)
        options = ["remount", "bind", "nosuid", "nodev"]
        if read_only:
            options.append("ro")
        if noexec:
            options.append("noexec")
        self._run(["mount", "-o", ",".join(options), path])

    def unmount(self, path: str) -> None:
        if not self.is_mount_point(path):
            return
        last: MountError | None = None
        for command in (
            ["umount", path],
            ["fusermount3", "-u", "-z", path],
            ["umount", "-l", path],
        ):
            try:
                self._run(command)
            except MountError as exc:
                last = exc
                continue
            if not self.is_mount_point(path):
                return
        if self.is_mount_point(path) and last is not None:
            raise last

    def is_mount_point(self, path: str) -> bool:
        normalized = os.path.normpath(path)
        if normalized in _read_mount_table(self._mount_table):
            return True
        try:
            return os.path.ismount(normalized)
        except OSError:
            return False

    @staticmethod
    def _run(command: list[str]) -> None:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
        except FileNotFoundError as exc:
            raise MountError(f"{command[0]} is not available: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise MountError(f"{' '.join(command)} failed ({result.returncode}): {detail}")


# ---------------------------------------------------------------------------
# Test implementation
# ---------------------------------------------------------------------------


@dataclass
class FakeProcess:
    bridge_uid: str
    relay_url: str
    mount_token: str
    mount_path: str
    mode: str
    alive: bool = True
    reason: str = ""


@dataclass
class FakeMounter(Mounter):
    """An in-memory mounter: records every call, mounts nothing.

    ``kill(bridge_uid, reason)`` simulates the bridge filesystem process
    exiting — the relay refusing the token, the laptop going away — so the
    driver's watcher and stats can be exercised. Set ``fail_start`` to make the
    next ``start`` raise :class:`MountError` with that message.
    """

    fail_start: str | None = None
    fail_bind_dir: str | None = None
    processes: dict[str, FakeProcess] = field(default_factory=dict)
    mounts: set[str] = field(default_factory=set)
    binds: dict[str, tuple[str, bool]] = field(default_factory=dict)
    shared: set[str] = field(default_factory=set)
    attrs: dict[str, dict] = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)

    def start(self, *, bridge_uid: str, relay_url: str, mount_token: str, mount_path: str, mode: str) -> MountHandle:
        self.calls.append(("start", bridge_uid, relay_url, mount_token, mount_path, mode))
        if self.fail_start:
            raise MountError(self.fail_start)
        self.processes[bridge_uid] = FakeProcess(
            bridge_uid=bridge_uid,
            relay_url=relay_url,
            mount_token=mount_token,
            mount_path=mount_path,
            mode=mode,
        )
        self.mounts.add(mount_path)
        return MountHandle(bridge_uid=bridge_uid, mount_path=mount_path, pid=4242)

    def kill(self, bridge_uid: str, reason: str = "mounter exited") -> None:
        process = self.processes[bridge_uid]
        process.alive = False
        process.reason = reason

    def alive(self, handle: MountHandle) -> bool:
        process = self.processes.get(handle.bridge_uid)
        return process is not None and process.alive

    def exit_reason(self, handle: MountHandle) -> str:
        process = self.processes.get(handle.bridge_uid)
        if process is None:
            return "mounter is gone"
        return process.reason or "mounter exited"

    def stop(self, handle: MountHandle) -> None:
        self.calls.append(("stop", handle.bridge_uid))
        self.processes.pop(handle.bridge_uid, None)
        self.mounts.discard(handle.mount_path)

    def bind(self, source: str, target: str, read_only: bool) -> None:
        self.calls.append(("bind", source, target, read_only))
        if source not in self.mounts:
            raise MountError(f"{source} is not mounted")
        self.binds[target] = (source, read_only)
        self.mounts.add(target)

    def bind_dir(self, source: str, target: str, *, recursive: bool = False) -> None:
        self.calls.append(("bind_dir", source, target, recursive))
        if self.fail_bind_dir:
            raise MountError(self.fail_bind_dir)
        self.binds[target] = (source, False)
        self.mounts.add(target)

    def make_shared(self, path: str) -> None:
        self.calls.append(("make_shared", path))
        self.shared.add(path)

    def set_attrs(self, path: str, *, read_only: bool, noexec: bool, recursive: bool = True) -> None:
        self.calls.append(("set_attrs", path, read_only, noexec, recursive))
        if path not in self.mounts:
            raise MountError(f"{path} is not mounted")
        self.attrs[path] = {"read_only": read_only, "noexec": noexec, "recursive": recursive}

    def unmount(self, path: str) -> None:
        self.calls.append(("unmount", path))
        self.mounts.discard(path)
        self.binds.pop(path, None)
        self.shared.discard(path)
        self.attrs.pop(path, None)

    def is_mount_point(self, path: str) -> bool:
        return path in self.mounts

    # -- helpers for assertions ------------------------------------------

    def started(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "start"]
