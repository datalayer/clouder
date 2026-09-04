"""Mounters: how the Local CSI driver makes and unmakes mounts.

The driver never shells out itself. Everything that touches the node's mount
table goes through a :class:`Mounter`:

- :class:`ProcessMounter` runs on a node. It starts the bridge filesystem
  process (``python -m clouder.csi.bridge_mount``, which runs
  ``code_sandboxes.bridge_mount.run_bridge_mount``), bind-mounts its mount
  point at the pod's target path with ``mount --bind`` — remounted read-only
  when the bridge is — and unmounts with ``umount``.
- :class:`FakeMounter` records the same calls in memory for the tests.

The Node Mount Gateway uses the same mounter for a different job: ``bind_dir``,
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

from .linux import UnsupportedKernel, attach_mount, mount_setattr

log = logging.getLogger("clouder.csi.mounter")

#: Environment variable the bridge filesystem process reads its token from.
#: The token travels in the environment, never on the command line, so it is
#: not visible in ``ps`` output.
#: How long any one mount command is given. A mount command that does not
#: finish is not a slow one: it is `umount` waiting on a filesystem whose
#: process is gone, and the wait has no end.
MOUNT_COMMAND_TIMEOUT_SECONDS = 20.0

MOUNT_TOKEN_ENV = "DATALAYER_BRIDGE_MOUNT_TOKEN"
#: The session key, in the environment for the reason the token is: `ps`
#: output is readable by anything on the node. Both ends seal their frames
#: with it, and a mount started without one speaks plaintext at a client
#: that does not.
SESSION_KEY_ENV = "DATALAYER_BRIDGE_SESSION_KEY"


#: Who a sandbox runs as. Every Datalayer sandbox is `jovyan`, 1000:100 — the
#: same pair `datalayer_common.home_folders` writes home folders for. Said
#: here rather than imported, because a node agent has no business installing
#: a services package to learn it.
SANDBOX_UID = 1000
SANDBOX_GID = 100


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
    def start(
        self,
        *,
        bridge_uid: str,
        relay_url: str,
        mount_token: str,
        mount_path: str,
        mode: str,
        session_key: str = "",
    ) -> MountHandle:
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
        """Unmount ``path`` until nothing is mounted there; a no-op otherwise."""

    @abc.abstractmethod
    def unmount_once(self, path: str) -> None:
        """Remove exactly the topmost mount at ``path``, and raise if it will not go.

        The gateway stacks its tree on top of kubelet's `emptyDir` tmpfs, so
        "unmount until nothing is left there" would take kubelet's own volume
        out from under a running pod. It also must not fall back to a lazy
        unmount: a mount that will not come down is the leak the whole design
        wants to be loud about, and detaching it quietly is how a Pod stuck in
        `Terminating` becomes a mystery.
        """

    @abc.abstractmethod
    def is_mount_point(self, path: str) -> bool:
        """Whether ``path`` is a mount point, including a dead FUSE one."""

    # -- what the Node Mount Gateway needs on top of a bridge bind ---------------

    @abc.abstractmethod
    def bind_dir(self, source: str, target: str, *, recursive: bool = False) -> None:
        """Bind the directory ``source`` at ``target``.

        Unlike :meth:`bind`, ``source`` is a directory on the node rather than
        a bridge filesystem this mounter started, and the read-only decision
        is :meth:`set_attrs`' — a remount is not recursive, and the gateway
        needs one that is.
        """

    @abc.abstractmethod
    def attach(self, source: str, target: str, *, read_only: bool, noexec: bool) -> None:
        """Bind ``source`` at ``target`` with its attributes already set.

        One call, not a bind followed by :meth:`set_attrs`, because **mount
        attributes do not propagate to peers**: a mount is copied to every peer
        when it is attached, with the flags it has at that instant, and
        changing one copy afterwards leaves the others as they were. A `ro`
        grant made read-only a moment after the bind reaches the sandbox
        writable.
        """

    @abc.abstractmethod
    def mount_filesystem(
        self, fs_type: str, source: str, target: str, *, options: list[str]
    ) -> None:
        """Mount a filesystem the kernel knows how to mount, at ``target``.

        NFS is the one that matters: a share the node can reach directly, with
        no process to keep running and nothing to watch. It is neither a bind
        of something already mounted nor a userspace filesystem, which is why
        it is its own call rather than a special case of either.
        """

    @abc.abstractmethod
    def make_shared(self, path: str) -> None:
        """Put ``path`` in a shared peer group, so submounts propagate out of it."""

    @abc.abstractmethod
    def set_attrs(self, path: str, *, read_only: bool, noexec: bool, recursive: bool = True) -> None:
        """Apply mount attributes to ``path``, recursively by default."""

    @abc.abstractmethod
    def mount_identity(self, path: str) -> tuple[str, str] | None:
        """What is mounted at ``path``: its device and its root within that device.

        ``None`` when nothing is. Two paths showing the same pair are the same
        filesystem subtree — which is how the gateway tells a mount **it**
        made from one somebody else did. `is_mount_point` cannot: kubelet's
        own `emptyDir` tmpfs is a mount point too, and treating it as "already
        published" is a gateway that binds nothing and reports success.
        """


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


def _mount_identity(mount_point: str, path: str = "/proc/self/mountinfo") -> tuple[str, str] | None:
    """The device and subtree root of the topmost mount at ``mount_point``.

    `mountinfo` lists mounts in order and a later entry shadows an earlier one
    at the same path, so the last match is what is actually visible there.
    """
    identity: tuple[str, str] | None = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 5:
                    continue
                point = (
                    fields[4]
                    .replace("\\040", " ")
                    .replace("\\011", "\t")
                    .replace("\\012", "\n")
                    .replace("\\134", "\\")
                )
                if point == mount_point:
                    identity = (fields[2], fields[3])
    except OSError:
        return None
    return identity


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
        mount_info: str = "/proc/self/mountinfo",
    ):
        self._python = python or sys.executable
        self._mount_timeout = mount_timeout
        self._stop_timeout = stop_timeout
        self._mount_table = mount_table
        self._mount_info = mount_info
        self._procs: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, str] = {}

    # -- process -----------------------------------------------------------

    def start(
        self,
        *,
        bridge_uid: str,
        relay_url: str,
        mount_token: str,
        mount_path: str,
        mode: str,
        session_key: str = "",
    ) -> MountHandle:
        os.makedirs(mount_path, exist_ok=True)
        log_path = os.path.join(os.path.dirname(mount_path), "mounter.log")
        env = dict(os.environ)
        env[MOUNT_TOKEN_ENV] = mount_token
        env[SESSION_KEY_ENV] = session_key
        command = [
            self._python,
            "-m",
            "clouder.csi.bridge_mount",
            "--relay-url",
            relay_url,
            "--bridge-uid",
            bridge_uid,
            "--mount-path",
            mount_path,
            "--mode",
            mode,
            # This agent mounts as root and the sandbox reads as its own
            # user, in another namespace: without these the folder answers
            # `Permission denied` to the person who asked for it, and every
            # file in it comes back owned by root.
            "--allow-other",
            "--uid",
            str(SANDBOX_UID),
            "--gid",
            str(SANDBOX_GID),
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

    def attach(self, source: str, target: str, *, read_only: bool, noexec: bool) -> None:
        try:
            attach_mount(source, target, read_only=read_only, nosuid=True, nodev=True, noexec=noexec)
        except UnsupportedKernel as exc:
            # The fallback would bind first and set the flags after, which
            # leaves every propagated copy — the sandbox's included — with the
            # flags the mount had when it was attached. A folder that is
            # read-only on this node and writable in the sandbox is worse than
            # one that was refused, so refuse it.
            raise MountError(
                f"this kernel cannot attach a mount with its attributes set ({exc}); "
                "the Node Mount Gateway needs Linux 5.12 or newer"
            ) from exc

    def mount_filesystem(
        self, fs_type: str, source: str, target: str, *, options: list[str]
    ) -> None:
        command = ["mount", "-t", fs_type]
        if options:
            command += ["-o", ",".join(options)]
        self._run(command + [source, target])

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

    def unmount_once(self, path: str) -> None:
        if not self.is_mount_point(path):
            return
        self._run(["umount", path])

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

    def mount_identity(self, path: str) -> tuple[str, str] | None:
        return _mount_identity(os.path.normpath(path), self._mount_info)

    @staticmethod
    def _run(command: list[str], timeout: float = MOUNT_COMMAND_TIMEOUT_SECONDS) -> None:
        try:
            result = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, check=False, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise MountError(f"{command[0]} is not available: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            # `umount` on a filesystem whose process is gone waits for a
            # server that will never answer, and it waits forever. Without
            # this the agent's one reconcile thread blocked there and every
            # pod on the node stopped getting its mounts, while the health
            # endpoint went on saying the agent was fine. A timeout makes it
            # a failed command, which is what the caller already knows how
            # to answer: the lazy forms it tries next do come down.
            raise MountError(f"{' '.join(command)} did not finish in {timeout:.0f}s") from exc
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
    fail_unmount_once: str | None = None
    processes: dict[str, FakeProcess] = field(default_factory=dict)
    mounts: set[str] = field(default_factory=set)
    binds: dict[str, tuple[str, bool]] = field(default_factory=dict)
    shared: set[str] = field(default_factory=set)
    attrs: dict[str, dict] = field(default_factory=dict)
    foreign: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)
    session_keys: dict[str, str] = field(default_factory=dict)

    def start(
        self,
        *,
        bridge_uid: str,
        relay_url: str,
        mount_token: str,
        mount_path: str,
        mode: str,
        session_key: str = "",
    ) -> MountHandle:
        self.calls.append(("start", bridge_uid, relay_url, mount_token, mount_path, mode))
        self.session_keys[bridge_uid] = session_key
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

    def attach(self, source: str, target: str, *, read_only: bool, noexec: bool) -> None:
        self.calls.append(("attach", source, target, read_only, noexec))
        if self.fail_bind_dir:
            raise MountError(self.fail_bind_dir)
        self.binds[target] = (source, read_only)
        self.mounts.add(target)
        self.attrs[target] = {"read_only": read_only, "noexec": noexec, "recursive": True}

    def mount_filesystem(
        self, fs_type: str, source: str, target: str, *, options: list[str]
    ) -> None:
        self.calls.append(("mount_filesystem", fs_type, source, target, tuple(options)))
        if self.fail_bind_dir:
            raise MountError(self.fail_bind_dir)
        self.binds[target] = (f"{fs_type}:{source}", "ro" in options)
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
        self.foreign.discard(path)

    def unmount_once(self, path: str) -> None:
        self.calls.append(("unmount_once", path))
        if self.fail_unmount_once:
            raise MountError(self.fail_unmount_once)
        self.binds.pop(path, None)
        self.attrs.pop(path, None)
        self.shared.discard(path)
        if path not in self.foreign:
            self.mounts.discard(path)

    def is_mount_point(self, path: str) -> bool:
        return path in self.mounts

    def mount_identity(self, path: str) -> tuple[str, str] | None:
        """Whatever this mounter bound there, named by its source.

        `foreign(path)` stands in for a mount somebody else made — kubelet's
        `emptyDir` tmpfs, in the case that matters — which is a mount point
        with an identity of its own that no bind of ours will ever match.
        """
        if path in self.foreign:
            return ("0:1", f"foreign:{path}")
        if path in self.binds:
            return ("0:2", self.binds[path][0])
        return ("0:2", path) if path in self.mounts else None

    def foreign_mount(self, path: str) -> None:
        """Say that something not this mounter's is mounted at ``path``."""
        self.foreign.add(path)
        self.mounts.add(path)

    # -- helpers for assertions ------------------------------------------

    def started(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "start"]
