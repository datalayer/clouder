"""Clouder CLI - kubeadm list command."""

import json

import typer
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from rich import print
from rich.table import Table

from ...util.utils import CLOUDER_KUBEADM_FOLDER, kubeadm_kubeconfig_path, kubeadm_metadata_path
from ._helpers import get_default_kubeadm_cluster


def _cluster_node_details(cluster_name: str) -> tuple[str, str, str, str]:
    """Return (masters_count, masters_state, workers_count, workers_state)."""
    kubeconfig_path = kubeadm_kubeconfig_path(cluster_name)
    if not kubeconfig_path.exists():
        return ("-", "no-kubeconfig", "-", "no-kubeconfig")

    try:
        k8s_config.load_kube_config(config_file=str(kubeconfig_path))
        core_v1 = k8s_client.CoreV1Api()
        nodes = core_v1.list_node().items
    except Exception:
        return ("-", "unreachable", "-", "unreachable")

    master_total = 0
    master_ready = 0
    worker_total = 0
    worker_ready = 0

    for node in nodes:
        labels = getattr(getattr(node, "metadata", None), "labels", {}) or {}
        is_master = (
            "node-role.kubernetes.io/control-plane" in labels
            or "node-role.kubernetes.io/master" in labels
        )

        ready_status = "Unknown"
        for condition in (getattr(getattr(node, "status", None), "conditions", None) or []):
            if condition.type == "Ready":
                ready_status = str(condition.status or "Unknown")
                break

        if is_master:
            master_total += 1
            if ready_status == "True":
                master_ready += 1
        else:
            worker_total += 1
            if ready_status == "True":
                worker_ready += 1

    master_state = f"{master_ready}/{master_total} Ready" if master_total else "none"
    worker_state = f"{worker_ready}/{worker_total} Ready" if worker_total else "none"
    return (str(master_total), master_state, str(worker_total), worker_state)


def register(kubeadm_app: typer.Typer):
    """Register the list command on the given Typer app."""

    @kubeadm_app.command("ls")
    def kubeadm_list(
        details: bool = typer.Option(
            False,
            "--details",
            help="Include master/worker counts and readiness state (slower).",
        ),
    ):
        """List locally known kubeadm clusters from ~/.clouder/kubeadm/."""
        if not CLOUDER_KUBEADM_FOLDER.exists():
            print("[yellow]No kubeadm clusters found. Folder does not exist: ~/.clouder/kubeadm[/yellow]")
            return

        cluster_dirs = sorted(p for p in CLOUDER_KUBEADM_FOLDER.iterdir() if p.is_dir())
        if not cluster_dirs:
            print("[yellow]No kubeadm clusters found in ~/.clouder/kubeadm.[/yellow]")
            return

        table = Table(title="Kubeadm Clusters")
        table.add_column("Name", style="cyan")
        table.add_column("Default", style="yellow")
        table.add_column("Cloud", style="green")
        table.add_column("Region", style="green")
        table.add_column("Resource Group", style="green")
        table.add_column("Kubeconfig", style="yellow")
        table.add_column("Setup", style="yellow")
        if details:
            table.add_column("Masters", style="cyan")
            table.add_column("Masters State", style="green")
            table.add_column("Workers", style="cyan")
            table.add_column("Workers State", style="green")

        default_cluster = get_default_kubeadm_cluster()

        found = 0
        for cluster_dir in cluster_dirs:
            name = cluster_dir.name
            metadata_path = kubeadm_metadata_path(name)
            if not metadata_path.exists():
                continue

            metadata = {}
            try:
                metadata = json.loads(metadata_path.read_text())
            except Exception:
                metadata = {}

            cloud = metadata.get("cloud", "-")
            region = metadata.get("region", "-")
            resource_group = metadata.get("resource_group", "-")
            setup = "yes" if metadata.get("setup_complete") else "no"
            kubeconfig_exists = "yes" if kubeadm_kubeconfig_path(name).exists() else "no"
            default_marker = "*" if default_cluster and name == default_cluster else ""

            if details:
                masters_count, masters_state, workers_count, workers_state = _cluster_node_details(name)
                table.add_row(
                    name,
                    default_marker,
                    cloud,
                    region,
                    resource_group,
                    kubeconfig_exists,
                    setup,
                    masters_count,
                    masters_state,
                    workers_count,
                    workers_state,
                )
            else:
                table.add_row(name, default_marker, cloud, region, resource_group, kubeconfig_exists, setup)
            found += 1

        if found == 0:
            print("[yellow]No kubeadm clusters with metadata found in ~/.clouder/kubeadm.[/yellow]")
            return

        print(table)
