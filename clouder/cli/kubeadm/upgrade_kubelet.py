"""Clouder CLI - kubeadm upgrade-kubelet command.

Upgrades kubelet, kubeadm, and kubectl on all nodes of a kubeadm cluster
to the version defined by K8S_VERSION.  This is required when the running
cluster was provisioned with an older Kubernetes version and needs kubelet
features available in newer releases (e.g. checkpoint timeout parameter
added in Kubernetes 1.30+).
"""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ...util.utils import SSH_FOLDER

from ._helpers import (
    K8S_VERSION,
    resolve_kubeadm_cluster_name,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd_stream,
)


# ---------------------------------------------------------------------------
# Upgrade script template
# ---------------------------------------------------------------------------

_SCRIPT_UPGRADE_KUBELET = f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== Upgrading kubelet / kubeadm / kubectl to v{K8S_VERSION}.x ==="

# --- Update the Kubernetes apt repo to the target version ---
sudo mkdir -p /etc/apt/keyrings
sudo rm -f /etc/apt/keyrings/kubernetes-apt-keyring.gpg
curl -fsSL https://pkgs.k8s.io/core:/stable:/v{K8S_VERSION}/deb/Release.key | \\
    sudo gpg --yes --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg 2>/dev/null
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v{K8S_VERSION}/deb/ /' | \\
    sudo tee /etc/apt/sources.list.d/kubernetes.list > /dev/null
sudo apt-get update -qq

# --- Unhold, upgrade, re-hold ---
sudo apt-mark unhold kubelet kubeadm kubectl 2>/dev/null || true
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq kubelet kubeadm kubectl > /dev/null
sudo apt-mark hold kubelet kubeadm kubectl

# --- Restart kubelet ---
sudo systemctl daemon-reload
sudo systemctl restart kubelet

echo "kubelet version: $(kubelet --version 2>&1)"
echo "kubeadm version: $(kubeadm version -o short 2>&1)"
echo "kubectl version: $(kubectl version --client -o yaml 2>&1 | head -3)"
echo "=== Upgrade complete ==="
"""


def register(kubeadm_app: typer.Typer):
    """Register the upgrade-kubelet command on the given Typer app."""

    @kubeadm_app.command("upgrade-kubelet", help="Upgrade kubelet, kubeadm, and kubectl on all nodes to the target K8s version.")
    def kubeadm_upgrade_kubelet(
        name: str | None = typer.Argument(None, help="Cluster name (must match vm-create name). If omitted, uses default kubeadm cluster."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
    ):
        """Upgrade kubelet, kubeadm, and kubectl on ALL nodes to the target K8s version.

        This is a rolling upgrade: each node is upgraded sequentially.
        The kubelet is restarted after the upgrade so the new version takes
        effect immediately.  Pods are NOT drained — for a zero-downtime
        upgrade, drain nodes manually before running this command.

        The target version is determined by K8S_VERSION in the clouder
        kubeadm helpers (currently v{k8s_version}).
        """.format(k8s_version=K8S_VERSION)

        name = resolve_kubeadm_cluster_name(name)
        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        workers = cluster["workers"]

        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        all_nodes = [master] + workers

        print(Panel(
            f"[bold]Cluster:[/bold]        {name}\n"
            f"[bold]Target K8s:[/bold]     v{K8S_VERSION}\n"
            f"[bold]Master:[/bold]         {master['name']} ({master['ip']})\n"
            f"[bold]Workers:[/bold]        {', '.join(w['name'] for w in workers)}\n"
            f"[bold]Key:[/bold]            {key_path}",
            title="Kubelet Upgrade",
        ))

        if not Confirm.ask(
            f"\nUpgrade kubelet on all {len(all_nodes)} node(s) to v{K8S_VERSION}?",
            default=True,
        ):
            raise typer.Abort()

        failed: list[str] = []
        for i, node in enumerate(all_nodes, 1):
            role = "master" if node is master else "worker"
            print(f"\n[bold][{i}/{len(all_nodes)}] Upgrading {role} {node['name']} ({node['ip']})...[/bold]")
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
