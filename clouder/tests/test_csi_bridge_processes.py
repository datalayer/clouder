"""Running a local bridge as a gateway mount.

The filesystem is the CSI driver's, unchanged. What is new is that the mount
is made after the pod is running and the agent may be restarted while it
stands — so these are mostly tests about a process the agent did not start.
"""

from __future__ import annotations

import os

import pytest

from ..csi.bridge_processes import (
    LOCAL_BRIDGE_KIND,
    SECRET_RELAY_KEY,
    SECRET_TOKEN_KEY,
    BridgeProcesses,
)
from ..csi.node_mount_gateway import ERROR_PROCESS_UNSUPPORTED, NodeMountGatewayError
from ..csi.mounter import FakeMounter

BRIDGE = "brd-0123456789abcdef"
RELAY = f"wss://r1.datalayer.run/api/contents/v1/bridges/{BRIDGE}"
SECRET = {SECRET_TOKEN_KEY: b"mount-token", SECRET_RELAY_KEY: RELAY.encode()}


@pytest.fixture
def runner():
    mounter = FakeMounter()
    return BridgeProcesses(mounter, relay_host="r1.datalayer.run"), mounter


def start(runner, **overrides):
    kwargs = {
        "kind": LOCAL_BRIDGE_KIND,
        "source": BRIDGE,
        "target": "/gw/pods/x/local",
        "read_only": False,
        "credential": SECRET,
    }
    kwargs.update(overrides)
    return runner.start(**kwargs)


def test_the_filesystem_is_started_with_the_token_from_the_secret(runner):
    processes, mounter = runner

    pid = start(processes)

    assert pid
    call = mounter.started()[0]
    assert call[1] == BRIDGE
    assert call[2] == RELAY
    assert call[3] == "mount-token"
    assert call[5] == "rw"


def test_a_read_only_grant_starts_a_read_only_bridge(runner):
    processes, mounter = runner

    start(processes, read_only=True)

    assert mounter.started()[0][5] == "ro"


def test_a_secret_without_the_token_starts_nothing(runner):
    processes, mounter = runner

    with pytest.raises(NodeMountGatewayError) as raised:
        start(processes, credential={SECRET_RELAY_KEY: RELAY.encode()})

    assert raised.value.code == ERROR_PROCESS_UNSUPPORTED
    assert mounter.started() == []


def test_a_relay_that_is_not_the_configured_one_is_refused(runner):
    processes, mounter = runner

    with pytest.raises(NodeMountGatewayError):
        start(processes, credential={
            SECRET_TOKEN_KEY: b"t",
            SECRET_RELAY_KEY: f"wss://elsewhere.example/api/contents/v1/bridges/{BRIDGE}".encode(),
        })

    assert mounter.started() == []


def test_a_relay_url_that_names_another_bridge_is_refused(runner):
    processes, mounter = runner

    # Otherwise a grant could point a sandbox's mount at somebody else's
    # session by naming their bridge in the URL.
    with pytest.raises(NodeMountGatewayError):
        start(processes, credential={
            SECRET_TOKEN_KEY: b"t",
            SECRET_RELAY_KEY: b"wss://r1.datalayer.run/api/contents/v1/bridges/brd-somebody-else",
        })

    assert mounter.started() == []


def test_a_plaintext_relay_is_refused_unless_it_was_allowed(runner):
    processes, mounter = runner
    insecure = f"ws://r1.datalayer.run/api/contents/v1/bridges/{BRIDGE}".encode()

    with pytest.raises(NodeMountGatewayError):
        start(processes, credential={SECRET_TOKEN_KEY: b"t", SECRET_RELAY_KEY: insecure})

    permissive = BridgeProcesses(mounter, relay_host="r1.datalayer.run", allow_insecure_relay=True)
    assert start(permissive, credential={SECRET_TOKEN_KEY: b"t", SECRET_RELAY_KEY: insecure})


def test_a_kind_this_runner_does_not_serve_is_refused(runner):
    processes, _mounter = runner

    with pytest.raises(NodeMountGatewayError) as raised:
        start(processes, kind="cloud-storage")

    assert raised.value.code == ERROR_PROCESS_UNSUPPORTED


# ---------------------------------------------------------------------------
# A process the agent did not start
# ---------------------------------------------------------------------------


def test_a_running_process_it_has_no_handle_for_is_alive(runner):
    processes, _mounter = runner

    # A restarted agent did not start these and has no handle for them, but
    # they are still serving their mounts. Reporting them dead would take a
    # working folder away from a sandbox because the agent was replaced.
    assert processes.alive(os.getpid()) is True


def test_a_pid_that_is_gone_is_not_alive(runner):
    processes, _mounter = runner

    assert processes.alive(2**22) is False
    assert processes.alive(0) is False


def test_stopping_an_adopted_process_still_unmounts_it(runner, monkeypatch):
    processes, mounter = runner
    mounter.mounts.add("/gw/pods/x/local")
    import signal as signal_module

    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "clouder.csi.bridge_processes.os.kill",
        lambda pid, sig: signalled.append((pid, sig)),
    )

    processes.stop(4242, "/gw/pods/x/local")

    # Probed with signal 0, then asked to stop. A bridge left mounted is a
    # sandbox reading a folder nobody granted any more, so the mount point is
    # cleared whether the signal landed or not.
    assert (4242, signal_module.SIGTERM) in signalled
    assert "/gw/pods/x/local" not in mounter.mounts


def test_stopping_one_it_started_goes_through_the_mounter(runner):
    processes, mounter = runner
    pid = start(processes)

    processes.stop(pid, "/gw/pods/x/local")

    assert ("stop", BRIDGE) in mounter.calls
