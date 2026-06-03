"""Clouder CLI - kubeadm scale command."""

import json
import time

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ..ctx import get_current_context
from ...util.utils import SSH_FOLDER

from ._helpers import (
    _SCRIPT_PREREQS,
    _SCRIPT_WORKER_FEATURE_GATE,
    resolve_kubeadm_cluster_name,
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
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        workers: int = typer.Option(..., "--workers", "-w", help="Desired number of worker nodes."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    ):
        """Scale the number of worker nodes in an existing kubeadm cluster.

        Compares the desired worker count with the current count, then:
        - Scale up: creates new VMs, installs prerequisites, joins them to the cluster.
                - Scale down: iteratively removes the least-loaded worker with explicit
                    cordon, pod deletion, and node/VM deletion completion waits.
        """
        if workers < 0:
            typer.echo("Worker count must be >= 0.", err=True)
            raise typer.Exit(1)

        name = resolve_kubeadm_cluster_name(name)
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
            f"[bold]Masters:[/bold]     {master['name']} ({master['ip']})\n"
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
    """Remove worker nodes from the cluster (least running pods first, one by one)."""
    from ...cloud.azure.api import delete_azure_vm, list_azure_vms

    current_count = len(current_workers)
    nodes_to_remove = current_count - new_count

    def _worker_number(w):
        parts = w["name"].rsplit("-", 1)
        return int(parts[-1]) if parts[-1].isdigit() else 0

    def _running_pod_counts(node_names: list[str]) -> dict[str, int]:
        """Return running pod counts per node using kubectl on the master."""
        cmd = (
            "kubectl get pods -A --field-selector=status.phase=Running "
            "-o custom-columns=NODE:.spec.nodeName --no-headers 2>/dev/null || true"
        )
        result = _ssh_cmd(master["ip"], user, key_path, cmd, check=False)
        counts = {node: 0 for node in node_names}
        for line in result.stdout.strip().splitlines():
            node = line.strip()
            if node in counts:
                counts[node] += 1
        return counts

    remaining_workers = list(current_workers)
    removed_workers = []

    print(f"\n[bold]Scale-down plan: remove {nodes_to_remove} worker node(s), one by one.[/bold]")

    for iteration in range(1, nodes_to_remove + 1):
        candidate_names = [w["name"] for w in remaining_workers]
        pod_counts = _running_pod_counts(candidate_names)

        victims_sorted = sorted(
            remaining_workers,
            key=lambda w: (pod_counts.get(w["name"], 0), -_worker_number(w)),
        )
        victim = victims_sorted[0]
        k8s_node_name = victim["name"]
        running_pods = pod_counts.get(k8s_node_name, 0)

        print(f"\n[bold]Node removal {iteration}/{nodes_to_remove}[/bold]")
        print("  Candidate running pod counts:")
        for w in sorted(remaining_workers, key=lambda x: _worker_number(x)):
            print(f"    - {w['name']}: {pod_counts.get(w['name'], 0)} pod(s)")
        print(
            f"  [cyan]Selected node:[/cyan] {k8s_node_name} "
            f"([cyan]{running_pods}[/cyan] running pod(s), least-loaded priority)"
        )

        # --- Step 1: Mark unschedulable ---
        print(f"\n  [bold]Step 1/4:[/bold] Mark node as unschedulable ({k8s_node_name})")
        cordon_result = _ssh_cmd(
            master["ip"], user, key_path,
            f"kubectl cordon {k8s_node_name}",
            check=False,
        )
        if cordon_result.returncode != 0 and "already cordoned" not in (cordon_result.stdout + cordon_result.stderr):
            print(f"  [red]Failed to cordon {k8s_node_name}.[/red]")
            if cordon_result.stderr.strip():
                print(f"  [dim]{cordon_result.stderr.strip()}[/dim]")
            raise typer.Exit(1)

        cordoned = False
        for _ in range(24):
            status = _ssh_cmd(
                master["ip"], user, key_path,
                f"kubectl get node {k8s_node_name} -o jsonpath='{{.spec.unschedulable}}' 2>/dev/null || true",
                check=False,
            ).stdout.strip().lower()
            if status == "true":
                cordoned = True
                break
            time.sleep(2)
        if not cordoned:
            print(f"  [red]Node {k8s_node_name} did not become unschedulable in time.[/red]")
            raise typer.Exit(1)
        print(f"  [green]{k8s_node_name} is unschedulable.[/green]")

        # --- Step 2: Delete all pods on the node ---
        print(f"\n  [bold]Step 2/4:[/bold] Delete all pods from {k8s_node_name}")
        _ssh_cmd(
            master["ip"], user, key_path,
            (
                f"kubectl delete pod -A --field-selector spec.nodeName={k8s_node_name} "
                "--ignore-not-found=true --grace-period=30 --force"
            ),
            check=False,
        )

        # --- Step 3: Wait until evictable pods are gone, then remove K8s node object ---
        print(f"\n  [bold]Step 3/4:[/bold] Wait for pod termination and remove Kubernetes node object")
        pods_gone = False
        for poll in range(1, 61):
            pods_result = _ssh_cmd(
                master["ip"], user, key_path,
                (
                    f"kubectl get pods -A --field-selector spec.nodeName={k8s_node_name},"
                    "status.phase!=Succeeded,status.phase!=Failed -o json 2>/dev/null || true"
                ),
                check=False,
            )
            remaining = -1
            try:
                pods_json = pods_result.stdout.strip()
                if pods_json:
                    items = json.loads(pods_json).get("items", [])
                    daemonset_pods = []
                    other_pods = []
                    for pod in items:
                        owners = pod.get("metadata", {}).get("ownerReferences", [])
                        owner_kind = owners[0].get("kind") if owners else ""
                        ns = pod.get("metadata", {}).get("namespace", "")
                        pod_name = pod.get("metadata", {}).get("name", "")
                        if owner_kind == "DaemonSet":
                            daemonset_pods.append(f"{ns}/{pod_name}")
                        else:
                            other_pods.append(f"{ns}/{pod_name}")
                    remaining = len(items)
                    evictable_remaining = len(other_pods)
                else:
                    daemonset_pods = []
                    other_pods = []
                    evictable_remaining = 0
            except Exception:
                remaining = -1
                daemonset_pods = []
                other_pods = []
                evictable_remaining = -1

            if evictable_remaining == 0:
                pods_gone = True
                if daemonset_pods:
                    print(
                        f"  [yellow]Only DaemonSet-managed pods remain on {k8s_node_name} "
                        "(expected). Proceeding to node deletion.[/yellow]"
                    )
                    for ds_pod in daemonset_pods:
                        print(f"    [dim]- {ds_pod}[/dim]")
                else:
                    print(f"  [green]All pods terminated on {k8s_node_name}.[/green]")
                break
            if evictable_remaining >= 0:
                print(
                    f"  Waiting for evictable pods to terminate on {k8s_node_name}: "
                    f"{evictable_remaining} remaining"
                    f" ({len(daemonset_pods)} DaemonSet pod(s) ignored)..."
                )
                for pod_ref in other_pods[:5]:
                    print(f"    [dim]- {pod_ref}[/dim]")
            else:
                print("  Waiting for pod status to stabilize...")
            time.sleep(5)

        if not pods_gone:
            print(f"  [red]Timed out waiting for evictable pods to terminate on {k8s_node_name}.[/red]")
            raise typer.Exit(1)

        # Request node object deletion before VM shutdown; kubelet may recreate
        # it until the VM is actually terminated, so final wait happens after VM deletion.
        _ssh_cmd(
            master["ip"], user, key_path,
            f"kubectl delete node {k8s_node_name} --ignore-not-found=true",
            check=False,
        )

        # --- Step 4: Delete VM and wait for Azure completion ---
        print(f"\n  [bold]Step 4/4:[/bold] Delete virtual machine node {k8s_node_name}")
        try:
            delete_azure_vm(resource_group, k8s_node_name, subscription_id=context_id)
        except Exception as e:
            print(f"  [red]Failed to delete VM {k8s_node_name}: {e}[/red]")
            raise typer.Exit(1)

        vm_deleted = False
        for _ in range(24):
            vm_names = {
                vm["name"]
                for vm in list_azure_vms(resource_group=resource_group, subscription_id=context_id)
            }
            if k8s_node_name not in vm_names:
                vm_deleted = True
                break
            time.sleep(5)
        if not vm_deleted:
            print(f"  [red]Timed out waiting for Azure VM deletion: {k8s_node_name}[/red]")
            raise typer.Exit(1)

        print(f"  [green]VM fully deleted: {k8s_node_name}[/green]")

        node_deleted = False
        for _ in range(36):
            exists = _ssh_cmd(
                master["ip"], user, key_path,
                f"kubectl get node {k8s_node_name} -o name 2>/dev/null || true",
                check=False,
            ).stdout.strip()
            if not exists:
                node_deleted = True
                break
            time.sleep(5)
        if not node_deleted:
            print(
                f"  [yellow]Kubernetes node object still present after VM deletion: {k8s_node_name}. "
                "It should be cleaned up shortly by the control plane.[/yellow]"
            )
        else:
            print(f"  [green]Kubernetes node removed: {k8s_node_name}[/green]")

        removed_workers.append(victim)
        remaining_workers = [w for w in remaining_workers if w["name"] != k8s_node_name]

    victims = removed_workers

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
