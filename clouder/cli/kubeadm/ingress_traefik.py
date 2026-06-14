"""Clouder CLI - kubeadm enable/disable-ingress-traefik commands."""

import json

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from ...util.wait import wait_with_spinner

from ...util.utils import SSH_FOLDER

from ._helpers import (
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _ssh_cmd_stream,
    _update_cluster_metadata,
)


# ---------------------------------------------------------------------------
# Traefik manifest (NodePort mode)
# ---------------------------------------------------------------------------

_TRAEFIK_NAMESPACE = "datalayer-traefik"

_SCRIPT_INSTALL_TRAEFIK = """
set -euo pipefail

# Create datalayer-traefik namespace
kubectl create namespace datalayer-traefik 2>/dev/null || true

# Install Traefik via Helm (NodePort mode)
# Install Helm if not present
if ! command -v helm &>/dev/null; then
    echo "Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# Add Traefik Helm repo
helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true
helm repo update

# Install Traefik with NodePort service type
# Set ingressClass.name=datalayer-traefik to match plane helm chart expectations
HELM_TIMEOUT="${HELM_TIMEOUT:-300s}"

if ! HELM_ERR=$(helm upgrade --install traefik traefik/traefik \
    --namespace datalayer-traefik \
    --set ingressClass.name=datalayer-traefik \
    --set service.type=NodePort \
    --set ports.web.nodePort=30080 \
    --set ports.websecure.nodePort=30443 \
    --set providers.kubernetesIngress.enabled=true \
    --set providers.kubernetesCRD.enabled=true 2>&1); then
    echo "$HELM_ERR"
    if echo "$HELM_ERR" | grep -q "another operation (install/upgrade/rollback) is in progress"; then
        echo "Helm release is currently locked by another operation."
        echo "Proceeding to Kubernetes rollout checks; if Traefik is healthy, this is non-fatal."
    else
    echo "Traefik helm install/upgrade command failed."
    echo "Collecting diagnostics..."
    kubectl -n datalayer-traefik get pods -o wide 2>/dev/null || true
    kubectl -n datalayer-traefik get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 60 || true
    echo "Retrying helm upgrade once..."
        helm upgrade --install traefik traefik/traefik \
        --namespace datalayer-traefik \
        --set ingressClass.name=datalayer-traefik \
        --set service.type=NodePort \
        --set ports.web.nodePort=30080 \
        --set ports.websecure.nodePort=30443 \
        --set providers.kubernetesIngress.enabled=true \
        --set providers.kubernetesCRD.enabled=true
    fi
fi

echo "Validating Traefik deployment readiness..."
if ! kubectl -n datalayer-traefik rollout status deployment/traefik --timeout "$HELM_TIMEOUT"; then
    echo "ERROR: Traefik deployment did not become Ready within $HELM_TIMEOUT"
    kubectl -n datalayer-traefik get pods -o wide 2>/dev/null || true
    kubectl -n datalayer-traefik describe deployment traefik 2>/dev/null || true
    kubectl -n datalayer-traefik get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 80 || true
    exit 1
fi

if ! kubectl -n datalayer-traefik get svc traefik >/dev/null 2>&1; then
    echo "ERROR: Traefik service was not created"
    kubectl -n datalayer-traefik get all 2>/dev/null || true
    exit 1
fi

echo "Traefik ingress controller installed (NodePort mode) in datalayer-traefik namespace."
echo ""
echo "NodePort assignments:"
kubectl -n datalayer-traefik get svc traefik -o jsonpath='{range .spec.ports[*]}{.name}: {.nodePort}{"\\n"}{end}' 2>/dev/null || true
"""


_SCRIPT_INSTALL_TRAEFIK_AWS = """
set -euo pipefail

# Create datalayer-traefik namespace
kubectl create namespace datalayer-traefik 2>/dev/null || true

# Install Helm if not present
if ! command -v helm &>/dev/null; then
    echo "Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# Add Traefik Helm repo
helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true
helm repo update

# Install Traefik with AWS LoadBalancer service (NLB via AWS Load Balancer Controller)
# Keep ingressClass.name=datalayer-traefik to match plane helm chart expectations.
HELM_TIMEOUT="${HELM_TIMEOUT:-300s}"

run_helm_install() {
    helm upgrade --install traefik traefik/traefik \\
        --namespace datalayer-traefik \\
        --set ingressClass.name=datalayer-traefik \\
        --set service.type=LoadBalancer \\
        --set providers.kubernetesIngress.enabled=true \\
        --set providers.kubernetesCRD.enabled=true \\
        --set service.annotations."service\\.beta\\.kubernetes\\.io/aws-load-balancer-type"=external \\
    --set service.annotations."service\\.beta\\.kubernetes\\.io/aws-load-balancer-nlb-target-type"=instance \\
        --set service.annotations."service\\.beta\\.kubernetes\\.io/aws-load-balancer-scheme"=internet-facing \\
        --wait --timeout "$HELM_TIMEOUT"
}

if ! HELM_ERR=$(run_helm_install 2>&1); then
    echo "$HELM_ERR"
    if echo "$HELM_ERR" | grep -q "another operation (install/upgrade/rollback) is in progress"; then
        echo "Helm release lock detected for traefik. Attempting recovery from pending release..."
        helm status traefik -n datalayer-traefik || true
        helm uninstall traefik -n datalayer-traefik 2>/dev/null || true
        echo "Retrying Traefik install after release cleanup..."
        run_helm_install
    else
        echo "Traefik helm install/upgrade command failed."
        echo "Collecting diagnostics..."
        kubectl -n datalayer-traefik get pods -o wide 2>/dev/null || true
        kubectl -n datalayer-traefik get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 80 || true
        exit 1
    fi
fi

echo "Validating Traefik deployment readiness..."
if ! kubectl -n datalayer-traefik rollout status deployment/traefik --timeout "$HELM_TIMEOUT"; then
    echo "ERROR: Traefik deployment did not become Ready within $HELM_TIMEOUT"
    kubectl -n datalayer-traefik get pods -o wide 2>/dev/null || true
    kubectl -n datalayer-traefik describe deployment traefik 2>/dev/null || true
    kubectl -n datalayer-traefik get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 80 || true
    exit 1
fi

echo "Traefik ingress controller installed (LoadBalancer mode) in datalayer-traefik namespace."
echo "Service details:"
kubectl -n datalayer-traefik get svc traefik -o wide 2>/dev/null || true
"""


def _ensure_aws_node_provider_ids(
    master_ip: str,
    ssh_user: str,
    key_path: str,
    instance_ids: list[str],
    region: str,
) -> tuple[int, int, int]:
    """Patch missing Kubernetes node providerIDs from EC2 instance metadata."""
    if not instance_ids:
        return (0, 0, 0)

    from ...cloud.aws.api import get_aws_instances_details

    nodes_result = _ssh_cmd(master_ip, ssh_user, key_path, "kubectl get nodes -o json", check=False)
    if nodes_result.returncode != 0 or not nodes_result.stdout.strip():
        return (0, 0, 0)

    nodes_doc = json.loads(nodes_result.stdout)
    node_items = nodes_doc.get("items", []) or []

    instance_details = get_aws_instances_details(instance_ids=instance_ids, region=region or None)
    ip_to_instance: dict[str, tuple[str, str]] = {}
    for instance_id, details in instance_details.items():
        private_ip = str(details.get("private_ip") or "").strip()
        availability_zone = str(details.get("availability_zone") or "").strip()
        if private_ip:
            ip_to_instance[private_ip] = (instance_id, availability_zone)

    patched = 0
    already = 0
    unresolved = 0

    for node in node_items:
        metadata = node.get("metadata") or {}
        node_name = str(metadata.get("name") or "").strip()
        spec = node.get("spec") or {}
        provider_id = str(spec.get("providerID") or "").strip()
        if provider_id:
            already += 1
            continue

        addresses = (node.get("status") or {}).get("addresses") or []
        internal_ip = ""
        for address in addresses:
            if address.get("type") == "InternalIP":
                internal_ip = str(address.get("address") or "").strip()
                break

        if not node_name or not internal_ip or internal_ip not in ip_to_instance:
            unresolved += 1
            continue

        instance_id, availability_zone = ip_to_instance[internal_ip]
        new_provider_id = f"aws:///{availability_zone}/{instance_id}" if availability_zone else f"aws:///{instance_id}"
        patch_payload = json.dumps({"spec": {"providerID": new_provider_id}})
        patch_result = _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            f"kubectl patch node {node_name} --type=merge -p '{patch_payload}'",
            check=False,
        )
        if patch_result.returncode == 0:
            patched += 1
        else:
            unresolved += 1

    return (patched, already, unresolved)


def register(kubeadm_app: typer.Typer):
    """Register ingress-traefik commands on the given Typer app."""

    @kubeadm_app.command("enable-ingress-traefik")
    def kubeadm_enable_ingress_traefik(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
        user: str | None = typer.Option(None, "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
    ):
        """Enable ingress load balancing with Traefik.

        Azure: deploy Traefik in NodePort mode, create Azure Load Balancer,
        and wire LB rules to worker NICs.

        AWS: deploy Traefik in LoadBalancer mode so the AWS Load Balancer
        Controller provisions an external NLB endpoint.

        This command is idempotent — safe to run multiple times. Helm will
        upgrade-or-install, the LB and public IP use create-or-update, and
        NIC backend pool membership is checked before adding.
        """
        import os as _os
        import time as _time

        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        if cloud not in {"azure", "aws"}:
            typer.echo("Load balancer setup is currently only supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        workers = cluster["workers"]
        metadata = _load_cluster_metadata(name) or {}
        resolved_user = user or metadata.get("admin_username") or ("azureuser" if cloud == "azure" else "ubuntu")
        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        if cloud == "azure" and not workers:
            typer.echo("No worker nodes found. Load balancer needs at least one worker.", err=True)
            raise typer.Exit(1)

        rg = master.get("resource_group")

        # Detect if this is a re-run (existing LB public IP)
        existing_ip = None
        lb_ip_name = f"{name}-lb-ip"
        if cloud == "azure":
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

        workers_display = ", ".join(w["name"] for w in workers) if workers else "(none)"
        if cloud == "azure":
            steps_text = (
                "  1. Deploy/upgrade Traefik ingress controller (NodePort mode via Helm)\n"
                "  2. Create/update Azure Load Balancer with public IP\n"
                "  3. Wire LB → worker NICs for ports 80/443"
            )
            platform_lines = f"[bold]Workers:[/bold]  {workers_display}\n[bold]RG:[/bold]       {rg}\n\n"
        else:
            steps_text = (
                "  1. Deploy/upgrade Traefik ingress controller (LoadBalancer mode via Helm)\n"
                "  2. Wait for AWS Load Balancer Controller to provision external endpoint\n"
                "  3. Validate endpoint and persist ingress domain"
            )
            platform_lines = f"[bold]Workers:[/bold]  {workers_display}\n\n"

        print(Panel(
            f"[bold]Cluster:[/bold]  {name}\n"
            f"[bold]Masters:[/bold]  {master['name']} ({master['ip']})\n"
            f"{platform_lines}"
            f"This will:\n"
            f"{steps_text}"
            f"{rerun_note}",
            title="Enable Ingress (Traefik)",
        ))

        if not Confirm.ask("\nProceed?", default=True):
            raise typer.Abort()

        # ----- Pre-check: verify kubectl is available on master -----
        check = _ssh_cmd(master["ip"], resolved_user, key_path, "which kubectl", check=False)
        if check.returncode != 0:
            print("[red]kubectl not found on master node.[/red]")
            print(
                Panel.fit(
                    f"[bold cyan]clouder kubeadm setup {name}[/bold cyan]",
                    title="Prerequisite",
                    subtitle="Run this first to install the cluster",
                    border_style="bright_yellow",
                )
            )
            raise typer.Exit(1)

        # ----- Step 1: Deploy Traefik on the cluster -----
        print("\n[bold]Step 1/4: Deploying Traefik ingress controller...[/bold]")
        install_script = _SCRIPT_INSTALL_TRAEFIK if cloud == "azure" else _SCRIPT_INSTALL_TRAEFIK_AWS
        rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, install_script)
        if rc != 0:
            print("[red]Failed to deploy Traefik.[/red]")
            raise typer.Exit(1)

        # ----- Step 2: Resolve Traefik service endpoint details -----
        print("\n[bold]Step 2/4: Reading Traefik service details...[/bold]")
        result = _ssh_cmd(
            master["ip"], resolved_user, key_path,
            f"kubectl -n {_TRAEFIK_NAMESPACE} get svc traefik "
            "-o jsonpath='{.spec.ports[?(@.name==\"web\")].nodePort} {.spec.ports[?(@.name==\"websecure\")].nodePort}'",
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            print("[red]Could not read Traefik NodePorts.[/red]")
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

        if cloud == "aws":
            # For AWS NLB target-type=instance, health checks hit node ports.
            # Open required ports on attached instance security groups to avoid target timeouts.
            aws_region = str(metadata.get("region") or master.get("region") or "")
            instance_ids = [str(w.get("instance_id") or "").strip() for w in workers]
            instance_ids = [instance_id for instance_id in instance_ids if instance_id]
            if instance_ids:
                try:
                    from ...cloud.aws.api import ensure_aws_instance_security_group_ingress

                    sg_result = ensure_aws_instance_security_group_ingress(
                        instance_ids=instance_ids,
                        ports=[80, 443, http_nodeport, https_nodeport],
                        cidr="0.0.0.0/0",
                        region=aws_region or None,
                    )
                    print(
                        "  [green]Ensured worker security-group ingress for AWS LB traffic:[/green] "
                        f"ports {sg_result.get('ports', [])}"
                    )
                except Exception as exc:
                    print(
                        "  [yellow]Could not pre-open AWS security-group ingress automatically; "
                        f"continuing with LB provisioning: {exc}[/yellow]"
                    )

            cluster_instance_ids = [str(master.get("instance_id") or "").strip()] + instance_ids
            cluster_instance_ids = [instance_id for instance_id in cluster_instance_ids if instance_id]
            if cluster_instance_ids:
                try:
                    patched, already, unresolved = _ensure_aws_node_provider_ids(
                        master_ip=master["ip"],
                        ssh_user=resolved_user,
                        key_path=key_path,
                        instance_ids=cluster_instance_ids,
                        region=aws_region,
                    )
                    print(
                        "  [green]AWS node providerID reconciliation:[/green] "
                        f"patched={patched}, already_set={already}, unresolved={unresolved}"
                    )
                except Exception as exc:
                    print(
                        "  [yellow]Could not reconcile Kubernetes node providerIDs automatically; "
                        f"continuing with LB provisioning: {exc}[/yellow]"
                    )

        if cloud == "aws":
            svc_endpoint = ""
            print("\n[bold]Step 3/4: Waiting for AWS LoadBalancer endpoint...[/bold]")
            for _ in range(90):
                endpoint_res = _ssh_cmd(
                    master["ip"],
                    resolved_user,
                    key_path,
                    (
                        f"kubectl -n {_TRAEFIK_NAMESPACE} get svc traefik "
                        "-o jsonpath='{.status.loadBalancer.ingress[0].hostname} {.status.loadBalancer.ingress[0].ip}' "
                        "2>/dev/null || true"
                    ),
                    check=False,
                )
                raw = endpoint_res.stdout.strip().strip("'")
                parts = [p for p in raw.split() if p]
                if parts:
                    svc_endpoint = parts[0]
                    break
                _time.sleep(5)

            if not svc_endpoint:
                print("[red]Timed out waiting for Traefik LoadBalancer endpoint on AWS.[/red]")
                _ssh_cmd_stream(
                    master["ip"],
                    resolved_user,
                    key_path,
                    f"kubectl -n {_TRAEFIK_NAMESPACE} get svc traefik -o wide; "
                    f"kubectl -n {_TRAEFIK_NAMESPACE} describe svc traefik",
                )
                raise typer.Exit(1)

            print(f"  AWS LB endpoint: [green]{svc_endpoint}[/green]")
            public_endpoint = svc_endpoint
        else:
            public_endpoint = ""

        # ----- Step 3: Create Azure Load Balancer -----
        if cloud == "azure":
            print("\n[bold]Step 3/4: Creating Azure Load Balancer...[/bold]")

            # Get location from one of the VMs
            from ...cloud.azure.api import list_azure_vms as _list_vms
            all_vms = _list_vms(resource_group=rg, subscription_id=context_id)
            location = next((v["location"] for v in all_vms if v["name"] == master["name"]), None)
            if not location:
                typer.echo("Could not determine cluster location.", err=True)
                raise typer.Exit(1)

            lb_name = f"{name}-lb"
            lb_ip_name = f"{name}-lb-ip"

            from ...cloud.azure.api import _get_network_client
            network_client = _get_network_client(context_id)

            # Create public IP
            ip_poller = network_client.public_ip_addresses.begin_create_or_update(
                rg, lb_ip_name,
                {
                    "location": location,
                    "sku": {"name": "Standard"},
                    "public_ip_allocation_method": "Static",
                },
            )
            public_ip = wait_with_spinner(
                lambda: ip_poller.result(),
                f"Creating load balancer public IP {lb_ip_name}",
            )

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
            lb = wait_with_spinner(
                lambda: lb_poller.result(),
                f"Creating load balancer {lb_name}",
            )

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

            # Wait until public IP is fully assigned.
            if not public_ip.ip_address:
                print(f"\n[bold]Waiting for load balancer IP assignment for {lb_ip_name}...[/bold]")
                for _ in range(60):
                    refreshed = network_client.public_ip_addresses.get(rg, lb_ip_name)
                    if refreshed and refreshed.ip_address:
                        public_ip = refreshed
                        break
                    _time.sleep(2)
                if not public_ip.ip_address:
                    print(f"[red]Timed out waiting for load balancer IP assignment for {lb_ip_name}.[/red]")
                    raise typer.Exit(1)

            public_endpoint = public_ip.ip_address

        # ----- Step 4/4 on AWS is endpoint verification (already done) -----
        if cloud == "aws":
            print("\n[bold]Step 4/4: Finalizing ingress endpoint metadata...[/bold]")

        # ----- Done -----
        run_url = _os.environ.get("DATALAYER_RUN_URL", "")
        run_host = run_url.replace("https://", "").replace("http://", "").rstrip("/") if run_url else ""

        endpoint_label = "Load balancer endpoint" if cloud == "aws" else "Load balancer IP"
        print(f"\n[bold]{endpoint_label}:[/bold] [green]{public_endpoint}[/green]")

        metadata = _load_cluster_metadata(name) or {}
        saved_domain = str(metadata.get("ingress_traefik_domain") or "").strip()
        default_domain = saved_domain or str(metadata.get("public_hostname") or "").strip() or f"{name}.datalayer.run"

        endpoint_is_hostname = "." in public_endpoint and not public_endpoint.replace(".", "").isdigit()

        # Show DNS guidance before prompting for hostname validation.
        dns_record_type = "CNAME" if endpoint_is_hostname else "A"
        dns_title = f"[bold yellow]Configure your DNS {dns_record_type} record:[/bold yellow]"
        verify_hint = f"dig +short {default_domain}" if not endpoint_is_hostname else f"dig +short {default_domain} && dig +short {public_endpoint}"
        print(Panel(
            f"{dns_title}\n\n"
            f"  [bold]{default_domain}[/bold]  →  [bold]{public_endpoint}[/bold]\n\n"
            f"  Update your DNS provider to point [cyan]{default_domain}[/cyan]\n"
            f"  to the Load Balancer endpoint [cyan]{public_endpoint}[/cyan].\n\n"
            f"  You can verify with:  [dim]{verify_hint}[/dim]",
            title="⚠ DNS Configuration Required",
            border_style="yellow",
        ))

        import socket as _socket
        import time as _time

        def _resolve_domain_ip(hostname: str) -> str | None:
            try:
                return _socket.gethostbyname(hostname)
            except Exception:
                return None

        def _resolve_domain_ips(hostname: str) -> set[str]:
            try:
                _, _, ips = _socket.gethostbyname_ex(hostname)
                return {ip for ip in ips if ip}
            except Exception:
                return set()

        def _endpoint_match(hostname: str, endpoint: str) -> tuple[bool, str | None, set[str]]:
            resolved_ip = _resolve_domain_ip(hostname)
            if not resolved_ip:
                return (False, None, set())

            # If endpoint is already an IP address, direct equality is sufficient.
            if endpoint.replace(".", "").isdigit():
                return (resolved_ip == endpoint, resolved_ip, {endpoint})

            endpoint_ips = _resolve_domain_ips(endpoint)
            if not endpoint_ips:
                return (False, resolved_ip, set())
            return (resolved_ip in endpoint_ips, resolved_ip, endpoint_ips)

        domain_name = ""
        print("\n[bold]DNS configuration:[/bold]")
        while True:
            if default_domain:
                domain_name = Prompt.ask("Hostname mapped to this load balancer", default=default_domain).strip()
            else:
                domain_name = Prompt.ask("Hostname mapped to this load balancer").strip()
            domain_name = domain_name.replace("https://", "").replace("http://", "").strip().rstrip("/")

            if not domain_name:
                print("[yellow]Hostname is required to continue.[/yellow]")
                continue

            print(f"  Checking DNS resolution for [cyan]{domain_name}[/cyan]...")
            resolved_ip = None
            for attempt in range(1, 11):
                matches, resolved_ip, endpoint_ips = _endpoint_match(domain_name, public_endpoint)
                if matches:
                    print(f"  [green]DNS resolved correctly: {domain_name} -> {resolved_ip}[/green]")
                    _update_cluster_metadata(name, {
                        "ingress_traefik_domain": domain_name,
                        "public_hostname": domain_name,
                    })
                    print(f"  [green]Saved ingress-traefik domain in kubeadm metadata:[/green] {domain_name}")
                    break
                if resolved_ip:
                    expected = public_endpoint
                    if endpoint_ips:
                        expected = ", ".join(sorted(endpoint_ips))
                    print(
                        f"  Attempt {attempt}/10: [yellow]{domain_name} resolves to {resolved_ip}, "
                        f"expected {expected}[/yellow]"
                    )
                else:
                    print(f"  Attempt {attempt}/10: [yellow]DNS not resolved yet for {domain_name}[/yellow]")
                if attempt < 10:
                    _time.sleep(5)

            if resolved_ip:
                matches, _, _ = _endpoint_match(domain_name, public_endpoint)
            else:
                matches = False
            if matches:
                break

            if not Confirm.ask("DNS not ready. Retry with a hostname?", default=True):
                print("[red]DNS validation is required before persisting hostname.[/red]")
                raise typer.Exit(1)

        print(Panel(
            f"[green]Traefik ingress + load balancer enabled for cluster '{name}'![/green]\n\n"
            f"  LB Endpoint:    [bold]{public_endpoint}[/bold]\n"
            f"  HTTP:           {public_endpoint}:80  → NodePort {http_nodeport}\n"
            f"  HTTPS:          {public_endpoint}:443 → NodePort {https_nodeport}\n\n"
            f"  Remove with:    clouder kubeadm disable-ingress-traefik {name}",
            title="Ingress Traefik + LB Ready",
        ))

    @kubeadm_app.command("disable-ingress-traefik")
    def kubeadm_disable_ingress_traefik(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
        user: str | None = typer.Option(None, "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    ):
        """Remove load balancer and Traefik from the cluster.

        Azure: deletes Azure Load Balancer + public IP and uninstalls Traefik.
        AWS: uninstalls Traefik (Kubernetes LoadBalancer service is removed with it).
        """
        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        if cloud not in {"azure", "aws"}:
            typer.echo("Load balancer commands are currently only supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        metadata = _load_cluster_metadata(name) or {}
        resolved_user = user or metadata.get("admin_username") or ("azureuser" if cloud == "azure" else "ubuntu")
        rg = master.get("resource_group")
        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        lb_name = f"{name}-lb"
        lb_ip_name = f"{name}-lb-ip"

        print(f"\n[bold]Resources to delete:[/bold]")
        if cloud == "azure":
            typer.echo(f"  Azure LB:       {lb_name}")
            typer.echo(f"  LB Public IP:   {lb_ip_name}")
        else:
            typer.echo("  AWS LB:         Managed by Kubernetes Service/Traefik")
        typer.echo(f"  Traefik:        {_TRAEFIK_NAMESPACE} namespace")

        if not force:
            if not Confirm.ask(f"\nRemove Traefik + load balancer from cluster '{name}'?", default=False):
                raise typer.Abort()

        if cloud == "azure":
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

        # Step 3: Remove Traefik from the cluster
        print("[bold]Removing Traefik from cluster...[/bold]")
        rc = _ssh_cmd_stream(
            master["ip"], resolved_user, key_path,
            f"helm uninstall traefik --namespace {_TRAEFIK_NAMESPACE} 2>/dev/null || true; "
            f"kubectl delete namespace {_TRAEFIK_NAMESPACE} 2>/dev/null || true",
        )
        if rc == 0:
            print("  [green]Traefik removed.[/green]")
        else:
            print("  [dim]Traefik was not installed or already removed.[/dim]")

        print(f"\n[green]Traefik + load balancer removed from cluster '{name}'.[/green]")
