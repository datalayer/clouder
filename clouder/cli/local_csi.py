"""Clouder CLI - Local CSI driver (local.csi.datalayer.io) checks."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich import print
from rich.panel import Panel
from rich.table import Table

from ._completions import deployment_name_completion, ssh_key_name_completion
from .criu import _default_admin_user, _infer_cluster_name
from .kubeadm._helpers import (
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    resolve_kubeadm_cloud_context,
)
from .kubeadm.local_csi import DRIVER_NAME, NAMESPACE, RELEASE_NAME
from ..util.utils import SSH_FOLDER


local_csi_app = typer.Typer(no_args_is_help=True)


@local_csi_app.callback()
def local_csi_callback():
    """Local CSI driver (local.csi.datalayer.io): the node plugin serving Local Mounts."""


def _collect_status(master_ip: str, user: str, key_path: str, namespace: str, release: str, health_port: int) -> dict:
    """Ask the master about the CSIDriver, the DaemonSet and each node plugin's mounts."""
    status: dict = {"csidriver": False, "daemonset": None, "pods": []}

    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl get csidriver {DRIVER_NAME} -o jsonpath='{{.metadata.name}}' 2>/dev/null || true",
        check=False,
    )
    status["csidriver"] = result.stdout.strip() == DRIVER_NAME

    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl -n {namespace} get daemonset {release} -o json 2>/dev/null || true",
        check=False,
    )
    if result.stdout.strip():
        try:
            ds_status = json.loads(result.stdout).get("status", {})
            status["daemonset"] = {
                "desired": ds_status.get("desiredNumberScheduled", 0),
                "ready": ds_status.get("numberReady", 0),
            }
        except json.JSONDecodeError:
            status["daemonset"] = None

    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl -n {namespace} get pods -l app={release} -o json 2>/dev/null || true",
        check=False,
    )
    pods = []
    if result.stdout.strip():
        try:
            pods = json.loads(result.stdout).get("items", [])
        except json.JSONDecodeError:
            pods = []

    for pod in pods:
        name = pod["metadata"]["name"]
        node = pod.get("spec", {}).get("nodeName", "-")
        phase = pod.get("status", {}).get("phase", "-")
        ready = all(
            c.get("ready") for c in pod.get("status", {}).get("containerStatuses", [])
        ) and bool(pod.get("status", {}).get("containerStatuses"))
        entry = {"pod": name, "node": node, "phase": phase, "ready": ready, "mounts": None, "error": ""}
        if ready:
            result = _ssh_cmd(
                master_ip,
                user,
                key_path,
                f"kubectl -n {namespace} exec {name} -c driver -- wget -qO- http://127.0.0.1:{health_port}/mounts 2>/dev/null || true",
                check=False,
            )
            try:
                entry["mounts"] = json.loads(result.stdout) if result.stdout.strip() else None
            except json.JSONDecodeError:
                entry["error"] = "unreadable /mounts"
        status["pods"].append(entry)
    return status


@local_csi_app.command("status")
def local_csi_status(
    cluster: Optional[str] = typer.Option(
        None,
        "--cluster",
        help="Kubeadm cluster name.",
        autocompletion=deployment_name_completion,
    ),
    user: Optional[str] = typer.Option(None, "--admin-user", "-u", help="SSH username on nodes."),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-i",
        help="SSH key name (from ~/.ssh/).",
        autocompletion=ssh_key_name_completion,
    ),
    cloud: Optional[str] = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws)."),
    namespace: str = typer.Option(NAMESPACE, "--namespace", "-n", help="Namespace of the DaemonSet."),
    release: str = typer.Option(RELEASE_NAME, "--release", help="Helm release name."),
    health_port: int = typer.Option(9808, "--health-port", help="Driver health port (driver.healthPort)."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw status as JSON."),
):
    """Show the Local CSI driver: CSIDriver, DaemonSet, and each node's bridge mounts."""
    cluster_name = _infer_cluster_name(cluster)
    resolved_cloud, resolved_context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=cluster_name)
    resolved_user = _default_admin_user(user, cloud=resolved_cloud)
    cluster_data = _resolve_cluster_vms(cluster_name, cloud=resolved_cloud, context_id=resolved_context_id)
    key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(cluster_name)
    master = cluster_data["master"]

    status = _collect_status(master["ip"], resolved_user, key_path, namespace, release, health_port)

    if as_json:
        typer.echo(json.dumps(status, indent=2, sort_keys=True))
        return

    daemonset = status["daemonset"]
    print(
        Panel(
            f"CSIDriver {DRIVER_NAME}: [bold]{'present' if status['csidriver'] else 'missing'}[/bold]\n"
            + (
                f"DaemonSet {namespace}/{release}: [bold]{daemonset['ready']}/{daemonset['desired']}[/bold] ready"
                if daemonset
                else f"DaemonSet {namespace}/{release}: [bold]missing[/bold]"
            ),
            title=f"Local CSI - {cluster_name}",
        )
    )

    table = Table(title="Node plugins")
    table.add_column("Node", style="cyan")
    table.add_column("Pod", style="dim")
    table.add_column("Ready", style="green")
    table.add_column("Bridges", style="magenta")
    table.add_column("Volumes", style="magenta")
    table.add_column("Disconnected", style="yellow")

    for entry in status["pods"]:
        mounts = entry["mounts"] or {}
        bridges = mounts.get("bridges", {}) or {}
        connected = sum(1 for b in bridges.values() if b.get("connected"))
        disconnected = [
            f"{uid}: {b.get('reason') or 'disconnected'}" for uid, b in bridges.items() if not b.get("connected")
        ]
        table.add_row(
            entry["node"],
            entry["pod"],
            "yes" if entry["ready"] else entry["phase"],
            f"{connected}/{len(bridges)}" if entry["mounts"] is not None else (entry["error"] or "-"),
            str(len(mounts.get("volumes", {}) or {})) if entry["mounts"] is not None else "-",
            "\n".join(disconnected) or "-",
        )
    print(table)
