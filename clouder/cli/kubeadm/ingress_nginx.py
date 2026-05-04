"""Clouder CLI - kubeadm enable/disable-ingress-nginx commands."""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ..ctx import get_current_context
from ...util.utils import SSH_FOLDER

from ._helpers import (
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _ssh_cmd_stream,
)


# ---------------------------------------------------------------------------
# Ingress NGINX manifest (NodePort mode)
# ---------------------------------------------------------------------------

_NGINX_NAMESPACE = "datalayer-nginx"

_SCRIPT_INSTALL_INGRESS_NGINX = """
set -euo pipefail

# Create datalayer-nginx namespace
kubectl create namespace datalayer-nginx 2>/dev/null || true

# Install Helm if not present
if ! command -v helm &>/dev/null; then
    echo "Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# Add ingress-nginx Helm repo
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>/dev/null || true
helm repo update

# Install ingress-nginx with NodePort service type
# Set controller.ingressClassResource.name=datalayer-nginx to match plane helm chart expectations
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \\
    --namespace datalayer-nginx \\
    --set controller.ingressClassResource.name=datalayer-nginx \\
    --set controller.ingressClassResource.controllerValue=k8s.io/datalayer-nginx \\
    --set controller.service.type=NodePort \\
    --set controller.service.nodePorts.http=30080 \\
    --set controller.service.nodePorts.https=30443 \\
    --wait --timeout 120s

echo "Ingress NGINX controller installed (NodePort mode) in datalayer-nginx namespace."
echo ""
echo "NodePort assignments:"
kubectl -n datalayer-nginx get svc ingress-nginx-controller -o jsonpath='{range .spec.ports[*]}{.name}: {.nodePort}{"\n"}{end}' 2>/dev/null || true
"""


def register(kubeadm_app: typer.Typer):
    """Register ingress-nginx commands on the given Typer app."""

    @kubeadm_app.command("enable-ingress-nginx")
    def kubeadm_enable_ingress_nginx(
        name: str = typer.Argument(..., help="Cluster name."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
    ):
        """Enable ingress load balancing: deploy ingress-nginx + Azure Load Balancer.

        Deploys ingress-nginx in NodePort mode on the cluster, creates an Azure
        Load Balancer with a public IP, and wires LB rules (80/443) to the
        ingress controller's NodePorts via worker VM NICs.

        This command is idempotent — safe to run multiple times. Helm will
        upgrade-or-install, the LB and public IP use create-or-update, and
        NIC backend pool membership is checked before adding.
        """
        import os as _os

        (cloud, context_id) = get_current_context()
        if cloud != "azure":
            typer.echo("Load balancer setup is currently only supported for Azure.", err=True)
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        workers = cluster["workers"]
        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        if not workers:
            typer.echo("No worker nodes found. Load balancer needs at least one worker.", err=True)
            raise typer.Exit(1)

        rg = master["resource_group"]

        # Detect if this is a re-run (existing LB public IP)
        existing_ip = None
        lb_ip_name = f"{name}-lb-ip"
        try:
            from ...cloud.azure.api import _get_network_client
            nc = _get_network_client(context_id)
            pip = nc.public_ip_addresses.get(rg, lb_ip_name)
            existing_ip = pip.ip_address
        except Exception:
            pass

        rerun_note = ""
        if existing_ip:
            rerun_note = f"\n\n  [dim]Existing LB detected (IP: {existing_ip}) — this is a re-run.[/dim]"

        print(Panel(
            f"[bold]Cluster:[/bold]  {name}\n"
            f"[bold]Master:[/bold]   {master['name']} ({master['ip']})\n"
            f"[bold]Workers:[/bold]  {', '.join(w['name'] for w in workers)}\n"
            f"[bold]RG:[/bold]       {rg}\n\n"
            f"This will:\n"
            f"  1. Deploy/upgrade ingress-nginx controller (NodePort mode)\n"
            f"  2. Create/update Azure Load Balancer with public IP\n"
            f"  3. Wire LB → worker NICs for ports 80/443"
            f"{rerun_note}",
            title="Enable Ingress (NGINX)",
        ))

        if not Confirm.ask("\nProceed?", default=True):
            raise typer.Abort()

        # ----- Pre-check: verify kubectl is available on master -----
        check = _ssh_cmd(master["ip"], user, key_path, "which kubectl", check=False)
        if check.returncode != 0:
            print("[red]kubectl not found on master node.[/red]")
            print(f"[yellow]Run 'clouder kubeadm setup {name}' first to install the cluster.[/yellow]")
            raise typer.Exit(1)

        # ----- Step 1: Deploy ingress-nginx on the cluster -----
        print("\n[bold]Step 1/4: Deploying ingress-nginx controller...[/bold]")
        rc = _ssh_cmd_stream(master["ip"], user, key_path, _SCRIPT_INSTALL_INGRESS_NGINX)
        if rc != 0:
            print("[red]Failed to deploy ingress-nginx.[/red]")
            raise typer.Exit(1)

        # ----- Step 2: Get the NodePort assignments -----
        print("\n[bold]Step 2/4: Reading NodePort assignments...[/bold]")
        result = _ssh_cmd(
            master["ip"], user, key_path,
            f"kubectl -n {_NGINX_NAMESPACE} get svc ingress-nginx-controller "
            "-o jsonpath='{.spec.ports[?(@.name==\"http\")].nodePort} {.spec.ports[?(@.name==\"https\")].nodePort}'",
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            print("[red]Could not read ingress-nginx NodePorts.[/red]")
            typer.echo(result.stderr)
            raise typer.Exit(1)

        ports = result.stdout.strip().strip("'").split()
        if len(ports) < 2:
            print(f"[red]Expected 2 NodePorts, got: {result.stdout.strip()}[/red]")
            raise typer.Exit(1)
        http_nodeport = int(ports[0])
        https_nodeport = int(ports[1])
        print(f"  HTTP  NodePort: [cyan]{http_nodeport}[/cyan]")
        print(f"  HTTPS NodePort: [cyan]{https_nodeport}[/cyan]")

        # ----- Step 3: Create Azure Load Balancer -----
        print("\n[bold]Step 3/4: Creating Azure Load Balancer...[/bold]")
        from ...cloud.azure.api import create_azure_load_balancer

        # Get location from one of the VMs
        from ...cloud.azure.api import list_azure_vms as _list_vms
        all_vms = _list_vms(resource_group=rg, subscription_id=context_id)
        location = next((v["location"] for v in all_vms if v["name"] == master["name"]), None)
        if not location:
            typer.echo("Could not determine cluster location.", err=True)
            raise typer.Exit(1)

        lb_name = f"{name}-lb"
        lb_ip_name = f"{name}-lb-ip"

        # Override the LB rules to use NodePorts as backend ports
        from ...cloud.azure.api import _get_network_client
        network_client = _get_network_client(context_id)

        # First create public IP
        ip_poller = network_client.public_ip_addresses.begin_create_or_update(
            rg, lb_ip_name,
            {
                "location": location,
                "sku": {"name": "Standard"},
                "public_ip_allocation_method": "Static",
            },
        )
        public_ip = ip_poller.result()

        sub_id = context_id
        frontend_name = "lb-frontend"
        backend_pool_name = "lb-backend-pool"
        frontend_id = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Network"
            f"/loadBalancers/{lb_name}/frontendIPConfigurations/{frontend_name}"
        )
        backend_pool_ref = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Network"
            f"/loadBalancers/{lb_name}/backendAddressPools/{backend_pool_name}"
        )
        probe_base = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Network"
            f"/loadBalancers/{lb_name}/probes"
        )

        lb_params = {
            "location": location,
            "sku": {"name": "Standard"},
            "frontend_ip_configurations": [
                {
                    "name": frontend_name,
                    "public_ip_address": {"id": public_ip.id},
                }
            ],
            "backend_address_pools": [
                {"name": backend_pool_name}
            ],
            "probes": [
                {
                    "name": "http-probe",
                    "protocol": "Tcp",
                    "port": http_nodeport,
                    "interval_in_seconds": 15,
                    "number_of_probes": 2,
                },
                {
                    "name": "https-probe",
                    "protocol": "Tcp",
                    "port": https_nodeport,
                    "interval_in_seconds": 15,
                    "number_of_probes": 2,
                },
            ],
            "load_balancing_rules": [
                {
                    "name": "http-rule",
                    "protocol": "Tcp",
                    "frontend_port": 80,
                    "backend_port": http_nodeport,
                    "frontend_ip_configuration": {"id": frontend_id},
                    "backend_address_pool": {"id": backend_pool_ref},
                    "probe": {"id": f"{probe_base}/http-probe"},
                    "idle_timeout_in_minutes": 15,
                    "enable_tcp_reset": True,
                },
                {
                    "name": "https-rule",
                    "protocol": "Tcp",
                    "frontend_port": 443,
                    "backend_port": https_nodeport,
                    "frontend_ip_configuration": {"id": frontend_id},
                    "backend_address_pool": {"id": backend_pool_ref},
                    "probe": {"id": f"{probe_base}/https-probe"},
                    "idle_timeout_in_minutes": 15,
                    "enable_tcp_reset": True,
                },
            ],
        }

        lb_poller = network_client.load_balancers.begin_create_or_update(rg, lb_name, lb_params)
        lb = lb_poller.result()

        backend_pool_id = None
        for pool in (lb.backend_address_pools or []):
            if pool.name == backend_pool_name:
                backend_pool_id = pool.id
                break

        print(f"  Load Balancer:  [cyan]{lb_name}[/cyan]")
        print(f"  Public IP:      [green]{public_ip.ip_address}[/green]")
        print(f"  Rules:          80 → :{http_nodeport},  443 → :{https_nodeport}")

        # ----- Step 4: Add worker NICs to backend pool -----
        print("\n[bold]Step 4/4: Adding worker NICs to backend pool...[/bold]")
        from ...cloud.azure.api import add_nic_to_lb_backend_pool

        for worker in workers:
            nic_name = f"{worker['name']}-nic"
            typer.echo(f"  Adding {nic_name}...")
            try:
                add_nic_to_lb_backend_pool(rg, nic_name, backend_pool_id, subscription_id=context_id)
                print(f"  [green]{nic_name} added.[/green]")
            except Exception as e:
                print(f"  [red]Failed to add {nic_name}: {e}[/red]")

        # ----- Done -----
        run_url = _os.environ.get("DATALAYER_RUN_URL", "")
        run_host = run_url.replace("https://", "").replace("http://", "").rstrip("/") if run_url else ""

        print(Panel(
            f"[green]Ingress NGINX + load balancer enabled for cluster '{name}'![/green]\n\n"
            f"  LB Public IP:   [bold]{public_ip.ip_address}[/bold]\n"
            f"  HTTP:           {public_ip.ip_address}:80  → NodePort {http_nodeport}\n"
            f"  HTTPS:          {public_ip.ip_address}:443 → NodePort {https_nodeport}\n\n"
            f"  Remove with:    clouder kubeadm disable-ingress-nginx {name}",
            title="Ingress NGINX + LB Ready",
        ))

        # ----- DNS Configuration Reminder -----
        if run_host:
            print(Panel(
                f"[bold yellow]Configure your DNS A record:[/bold yellow]\n\n"
                f"  [bold]{run_host}[/bold]  →  [bold]{public_ip.ip_address}[/bold]\n\n"
                f"  Update your DNS provider to point [cyan]{run_host}[/cyan]\n"
                f"  to the Load Balancer IP [cyan]{public_ip.ip_address}[/cyan].\n\n"
                f"  You can verify with:  [dim]dig +short {run_host}[/dim]",
                title="⚠ DNS Configuration Required",
                border_style="yellow",
            ))
        else:
            print(Panel(
                f"[bold yellow]Configure your DNS A record:[/bold yellow]\n\n"
                f"  [bold]<your-domain>[/bold]  →  [bold]{public_ip.ip_address}[/bold]\n\n"
                f"  Set DATALAYER_RUN_URL to enable automatic DNS reminders.\n"
                f"  Example: export DATALAYER_RUN_URL=https://prod1.datalayer.run",
                title="⚠ DNS Configuration Required",
                border_style="yellow",
            ))

    @kubeadm_app.command("disable-ingress-nginx")
    def kubeadm_disable_ingress_nginx(
        name: str = typer.Argument(..., help="Cluster name."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    ):
        """Remove load balancer and ingress-nginx from the cluster.

        Deletes the Azure Load Balancer, its public IP, and uninstalls
        the ingress-nginx controller from the cluster.
        """
        (cloud, context_id) = get_current_context()
        if cloud != "azure":
            typer.echo("Load balancer commands are currently only supported for Azure.", err=True)
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        rg = master["resource_group"]
        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        lb_name = f"{name}-lb"
        lb_ip_name = f"{name}-lb-ip"

        print(f"\n[bold]Resources to delete:[/bold]")
        typer.echo(f"  Azure LB:       {lb_name}")
        typer.echo(f"  LB Public IP:   {lb_ip_name}")
        typer.echo(f"  Ingress NGINX:  {_NGINX_NAMESPACE} namespace")

        if not force:
            if not Confirm.ask(f"\nRemove load balancer from cluster '{name}'?", default=False):
                raise typer.Abort()

        # Step 1: Delete Azure LB
        print("\n[bold]Deleting Azure Load Balancer...[/bold]")
        try:
            from ...cloud.azure.api import delete_azure_load_balancer
            delete_azure_load_balancer(rg, lb_name, subscription_id=context_id)
            print(f"  [green]Deleted: {lb_name}[/green]")
        except Exception:
            print(f"  [dim]{lb_name} not found or already deleted.[/dim]")

        # Step 2: Delete LB public IP
        print("[bold]Deleting LB public IP...[/bold]")
        try:
            from ...cloud.azure.api import delete_azure_public_ip
            delete_azure_public_ip(rg, lb_ip_name, subscription_id=context_id)
            print(f"  [green]Deleted: {lb_ip_name}[/green]")
        except Exception:
            print(f"  [dim]{lb_ip_name} not found or already deleted.[/dim]")

        # Step 3: Remove ingress-nginx from the cluster
        print("[bold]Removing ingress-nginx from cluster...[/bold]")
        rc = _ssh_cmd_stream(
            master["ip"], user, key_path,
            f"helm uninstall ingress-nginx --namespace {_NGINX_NAMESPACE} 2>/dev/null || true; "
            f"kubectl delete namespace {_NGINX_NAMESPACE} 2>/dev/null || true",
        )
        if rc == 0:
            print("  [green]Ingress NGINX removed.[/green]")
        else:
            print("  [dim]Ingress NGINX was not installed or already removed.[/dim]")

        print(f"\n[green]Ingress NGINX + load balancer removed from cluster '{name}'.[/green]")
