"""Clouder CLI - kubeadm list command."""

import json

import typer
from rich import print
from rich.table import Table

from ...util.utils import CLOUDER_KUBEADM_FOLDER, kubeadm_kubeconfig_path, kubeadm_metadata_path
from ._helpers import get_default_kubeadm_cluster


def register(kubeadm_app: typer.Typer):
    """Register the list command on the given Typer app."""

    @kubeadm_app.command("ls")
    def kubeadm_list():
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

            table.add_row(name, default_marker, cloud, region, resource_group, kubeconfig_exists, setup)
            found += 1

        if found == 0:
            print("[yellow]No kubeadm clusters with metadata found in ~/.clouder/kubeadm.[/yellow]")
            return

        print(table)
