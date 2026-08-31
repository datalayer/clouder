"""Datalayer Local CSI driver.

The ``local.csi.datalayer.io`` node plugin presents a folder of a user's own
computer inside a Kubernetes Runtime through the Contents local bridge. The
pod carries an ephemeral inline CSI volume naming a bridge; the driver runs
one bridge filesystem process per bridge on the node and bind-mounts it at the
pod's target path, read-only when the bridge is.

Layout:

- :mod:`clouder.csi.driver`  — the CSI Node semantics, independent of gRPC;
- :mod:`clouder.csi.gateway` — the mount gateway: binding folders of the
  shared filesystem into a pod that is already running, so a launch that
  mounts content can be served from the prewarmed pool;
- :mod:`clouder.csi.gateway_agent` — its pod watch and reconcile loop;
- :mod:`clouder.csi.linux`   — ``mount_setattr`` and ``openat2``, which the
  gateway needs and Python does not expose;
- :mod:`clouder.csi.mounter` — how mounts are made (``ProcessMounter`` on a
  node, ``FakeMounter`` in tests);
- :mod:`clouder.csi.server`  — the gRPC Identity and Node services;
- :mod:`clouder.csi.health`  — the HTTP health endpoint;
- :mod:`clouder.csi.proto`   — the vendored CSI spec and generated stubs.
"""

from .driver import (  # noqa: F401
    DRIVER_NAME,
    Code,
    CsiError,
    LocalCsiDriver,
    PublishRequest,
    VolumeStats,
)
from .gateway import (  # noqa: F401
    GATEWAY_MOUNTS_ANNOTATION,
    GATEWAY_READY_ANNOTATION,
    GATEWAY_VOLUME_NAME,
    Grant,
    GatewayError,
    MountGateway,
    PodRef,
    Report,
)
from .gateway_agent import GatewayAgent, KubernetesPods  # noqa: F401
from .mounter import FakeMounter, MountError, Mounter, MountHandle, ProcessMounter  # noqa: F401

__all__ = [
    "DRIVER_NAME",
    "GATEWAY_MOUNTS_ANNOTATION",
    "GATEWAY_READY_ANNOTATION",
    "GATEWAY_VOLUME_NAME",
    "Code",
    "CsiError",
    "FakeMounter",
    "GatewayAgent",
    "GatewayError",
    "Grant",
    "KubernetesPods",
    "MountGateway",
    "PodRef",
    "Report",
    "LocalCsiDriver",
    "MountError",
    "MountHandle",
    "Mounter",
    "ProcessMounter",
    "PublishRequest",
    "VolumeStats",
]
