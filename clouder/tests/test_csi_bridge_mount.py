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
    outcome: dict = {"value": 0}

    def run_bridge_mount(relay_url, mount_token, mount_path, mode, **kwargs):
        calls.append((relay_url, mount_token, mount_path, mode))
        if isinstance(outcome["value"], BaseException):
            raise outcome["value"]
        return outcome["value"]

    package = types.ModuleType("code_sandboxes")
    module = types.ModuleType("code_sandboxes.bridge_mount")
    module.run_bridge_mount = run_bridge_mount
    package.bridge_mount = module
    monkeypatch.setitem(sys.modules, "code_sandboxes", package)
    monkeypatch.setitem(sys.modules, "code_sandboxes.bridge_mount", module)
    return calls, outcome


ARGS = ["--relay-url", "wss://r1.datalayer.run/bridges/brd-1", "--mount-path", "/csi/mounts/brd-1/mnt", "--mode", "ro"]


def test_token_comes_from_the_environment_never_the_command_line(monkeypatch, fake_code_sandboxes):
    calls, _ = fake_code_sandboxes
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok-123")
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_OK
    assert calls == [("wss://r1.datalayer.run/bridges/brd-1", "tok-123", "/csi/mounts/brd-1/mnt", "ro")]
    assert "--token" not in " ".join(ARGS)


def test_missing_token_is_a_usage_error(monkeypatch, fake_code_sandboxes, capsys):
    calls, _ = fake_code_sandboxes
    monkeypatch.delenv(MOUNT_TOKEN_ENV, raising=False)
    assert bridge_mount.main(ARGS) == bridge_mount.EXIT_USAGE
    assert calls == []
    assert MOUNT_TOKEN_ENV in capsys.readouterr().err


@pytest.mark.parametrize("status", [2, 3, 4, 5])
def test_return_value_of_run_bridge_mount_is_the_exit_status(monkeypatch, fake_code_sandboxes, status):
    """code_sandboxes reports refusal (4) and relay-ended sessions (5) by return value, not by raising."""
    _, outcome = fake_code_sandboxes
    outcome["value"] = status
    monkeypatch.setenv(MOUNT_TOKEN_ENV, "tok")
    assert bridge_mount.main(ARGS) == status


def test_refusal_exception_maps_to_refused(monkeypatch, fake_code_sandboxes, capsys):
    _, outcome = fake_code_sandboxes
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
