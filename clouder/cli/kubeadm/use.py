"""Clouder CLI - kubeadm use command."""

import os

import typer
from rich import print

from ...util.utils import kubeadm_kubeconfig_path

from ._helpers import resolve_kubeadm_cluster_name
from .get_config import fetch_kubeadm_config_materials


def register(kubeadm_app: typer.Typer):
    """Register the use command on the given Typer app."""

    @kubeadm_app.command("use")
    def kubeadm_use(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the master VM when fetching kubeconfig."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/) when fetching kubeconfig."),
        print_export: bool = typer.Option(False, "--print-export", help="Print only the export command (shell-friendly)."),
    ):
        """Select kubeadm cluster kubeconfig (fetching if missing).

        If local kubeconfig does not exist, it is fetched from the cluster master
        (same behavior as `kubeadm get-config`).
        """
        name = resolve_kubeadm_cluster_name(name)

        kubeconfig_path = kubeadm_kubeconfig_path(name)
        if not kubeconfig_path.exists():
            if not print_export:
                print(f"[yellow]Local kubeconfig not found for cluster '{name}'. Fetching from server...[/yellow]")
            fetch_kubeadm_config_materials(name, user=user, key=key)

        os.environ["KUBECONFIG"] = str(kubeconfig_path)
        export_cmd = f'export KUBECONFIG="{kubeconfig_path}"'

        if print_export:
            typer.echo(export_cmd)
            return

        print(f"[green]Using kubeconfig for cluster '{name}'.[/green]")
        typer.echo(export_cmd)
        typer.echo("Run the line above in your shell, or use:")
        typer.echo(f'  eval "$(clouder kubeadm use {name} --print-export)"')
