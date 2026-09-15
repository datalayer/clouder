"""``open_beneath`` and ``mount_setattr``: the two calls the gateway needs.

The resolution is tested twice — once through ``openat2`` where the kernel has
it, once through the component walk — because a node that falls back must
refuse exactly what a node that does not refuses. A guarantee that depends on
the kernel version is not a guarantee.
"""

from __future__ import annotations

import os

import pytest

from ..csi import linux


@pytest.fixture
def root(tmp_path):
    (tmp_path / "home" / "users" / "01H-eric").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    os.symlink(str(tmp_path / "outside"), str(tmp_path / "home" / "users" / "elsewhere"))
    os.symlink("/etc", str(tmp_path / "home" / "absolute"))
    return tmp_path


@pytest.fixture(params=["openat2", "fallback"])
def resolve(request, monkeypatch):
    """The same resolution, through each implementation."""
    if request.param == "fallback":
        def no_libc():
            raise linux.UnsupportedKernel("pretending this kernel has no openat2")

        monkeypatch.setattr(linux, "_libc", no_libc)
    elif not linux.have_openat2():
        pytest.skip("this kernel has no openat2")
    return linux.resolve_beneath


def test_a_folder_under_the_root_resolves(resolve, root):
    assert resolve(str(root), "home/users/01H-eric") == str(root / "home" / "users" / "01H-eric")


def test_an_empty_path_is_the_root_itself(resolve, root):
    assert resolve(str(root), "") == str(root)


def test_a_symlink_component_is_refused(resolve, root):
    with pytest.raises(OSError):
        resolve(str(root), "home/users/elsewhere")


def test_an_absolute_symlink_is_refused(resolve, root):
    with pytest.raises(OSError):
        resolve(str(root), "home/absolute")


def test_walking_up_is_refused(resolve, root):
    with pytest.raises(PermissionError):
        resolve(str(root), "home/../../etc")
    with pytest.raises(PermissionError):
        resolve(str(root), "../etc")


def test_a_missing_path_is_an_error_not_a_guess(resolve, root):
    with pytest.raises(OSError):
        resolve(str(root), "home/users/nobody")


def test_a_file_descriptor_is_returned_and_closed_by_the_caller(root):
    fd = linux.open_beneath(str(root), "home/users/01H-eric")
    try:
        assert isinstance(fd, int) and fd >= 0
    finally:
        os.close(fd)


@pytest.mark.skipif(not linux.have_mount_setattr(), reason="not a Linux node")
def test_mount_setattr_refuses_a_path_that_is_not_a_mount(tmp_path):
    # Not a mount point, so the kernel says EINVAL. What matters is that the
    # wrapper reports the kernel's answer rather than pretending it worked.
    with pytest.raises(OSError):
        linux.mount_setattr(str(tmp_path), read_only=True)


def test_the_flags_are_the_kernels_numbers():
    # Wrong numbers here mount a folder with attributes nobody asked for, and
    # nothing else would notice.
    assert linux.MOUNT_ATTR_RDONLY == 0x00000001
    assert linux.MOUNT_ATTR_NOSUID == 0x00000002
    assert linux.MOUNT_ATTR_NODEV == 0x00000004
    assert linux.MOUNT_ATTR_NOEXEC == 0x00000008
    assert linux.AT_RECURSIVE == 0x8000
    assert linux.RESOLVE_NO_SYMLINKS == 0x04
    assert linux.RESOLVE_BENEATH == 0x08
