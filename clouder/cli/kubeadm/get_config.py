"""Clouder CLI - kubeadm get-config command."""

import re

import typer
from rich import print

from ...util.utils import (
    ensure_kubeadm_cluster_folder,
    kubeadm_kubeconfig_path,
    kubeadm_kubelet_client_cert_path,
    kubeadm_kubelet_client_key_path,
    SSH_FOLDER,
)

from ._helpers import (
    resolve_kubeadm_cluster_name,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
)


def fetch_kubeadm_config_materials(name: str, user: str = "azureuser", key: str | None = None) -> tuple[str, str | None, str | None]:
    """Fetch kubeconfig and kubelet client cert/key from master VM.

    Returns:
        tuple of (kubeconfig_path, kubelet_client_cert_path_or_none, kubelet_client_key_path_or_none)
    """
    cluster = _resolve_cluster_vms(name)
    master = cluster["master"]
    key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

    typer.echo(f"Fetching kubeconfig from {master['name']} ({master['ip']})...")

    result = _ssh_cmd(master["ip"], user, key_path, "cat $HOME/.kube/config", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        typer.echo(result.stderr, err=True)
        print("[red]Failed to fetch kubeconfig from master.[/red]")
        raise typer.Exit(1)

    # Replace the internal IP with the public IP in the kubeconfig
    kubeconfig_content = result.stdout
    kubeconfig_content = re.sub(
        r"(server: https://)[\d.]+(:6443)",
        rf"\g<1>{master['ip']}\g<2>",
        kubeconfig_content,
    )

    ensure_kubeadm_cluster_folder(name)
    kubeconfig_path = kubeadm_kubeconfig_path(name)
    kubeconfig_path.write_text(kubeconfig_content)
    kubeconfig_path.chmod(0o600)

    print(f"[green]Kubeconfig saved to {kubeconfig_path}[/green]")

    # -----------------------------------------------------------------
    # Fetch kubelet client certificates (for CRIU checkpoint API)
    # -----------------------------------------------------------------
    ensure_kubeadm_cluster_folder(name)

    cert_remote = "/etc/kubernetes/pki/apiserver-kubelet-client.crt"
    key_remote = "/etc/kubernetes/pki/apiserver-kubelet-client.key"
    cert_local = kubeadm_kubelet_client_cert_path(name)
    key_local = kubeadm_kubelet_client_key_path(name)

    typer.echo(f"Fetching kubelet client certificates from {master['name']}...")

    cert_result = _ssh_cmd(
        master["ip"], user, key_path,
        f"sudo cat {cert_remote}",
        check=False,
    )
    key_result = _ssh_cmd(
        master["ip"], user, key_path,
        f"sudo cat {key_remote}",
        check=False,
    )

    cert_path = None
    key_path_local = None

    if cert_result.returncode == 0 and cert_result.stdout.strip():
        cert_local.write_text(cert_result.stdout)
        cert_local.chmod(0o600)
        print(f"[green]Kubelet client cert saved to {cert_local}[/green]")
        cert_path = str(cert_local)
    else:
        print(f"[yellow]Could not fetch kubelet client cert from {cert_remote}[/yellow]")

    if key_result.returncode == 0 and key_result.stdout.strip():
        key_local.write_text(key_result.stdout)
        key_local.chmod(0o600)
        print(f"[green]Kubelet client key saved to {key_local}[/green]")
        key_path_local = str(key_local)
    else:
        print(f"[yellow]Could not fetch kubelet client key from {key_remote}[/yellow]")

    return str(kubeconfig_path), cert_path, key_path_local


def register(kubeadm_app: typer.Typer):
    """Register the get-config command on the given Typer app."""

    @kubeadm_app.command("get-config")
    def kubeadm_get_config(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the master VM."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
    ):
        """Fetch kubeconfig from the master and save to ~/.clouder/kubeadm/<NAME>/kubeconfig."""
        name = resolve_kubeadm_cluster_name(name)
        kubeconfig_path, cert_local, key_local = fetch_kubeadm_config_materials(name, user=user, key=key)

        # -----------------------------------------------------------------
        # Print usage
        # -----------------------------------------------------------------
        typer.echo(f"\nUsage:")
        typer.echo(f"  export KUBECONFIG={kubeconfig_path}")
        if cert_local and key_local:
            typer.echo(f"  export KUBELET_CLIENT_CERT={cert_local}")
            typer.echo(f"  export KUBELET_CLIENT_KEY={key_local}")
        typer.echo(f"  kubectl get nodes")
        typer.echo(f"  clouder kubectl {name} get nodes")
