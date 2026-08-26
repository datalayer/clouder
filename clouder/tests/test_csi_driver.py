"""Unit tests of the Local CSI driver semantics, with the FakeMounter."""

from __future__ import annotations

import json

import pytest

from ..csi.driver import (
    ATTR_BRIDGE_UID,
    ATTR_MOUNT_MODE,
    ATTR_RELAY_URL,
    ATTR_SANDBOX_UID,
    EPHEMERAL_CONTEXT_KEY,
    MSG_BLOCK_UNSUPPORTED,
    MSG_DISCONNECTED,
    MSG_HOST_PATH_ATTRIBUTE,
    MSG_MISSING_SECRET,
    MSG_MODE_MISMATCH,
    MSG_NOT_EPHEMERAL,
    POD_NAME_KEY,
    POD_NAMESPACE_KEY,
    POD_UID_KEY,
    SECRET_MOUNT_TOKEN,
    Code,
    CsiError,
    LocalCsiDriver,
    PublishRequest,
)
from ..csi.mounter import FakeMounter

BRIDGE = "brd-0123456789abcdef"
SANDBOX = "sbx-fedcba9876543210"
RELAY = f"wss://r1.datalayer.run/api/contents/v1/bridges/{BRIDGE}"
TOKEN = "mount-token-secret-value"


@pytest.fixture
def mounter() -> FakeMounter:
    return FakeMounter()


@pytest.fixture
def driver(tmp_path, mounter) -> LocalCsiDriver:
    return LocalCsiDriver(mounter=mounter, node_id="node-1", state_dir=str(tmp_path / "state"))


def target_for(tmp_path, pod_uid: str = "pod-uid-1", volume: str = "local-mount-1") -> str:
    return str(tmp_path / "pods" / pod_uid / "volumes" / "kubernetes.io~csi" / volume / "mount")


def make_request(
    tmp_path,
    *,
    volume_id: str = "csi-volume-1",
    mode: str = "ro",
    readonly: bool | None = None,
    bridge: str = BRIDGE,
    relay_url: str | None = None,
    secrets: dict | None = None,
    context_overrides: dict | None = None,
    target: str | None = None,
    block: bool = False,
) -> PublishRequest:
    context = {
        EPHEMERAL_CONTEXT_KEY: "true",
        POD_NAME_KEY: "runtime-abc",
        POD_NAMESPACE_KEY: "datalayer-runtimes",
        POD_UID_KEY: "pod-uid-1",
        ATTR_BRIDGE_UID: bridge,
        ATTR_SANDBOX_UID: SANDBOX,
        ATTR_MOUNT_MODE: mode,
        ATTR_RELAY_URL: relay_url or RELAY.replace(BRIDGE, bridge),
    }
    if context_overrides:
        context.update(context_overrides)
    return PublishRequest(
        volume_id=volume_id,
        target_path=target or target_for(tmp_path),
        volume_context=context,
        secrets={SECRET_MOUNT_TOKEN: TOKEN} if secrets is None else secrets,
        readonly=(mode == "ro") if readonly is None else readonly,
        block=block,
    )


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def test_publish_ro_starts_bridge_and_binds_read_only(tmp_path, driver, mounter):
    request = make_request(tmp_path, mode="ro")
    driver.publish(request)

    target = request.target_path
    assert (tmp_path / "pods").exists() and __import__("os").path.isdir(target)
    (_, bridge_uid, relay_url, token, mount_path, mode), = mounter.started()
    assert bridge_uid == BRIDGE
    assert relay_url == RELAY
    assert token == TOKEN
    assert mode == "ro"
    assert mount_path == driver.bridge_mount_path(BRIDGE)
    assert mount_path.startswith(str(tmp_path / "state"))
    assert mounter.binds[target] == (mount_path, True)
    assert mounter.is_mount_point(target)


def test_publish_rw_binds_read_write(tmp_path, driver, mounter):
    request = make_request(tmp_path, mode="rw")
    driver.publish(request)
    assert mounter.binds[request.target_path] == (driver.bridge_mount_path(BRIDGE), False)
    assert mounter.processes[BRIDGE].mode == "rw"


def test_second_publish_of_same_volume_is_a_noop(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    driver.publish(request)
    driver.publish(request)
    assert len(mounter.started()) == 1
    assert len([call for call in mounter.calls if call[0] == "bind"]) == 1
    assert driver.snapshot()["volumes"][request.volume_id]["targets"] == [request.target_path]


def test_publish_reuses_the_bridge_across_volumes(tmp_path, driver, mounter):
    first = make_request(tmp_path, volume_id="csi-a", target=target_for(tmp_path, "pod-a"))
    second = make_request(tmp_path, volume_id="csi-b", target=target_for(tmp_path, "pod-b"))
    driver.publish(first)
    driver.publish(second)
    assert len(mounter.started()) == 1
    assert set(mounter.binds) == {first.target_path, second.target_path}

    driver.unpublish("csi-a", first.target_path)
    assert BRIDGE in mounter.processes, "bridge stays up while a volume still uses it"
    driver.unpublish("csi-b", second.target_path)
    assert BRIDGE not in mounter.processes
    assert mounter.mounts == set()


def test_publish_clears_a_stale_mount_at_the_target(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    mounter.mounts.add(request.target_path)  # left by a previous driver incarnation
    driver.publish(request)
    unmounts = [call for call in mounter.calls if call[0] == "unmount"]
    assert unmounts == [("unmount", request.target_path)]
    assert mounter.binds[request.target_path][1] is True


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_mode_and_readonly_flag_must_agree(tmp_path, driver, mounter):
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, mode="ro", readonly=False))
    assert info.value.code is Code.INVALID_ARGUMENT
    assert info.value.message == MSG_MODE_MISMATCH

    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, mode="rw", readonly=True))
    assert info.value.message == MSG_MODE_MISMATCH
    assert mounter.started() == [], "nothing is started for a refused request"
    assert mounter.binds == {}


def test_missing_secret_is_refused_with_a_stable_error(tmp_path, driver, mounter):
    for secrets in ({}, {"token": TOKEN}, {SECRET_MOUNT_TOKEN: "   "}):
        with pytest.raises(CsiError) as info:
            driver.publish(make_request(tmp_path, secrets=secrets))
        assert info.value.code is Code.INVALID_ARGUMENT
        assert info.value.message == MSG_MISSING_SECRET
    assert mounter.started() == []


def test_non_ephemeral_volume_is_refused(tmp_path, driver, mounter):
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, context_overrides={EPHEMERAL_CONTEXT_KEY: "false"}))
    assert info.value.code is Code.FAILED_PRECONDITION
    assert info.value.message == MSG_NOT_EPHEMERAL
    assert mounter.started() == []


def test_block_volume_is_refused(tmp_path, driver):
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, block=True))
    assert info.value.message == MSG_BLOCK_UNSUPPORTED


@pytest.mark.parametrize("attribute", ["host-path", "hostPath", "local-root", "localRoot", "local-path", "source_path"])
def test_host_path_attributes_are_refused(tmp_path, driver, mounter, attribute):
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, context_overrides={attribute: "/Users/me/project"}))
    assert info.value.code is Code.INVALID_ARGUMENT
    assert info.value.message == MSG_HOST_PATH_ATTRIBUTE
    assert mounter.started() == []


def test_request_handling_never_sees_a_host_path(tmp_path, driver, mounter):
    """Everything the mounter receives is the relay, the token, the mode and driver-owned paths."""
    request = make_request(tmp_path)
    driver.publish(request)
    payload = json.dumps(mounter.calls)
    assert "/Users" not in payload and "/home/" not in payload.replace(str(tmp_path), "")
    (_, _, relay_url, token, mount_path, mode), = mounter.started()
    assert {relay_url, token, mode} == {RELAY, TOKEN, "ro"}
    assert mount_path.startswith(driver.state_dir)
    for call in mounter.calls:
        if call[0] == "bind":
            assert call[1].startswith(driver.state_dir)
            assert call[2] == request.target_path


@pytest.mark.parametrize(
    "attribute, value, fragment",
    [
        (ATTR_BRIDGE_UID, "", "is required"),
        (ATTR_BRIDGE_UID, "../etc", "not a valid identifier"),
        (ATTR_SANDBOX_UID, "", "is required"),
        (ATTR_MOUNT_MODE, "readonly", "must be one of"),
        (ATTR_RELAY_URL, "", "is required"),
        (ATTR_RELAY_URL, f"https://r1.datalayer.run/bridges/{BRIDGE}", "wss://"),
        (ATTR_RELAY_URL, f"ws://r1.datalayer.run/bridges/{BRIDGE}", "wss://"),
        (ATTR_RELAY_URL, f"wss://user:pw@r1.datalayer.run/bridges/{BRIDGE}", "credentials"),
        (ATTR_RELAY_URL, "wss://r1.datalayer.run/bridges/another", "does not name"),
    ],
)
def test_malformed_attributes_are_refused(tmp_path, driver, mounter, attribute, value, fragment):
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, context_overrides={attribute: value}))
    assert info.value.code is Code.INVALID_ARGUMENT
    assert fragment in info.value.message
    assert mounter.started() == []


def test_relay_host_allowlist(tmp_path, mounter):
    driver = LocalCsiDriver(
        mounter=mounter, node_id="node-1", state_dir=str(tmp_path / "state"), relay_host="r1.datalayer.run"
    )
    driver.publish(make_request(tmp_path))
    with pytest.raises(CsiError) as info:
        driver.publish(
            make_request(
                tmp_path,
                volume_id="csi-other",
                bridge="brd-other",
                target=target_for(tmp_path, "pod-2"),
                relay_url="wss://evil.example.com/bridges/brd-other",
            )
        )
    assert "not the configured relay host" in info.value.message
    assert len(mounter.started()) == 1


def test_insecure_relay_only_when_allowed(tmp_path, mounter):
    driver = LocalCsiDriver(
        mounter=mounter, node_id="node-1", state_dir=str(tmp_path / "state"), allow_insecure_relay=True
    )
    driver.publish(make_request(tmp_path, relay_url=f"ws://localhost:9402/bridges/{BRIDGE}"))
    assert len(mounter.started()) == 1


def test_bridge_with_different_attributes_is_refused(tmp_path, driver):
    driver.publish(make_request(tmp_path, mode="ro"))
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path, volume_id="csi-2", mode="rw", target=target_for(tmp_path, "pod-2")))
    assert info.value.code is Code.INVALID_ARGUMENT


def test_bridge_start_failure_is_unavailable(tmp_path, driver, mounter):
    mounter.fail_start = "mounter exited before mounting: relay refused the mount token"
    with pytest.raises(CsiError) as info:
        driver.publish(make_request(tmp_path))
    assert info.value.code is Code.UNAVAILABLE
    assert "relay refused the mount token" in info.value.message
    assert mounter.binds == {}
    assert driver.snapshot()["volumes"] == {}


# ---------------------------------------------------------------------------
# Unpublish
# ---------------------------------------------------------------------------


def test_unpublish_is_idempotent(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    driver.publish(request)

    driver.unpublish(request.volume_id, request.target_path)
    assert request.target_path not in mounter.mounts
    assert ("stop", BRIDGE) in mounter.calls
    assert mounter.mounts == set()
    assert driver.snapshot() == {"driver": "local.csi.datalayer.io", "node_id": "node-1", "bridges": {}, "volumes": {}}

    driver.unpublish(request.volume_id, request.target_path)  # already gone
    driver.unpublish("never-published", str(tmp_path / "nowhere"))  # never known
    assert len([call for call in mounter.calls if call[0] == "stop"]) == 1


def test_unpublish_of_unknown_volume_still_unmounts_a_stale_target(tmp_path, driver, mounter):
    target = target_for(tmp_path)
    mounter.mounts.add(target)
    driver.unpublish("unknown-volume", target)
    assert target not in mounter.mounts


# ---------------------------------------------------------------------------
# Stats, disconnection, revocation
# ---------------------------------------------------------------------------


def test_stats_are_normal_while_connected(tmp_path, driver):
    request = make_request(tmp_path)
    driver.publish(request)
    stats = driver.volume_stats(request.volume_id, request.target_path)
    assert stats.abnormal is False
    assert stats.message == ""
    assert stats.total is not None and stats.total >= 0


def test_stats_abnormal_after_the_mounter_disconnects(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    driver.publish(request)

    mounter.kill(BRIDGE, reason="mounter exited with status 4: relay refused the mount token")
    # Before the watcher runs, stats already say abnormal: no stale data.
    stats = driver.volume_stats(request.volume_id, request.target_path)
    assert stats.abnormal is True
    assert stats.message.startswith(MSG_DISCONNECTED)

    assert driver.reap() == [BRIDGE]
    assert request.target_path not in mounter.mounts, "the target is unmounted, never served stale"
    assert mounter.mounts == set()

    stats = driver.volume_stats(request.volume_id, request.target_path)
    assert stats.abnormal is True
    assert stats.message == f"{MSG_DISCONNECTED}: mounter exited with status 4: relay refused the mount token"
    assert driver.reap() == [], "a disconnected bridge is reaped once"

    snapshot = driver.snapshot()
    assert snapshot["bridges"][BRIDGE]["connected"] is False
    assert request.volume_id in snapshot["volumes"], "the volume stays known until the pod is unpublished"

    driver.unpublish(request.volume_id, request.target_path)
    with pytest.raises(CsiError) as info:
        driver.volume_stats(request.volume_id, request.target_path)
    assert info.value.code is Code.NOT_FOUND


def test_republish_after_disconnect_restarts_the_bridge(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    driver.publish(request)
    mounter.kill(BRIDGE, reason="laptop went away")
    driver.reap()

    driver.publish(request)  # kubelet retry
    assert len(mounter.started()) == 2
    assert mounter.binds[request.target_path] == (driver.bridge_mount_path(BRIDGE), True)
    assert driver.volume_stats(request.volume_id, request.target_path).abnormal is False


def test_republish_after_revocation_fails_and_stays_abnormal(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    driver.publish(request)
    mounter.kill(BRIDGE, reason="relay refused the mount token")
    driver.reap()

    mounter.fail_start = "mounter exited before mounting: relay refused the mount token"
    with pytest.raises(CsiError) as info:
        driver.publish(request)
    assert info.value.code is Code.UNAVAILABLE
    assert request.target_path not in mounter.mounts
    stats = driver.volume_stats(request.volume_id, request.target_path)
    assert stats.abnormal is True and stats.message.startswith(MSG_DISCONNECTED)


def test_stats_report_an_unmounted_target(tmp_path, driver, mounter):
    request = make_request(tmp_path)
    driver.publish(request)
    mounter.mounts.discard(request.target_path)  # someone unmounted it behind our back
    stats = driver.volume_stats(request.volume_id, request.target_path)
    assert stats.abnormal is True
    assert stats.message == "target path is not mounted"


def test_watcher_thread_reaps(tmp_path, mounter):
    driver = LocalCsiDriver(mounter=mounter, node_id="node-1", state_dir=str(tmp_path / "state"), watch_interval=0.05)
    request = make_request(tmp_path)
    driver.publish(request)
    driver.start()
    try:
        mounter.kill(BRIDGE, reason="heartbeat expired")
        import time

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and request.target_path in mounter.mounts:
            time.sleep(0.02)
        assert request.target_path not in mounter.mounts
    finally:
        driver.close()
