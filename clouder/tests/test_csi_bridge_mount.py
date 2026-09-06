"""The per-bridge child process hands over to code_sandboxes and reports by exit status."""

from __future__ import annotations

import sys
import types

import pytest

from ..csi import bridge_mount
from ..csi.mounter import MOUNT_TOKEN_ENV


@pytest.fixture
def fake_code_sandboxes(monkeypatch):
    """Install a fake ``code_sandboxes.bridge_mount`` and record what it is called with."""
    calls: list[tuple] = []
    options: list[dict] = []
    outcome: dict = {"value": 0}

    def run_bridge_mount(relay_url, mount_token, mount_path, mode, **kwargs):
        calls.append((relay_url, mount_token, mount_path, mode))
        options.append(kwargs)
        if isinstance(outcome["value"], BaseException):
            raise outcome["value"]
        return outcome["value"]

    package = types.ModuleType("code_sandboxes")
    module = types.ModuleType("code_sandboxes.bridge_mount")
    module.run_bridge_mount = run_bridge_mount
    package.bridge_mount = module
    monkeypatch.setitem(sys.modules, "code_sandboxes", package)
    monkeypatch.setitem(sys.modules, "code_sandboxes.bridge_mount", module)
    return calls, outcome, options


ARGS = ["--relay-url", "wss://r1.datalayer.run/bridges/brd-1", "--mount-path", "/csi/mounts/brd-1/mnt", "--mode", "ro"]


def test_token_comes_from_the_environment_never_the_command_line(monkeypatch, fake_code_sandboxes):
    calls, _, _options = fake_code_sandboxes
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok-123")
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_OK
    assert calls == [("wss://r1.datalayer.run/bridges/brd-1", "tok-123", "/csi/mounts/brd-1/mnt", "ro")]
    assert "--token" not in " ".join(ARGS)


def test_missing_token_is_a_usage_error(monkeypatch, fake_code_sandboxes, capsys):
    calls, _, _options = fake_code_sandboxes
    monkeypatch.delenv(MOUNT_TOKEN_ENV, raising=False)
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_USAGE
    assert calls == []
    assert MOUNT_TOKEN_ENV in capsys.readouterr().err


@pytest.mark.parametrize("status", [2, 3, 4, 5])
def test_return_value_of_run_bridge_mount_is_the_exit_status(monkeypatch, fake_code_sandboxes, status):
    """code_sandboxes reports refusal (4) and relay-ended sessions (5) by return value, not by raising."""
    _, outcome, _options = fake_code_sandboxes
    outcome["value"] = status
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")
    assert bridge_mount.main(ARGS) == status


def test_refusal_exception_maps_to_refused(monkeypatch, fake_code_sandboxes, capsys):
    _, outcome, _options = fake_code_sandboxes
    outcome["value"] = RuntimeError("relay refused the token: revoked")
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_REFUSED
    assert "relay refused" in capsys.readouterr().err

    outcome["value"] = OSError("transport endpoint is not connected")
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_FAILED


def test_missing_package_is_reported(monkeypatch):
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")
    monkeypatch.setitem(sys.modules, "code_sandboxes", None)
    monkeypatch.setitem(sys.modules, "code_sandboxes.bridge_mount", None)
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_NO_PACKAGE


def test_the_agent_mounts_for_the_sandbox_user_not_for_root(monkeypatch, fake_code_sandboxes):
    """Audit 57: the agent mounts as root and the sandbox reads as `jovyan`.

    A FUSE mount belongs to whoever made it. Without `--allow-other` the
    folder the person asked for answered them `Permission denied`; without
    the ids every file in it came back owned by root and unwritable. Inside
    a sandbox the mounter is the reader and neither is passed.
    """
    _calls, _outcome, options = fake_code_sandboxes
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")

    assert bridge_mount.main(ARGS + ["--allow-other", "--uid", "1000", "--gid", "100"]) == bridge_mount.EXIT_OK

    assert options[-1] == {
        "allow_other": True, "uid": 1000, "gid": 100, "bridge_uid": None, "session_key": None
    }


def test_without_the_flags_the_mount_stays_the_mounters_own(monkeypatch, fake_code_sandboxes):
    _calls, _outcome, options = fake_code_sandboxes
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")

    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_OK

    assert options[-1] == {
        "allow_other": False, "uid": None, "gid": None, "bridge_uid": None, "session_key": None
    }


def test_the_session_key_comes_from_the_environment_with_the_bridge_it_seals(monkeypatch, fake_code_sandboxes):
    """Audit 58: both ends seal their frames with the session key.

    Contents mints one per session and hands it to both — the person's
    client and the sandbox side. The node agent was given only the token, so
    it spoke plaintext at a client speaking sealed frames and every frame
    read as `frame header runs past the end of the frame`. Like the token,
    the key travels in the environment: `ps` is readable on a node.
    """
    _calls, _outcome, options = fake_code_sandboxes
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")
    monkeypatch.setenv(bridge_mount.SESSION_KEY_ENV, "ab" * 32)

    assert bridge_mount.main(ARGS + ["--bridge-uid", "brd-1"]) == bridge_mount.EXIT_OK

    assert options[-1]["session_key"] == "ab" * 32
    assert options[-1]["bridge_uid"] == "brd-1"
    assert "ab" * 32 not in " ".join(ARGS)
