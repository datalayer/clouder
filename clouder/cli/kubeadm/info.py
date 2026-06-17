"""Clouder CLI - kubeadm info command."""

import json

import typer
from rich import print
from rich.panel import Panel

from ._helpers import (
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
)


def _discover_load_balancer_addresses(cluster_name: str, cluster: dict, metadata: dict | None) -> list[str]:
    """Best-effort discovery of load balancer addresses for kubeadm clusters."""
    addresses: list[str] = []

    # 1) Cloud-native Azure LB public IP used by ingress helpers.
    if metadata and metadata.get("cloud") == "azure" and metadata.get("resource_group"):
        try:
            from ...cloud.azure.api import _get_network_client

            subscription_id = cluster.get("context_id")
            rg = metadata["resource_group"]
            lb_ip_name = f"{cluster_name}-lb-ip"
            network_client = _get_network_client(subscription_id)
            pip = network_client.public_ip_addresses.get(rg, lb_ip_name)
            if pip and getattr(pip, "ip_address", None):
                addresses.append(str(pip.ip_address))
        except Exception:
            # Keep kubeadm info resilient even if Azure lookup fails.
            pass

    # 2) Kubernetes LoadBalancer services (covers Azure and AWS).
    try:
        master = cluster["master"]
        cloud = (metadata or {}).get("cloud")
        user = "azureuser" if cloud == "azure" else "ec2-user"
        key_path = _resolve_ssh_key_for_cluster(cluster_name)
        svc_cmd = (
            "kubectl get svc -A "
            "--field-selector spec.type=LoadBalancer "
            "-o json"
        )
        result = _ssh_cmd(master["ip"], user, key_path, svc_cmd, check=False)
        if result.returncode == 0 and result.stdout:
            payload = json.loads(result.stdout)
            for item in payload.get("items", []):
                ingress = ((item.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []
                for entry in ingress:
                    ip = (entry or {}).get("ip")
                    host = (entry or {}).get("hostname")
                    if ip:
                        addresses.append(str(ip))
                    elif host:
                        addresses.append(str(host))
                for external_ip in (item.get("spec") or {}).get("externalIPs") or []:
                    if external_ip:
                        addresses.append(str(external_ip))
    except Exception:
        # Optional enrichment only; don't fail info output.
        pass

    # De-duplicate while preserving order.
    unique: list[str] = []
    for addr in addresses:
        if addr not in unique:
            unique.append(addr)
    return unique


def register(kubeadm_app: typer.Typer):
    """Register the info command on the given Typer app."""

    @kubeadm_app.command("info")
    def kubeadm_info(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
    ):
        """Show cluster information and next steps.

        Displays the current state of a kubeadm cluster and lists useful
        commands for day-to-day operations as well as further setup steps.
        """
        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        workers = cluster["workers"]
        metadata = _load_cluster_metadata(name)

        # --- Cluster status ---
        status_lines = []
        if metadata and metadata.get("setup_complete"):
            status_lines.append(f"[green]Cluster '{name}' is ready![/green]")
        else:
            status_lines.append(f"[yellow]Cluster '{name}' — setup may not be complete.[/yellow]")

        status_lines.append("")
        status_lines.append("  [bold bright_cyan]Total masters:[/bold bright_cyan] [bold bright_cyan]1[/bold bright_cyan]")
        status_lines.append(
            f"  [bold bright_green]Total workers:[/bold bright_green] "
            f"[bold bright_green]{len(workers)}[/bold bright_green]"
        )
        status_lines.append("")
        status_lines.append(f"  [bold]Masters:[/bold]  {master['name']} ({master['ip']})")
        if workers:
            status_lines.append(f"  [bold]Workers:[/bold]  {', '.join(w['name'] + ' (' + w['ip'] + ')' for w in workers)}")
        else:
            status_lines.append(f"  [bold]Workers:[/bold]  (none)")

        if metadata:
            if metadata.get("k8s_version"):
                status_lines.append(f"  [bold]K8s:[/bold]      v{metadata['k8s_version']}")
            if metadata.get("region"):
                status_lines.append(f"  [bold]Region:[/bold]   {metadata['region']}")
            if metadata.get("resource_group"):
                status_lines.append(f"  [bold]RG:[/bold]       {metadata['resource_group']}")

        print(
            Panel(
                "\n".join(status_lines),
                title="Cluster Info",
                border_style="cyan",
            )
        )

        # --- Load balancer addresses ---
        lb_addresses = _discover_load_balancer_addresses(name, cluster, metadata)
        lb_lines = []
        if lb_addresses:
            lb_lines.append("Discovered load balancer addresses:")
            lb_lines.append("")
            for addr in lb_addresses:
                lb_lines.append(f"  - {addr}")
        else:
            lb_lines.append("No load balancer address detected yet.")
            lb_lines.append("")
            lb_lines.append("Hint: run ingress setup first, then re-run this command.")

        print(
            Panel(
                "\n".join(lb_lines),
                title="Load Balancer",
                border_style="green",
            )
        )

        # --- Cluster commands ---
        cmd_lines = [
            f"  Get kubeconfig:    [cyan]clouder kubeadm get-config {name}[/cyan]",
            f"  Run kubectl:       [cyan]clouder kubectl {name} get nodes[/cyan]",
            f"  SSH to master:     [cyan]clouder ssh {master['name']}[/cyan]",
            f"  Scale workers:     [cyan]clouder kubeadm scale {name} --workers N[/cyan]",
            f"  Remove one node:   [cyan]clouder kubeadm remove-node {name} <node-name>[/cyan]",
            f"  Repair workers:    [cyan]clouder kubeadm repair {name}[/cyan]",
            f"  Ingress (nginx):   [cyan]clouder kubeadm enable-ingress-nginx {name}[/cyan]",
            f"  Ingress (traefik): [cyan]clouder kubeadm enable-ingress-traefik {name}[/cyan]",
            f"  Smoke test:        [cyan]clouder kubeadm smoke-test {name}[/cyan]",
            f"  Terminate:         [cyan]clouder kubeadm terminate {name}[/cyan]",
        ]

        print(
            Panel(
                "\n".join(cmd_lines),
                title="Cluster Commands",
                border_style="bright_blue",
            )
        )

        # --- Plane setup steps ---
        plane_lines = [
            "Start by retrieving the kubeconfig, then run the following commands",
            "to complete the platform setup:\n",
            f"  [cyan]clouder kubeadm get-config {name}[/cyan]",
            "",
            f"  [cyan]clouder kubeadm enable-ingress-traefik {name}[/cyan]",
            f"  [cyan]clouder kubeadm smoke-test {name}[/cyan]",
            "",
            "  [cyan]plane k8s-label-nodes[/cyan]",
            "  [cyan]plane k8s-create-namespaces[/cyan]",
            "  [cyan]plane reg-creds-create[/cyan]",
            "  [cyan]plane k8s-prepull-cpu[/cyan]",
            "  [cyan]plane up datalayer-cert-manager[/cyan]",
            "  [cyan]plane create-cert-issuer[/cyan]",
            "",
            "Full services documentation:",
            "",
            "  [link=https://clouder.sh/services]https://clouder.sh/services[/link]",
        ]

        print(
            Panel(
                "\n".join(plane_lines),
                title="Next Steps",
                border_style="magenta",
            )
        )
