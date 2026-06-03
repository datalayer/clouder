"""Clouder CLI - kubeadm set-default command."""

import typer
from rich import print

from ...util.utils import kubeadm_metadata_path
from ._helpers import set_default_kubeadm_cluster


def register(kubeadm_app: typer.Typer):
    """Register the set-default command on the given Typer app."""

    @kubeadm_app.command("set-default")
    def kubeadm_set_default(
        name: str = typer.Argument(..., help="Cluster name to use as default for kubeadm commands."),
    ):
        """Set the default kubeadm cluster name for commands where <name> is omitted."""
        if not kubeadm_metadata_path(name).exists():
            raise typer.BadParameter(
                f"Cluster metadata not found for '{name}' at {kubeadm_metadata_path(name)}."
            )
        set_default_kubeadm_cluster(name)
        print(f"[green]Default kubeadm cluster set to '{name}'.[/green]")
