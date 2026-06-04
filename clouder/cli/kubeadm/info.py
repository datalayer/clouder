"""Clouder CLI - kubeadm info command."""

import typer
from rich import print
from rich.panel import Panel

from ._helpers import (
    resolve_kubeadm_cluster_name,
    _load_cluster_metadata,
    _resolve_cluster_vms,
)


def register(kubeadm_app: typer.Typer):
    """Register the info command on the given Typer app."""

    @kubeadm_app.command("info")
    def kubeadm_info(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
    ):
        """Show cluster information and next steps.

        Displays the current state of a kubeadm cluster and lists useful
        commands for day-to-day operations as well as further setup steps.
        """
        name = resolve_kubeadm_cluster_name(name)
        cluster = _resolve_cluster_vms(name)
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
        status_lines.append(f"  [bold]Masters:[/bold]   {master['name']} ({master['ip']})")
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

        print(Panel("\n".join(status_lines), title="[bold cyan]Cluster Info[/bold cyan]"))

        # --- Cluster commands ---
        cmd_lines = [
            f"  Get kubeconfig:    clouder kubeadm get-config {name}",
            f"  Run kubectl:       clouder kubectl {name} get nodes",
            f"  SSH to master:     clouder ssh {master['name']}",
            f"  Scale workers:     clouder kubeadm scale {name} --workers N",
            f"  Ingress (nginx):   clouder kubeadm enable-ingress-nginx {name}",
            f"  Ingress (traefik): clouder kubeadm enable-ingress-traefik {name}",
            f"  Smoke test:        clouder kubeadm smoke-test {name}",
            f"  Terminate:         clouder kubeadm terminate {name}",
        ]

        print(Panel("\n".join(cmd_lines), title="[bold bright_blue]Cluster Commands[/bold bright_blue]"))

        # --- Plane setup steps ---
        plane_lines = [
            "After retrieving the kubeconfig, run the following plane commands",
            "to complete the platform setup:\n",
            f"  clouder kubeadm get-config {name}",
            f"  clouder kubeadm enable-ingress-traefik {name} ",
            f"  clouder kubeadm smoke-test {name} ",
            "",
            "  plane k8s-label-nodes",
            "  plane k8s-create-namespaces",
            "  plane reg-creds-create",
            "  plane k8s-prepull-cpu",
            "  plane up datalayer-cert-manager",
            "  plane create-cert-issuer",
            "",
            "Full services documentation:",
            "",
            "  [link=https://clouder.sh/services]https://clouder.sh/services[/link]",
        ]

        print(Panel("\n".join(plane_lines), title="[bold magenta]Next Steps[/bold magenta]"))
