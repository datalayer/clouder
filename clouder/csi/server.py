"""gRPC Identity and Node services of ``local.csi.datalayer.io``.

Thin: every call is translated to :class:`clouder.csi.driver.LocalCsiDriver`
and every :class:`clouder.csi.driver.CsiError` to a gRPC status. Secrets are
never logged.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from concurrent import futures
from urllib.parse import urlsplit

import grpc
from google.protobuf import wrappers_pb2

from .driver import DRIVER_NAME, Code, CsiError, LocalCsiDriver, PublishRequest
from .health import HealthServer
from .proto import csi_pb2, csi_pb2_grpc

log = logging.getLogger("clouder.csi.server")

_STATUS = {
    Code.INVALID_ARGUMENT: grpc.StatusCode.INVALID_ARGUMENT,
    Code.NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    Code.ALREADY_EXISTS: grpc.StatusCode.ALREADY_EXISTS,
    Code.FAILED_PRECONDITION: grpc.StatusCode.FAILED_PRECONDITION,
    Code.UNAVAILABLE: grpc.StatusCode.UNAVAILABLE,
    Code.INTERNAL: grpc.StatusCode.INTERNAL,
    Code.UNIMPLEMENTED: grpc.StatusCode.UNIMPLEMENTED,
}


def _abort(context, error: CsiError):
    log.warning("%s: %s", error.code.value, error.message)
    context.abort(_STATUS[error.code], error.message)


class IdentityServicer(csi_pb2_grpc.IdentityServicer):
    def __init__(self, driver: LocalCsiDriver, version: str):
        self.driver = driver
        self.version = version

    def GetPluginInfo(self, request, context):  # noqa: N802
        return csi_pb2.GetPluginInfoResponse(name=DRIVER_NAME, vendor_version=self.version)

    def GetPluginCapabilities(self, request, context):  # noqa: N802
        # Node-only plugin: no controller service, no topology, no expansion.
        return csi_pb2.GetPluginCapabilitiesResponse(capabilities=[])

    def Probe(self, request, context):  # noqa: N802
        return csi_pb2.ProbeResponse(ready=wrappers_pb2.BoolValue(value=True))


class NodeServicer(csi_pb2_grpc.NodeServicer):
    def __init__(self, driver: LocalCsiDriver):
        self.driver = driver

    def NodeGetInfo(self, request, context):  # noqa: N802
        return csi_pb2.NodeGetInfoResponse(node_id=self.driver.node_id, max_volumes_per_node=0)

    def NodeGetCapabilities(self, request, context):  # noqa: N802
        rpc = csi_pb2.NodeServiceCapability.RPC
        return csi_pb2.NodeGetCapabilitiesResponse(
            capabilities=[
                csi_pb2.NodeServiceCapability(rpc=rpc(type=rpc.GET_VOLUME_STATS)),
                csi_pb2.NodeServiceCapability(rpc=rpc(type=rpc.VOLUME_CONDITION)),
            ]
        )

    def NodePublishVolume(self, request, context):  # noqa: N802
        log.info("NodePublishVolume volume_id=%s target=%s", request.volume_id, request.target_path)
        capability = request.volume_capability
        publish = PublishRequest(
            volume_id=request.volume_id,
            target_path=request.target_path,
            volume_context=dict(request.volume_context),
            secrets=dict(request.secrets),
            readonly=bool(request.readonly),
            block=capability.HasField("block") if capability is not None else False,
        )
        try:
            self.driver.publish(publish)
        except CsiError as error:
            _abort(context, error)
        return csi_pb2.NodePublishVolumeResponse()

    def NodeUnpublishVolume(self, request, context):  # noqa: N802
        log.info("NodeUnpublishVolume volume_id=%s target=%s", request.volume_id, request.target_path)
        try:
            self.driver.unpublish(request.volume_id, request.target_path)
        except CsiError as error:
            _abort(context, error)
        return csi_pb2.NodeUnpublishVolumeResponse()

    def NodeGetVolumeStats(self, request, context):  # noqa: N802
        try:
            stats = self.driver.volume_stats(request.volume_id, request.volume_path)
        except CsiError as error:
            _abort(context, error)
            return csi_pb2.NodeGetVolumeStatsResponse()
        response = csi_pb2.NodeGetVolumeStatsResponse(
            volume_condition=csi_pb2.VolumeCondition(abnormal=stats.abnormal, message=stats.message),
        )
        if stats.total is not None:
            response.usage.append(
                csi_pb2.VolumeUsage(
                    available=stats.available or 0,
                    total=stats.total or 0,
                    used=stats.used or 0,
                    unit=csi_pb2.VolumeUsage.BYTES,
                )
            )
        return response

    def NodeStageVolume(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "staging is not supported")

    def NodeUnstageVolume(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "staging is not supported")

    def NodeExpandVolume(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "expansion is not supported")


def _prepare_endpoint(endpoint: str) -> str:
    """Return the gRPC address for ``endpoint``; clear a stale unix socket."""
    parts = urlsplit(endpoint)
    if parts.scheme == "unix":
        path = parts.path or parts.netloc
        if not path:
            raise ValueError(f"invalid unix endpoint: {endpoint}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        return f"unix:{path}"
    if parts.scheme == "tcp":
        return parts.netloc
    return endpoint


def build_server(driver: LocalCsiDriver, version: str, max_workers: int = 8) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    csi_pb2_grpc.add_IdentityServicer_to_server(IdentityServicer(driver, version), server)
    csi_pb2_grpc.add_NodeServicer_to_server(NodeServicer(driver), server)
    return server


def serve(
    *,
    driver: LocalCsiDriver,
    endpoint: str,
    version: str,
    health_port: int | None = 9808,
) -> None:
    """Run the plugin until SIGTERM/SIGINT."""
    address = _prepare_endpoint(endpoint)
    server = build_server(driver, version)
    server.add_insecure_port(address)

    health = HealthServer(driver, port=health_port) if health_port else None
    if health is not None:
        health.start()

    driver.start()
    server.start()
    if health is not None:
        health.serving = True
    log.info("%s %s serving on %s (node %s)", DRIVER_NAME, version, endpoint, driver.node_id)

    stop = threading.Event()

    def _on_signal(signum, frame):  # noqa: ARG001
        log.info("signal %s: stopping", signum)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except ValueError:
            # Not the main thread: the caller handles shutdown.
            pass

    try:
        while not stop.wait(1.0):
            pass
    finally:
        server.stop(grace=5).wait()
        driver.close()
        if health is not None:
            health.stop()
