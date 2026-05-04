"""Clouder CLI - kubeadm scale command."""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ..ctx import get_current_context
from ...util.utils import SSH_FOLDER

from ._helpers import (
    _SCRIPT_PREREQS,
    _SCRIPT_WORKER_FEATURE_GATE,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _save_cluster_metadata,
    _ssh_cmd,
    _ssh_cmd_stream,
    _update_cluster_metadata,
)


def register(kubeadm_app: typer.Typer):
    """Register the scale command on the given Typer app."""

    @kubeadm_app.command("scale")
    def kubeadm_scale(
        name: str = typer.Argument(..., help="Cluster name."),
        workers: int = typer.Option(..., "--workers", "-w", help="Desired number of worker nodes."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    ):
        """Scale the number of worker nodes in an existing kubeadm cluster.

        Compares the desired worker count with the current count, then:
        - Scale up: creates new VMs, installs prerequisites, joins them to the cluster.
        - Scale down: drains and deletes the highest-numbered worker nodes.
        """
        if workers < 0:
            typer.echo("Worker count must be >= 0.", err=True)
            raise typer.Exit(1)

        (cloud, context_id) = get_current_context()
        if cloud != "azure":
            typer.echo("Kubeadm scale is currently only supported for Azure.", err=True)
            raise typer.Exit(1)

        # --- Load cluster metadata (or discover from Azure) ---
        metadata = _load_cluster_metadata(name)
        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        current_workers = cluster["workers"]
        current_count = len(current_workers)

        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        if workers == current_count:
            print(f"Cluster '{name}' already has {current_count} worker(s). Nothing to do.")
            raise typer.Exit(0)

        # --- Resolve cluster parameters (from metadata or Azure discovery) ---
        if metadata:
            resource_group = metadata["resource_group"]
            region = metadata["region"]
            node_size = metadata["workers"][0]["vm_size"] if metadata.get("workers") else "Standard_B2s"
            admin_username = metadata.get("admin_username", user)
            ssh_key_name = metadata.get("ssh_key_name")
            image_publisher = metadata.get("image_publisher", "Canonical")
            image_offer = metadata.get("image_offer", "0001-com-ubuntu-server-jammy")
            image_sku = metadata.get("image_sku", "22_04-lts-gen2")
            subnet_id = metadata.get("networking", {}).get("subnet_id")
            nsg_id = metadata.get("networking", {}).get("nsg_id")
        else:
            # Discover from Azure (no local metadata available)
            resource_group = master["resource_group"]
            admin_username = user
            ssh_key_name = None
            from ...cloud.azure.api import _get_compute_client
            compute_client = _get_compute_client(context_id)
            master_vm = compute_client.virtual_machines.get(resource_group, master["name"])
            region = master_vm.location
            if current_workers:
                worker_vm = compute_client.virtual_machines.get(resource_group, current_workers[0]["name"])
                node_size = worker_vm.hardware_profile.vm_size
            else:
                node_size = master_vm.hardware_profile.vm_size
            image_ref = master_vm.storage_profile.image_reference
            image_publisher = image_ref.publisher
            image_offer = image_ref.offer
            image_sku = image_ref.sku
            from ...cloud.azure.api import _get_network_client
            network_client = _get_network_client(context_id)
            master_nic_id = master_vm.network_profile.network_interfaces[0].id
            master_nic_name = master_nic_id.split("/")[-1]
            master_nic = network_client.network_interfaces.get(resource_group, master_nic_name)
            subnet_id = master_nic.ip_configurations[0].subnet.id
            nsg_id = master_nic.network_security_group.id if master_nic.network_security_group else None

        direction = "up" if workers > current_count else "down"
        diff = abs(workers - current_count)

        # --- Show plan ---
        print(Panel(
            f"[bold]Cluster:[/bold]    {name}\n"
            f"[bold]Master:[/bold]     {master['name']} ({master['ip']})\n"
            f"[bold]Current workers:[/bold] {current_count}\n"
            f"[bold]Desired workers:[/bold] {workers}\n"
            f"[bold]Action:[/bold]     Scale {direction} by {diff} node(s)\n"
            f"[bold]Node size:[/bold]  {node_size}\n"
            f"[bold]Region:[/bold]     {region}",
            title=f"Scale {'Up' if direction == 'up' else 'Down'}",
        ))

        if not force:
            if not Confirm.ask(f"\nProceed with scale {direction}?", default=True):
                raise typer.Abort()

        if direction == "up":
            _scale_up(
                cluster_name=name,
                context_id=context_id,
                master=master,
                current_workers=current_workers,
                new_count=workers,
                resource_group=resource_group,
                region=region,
                node_size=node_size,
                admin_username=admin_username,
                ssh_key_name=ssh_key_name,
                image_publisher=image_publisher,
                image_offer=image_offer,
                image_sku=image_sku,
                subnet_id=subnet_id,
                nsg_id=nsg_id,
                key_path=key_path,
                user=user,
                metadata=metadata,
            )
        else:
            _scale_down(
                cluster_name=name,
                context_id=context_id,
                master=master,
                current_workers=current_workers,
                new_count=workers,
                resource_group=resource_group,
                key_path=key_path,
                user=user,
                metadata=metadata,
            )


def _scale_up(
    cluster_name, context_id, master, current_workers, new_count,
    resource_group, region, node_size, admin_username, ssh_key_name,
    image_publisher, image_offer, image_sku, subnet_id, nsg_id,
    key_path, user, metadata,
):
    """Add new worker nodes to the cluster."""
    from ...cloud.azure.api import create_azure_vm

    current_count = len(current_workers)
    existing_numbers = []
    for w in current_workers:
        parts = w["name"].rsplit("-", 1)
        if parts[-1].isdigit():
            existing_numbers.append(int(parts[-1]))
    next_start = max(existing_numbers) + 1 if existing_numbers else 1

    nodes_to_add = new_count - current_count
    new_worker_names = [f"{cluster_name}-node-{next_start + i}" for i in range(nodes_to_add)]

    # --- Read SSH public key ---
    ssh_public_key = None
    if ssh_key_name:
        pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
        if pub_path.exists():
            ssh_public_key = pub_path.read_text().strip()

    # --- Step 1: Create new VMs ---
    print(f"\n[bold]Step 1/4: Creating {nodes_to_add} new worker VM(s)...[/bold]")
    new_workers = []
    for vm_name in new_worker_names:
        typer.echo(f"  Creating {vm_name} ({node_size})...")
        result = create_azure_vm(
            resource_group=resource_group,
            vm_name=vm_name,
            location=region,
            vm_size=node_size,
            admin_username=admin_username,
            ssh_public_key=ssh_public_key,
            image_publisher=image_publisher,
            image_offer=image_offer,
            image_sku=image_sku,
            subnet_id=subnet_id,
            nsg_id=nsg_id,
            subscription_id=context_id,
        )
        print(f"  [green]{vm_name} created - IP: {result.get('public_ip', 'N/A')}[/green]")
        new_workers.append({"name": vm_name, "ip": result.get("public_ip"), "resource_group": resource_group})

    # --- Step 2: Install prerequisites on new workers ---
    print(f"\n[bold]Step 2/4: Installing prerequisites on new workers...[/bold]")
    for worker in new_workers:
        print(f"  [cyan]{worker['name']}[/cyan] ({worker['ip']})...")
        rc = _ssh_cmd_stream(worker["ip"], user, key_path, _SCRIPT_PREREQS)
        if rc != 0:
            print(f"  [red]Failed on {worker['name']}[/red]")
            raise typer.Exit(1)
        print(f"  [green]{worker['name']} done.[/green]")

    # --- Step 3: Get fresh join command from master ---
    print(f"\n[bold]Step 3/4: Getting join command from master...[/bold]")
    result = _ssh_cmd(master["ip"], user, key_path,
                      "sudo kubeadm token create --print-join-command", check=False)
    if result.returncode != 0:
        typer.echo(result.stderr)
        print("[red]Failed to create join token on master.[/red]")
        raise typer.Exit(1)

    join_command = result.stdout.strip()
    if not join_command or "kubeadm join" not in join_command:
        print("[red]Could not get a valid join command from master.[/red]")
        typer.echo(f"Output: {result.stdout}")
        raise typer.Exit(1)
    print(f"  [dim]Join command: {join_command}[/dim]")

    # --- Step 4: Join new workers + enable feature gates ---
    print(f"\n[bold]Step 4/4: Joining new workers and enabling feature gates...[/bold]")
    for worker in new_workers:
        print(f"  [cyan]{worker['name']}[/cyan] joining...")
        rc = _ssh_cmd_stream(worker["ip"], user, key_path, f"sudo {join_command}")
        if rc != 0:
            print(f"  [red]Join failed on {worker['name']}[/red]")
            raise typer.Exit(1)
        print(f"  [green]{worker['name']} joined.[/green]")

        print(f"  [cyan]{worker['name']}[/cyan] enabling feature gates...")
        rc = _ssh_cmd_stream(worker["ip"], user, key_path, _SCRIPT_WORKER_FEATURE_GATE)
        if rc != 0:
            print(f"  [yellow]Feature gate setup failed on {worker['name']} (non-fatal)[/yellow]")
        else:
            print(f"  [green]{worker['name']} feature gates enabled.[/green]")

    # --- Update metadata ---
    if metadata:
        all_workers = metadata.get("workers", []) + [
            {"name": w["name"], "vm_size": node_size, "ip": w["ip"]}
            for w in new_workers
        ]
        _update_cluster_metadata(cluster_name, {"workers": all_workers})
    else:
        cluster = _resolve_cluster_vms(cluster_name)
        _save_cluster_metadata(cluster_name, {
            "name": cluster_name,
            "cloud": "azure",
            "subscription_id": context_id,
            "resource_group": resource_group,
            "region": region,
            "admin_username": user,
            "master": {
                "name": master["name"],
                "vm_size": "unknown",
                "ip": master["ip"],
            },
            "workers": [
                {"name": w["name"], "vm_size": node_size, "ip": w["ip"]}
                for w in cluster["workers"]
            ],
        })

    # --- Done ---
    print(Panel(
        f"[green]Scaled up cluster '{cluster_name}' to {new_count} workers.[/green]\n\n"
        f"  New nodes: {', '.join(w['name'] for w in new_workers)}\n"
        f"  Check:     clouder kubectl {cluster_name} get nodes",
        title="Scale Up Complete",
    ))


def _scale_down(
    cluster_name, context_id, master, current_workers, new_count,
    resource_group, key_path, user, metadata,
):
    """Remove worker nodes from the cluster (highest-numbered first)."""
    from ...cloud.azure.api import delete_azure_vm

    current_count = len(current_workers)
    nodes_to_remove = current_count - new_count

    def _worker_number(w):
        parts = w["name"].rsplit("-", 1)
        return int(parts[-1]) if parts[-1].isdigit() else 0

    sorted_workers = sorted(current_workers, key=_worker_number, reverse=True)
    victims = sorted_workers[:nodes_to_remove]

    print(f"\n[bold]Removing {nodes_to_remove} worker(s): {', '.join(v['name'] for v in victims)}[/bold]")

    # --- Step 1: Drain and remove nodes from Kubernetes ---
    print(f"\n[bold]Step 1/2: Draining and removing nodes from Kubernetes...[/bold]")
    for victim in victims:
        k8s_node_name = victim["name"]
        print(f"  [cyan]Draining {k8s_node_name}...[/cyan]")
        _ssh_cmd_stream(
            master["ip"], user, key_path,
            f"kubectl drain {k8s_node_name} --ignore-daemonsets --delete-emptydir-data --force --timeout=120s 2>&1 || true",
        )
        print(f"  [cyan]Removing {k8s_node_name} from cluster...[/cyan]")
        _ssh_cmd(
            master["ip"], user, key_path,
            f"kubectl delete node {k8s_node_name} --ignore-not-found=true",
            check=False,
        )
        print(f"  [green]{k8s_node_name} drained and removed.[/green]")

    # --- Step 2: Delete Azure resources (VM + disks, NICs, IPs) ---
    print(f"\n[bold]Step 2/2: Deleting Azure resources...[/bold]")
    rg = resource_group
    for victim in victims:
        vm_name = victim["name"]
        typer.echo(f"  Deleting VM: {vm_name} (with disks, NIC, IP)...")
        try:
            delete_azure_vm(rg, vm_name, subscription_id=context_id)
            print(f"  [green]VM deleted: {vm_name} + associated resources[/green]")
        except Exception as e:
            print(f"  [red]Failed to delete VM {vm_name}: {e}[/red]")

    # --- Update metadata ---
    victim_names = {v["name"] for v in victims}
    if metadata:
        remaining_workers = [
            w for w in metadata.get("workers", [])
            if w["name"] not in victim_names
        ]
        _update_cluster_metadata(cluster_name, {"workers": remaining_workers})
    else:
        cluster = _resolve_cluster_vms(cluster_name)
        _save_cluster_metadata(cluster_name, {
            "name": cluster_name,
            "cloud": "azure",
            "subscription_id": context_id,
            "resource_group": resource_group,
            "admin_username": user,
            "master": {
                "name": master["name"],
                "vm_size": "unknown",
                "ip": master["ip"],
            },
            "workers": [
                {"name": w["name"], "vm_size": "unknown", "ip": w["ip"]}
                for w in cluster["workers"]
            ],
        })

    # --- Done ---
    print(Panel(
        f"[green]Scaled down cluster '{cluster_name}' to {new_count} workers.[/green]\n\n"
        f"  Removed: {', '.join(v['name'] for v in victims)}\n"
        f"  Check:   clouder kubectl {cluster_name} get nodes",
        title="Scale Down Complete",
    ))
