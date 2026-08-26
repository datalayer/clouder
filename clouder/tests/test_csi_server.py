"""The gRPC servicers translate requests and errors faithfully."""

from __future__ import annotations

import pytest

grpc = pytest.importorskip("grpc")

from ..csi.driver import (  # noqa: E402
    ATTR_BRIDGE_UID,
    ATTR_MOUNT_MODE,
    ATTR_RELAY_URL,
    ATTR_SANDBOX_UID,
    DRIVER_NAME,
    EPHEMERAL_CONTEXT_KEY,
    MSG_DISCONNECTED,
    MSG_MISSING_SECRET,
    SECRET_MOUNT_TOKEN,
    LocalCsiDriver,
)
from ..csi.mounter import FakeMounter  # noqa: E402
from ..csi.proto import csi_pb2  # noqa: E402
from ..csi.server import IdentityServicer, NodeServicer, build_server  # noqa: E402

BRIDGE = "brd-1"


class Aborted(Exception):
    def __init__(self, code, details):
        super().__init__(details)
        self.code = code
        self.details = details


class FakeContext:
    def abort(self, code, details):
        raise Aborted(code, details)


@pytest.fixture
def mounter():
    return FakeMounter()


@pytest.fixture
def driver(tmp_path, mounter):
    return LocalCsiDriver(mounter=mounter, node_id="node-9", state_dir=str(tmp_path / "state"))


def publish_request(tmp_path, *, secrets=None, readonly=True, mode="ro"):
    return csi_pb2.NodePublishVolumeRequest(
        volume_id="csi-1",
        target_path=str(tmp_path / "pods" / "p1" / "mount"),
        volume_capability=csi_pb2.VolumeCapability(
            mount=csi_pb2.VolumeCapability.MountVolume(),
            access_mode=csi_pb2.VolumeCapability.AccessMode(
                mode=csi_pb2.VolumeCapability.AccessMode.SINGLE_NODE_WRITER
            ),
        ),
        readonly=readonly,
        volume_context={
            EPHEMERAL_CONTEXT_KEY: "true",
            ATTR_BRIDGE_UID: BRIDGE,
            ATTR_SANDBOX_UID: "sbx-1",
            ATTR_MOUNT_MODE: mode,
            ATTR_RELAY_URL: f"wss://relay.example.com/bridges/{BRIDGE}",
        },
        secrets={SECRET_MOUNT_TOKEN: "tok"} if secrets is None else secrets,
    )


def test_identity(driver):
    identity = IdentityServicer(driver, "0.0.6")
    info = identity.GetPluginInfo(csi_pb2.GetPluginInfoRequest(), FakeContext())
    assert info.name == DRIVER_NAME
    assert info.vendor_version == "0.0.6"
    assert list(identity.GetPluginCapabilities(csi_pb2.GetPluginCapabilitiesRequest(), FakeContext()).capabilities) == []
    assert identity.Probe(csi_pb2.ProbeRequest(), FakeContext()).ready.value is True


def test_node_info_and_capabilities(driver):
    node = NodeServicer(driver)
    assert node.NodeGetInfo(csi_pb2.NodeGetInfoRequest(), FakeContext()).node_id == "node-9"
    types = {c.rpc.type for c in node.NodeGetCapabilities(csi_pb2.NodeGetCapabilitiesRequest(), FakeContext()).capabilities}
    rpc = csi_pb2.NodeServiceCapability.RPC
    assert types == {rpc.GET_VOLUME_STATS, rpc.VOLUME_CONDITION}


def test_publish_stats_unpublish_round_trip(tmp_path, driver, mounter):
    node = NodeServicer(driver)
    request = publish_request(tmp_path)
    node.NodePublishVolume(request, FakeContext())
    assert mounter.binds[request.target_path][1] is True

    stats = node.NodeGetVolumeStats(
        csi_pb2.NodeGetVolumeStatsRequest(volume_id="csi-1", volume_path=request.target_path), FakeContext()
    )
    assert stats.volume_condition.abnormal is False
    assert stats.usage[0].unit == csi_pb2.VolumeUsage.BYTES

    mounter.kill(BRIDGE, reason="relay refused the mount token")
    driver.reap()
    stats = node.NodeGetVolumeStats(
        csi_pb2.NodeGetVolumeStatsRequest(volume_id="csi-1", volume_path=request.target_path), FakeContext()
    )
    assert stats.volume_condition.abnormal is True
    assert stats.volume_condition.message.startswith(MSG_DISCONNECTED)
    assert len(stats.usage) == 0

    node.NodeUnpublishVolume(
        csi_pb2.NodeUnpublishVolumeRequest(volume_id="csi-1", target_path=request.target_path), FakeContext()
    )
    node.NodeUnpublishVolume(
        csi_pb2.NodeUnpublishVolumeRequest(volume_id="csi-1", target_path=request.target_path), FakeContext()
    )
    with pytest.raises(Aborted) as info:
        node.NodeGetVolumeStats(
            csi_pb2.NodeGetVolumeStatsRequest(volume_id="csi-1", volume_path=request.target_path), FakeContext()
        )
    assert info.value.code == grpc.StatusCode.NOT_FOUND


def test_errors_become_grpc_status(tmp_path, driver):
    node = NodeServicer(driver)
    with pytest.raises(Aborted) as info:
        node.NodePublishVolume(publish_request(tmp_path, secrets={}), FakeContext())
    assert info.value.code == grpc.StatusCode.INVALID_ARGUMENT
    assert info.value.details == MSG_MISSING_SECRET

    block = publish_request(tmp_path)
    block.volume_capability.CopyFrom(
        csi_pb2.VolumeCapability(
            block=csi_pb2.VolumeCapability.BlockVolume(),
            access_mode=csi_pb2.VolumeCapability.AccessMode(
                mode=csi_pb2.VolumeCapability.AccessMode.SINGLE_NODE_WRITER
            ),
        )
    )
    with pytest.raises(Aborted) as info:
        node.NodePublishVolume(block, FakeContext())
    assert info.value.code == grpc.StatusCode.INVALID_ARGUMENT

    with pytest.raises(Aborted) as info:
        node.NodeStageVolume(csi_pb2.NodeStageVolumeRequest(), FakeContext())
    assert info.value.code == grpc.StatusCode.UNIMPLEMENTED


def test_server_serves_identity_over_a_unix_socket(tmp_path, driver):
    from ..csi.proto import csi_pb2_grpc

    socket_path = tmp_path / "csi.sock"
    server = build_server(driver, "0.0.6")
    server.add_insecure_port(f"unix:{socket_path}")
    server.start()
    try:
        with grpc.insecure_channel(f"unix:{socket_path}") as channel:
            stub = csi_pb2_grpc.IdentityStub(channel)
            info = stub.GetPluginInfo(csi_pb2.GetPluginInfoRequest(), timeout=5)
            assert info.name == DRIVER_NAME
            node = csi_pb2_grpc.NodeStub(channel)
            assert node.NodeGetInfo(csi_pb2.NodeGetInfoRequest(), timeout=5).node_id == "node-9"
    finally:
        server.stop(grace=None)


def test_health_endpoint(driver):
    import json
    from urllib.request import urlopen

    from ..csi.health import HealthServer

    health = HealthServer(driver, port=0, host="127.0.0.1")
    health.start()
    try:
        port = health.bound_port
        with pytest.raises(Exception):
            urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)  # 503 while not serving
        health.serving = True
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
            assert json.loads(response.read())["status"] == "ok"
        with urlopen(f"http://127.0.0.1:{port}/mounts", timeout=5) as response:
            payload = json.loads(response.read())
        assert payload["driver"] == DRIVER_NAME
        assert payload["bridges"] == {}
    finally:
        health.stop()
