"""Clouder CLI - kubeadm repair-load-balancer command.

Re-provision the cluster load balancer used by the Traefik ingress controller.

Use this when ``clouder kubeadm info`` reports "No load balancer address
detected yet" even though the cluster previously had a working endpoint — for
example after the AWS NLB or the Azure Load Balancer was deleted, or after the
Traefik ``LoadBalancer`` service lost its external address.

This command does not reinstall the cluster or the plane; it only re-applies
the Traefik LoadBalancer configuration and re-creates the cloud load balancer
(idempotent, safe to run multiple times).
"""

from __future__ import annotations

import time

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ...util.utils import SSH_FOLDER
from ...util.wait import wait_with_spinner
from ._helpers import (
    _load_cluster_metadata,
    _print_step_header,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _ssh_cmd_stream,
    _update_cluster_metadata,
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
)
from .ingress_traefik import (
    _SCRIPT_INSTALL_TRAEFIK,
    _SCRIPT_INSTALL_TRAEFIK_AWS,
    _TRAEFIK_NAMESPACE,
    _ensure_aws_node_provider_ids,
)


def _read_traefik_nodeports(
    master_ip: str, user: str, key_path: str
) -> tuple[int, int]:
    """Read the Traefik web/websecure NodePorts from the live service."""
    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl -n {_TRAEFIK_NAMESPACE} get svc traefik "
        "-o jsonpath='{.spec.ports[?(@.name==\"web\")].nodePort} "
        "{.spec.ports[?(@.name==\"websecure\")].nodePort}'",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print("[red]Could not read Traefik NodePorts. Is Traefik installed?[/red]")
        if result.stderr:
            typer.echo(result.stderr)
        raise typer.Exit(1)
    ports = result.stdout.strip().strip("'").split()
    if len(ports) < 2:
        print(f"[red]Expected 2 NodePorts, got: {result.stdout.strip()}[/red]")
        raise typer.Exit(1)
    return int(ports[0]), int(ports[1])


def _current_traefik_lb_endpoint(master_ip: str, user: str, key_path: str) -> str:
    """Return the current Traefik LoadBalancer endpoint, or '' if none."""
    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        (
            f"kubectl -n {_TRAEFIK_NAMESPACE} get svc traefik "
            "-o jsonpath='{.status.loadBalancer.ingress[0].hostname} "
            "{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true"
        ),
        check=False,
    )
    raw = (result.stdout or "").strip().strip("'")
    parts = [p for p in raw.split() if p]
    return parts[0] if parts else ""


def _wait_for_aws_endpoint(
    master_ip: str, user: str, key_path: str, attempts: int = 90
) -> str:
    """Poll the Traefik service until the AWS LoadBalancer endpoint appears."""
    for _ in range(attempts):
        endpoint = _current_traefik_lb_endpoint(master_ip, user, key_path)
        if endpoint:
            return endpoint
        time.sleep(5)
    return ""


def _repair_aws_load_balancer(
    name: str,
    master: dict,
    workers: list[dict],
    metadata: dict,
    resolved_user: str,
    key_path: str,
    http_nodeport: int,
    https_nodeport: int,
    prior_endpoint: str = "",
) -> str:
    """Ensure AWS networking prerequisites and (re)use the NLB endpoint.

    The AWS NLB is managed by the cloud controller via the Traefik
    ``LoadBalancer`` service. As long as the service is not deleted, AWS keeps
    the same endpoint, so this repair preserves any previously assigned
    address. ``prior_endpoint`` is the endpoint discovered before the repair.
    """
    aws_region = str(metadata.get("region") or master.get("region") or "")

    if prior_endpoint:
        print(
            "  [green]Existing AWS load balancer detected — preserving endpoint:[/green] "
            f"{prior_endpoint}"
        )

    # Open required ports on worker instance security groups for NLB health checks.
    instance_ids = [str(w.get("instance_id") or "").strip() for w in workers]
    instance_ids = [iid for iid in instance_ids if iid]
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
                "  [green]Ensured worker security-group ingress:[/green] "
                f"ports {sg_result.get('ports', [])}"
            )
        except Exception as exc:  # noqa: BLE001
            print(
                "  [yellow]Could not pre-open AWS security-group ingress automatically; "
                f"continuing: {exc}[/yellow]"
            )

    # Reconcile Kubernetes node providerIDs so the cloud controller can manage the LB.
    cluster_instance_ids = [str(master.get("instance_id") or "").strip()] + instance_ids
    cluster_instance_ids = [iid for iid in cluster_instance_ids if iid]
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
        except Exception as exc:  # noqa: BLE001
            print(
                "  [yellow]Could not reconcile node providerIDs automatically; "
                f"continuing: {exc}[/yellow]"
            )

    # Reuse the existing endpoint if AWS still reports one; otherwise wait for
    # the controller to (re)provision it.
    endpoint = _current_traefik_lb_endpoint(master["ip"], resolved_user, key_path)
    if not endpoint:
        endpoint = _wait_for_aws_endpoint(master["ip"], resolved_user, key_path)
    if not endpoint:
        print("[red]Timed out waiting for the Traefik LoadBalancer endpoint on AWS.[/red]")
        _ssh_cmd_stream(
            master["ip"],
            resolved_user,
            key_path,
            f"kubectl -n {_TRAEFIK_NAMESPACE} get svc traefik -o wide; "
            f"kubectl -n {_TRAEFIK_NAMESPACE} describe svc traefik",
        )
        raise typer.Exit(1)
    if prior_endpoint and endpoint != prior_endpoint:
        print(
            f"  [yellow]Note:[/yellow] endpoint changed from "
            f"{prior_endpoint} to {endpoint}; update DNS if a domain is mapped."
        )
    return endpoint


def _repair_azure_load_balancer(
    name: str,
    context_id: str,
    rg: str,
    master: dict,
    workers: list[dict],
    http_nodeport: int,
    https_nodeport: int,
) -> str:
    """Re-create (create-or-update) the Azure Load Balancer and NIC wiring."""
    from ...cloud.azure.api import (
        _get_network_client,
        add_nic_to_lb_backend_pool,
        list_azure_vms,
    )

    all_vms = list_azure_vms(resource_group=rg, subscription_id=context_id)
    location = next(
        (v["location"] for v in all_vms if v["name"] == master["name"]), None
    )
    if not location:
        typer.echo("Could not determine cluster location.", err=True)
        raise typer.Exit(1)

    lb_name = f"{name}-lb"
    lb_ip_name = f"{name}-lb-ip"
    network_client = _get_network_client(context_id)

    # Inspect for a prior load balancer public IP and reuse it so the existing
    # address is preserved. A Static public IP keeps the same address through
    # create-or-update, so re-applying it does not change the IP.
    existing_ip_addr = None
    try:
        existing_pip = network_client.public_ip_addresses.get(rg, lb_ip_name)
        existing_ip_addr = existing_pip.ip_address
    except Exception:  # noqa: BLE001
        existing_ip_addr = None
    if existing_ip_addr:
        print(
            "  [green]Reusing existing load balancer public IP:[/green] "
            f"{existing_ip_addr} ({lb_ip_name})"
        )
    else:
        print(
            f"  [dim]No existing load balancer public IP ({lb_ip_name}); "
            "allocating a new static IP.[/dim]"
        )

    ip_poller = network_client.public_ip_addresses.begin_create_or_update(
        rg,
        lb_ip_name,
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

    frontend_name = "lb-frontend"
    backend_pool_name = "lb-backend-pool"
    base = (
        f"/subscriptions/{context_id}/resourceGroups/{rg}/providers/Microsoft.Network"
        f"/loadBalancers/{lb_name}"
    )
    frontend_id = f"{base}/frontendIPConfigurations/{frontend_name}"
    backend_pool_ref = f"{base}/backendAddressPools/{backend_pool_name}"
    probe_base = f"{base}/probes"

    lb_params = {
        "location": location,
        "sku": {"name": "Standard"},
        "frontend_ip_configurations": [
            {"name": frontend_name, "public_ip_address": {"id": public_ip.id}}
        ],
        "backend_address_pools": [{"name": backend_pool_name}],
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

    lb_poller = network_client.load_balancers.begin_create_or_update(
        rg, lb_name, lb_params
    )
    lb = wait_with_spinner(
        lambda: lb_poller.result(),
        f"Creating load balancer {lb_name}",
    )

    backend_pool_id = next(
        (pool.id for pool in (lb.backend_address_pools or []) if pool.name == backend_pool_name),
        None,
    )
    print(f"  Load Balancer:  [cyan]{lb_name}[/cyan]")
    print(f"  Rules:          80 → :{http_nodeport},  443 → :{https_nodeport}")

    for worker in workers:
        nic_name = f"{worker['name']}-nic"
        typer.echo(f"  Adding {nic_name}...")
        try:
            add_nic_to_lb_backend_pool(
                rg, nic_name, backend_pool_id, subscription_id=context_id
            )
            print(f"  [green]{nic_name} added.[/green]")
        except Exception as exc:  # noqa: BLE001
            print(f"  [red]Failed to add {nic_name}: {exc}[/red]")

    if not public_ip.ip_address:
        print(f"\n[bold]Waiting for load balancer IP assignment for {lb_ip_name}...[/bold]")
        for _ in range(60):
            refreshed = network_client.public_ip_addresses.get(rg, lb_ip_name)
            if refreshed and refreshed.ip_address:
                public_ip = refreshed
                break
            time.sleep(2)
    if not public_ip.ip_address:
        print(f"[red]Timed out waiting for load balancer IP assignment for {lb_ip_name}.[/red]")
        raise typer.Exit(1)

    return str(public_ip.ip_address)


def register(kubeadm_app: typer.Typer):
    """Register the repair-load-balancer command on the given Typer app."""

    @kubeadm_app.command("repair-load-balancer")
    def kubeadm_repair_load_balancer(
        name: str | None = typer.Argument(
            None, help="Cluster name. If omitted, uses default kubeadm cluster."
        ),
        cloud: str | None = typer.Option(
            None,
            "--cloud",
            help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud.",
        ),
        user: str | None = typer.Option(
            None, "--admin-user", "-u", help="SSH username on the VMs."
        ),
        key: str = typer.Option(
            None, "--key", "-i", help="SSH key name (from ~/.ssh/)."
        ),
        reinstall: bool = typer.Option(
            False,
            "--reinstall",
            help="Re-run the Traefik Helm install/upgrade before re-provisioning the load balancer.",
        ),
        force: bool = typer.Option(
            False, "--force", "-f", help="Skip confirmation prompt."
        ),
    ):
        """Repair the Traefik ingress load balancer for a cluster.

        Re-provisions the cloud load balancer when the ingress endpoint is
        missing (AWS NLB / Azure Load Balancer). Optionally re-applies the
        Traefik LoadBalancer service with ``--reinstall``.
        """
        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        if cloud not in {"azure", "aws"}:
            typer.echo(
                "Load balancer repair is currently only supported for Azure and AWS.",
                err=True,
            )
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        workers = cluster["workers"]
        metadata = _load_cluster_metadata(name) or {}
        resolved_user = (
            user
            or metadata.get("admin_username")
            or ("azureuser" if cloud == "azure" else "ubuntu")
        )
        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)
        rg = master.get("resource_group")

        if cloud == "azure" and not workers:
            typer.echo(
                "No worker nodes found. Load balancer needs at least one worker.",
                err=True,
            )
            raise typer.Exit(1)

        # ----- Pre-check: kubectl on master -----
        check = _ssh_cmd(
            master["ip"], resolved_user, key_path, "which kubectl", check=False
        )
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

        # ----- Diagnose current state -----
        current_endpoint = _current_traefik_lb_endpoint(
            master["ip"], resolved_user, key_path
        )
        diagnosis = (
            f"[green]current endpoint: {current_endpoint}[/green]"
            if current_endpoint
            else "[yellow]no load balancer endpoint detected[/yellow]"
        )
        workers_display = ", ".join(w["name"] for w in workers) if workers else "(none)"
        if cloud == "aws":
            steps = (
                "  1. Re-apply Traefik LoadBalancer service (optional --reinstall)\n"
                "  2. Ensure worker security groups + node providerIDs\n"
                "  3. Reuse the existing AWS NLB endpoint (or wait for re-provision)"
            )
        else:
            steps = (
                "  1. Re-apply Traefik service (optional --reinstall)\n"
                "  2. Reuse the existing Azure LB public IP (create-or-update)\n"
                "  3. Re-wire worker NICs to the backend pool"
            )
        print(
            Panel(
                f"[bold]Cluster:[/bold]  {name}\n"
                f"[bold]Cloud:[/bold]    {cloud}\n"
                f"[bold]Masters:[/bold]  {master['name']} ({master['ip']})\n"
                f"[bold]Workers:[/bold]  {workers_display}\n"
                f"[bold]Status:[/bold]   {diagnosis}\n\n"
                f"This will:\n{steps}",
                title="Repair Load Balancer (Traefik)",
            )
        )

        if not force and not Confirm.ask("\nProceed?", default=True):
            raise typer.Abort()

        total_steps = 3
        step = 1

        # ----- Optionally re-apply the Traefik LoadBalancer service -----
        if reinstall:
            _print_step_header(step, total_steps, "Re-applying Traefik ingress controller")
            install_script = (
                _SCRIPT_INSTALL_TRAEFIK if cloud == "azure" else _SCRIPT_INSTALL_TRAEFIK_AWS
            )
            rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, install_script)
            if rc != 0:
                print("[red]Failed to re-apply Traefik.[/red]")
                raise typer.Exit(1)
        step += 1

        # ----- Read NodePorts -----
        http_nodeport, https_nodeport = _read_traefik_nodeports(
            master["ip"], resolved_user, key_path
        )
        print(f"  HTTP  NodePort: [cyan]{http_nodeport}[/cyan]")
        print(f"  HTTPS NodePort: [cyan]{https_nodeport}[/cyan]")

        # ----- Re-provision the cloud load balancer -----
        if cloud == "aws":
            _print_step_header(step, total_steps, "Re-provisioning AWS load balancer")
            public_endpoint = _repair_aws_load_balancer(
                name=name,
                master=master,
                workers=workers,
                metadata=metadata,
                resolved_user=resolved_user,
                key_path=key_path,
                http_nodeport=http_nodeport,
                https_nodeport=https_nodeport,
                prior_endpoint=current_endpoint,
            )
        else:
            _print_step_header(step, total_steps, "Re-provisioning Azure load balancer")
            public_endpoint = _repair_azure_load_balancer(
                name=name,
                context_id=context_id,
                rg=rg,
                master=master,
                workers=workers,
                http_nodeport=http_nodeport,
                https_nodeport=https_nodeport,
            )

        # ----- Persist + report -----
        _update_cluster_metadata(
            name,
            {"loadbalancer_ready": True, "loadbalancer_endpoint": public_endpoint},
        )
        saved_domain = str(metadata.get("ingress_traefik_domain") or "").strip()

        endpoint_label = "LB Endpoint" if cloud == "aws" else "LB IP"
        lines = [
            f"[green]Load balancer repaired for cluster '{name}'.[/green]",
            "",
            f"  {endpoint_label}:    [bold]{public_endpoint}[/bold]",
            f"  HTTP:           {public_endpoint}:80  → NodePort {http_nodeport}",
            f"  HTTPS:          {public_endpoint}:443 → NodePort {https_nodeport}",
        ]
        if saved_domain:
            lines.append("")
            lines.append(f"  Mapped domain:  [cyan]{saved_domain}[/cyan]")
            lines.append(
                f"  [dim]Verify DNS still points {saved_domain} → {public_endpoint}[/dim]"
            )
        lines.append("")
        lines.append(f"  Verify: [bold cyan]clouder kubeadm info {name}[/bold cyan]")
        print(Panel("\n".join(lines), title="Load Balancer Ready", border_style="green"))
