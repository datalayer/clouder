"""Clouder CLI - kubeadm set-default command."""

import json

import typer
from rich import print

from ...util.utils import kubeadm_metadata_path
from ._helpers import set_default_kubeadm_cluster


def register(kubeadm_app: typer.Typer):
    """Register the set-default command on the given Typer app."""

    @kubeadm_app.command("set-default")
    def kubeadm_set_default(
        name: str = typer.Argument(..., help="Cluster name to use as default for kubeadm commands."),
        cloud: str | None = typer.Option(None, "--cloud", help="Expected cloud provider for this cluster (azure or aws)."),
    ):
        """Set the default kubeadm cluster name for commands where <name> is omitted."""
        metadata_path = kubeadm_metadata_path(name)
        if not metadata_path.exists():
            raise typer.BadParameter(
                f"Cluster metadata not found for '{name}' at {metadata_path}."
            )

        if cloud:
            metadata = json.loads(metadata_path.read_text())
            cluster_cloud = str(metadata.get("cloud") or "")
            if cluster_cloud != cloud:
                raise typer.BadParameter(
                    f"Cluster '{name}' uses cloud={cluster_cloud}, but --cloud={cloud}."
                )

        set_default_kubeadm_cluster(name)
        print(f"[green]Default kubeadm cluster set to '{name}'.[/green]")
