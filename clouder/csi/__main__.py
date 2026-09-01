"""Run the Local CSI node plugin.

    python -m clouder.csi --endpoint unix:///csi/csi.sock --node-id $NODE_ID

The chart runs it beside the upstream ``csi-node-driver-registrar`` sidecar,
which registers the socket with kubelet.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .._version import __version__
from .driver import DRIVER_NAME, LocalCsiDriver
from .git_materializer import DEFAULT_CLONE_TIMEOUT_SECONDS
from .node_mount_gateway import (
    DEFAULT_NODE_MOUNT_GATEWAY_ROOT,
    DEFAULT_KUBELET_DIR,
    DEFAULT_MAX_MOUNTS_PER_NODE,
    DEFAULT_MAX_MOUNTS_PER_POD,
    DEFAULT_SHARED_ROOT,
)
from .mounter import ProcessMounter

DEFAULT_ENDPOINT = "unix:///csi/csi.sock"
DEFAULT_STATE_DIR = "/csi/mounts"
DEFAULT_HEALTH_PORT = 9808


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clouder.csi", description=f"{DRIVER_NAME} node plugin")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("CSI_ENDPOINT", DEFAULT_ENDPOINT),
        help="CSI endpoint (unix:///path or tcp://host:port). Env: CSI_ENDPOINT.",
    )
    parser.add_argument(
        "--node-id",
        default=os.environ.get("NODE_ID") or os.environ.get("KUBE_NODE_NAME") or "",
        help="Kubernetes node name. Env: NODE_ID, KUBE_NODE_NAME.",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("DATALAYER_LOCAL_CSI_STATE_DIR", DEFAULT_STATE_DIR),
        help="Where bridge filesystems are mounted before being bound into pods. Must propagate to the host.",
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=int(os.environ.get("DATALAYER_LOCAL_CSI_HEALTH_PORT", DEFAULT_HEALTH_PORT)),
        help="HTTP port of /healthz and /mounts; 0 disables it.",
    )
    parser.add_argument(
        "--relay-host",
        default=os.environ.get("DATALAYER_LOCAL_CSI_RELAY_HOST", ""),
        help="If set, refuse a relay-url whose host is not this one.",
    )
    parser.add_argument(
        "--allow-insecure-relay",
        action="store_true",
        default=os.environ.get("DATALAYER_LOCAL_CSI_ALLOW_INSECURE_RELAY", "").lower() in ("1", "true", "yes"),
        help="Accept ws:// relay URLs (development only).",
    )
    parser.add_argument(
        "--node-mount-gateway",
        action="store_true",
        default=os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_ENABLED", "").lower() in ("1", "true", "yes"),
        help=(
            "Run the Node Mount Gateway beside the CSI driver: bind folders of the shared "
            "filesystem into running pods from their gateway-mounts annotation. "
            "Env: DATALAYER_NODE_MOUNT_GATEWAY_ENABLED."
        ),
    )
    parser.add_argument(
        "--shared-root",
        default=os.environ.get("DATALAYER_SHARED_FS_MOUNT_PATH", DEFAULT_SHARED_ROOT),
        help="Where this DaemonSet mounts the shared filesystem claim.",
    )
    parser.add_argument(
        "--node-mount-gateway-root",
        default=os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_ROOT", DEFAULT_NODE_MOUNT_GATEWAY_ROOT),
        help="The agent's own per-pod trees. Must propagate to the host.",
    )
    parser.add_argument(
        "--kubelet-dir",
        default=os.environ.get("DATALAYER_KUBELET_DIR", DEFAULT_KUBELET_DIR),
        help="The kubelet directory holding pod volume directories.",
    )
    parser.add_argument(
        "--node-mount-gateway-namespace",
        default=os.environ.get("DATALAYER_RUNTIMES_NAMESPACE", ""),
        help="Limit the pod watch to one namespace; empty watches the node.",
    )
    parser.add_argument(
        "--node-mount-gateway-credentials",
        action="store_true",
        default=os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_CREDENTIALS", "").lower() in ("1", "true", "yes"),
        help=(
            "Let the gateway read a Secret a grant names, to make a mount that needs a "
            "credential. Off by default: reading Secrets is the one thing the agent does "
            "that its RBAC otherwise forbids. Env: DATALAYER_NODE_MOUNT_GATEWAY_CREDENTIALS."
        ),
    )
    parser.add_argument(
        "--node-mount-gateway-local-bridges",
        action="store_true",
        default=os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_LOCAL_BRIDGES", "").lower() in ("1", "true", "yes"),
        help=(
            "Serve `local-bridge` grants through the gateway, mounting a person's own "
            "folder into a sandbox that is already running. Needs --node-mount-gateway-credentials: "
            "the mount token is in the pod's Secret. "
            "Env: DATALAYER_NODE_MOUNT_GATEWAY_LOCAL_BRIDGES."
        ),
    )
    parser.add_argument(
        "--node-mount-gateway-buckets",
        action="store_true",
        default=os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_BUCKETS", "").lower() in ("1", "true", "yes"),
        help=(
            "Serve `cloud-storage` grants through the gateway with Mountpoint for S3. "
            "Needs --node-mount-gateway-credentials: the session is in the pod's Secret, and the "
            "agent serves it to the mount so it can be refreshed without remounting. "
            "Env: DATALAYER_NODE_MOUNT_GATEWAY_BUCKETS."
        ),
    )
    parser.add_argument(
        "--node-mount-gateway-repositories",
        action="store_true",
        default=os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_REPOSITORIES", "").lower() in ("1", "true", "yes"),
        help=(
            "Serve `git` grants through the gateway, checking the pinned revision out on "
            "the node and binding it read-only. The checkout is made once per node and "
            "shared by every sandbox pinned to the same revision. A private repository "
            "needs --node-mount-gateway-credentials: the token is in the pod's Secret. "
            "Env: DATALAYER_NODE_MOUNT_GATEWAY_REPOSITORIES."
        ),
    )
    parser.add_argument(
        "--node-mount-gateway-clone-timeout",
        type=int,
        default=int(os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_CLONE_TIMEOUT") or DEFAULT_CLONE_TIMEOUT_SECONDS),
        help=(
            "How long a checkout may take before the grant fails, in seconds. The sandbox "
            "is already running and waiting on the mount, so a clone that hangs is better "
            "failed with a reason. Env: DATALAYER_NODE_MOUNT_GATEWAY_CLONE_TIMEOUT."
        ),
    )
    parser.add_argument(
        "--max-mounts-per-pod",
        type=int,
        default=int(os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_MAX_MOUNTS_PER_POD", DEFAULT_MAX_MOUNTS_PER_POD)),
    )
    parser.add_argument(
        "--max-mounts-per-node",
        type=int,
        default=int(os.environ.get("DATALAYER_NODE_MOUNT_GATEWAY_MAX_MOUNTS_PER_NODE", DEFAULT_MAX_MOUNTS_PER_NODE)),
    )
    parser.add_argument("--watch-interval", type=float, default=2.0, help="Seconds between bridge process checks.")
    parser.add_argument("--mount-timeout", type=float, default=30.0, help="Seconds to wait for a bridge to mount.")
    parser.add_argument("--log-level", default=os.environ.get("DATALAYER_LOCAL_CSI_LOG_LEVEL", "INFO"))
    parser.add_argument("--version", action="version", version=f"{DRIVER_NAME} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.node_id:
        print("--node-id is required (or NODE_ID / KUBE_NODE_NAME)", file=sys.stderr)
        return 2

    from .server import serve

    mounter = ProcessMounter(mount_timeout=args.mount_timeout)
    driver = LocalCsiDriver(
        mounter=mounter,
        node_id=args.node_id,
        state_dir=args.state_dir,
        relay_host=args.relay_host or None,
        allow_insecure_relay=args.allow_insecure_relay,
        watch_interval=args.watch_interval,
    )
    gateway_agent = None
    if args.node_mount_gateway:
        from .node_mount_gateway_agent import build_agent

        gateway_agent = build_agent(
            mounter=mounter,
            node_name=args.node_id,
            namespace=args.node_mount_gateway_namespace or None,
            shared_root=args.shared_root,
            gateway_root=args.node_mount_gateway_root,
            kubelet_dir=args.kubelet_dir,
            max_mounts_per_pod=args.max_mounts_per_pod,
            max_mounts_per_node=args.max_mounts_per_node,
            credentials=args.node_mount_gateway_credentials,
            local_bridges=args.node_mount_gateway_local_bridges,
            buckets=args.node_mount_gateway_buckets,
            repositories=args.node_mount_gateway_repositories,
            clone_timeout=args.node_mount_gateway_clone_timeout,
            relay_host=args.relay_host,
            allow_insecure_relay=args.allow_insecure_relay,
        )

    serve(
        driver=driver,
        endpoint=args.endpoint,
        version=__version__,
        health_port=args.health_port or None,
        gateway_agent=gateway_agent,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
