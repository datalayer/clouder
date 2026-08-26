"""Datalayer Local CSI driver.

The ``local.csi.datalayer.io`` node plugin presents a folder of a user's own
computer inside a Kubernetes Runtime through the Contents local bridge. The
pod carries an ephemeral inline CSI volume naming a bridge; the driver runs
one bridge filesystem process per bridge on the node and bind-mounts it at the
pod's target path, read-only when the bridge is.

Layout:

- :mod:`clouder.csi.driver`  — the CSI Node semantics, independent of gRPC;
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
from .mounter import FakeMounter, MountError, Mounter, MountHandle, ProcessMounter  # noqa: F401

__all__ = [
    "DRIVER_NAME",
    "Code",
    "CsiError",
    "FakeMounter",
    "LocalCsiDriver",
    "MountError",
    "MountHandle",
    "Mounter",
    "ProcessMounter",
    "PublishRequest",
    "VolumeStats",
]
