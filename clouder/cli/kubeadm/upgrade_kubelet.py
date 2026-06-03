"""Clouder CLI - kubeadm upgrade-kubelet command.

Upgrades kubelet, kubeadm, and kubectl on all nodes of a kubeadm cluster
to the version defined by K8S_VERSION.
"""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ...util.utils import SSH_FOLDER

from ._helpers import (
    K8S_VERSION,
    _SCRIPT_UPGRADE_KUBELET,
    resolve_kubeadm_cluster_name,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd_stream,
)


def register(kubeadm_app: typer.Typer):
    """Register the upgrade-kubelet command on the given Typer app."""

    @kubeadm_app.command(
        "upgrade-kubelet",
        help="Upgrade kubelet, kubeadm, and kubectl on all nodes to the target K8s version.",
    )
    def kubeadm_upgrade_kubelet(
        name: str | None = typer.Argument(
            None,
            help="Cluster name (must match create name). If omitted, uses default kubeadm cluster.",
        ),
        user: str = typer.Option(
            "azureuser",
            "--admin-user",
            "-u",
            help="SSH username on the VMs.",
        ),
        key: str = typer.Option(
            None,
            "--key",
            "-i",
            help="SSH key name (from ~/.ssh/).",
        ),
    ):
        """Upgrade kubelet, kubeadm, and kubectl on all nodes."""

        name = resolve_kubeadm_cluster_name(name)
        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        workers = cluster["workers"]

        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        all_nodes = [master] + workers

        print(
            Panel(
                f"[bold]Cluster:[/bold]        {name}\n"
                f"[bold]Target K8s:[/bold]     v{K8S_VERSION}\n"
                f"[bold]Masters:[/bold]       {master['name']} ({master['ip']})\n"
                f"[bold]Workers:[/bold]       {', '.join(w['name'] for w in workers)}\n"
                f"[bold]Key:[/bold]           {key_path}",
                title="Kubelet Upgrade",
            )
        )

        if not Confirm.ask(
            f"\nUpgrade kubelet on all {len(all_nodes)} node(s) to v{K8S_VERSION}?",
            default=True,
        ):
            raise typer.Abort()

        failed: list[str] = []
        for i, node in enumerate(all_nodes, 1):
            role = "master" if node is master else "worker"
            print(
                f"\n[bold][{i}/{len(all_nodes)}] Upgrading {role} {node['name']} ({node['ip']})...[/bold]"
            )
            rc = _ssh_cmd_stream(node["ip"], user, key_path, _SCRIPT_UPGRADE_KUBELET)
            if rc != 0:
                print(f"  [red]Upgrade failed on {node['name']}[/red]")
                failed.append(node["name"])
            else:
                print(f"  [green]{node['name']} upgraded successfully.[/green]")

        if failed:
            print(f"\n[red]Upgrade failed on: {', '.join(failed)}[/red]")
            raise typer.Exit(1)

        print(f"\n[green]All {len(all_nodes)} node(s) upgraded to kubelet v{K8S_VERSION}.[/green]")
        print("[dim]Note: kubelet has been restarted on each node.[/dim]")
