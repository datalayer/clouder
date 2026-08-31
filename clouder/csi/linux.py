"""The two kernel calls the mount gateway needs and Python does not expose.

``mount_setattr(2)`` is how a bind mount is made read-only *recursively*.
``mount -o remount,bind,ro`` sets the flag on one mount and says nothing about
the mounts nested under it, so a "read-only" home folder with a nested mount
beneath it is not read-only where it matters. Kubernetes calls the same
problem out and answers it with ``recursiveReadOnly``; the gateway makes its
binds itself, so it makes the same syscall itself.

``openat2(2)`` with ``RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS`` is how a source
path is resolved under the shared filesystem without a symlink or a ``..``
taking it somewhere else, and without the check-then-mount race that
``realpath`` and a string comparison leave open. Where the kernel is too old
for it, :func:`open_beneath` walks the path one component at a time with
``O_NOFOLLOW``, which gives the same guarantee through a portable syscall.

Neither call is available on a non-Linux machine. Both raise
:class:`UnsupportedKernel` there, which is what the tests of everything above
this module rely on to stay honest: they use the fallback and a temporary
directory, and never the mount table.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import platform
import stat

__all__ = [
    "AT_RECURSIVE",
    "attach_mount",
    "MOUNT_ATTR_NODEV",
    "MOUNT_ATTR_NOEXEC",
    "MOUNT_ATTR_NOSUID",
    "MOUNT_ATTR_RDONLY",
    "MountAttrError",
    "UnsupportedKernel",
    "have_mount_setattr",
    "have_openat2",
    "make_beneath",
    "mount_setattr",
    "open_beneath",
    "resolve_beneath",
]

# mount_setattr(2) attributes.
MOUNT_ATTR_RDONLY = 0x00000001
MOUNT_ATTR_NOSUID = 0x00000002
MOUNT_ATTR_NODEV = 0x00000004
MOUNT_ATTR_NOEXEC = 0x00000008

AT_EMPTY_PATH = 0x1000
AT_RECURSIVE = 0x8000

# openat2(2) resolve flags.
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08

# Both syscalls were added after the syscall numbers were unified for new
# calls, so these are the numbers on x86_64, aarch64 and riscv64 alike. On
# anything else the wrapper reports the call as unavailable rather than
# invoking a syscall that means something different there.
_UNIFIED_ARCHES = ("x86_64", "aarch64", "arm64", "riscv64")
_NR_OPEN_TREE = 428
_NR_MOVE_MOUNT = 429
_NR_OPENAT2 = 437
_NR_MOUNT_SETATTR = 442

# open_tree(2) / move_mount(2).
OPEN_TREE_CLONE = 1
MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
AT_FDCWD = -100


class UnsupportedKernel(RuntimeError):
    """The running kernel or platform does not offer the call."""


class MountAttrError(OSError):
    """``mount_setattr`` failed."""


class _MountAttr(ctypes.Structure):
    _fields_ = [
        ("attr_set", ctypes.c_uint64),
        ("attr_clr", ctypes.c_uint64),
        ("propagation", ctypes.c_uint64),
        ("userns_fd", ctypes.c_uint64),
    ]


class _OpenHow(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


def _libc():
    if platform.system() != "Linux" or platform.machine() not in _UNIFIED_ARCHES:
        raise UnsupportedKernel(
            f"mount gateway syscalls are Linux-only ({platform.system()} {platform.machine()})"
        )
    name = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(name, use_errno=True)


def have_mount_setattr() -> bool:
    """Whether ``mount_setattr`` can be called here."""
    try:
        _libc()
    except UnsupportedKernel:
        return False
    return True


def have_openat2() -> bool:
    """Whether ``openat2`` is available; :func:`open_beneath` works either way."""
    try:
        libc = _libc()
    except UnsupportedKernel:
        return False
    how = _OpenHow(flags=os.O_PATH | os.O_CLOEXEC, mode=0, resolve=RESOLVE_BENEATH)
    fd = libc.syscall(
        ctypes.c_long(_NR_OPENAT2),
        ctypes.c_int(-1),
        ctypes.c_char_p(b"."),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if fd >= 0:
        os.close(fd)
        return True
    return ctypes.get_errno() != errno.ENOSYS


def _attr_of(read_only: bool, nosuid: bool, nodev: bool, noexec: bool) -> "_MountAttr":
    attr_set = 0
    attr_clr = 0
    for flag, wanted in (
        (MOUNT_ATTR_RDONLY, read_only),
        (MOUNT_ATTR_NOSUID, nosuid),
        (MOUNT_ATTR_NODEV, nodev),
        (MOUNT_ATTR_NOEXEC, noexec),
    ):
        if wanted:
            attr_set |= flag
        else:
            attr_clr |= flag
    return _MountAttr(attr_set=attr_set, attr_clr=attr_clr, propagation=0, userns_fd=0)


def _setattr_fd(libc, dir_fd: int, attr: "_MountAttr", *, recursive: bool, where: str) -> None:
    flags = AT_EMPTY_PATH | (AT_RECURSIVE if recursive else 0)
    result = libc.syscall(
        ctypes.c_long(_NR_MOUNT_SETATTR),
        ctypes.c_int(dir_fd),
        ctypes.c_char_p(b""),
        ctypes.c_uint(flags),
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
    )
    if result != 0:
        code = ctypes.get_errno()
        if code == errno.ENOSYS:
            raise UnsupportedKernel("mount_setattr is not available on this kernel")
        raise MountAttrError(code, f"mount_setattr({where}) failed: {os.strerror(code)}")


def attach_mount(
    source: str,
    target: str,
    *,
    read_only: bool = False,
    nosuid: bool = True,
    nodev: bool = True,
    noexec: bool = False,
) -> None:
    """Bind ``source`` at ``target`` with its attributes already set.

    Not a bind followed by :func:`mount_setattr`, because that is not the same
    thing: **mount attributes do not propagate to peers.** A mount created in
    a shared peer group is copied to every peer at the instant it is attached,
    with the flags it has then; setting `MOUNT_ATTR_RDONLY` on one copy
    afterwards leaves every other copy writable. For the gateway that means a
    `ro` grant, bound in the agent's tree and made read-only a moment later,
    reaches the sandbox **writable** — which is not a slow read-only mount, it
    is no read-only mount at all.

    So the mount is built detached and attached once it is already right:
    ``open_tree(OPEN_TREE_CLONE)`` clones the subtree without putting it
    anywhere, ``mount_setattr`` sets the flags on the clone, and
    ``move_mount`` attaches it. Every propagated copy is then created from a
    mount that is already read-only, `nosuid` and `nodev`.
    """
    libc = _libc()
    fd = libc.syscall(
        ctypes.c_long(_NR_OPEN_TREE),
        ctypes.c_int(AT_FDCWD),
        ctypes.c_char_p(source.encode("utf-8")),
        ctypes.c_uint(OPEN_TREE_CLONE | AT_RECURSIVE | os.O_CLOEXEC),
    )
    if fd < 0:
        code = ctypes.get_errno()
        if code == errno.ENOSYS:
            raise UnsupportedKernel("open_tree is not available on this kernel")
        raise MountAttrError(code, f"open_tree({source}) failed: {os.strerror(code)}")
    try:
        _setattr_fd(
            libc,
            fd,
            _attr_of(read_only, nosuid, nodev, noexec),
            recursive=True,
            where=source,
        )
        result = libc.syscall(
            ctypes.c_long(_NR_MOVE_MOUNT),
            ctypes.c_int(fd),
            ctypes.c_char_p(b""),
            ctypes.c_int(AT_FDCWD),
            ctypes.c_char_p(target.encode("utf-8")),
            ctypes.c_uint(MOVE_MOUNT_F_EMPTY_PATH),
        )
        if result != 0:
            code = ctypes.get_errno()
            if code == errno.ENOSYS:
                raise UnsupportedKernel("move_mount is not available on this kernel")
            raise MountAttrError(code, f"move_mount({source} -> {target}) failed: {os.strerror(code)}")
    finally:
        os.close(fd)


def mount_setattr(
    path: str,
    *,
    read_only: bool = False,
    nosuid: bool = True,
    nodev: bool = True,
    noexec: bool = False,
    recursive: bool = True,
) -> None:
    """Set mount attributes on ``path``, recursively by default.

    Everything the gateway mounts is `nosuid` and `nodev`: a file on a shared
    filesystem must not be able to give a sandbox a device or another
    identity. `noexec` is for data sources only — a home folder holds scripts
    and editable installs, and mounting it `noexec` breaks them — and
    read-only means read-only all the way down, which is the reason this call
    exists rather than a remount.
    """
    libc = _libc()
    attr = _attr_of(read_only, nosuid, nodev, noexec)
    dir_fd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        _setattr_fd(libc, dir_fd, attr, recursive=recursive, where=path)
    finally:
        os.close(dir_fd)


def open_beneath(root: str, relative: str) -> int:
    """Open ``relative`` under ``root``, refusing symlinks and any escape.

    Returns an ``O_PATH`` file descriptor the caller must close. Uses
    ``openat2`` where the kernel has it and a component-wise ``O_NOFOLLOW``
    walk where it does not; both refuse a symlink anywhere in the path and
    both refuse to leave ``root``, so the answer does not depend on which one
    ran.
    """
    parts = [part for part in str(relative or "").split("/") if part]
    for part in parts:
        if part in (".", ".."):
            raise PermissionError(errno.EACCES, f"'{relative}' walks outside {root}")
    if not parts:
        return os.open(root, os.O_PATH | os.O_CLOEXEC | os.O_DIRECTORY)

    root_fd = os.open(root, os.O_PATH | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        try:
            libc = _libc()
        except UnsupportedKernel:
            libc = None
        if libc is not None:
            how = _OpenHow(
                flags=os.O_PATH | os.O_CLOEXEC,
                mode=0,
                resolve=RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS,
            )
            fd = libc.syscall(
                ctypes.c_long(_NR_OPENAT2),
                ctypes.c_int(root_fd),
                ctypes.c_char_p("/".join(parts).encode("utf-8")),
                ctypes.byref(how),
                ctypes.c_size_t(ctypes.sizeof(how)),
            )
            if fd >= 0:
                return fd
            code = ctypes.get_errno()
            if code != errno.ENOSYS:
                raise OSError(code, f"'{relative}' is not reachable beneath {root}: {os.strerror(code)}")
        # No openat2: walk it, one O_NOFOLLOW component at a time.
        current = os.dup(root_fd)
        try:
            for index, part in enumerate(parts):
                last = index == len(parts) - 1
                flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW
                if not last:
                    flags |= os.O_DIRECTORY
                nxt = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = nxt
                # `O_PATH | O_NOFOLLOW` does not fail on a symlink — it opens
                # the link itself — so the refusal has to be made here, or the
                # fallback would accept exactly what `RESOLVE_NO_SYMLINKS`
                # exists to refuse.
                if stat.S_ISLNK(os.fstat(current).st_mode):
                    raise PermissionError(
                        errno.ELOOP, f"'{relative}' passes through a symlink at '{part}'"
                    )
            return current
        except BaseException:
            os.close(current)
            raise
    finally:
        os.close(root_fd)


def make_beneath(root: str, relative: str, mode: int = 0o755) -> str:
    """Create ``relative`` under ``root``, one component at a time.

    Each component is created with ``mkdirat`` relative to the previous one's
    descriptor and reopened with ``O_NOFOLLOW``, so a symlink appearing
    mid-walk is refused rather than followed — the same guarantee
    :func:`open_beneath` gives for reading, applied to writing. Returns the
    absolute path of the leaf.

    Existing components are left exactly as they are, including their mode and
    ownership: this creates what is missing, and never re-permissions what is
    already there.
    """
    parts = [part for part in str(relative or "").split("/") if part]
    for part in parts:
        if part in (".", ".."):
            raise PermissionError(errno.EACCES, f"'{relative}' walks outside {root}")
    if not parts:
        return os.path.realpath(root)

    current = os.open(root, os.O_PATH | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        for index, part in enumerate(parts):
            try:
                os.mkdir(part, mode, dir_fd=current)
            except FileExistsError:
                pass
            flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
            nxt = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = nxt
            if stat.S_ISLNK(os.fstat(current).st_mode):
                raise PermissionError(
                    errno.ELOOP, f"'{relative}' passes through a symlink at '{part}'"
                )
            del index
        try:
            return os.readlink(f"/proc/self/fd/{current}")
        except OSError:
            return os.path.join(os.path.realpath(root), *parts)
    finally:
        os.close(current)


def resolve_beneath(root: str, relative: str) -> str:
    """The absolute path of ``relative`` under ``root``, or raise.

    The path it returns is the one the descriptor points at — read back from
    ``/proc/self/fd`` where that is available — so a caller that mounts it is
    mounting what was checked, not a path that could have been swapped in
    between.
    """
    fd = open_beneath(root, relative)
    try:
        link = f"/proc/self/fd/{fd}"
        try:
            resolved = os.readlink(link)
        except OSError:
            resolved = os.path.join(root, *[p for p in str(relative or "").split("/") if p])
        real_root = os.path.realpath(root)
        if resolved != real_root and not resolved.startswith(real_root.rstrip("/") + "/"):
            raise PermissionError(errno.EACCES, f"'{relative}' resolves outside {root}: {resolved}")
        return resolved
    finally:
        os.close(fd)
