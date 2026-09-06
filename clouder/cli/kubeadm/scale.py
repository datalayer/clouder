"""Clouder CLI - kubeadm scale command."""

import json
import os
import re
import time
import uuid

import typer
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ...util.utils import SSH_FOLDER, kubeadm_kubeconfig_path

from ._helpers import (
    DEFAULT_NODE_LABELS,
    _print_section_header,
    _print_step_header,
    _SCRIPT_PREREQS,
    _SCRIPT_UPGRADE_KUBELET,
    _SCRIPT_WORKER_FEATURE_GATE,
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _save_cluster_metadata,
    _ssh_cmd,
    _ssh_cmd_stream,
    _update_cluster_metadata,
)

DEFAULT_PROTECTED_NAMESPACES = {
    "kube-system",
    "kube-public",
    "kube-node-lease",
}


def _protected_namespaces() -> set[str]:
    """Return namespaces excluded from force finalizer cleanup during scale-down."""
    raw = os.environ.get("CLOUDER_SCALE_DOWN_PROTECTED_NAMESPACES", "")
    configured = {part.strip() for part in raw.split(",") if part.strip()}
    return DEFAULT_PROTECTED_NAMESPACES | configured


def _worker_index_from_name(cluster_name: str, worker_name: str) -> int | None:
    """Extract worker index from names like <cluster>-node-10[-slug]."""

    pattern = rf"^{re.escape(cluster_name)}-node-(\d+)(?:-[a-z0-9]{{4}})?$"
    match = re.match(pattern, worker_name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _build_unique_worker_name(cluster_name: str, index: int, existing_names: set[str]) -> str:
    """Build a unique worker name with a short slug suffix.

    Example: r1-node-10-a1b2
    """

    for _ in range(16):
        slug = uuid.uuid4().hex[:4]
        candidate = f"{cluster_name}-node-{index}-{slug}"
        if candidate not in existing_names:
            existing_names.add(candidate)
            return candidate

    # Extremely unlikely fallback if random candidates collide repeatedly.
    candidate = f"{cluster_name}-node-{index}-{uuid.uuid4().hex[:8]}"
    existing_names.add(candidate)
    return candidate


def _resolve_node_labels(raw_labels: list[str] | None, metadata: dict | None = None) -> list[str]:
    """Resolve node labels, supporting repeated flags and comma-separated values."""
    if not raw_labels:
        stored = (metadata or {}).get("node_labels") if isinstance(metadata, dict) else None
        if isinstance(stored, list):
            stored_labels = [str(value).strip() for value in stored if str(value).strip()]
            if stored_labels:
                return stored_labels
        return list(DEFAULT_NODE_LABELS)

    labels: list[str] = []
    for value in raw_labels:
        for part in str(value).split(","):
            candidate = part.strip()
            if not candidate:
                continue
            if "=" not in candidate:
                typer.echo(f"Invalid --node-label '{candidate}'. Expected key=value.", err=True)
                raise typer.Exit(1)
            labels.append(candidate)
    if not labels:
        return list(DEFAULT_NODE_LABELS)
    return labels


def _build_k8s_api(cluster_name: str) -> k8s_client.CoreV1Api | None:
    """Build Kubernetes CoreV1 API client from cluster kubeconfig if available."""
    kubeconfig_path = kubeadm_kubeconfig_path(cluster_name)
    if not kubeconfig_path.exists():
        return None
    try:
        k8s_config.load_kube_config(config_file=str(kubeconfig_path))
        return k8s_client.CoreV1Api()
    except Exception:
        return None


def _registered_worker_names(core_v1: k8s_client.CoreV1Api | None, cluster_name: str) -> set[str]:
    """Return worker node names currently visible in Kubernetes for the cluster."""
    if core_v1 is None:
        return set()
    try:
        nodes = core_v1.list_node().items
    except Exception:
        return set()
    prefix = f"{cluster_name}-node-"
    names: set[str] = set()
    for node in nodes:
        node_name = str(getattr(getattr(node, "metadata", None), "name", "") or "")
        if node_name.startswith(prefix):
            names.add(node_name)
    return names


def _k8s_worker_names_by_readiness(
    core_v1: k8s_client.CoreV1Api | None,
    cluster_name: str,
) -> tuple[set[str], set[str]]:
    """Return (ready_workers, not_ready_workers) for cluster worker nodes seen by Kubernetes."""
    if core_v1 is None:
        return (set(), set())
    try:
        nodes = core_v1.list_node().items
    except Exception:
        return (set(), set())

    prefix = f"{cluster_name}-node-"
    ready_workers: set[str] = set()
    not_ready_workers: set[str] = set()
    for node in nodes:
        node_name = str(getattr(getattr(node, "metadata", None), "name", "") or "")
        if not node_name.startswith(prefix):
            continue

        ready_status = "Unknown"
        for condition in (getattr(getattr(node, "status", None), "conditions", None) or []):
            if condition.type == "Ready":
                ready_status = str(condition.status or "Unknown")
                break

        if ready_status == "True":
            ready_workers.add(node_name)
        else:
            not_ready_workers.add(node_name)

    return (ready_workers, not_ready_workers)


def _wait_for_node_ready_api(core_v1: k8s_client.CoreV1Api, node_name: str, timeout_seconds: int = 300) -> bool:
    """Wait until a Kubernetes node is registered and Ready via the API."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            node = core_v1.read_node(node_name)
            for condition in node.status.conditions or []:
                if condition.type == "Ready" and condition.status == "True":
                    return True
        except ApiException as exc:
            if exc.status != 404:
                pass
        except Exception:
            pass
        time.sleep(5)
    return False


def _read_node_ready_condition_api(
    core_v1: k8s_client.CoreV1Api,
    node_name: str,
) -> tuple[bool, str, str, str]:
    """Return node existence plus Ready condition tuple: (exists, status, reason, message)."""
    try:
        node = core_v1.read_node(node_name)
    except ApiException as exc:
        if exc.status == 404:
            return (False, "", "", "")
        return (False, "", "", str(exc))
    except Exception as exc:
        return (False, "", "", str(exc))

    for condition in node.status.conditions or []:
        if condition.type == "Ready":
            return (
                True,
                str(condition.status or ""),
                str(condition.reason or ""),
                str(condition.message or ""),
            )
    return (True, "", "", "Ready condition missing")


def _wait_for_node_ready_ssh(master_ip: str, ssh_user: str, key_path: str, node_name: str, timeout_seconds: int = 300) -> bool:
    """Wait until the Kubernetes node is registered and Ready."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            (
                f"kubectl get node {node_name} "
                "-o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' "
                "2>/dev/null || true"
            ),
            check=False,
        ).stdout.strip()
        if status == "True":
            return True
        time.sleep(5)
    return False


def _apply_node_labels_api(core_v1: k8s_client.CoreV1Api, node_name: str, labels: list[str]) -> None:
    """Apply labels to a Kubernetes node using the Kubernetes API."""
    patch_labels: dict[str, str] = {}
    for label in labels:
        key, value = label.split("=", 1)
        patch_labels[key] = value
    if not patch_labels:
        return
    core_v1.patch_node(node_name, {"metadata": {"labels": patch_labels}})


def _apply_node_labels_with_retry_api(
    core_v1: k8s_client.CoreV1Api,
    node_name: str,
    labels: list[str],
    timeout_seconds: int = 300,
) -> bool:
    """Apply labels once the node is visible in Kubernetes, retrying for transient delays."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            _apply_node_labels_api(core_v1, node_name, labels)
            return True
        except ApiException as exc:
            if exc.status != 404:
                pass
        except Exception:
            pass
        time.sleep(5)
    return False


def _apply_node_labels_ssh(master_ip: str, ssh_user: str, key_path: str, node_name: str, labels: list[str]) -> None:
    """Apply labels to a Kubernetes node using kubectl on the master."""
    for label in labels:
        _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            f"kubectl label node {node_name} {label} --overwrite",
            check=False,
        )


def register(kubeadm_app: typer.Typer):
    """Register the scale command on the given Typer app."""

    @kubeadm_app.command("scale")
    def kubeadm_scale(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
        workers: int = typer.Option(..., "--workers", "-w", help="Desired number of worker nodes."),
        user: str | None = typer.Option(None, "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        os_disk_size_gb: int | None = typer.Option(
            None,
            "--os-disk-size-gb",
            min=30,
            help=(
                "OS disk size in GiB for newly created worker VMs. "
                "Larger disks increase node ephemeral-storage capacity."
            ),
        ),
        node_labels: list[str] | None = typer.Option(
            None,
            "--node-label",
            help=(
                "Node label key=value to apply once each new worker becomes Ready. "
                "Repeatable or comma-separated. Defaults to runtime labels."
            ),
        ),
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
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)

        # --- Load cluster metadata (or discover from cloud provider) ---
        metadata = _load_cluster_metadata(name)
        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        current_workers = cluster["workers"]
        current_count = len(current_workers)

        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        core_v1 = _build_k8s_api(name)
        ready_workers, not_ready_workers = _k8s_worker_names_by_readiness(core_v1, name)
        registered_workers = ready_workers | not_ready_workers
        registered_count = len(registered_workers)
        ready_count = len(ready_workers)
        not_ready_count = len(not_ready_workers)

        vm_ready_count = current_count
        vm_unhealthy_count = 0
        vm_unhealthy_names: set[str] = set()
        try:
            if cloud == "azure":
                from ...cloud.azure.api import list_azure_vms

                vm_inventory = list_azure_vms(
                    resource_group=master["resource_group"],
                    subscription_id=context_id,
                )
                vm_state_by_name = {
                    str(item.get("name") or ""): str(item.get("provisioning_state") or "").lower()
                    for item in vm_inventory
                }
                unhealthy_state = "succeeded"
            else:
                from ...cloud.aws.api import list_aws_vms

                vm_inventory = list_aws_vms(region=master.get("region") or (metadata or {}).get("region"))
                vm_state_by_name = {
                    str(item.get("name") or ""): str(item.get("state") or "").lower()
                    for item in vm_inventory
                }
                unhealthy_state = "running"

            worker_names = {str(worker.get("name") or "") for worker in current_workers}
            vm_unhealthy_names = {
                worker_name
                for worker_name in worker_names
                if vm_state_by_name.get(worker_name, "") != unhealthy_state
            }
            vm_unhealthy_count = len(vm_unhealthy_names)
            vm_ready_count = max(0, current_count - vm_unhealthy_count)
        except Exception:
            pass

        workers_to_reconcile = [
            worker for worker in current_workers if worker["name"] not in registered_workers
        ]

        # --- Resolve cluster parameters (from metadata or cloud discovery) ---
        resource_group = None
        image_publisher = None
        image_offer = None
        image_sku = None
        subnet_id = None
        nsg_id = None
        aws_security_group_id = None
        aws_ami_id = None

        if cloud == "azure":
            if metadata:
                resource_group = metadata["resource_group"]
                region = metadata["region"]
                node_size = metadata["workers"][0]["vm_size"] if metadata.get("workers") else "Standard_B2s"
                # Legacy metadata may store a null admin_username; fall back to CLI/default user.
                admin_username = metadata.get("admin_username") or user
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
        else:
            metadata = metadata or {}
            region = metadata.get("region") or master.get("region") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            node_size = metadata.get("node_size")
            if not node_size and metadata.get("workers"):
                node_size = metadata["workers"][0].get("vm_size")
            node_size = node_size or "t3.large"
            admin_username = metadata.get("admin_username") or user
            ssh_key_name = metadata.get("ssh_key_name")
            aws_ami_id = metadata.get("ami_id")
            aws_security_group_id = metadata.get("networking", {}).get("security_group_id")
            subnet_id = metadata.get("networking", {}).get("subnet_id")

        default_admin = "azureuser" if cloud == "azure" else "ec2-user"
        admin_username = (admin_username or default_admin).strip()
        if not admin_username:
            admin_username = default_admin

        if metadata and not metadata.get("admin_username"):
            _update_cluster_metadata(name, {"admin_username": admin_username})

        # --- Preflight unhealthy cleanup (K8s NotReady and/or VM unhealthy) ---
        preflight_cleanup_candidates_summary = "-"
        unhealthy_worker_names = set(not_ready_workers) | set(vm_unhealthy_names)
        if unhealthy_worker_names:
            preflight_cleanup_candidates_summary = ", ".join(sorted(unhealthy_worker_names))
        if unhealthy_worker_names:
            if cloud == "azure":
                from ...cloud.azure.api import delete_azure_vm
            else:
                from ...cloud.aws.api import terminate_aws_vm, wait_aws_instances_terminated

            print(
                "\n[bold yellow]Preflight cleanup:[/bold yellow] "
                f"deleting {len(unhealthy_worker_names)} unhealthy worker(s) from Kubernetes and {cloud.upper()}..."
            )
            current_worker_names = {str(worker.get("name") or "") for worker in current_workers}
            worker_instance_ids = {
                str(worker.get("name") or ""): str(worker.get("instance_id") or "")
                for worker in current_workers
                if worker.get("instance_id")
            }
            for worker_name in sorted(unhealthy_worker_names):
                print(f"  [yellow]{worker_name}[/yellow] cleanup...")
                if core_v1 is not None:
                    try:
                        core_v1.delete_node(worker_name)
                        print(f"    [green]k8s node delete requested: {worker_name}[/green]")
                    except ApiException as exc:
                        if exc.status != 404:
                            print(
                                "    [yellow]k8s node delete failed: "
                                f"{type(exc).__name__}: {exc}[/yellow]"
                            )
                    except Exception as exc:
                        print(
                            "    [yellow]k8s node delete failed: "
                            f"{type(exc).__name__}: {exc}[/yellow]"
                        )

                if worker_name in current_worker_names:
                    try:
                        if cloud == "azure":
                            delete_azure_vm(
                                resource_group=resource_group,
                                vm_name=worker_name,
                                subscription_id=context_id,
                            )
                        else:
                            instance_id = worker_instance_ids.get(worker_name)
                            if instance_id:
                                terminate_aws_vm(instance_id=instance_id, region=region)
                                wait_aws_instances_terminated([instance_id], region=region)
                        print(f"    [green]vm delete requested: {worker_name}[/green]")
                    except Exception as exc:
                        print(
                            "    [yellow]vm delete failed: "
                            f"{type(exc).__name__}: {exc}[/yellow]"
                        )

            # Refresh inventory after cleanup requests before computing action plan.
            cluster = _resolve_cluster_vms(name)
            master = cluster["master"]
            current_workers = cluster["workers"]
            current_count = len(current_workers)

            ready_workers, not_ready_workers = _k8s_worker_names_by_readiness(core_v1, name)
            registered_workers = ready_workers | not_ready_workers
            registered_count = len(registered_workers)
            ready_count = len(ready_workers)
            not_ready_count = len(not_ready_workers)

            vm_ready_count = current_count
            vm_unhealthy_count = 0
            try:
                if cloud == "azure":
                    from ...cloud.azure.api import list_azure_vms

                    vm_inventory = list_azure_vms(
                        resource_group=master["resource_group"],
                        subscription_id=context_id,
                    )
                    vm_state_by_name = {
                        str(item.get("name") or ""): str(item.get("provisioning_state") or "").lower()
                        for item in vm_inventory
                    }
                    unhealthy_state = "succeeded"
                else:
                    from ...cloud.aws.api import list_aws_vms

                    vm_inventory = list_aws_vms(region=region)
                    vm_state_by_name = {
                        str(item.get("name") or ""): str(item.get("state") or "").lower()
                        for item in vm_inventory
                    }
                    unhealthy_state = "running"

                worker_names = {str(worker.get("name") or "") for worker in current_workers}
                vm_unhealthy_count = len(
                    [
                        worker_name
                        for worker_name in worker_names
                        if vm_state_by_name.get(worker_name, "") != unhealthy_state
                    ]
                )
                vm_ready_count = max(0, current_count - vm_unhealthy_count)
            except Exception:
                pass

            workers_to_reconcile = [
                worker for worker in current_workers if worker["name"] not in registered_workers
            ]

        if workers > ready_count:
            direction = "up"
            diff = abs(workers - ready_count)
            action_label = f"Scale up by {diff} node(s)"
        elif workers < ready_count:
            direction = "down"
            diff = abs(workers - ready_count)
            action_label = f"Scale down by {diff} node(s)"
        else:
            if workers_to_reconcile:
                direction = "up"
                diff = len(workers_to_reconcile)
                action_label = f"Reconcile {diff} unregistered worker(s)"
            else:
                direction = "none"
                diff = 0
                action_label = "No scale action required"

        if workers_to_reconcile:
            action_label = (
                f"{action_label} + reconcile {len(workers_to_reconcile)} unregistered worker(s)"
                if "reconcile" not in action_label.lower()
                else action_label
            )

        if workers_to_reconcile:
            reconcile_names = ", ".join(worker["name"] for worker in workers_to_reconcile)
        else:
            reconcile_names = "-"

        # --- Show plan ---
        plan_items = [
            ("Cluster", str(name)),
            ("Masters", f"{master['name']} ({master['ip']})"),
            (
                "Current workers (VM)",
                f"total={current_count}, healthy={vm_ready_count}, unhealthy={vm_unhealthy_count}",
            ),
            (
                "Current workers (K8s)",
                f"total={registered_count}, Ready={ready_count}, NotReady={not_ready_count}",
            ),
            ("Cleanup candidates", preflight_cleanup_candidates_summary),
            ("Desired workers", str(workers)),
            ("Action", str(action_label)),
            ("To reconcile", str(reconcile_names)),
            ("Node size", str(node_size)),
            ("Region", str(region)),
        ]
        label_width = max(len(label) for label, _ in plan_items)
        plan_lines = [
            f"[bold]{label + ':':<{label_width + 1}}[/bold] {value}"
            for label, value in plan_items
        ]
        panel_title = "[bold bright_cyan]Scale Plan[/bold bright_cyan]"
        if direction == "up":
            panel_title = "[bold bright_green]Scale Up[/bold bright_green]"
        elif direction == "down":
            panel_title = "[bold bright_yellow]Scale Down[/bold bright_yellow]"

        print(Panel(
            "\n".join(plan_lines),
            title=panel_title,
        ))

        if direction == "none":
            print(
                f"[green]Cluster '{name}' already has desired Ready workers ({ready_count}). Nothing to do.[/green]"
            )
            raise typer.Exit(0)

        if not force:
            if not Confirm.ask(f"\nProceed with scale {direction}?", default=True):
                raise typer.Abort()

        resolved_node_labels = _resolve_node_labels(node_labels, metadata)
        _update_cluster_metadata(name, {"node_labels": resolved_node_labels})

        if direction == "up":
            if cloud == "aws":
                from .aws.scale import scale_up as _scale_up_aws

                _scale_up_aws(
                    cluster_name=name,
                    context_id=context_id,
                    master=master,
                    current_workers=current_workers,
                    new_count=workers,
                    workers_to_reconcile=workers_to_reconcile,
                    region=region,
                    node_size=node_size,
                    admin_username=admin_username,
                    ssh_key_name=ssh_key_name,
                    subnet_id=subnet_id,
                    security_group_id=aws_security_group_id,
                    ami_id=aws_ami_id,
                    key_path=key_path,
                    user=user,
                    metadata=metadata,
                    node_labels=resolved_node_labels,
                    os_disk_size_gb=os_disk_size_gb,
                )
            else:
                from .azure.scale import scale_up as _scale_up_azure

                _scale_up_azure(
                    cluster_name=name,
                    context_id=context_id,
                    master=master,
                    current_workers=current_workers,
                    new_count=workers,
                    workers_to_reconcile=workers_to_reconcile,
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
                    node_labels=resolved_node_labels,
                    os_disk_size_gb=os_disk_size_gb,
                )
        else:
            if cloud == "aws":
                from .aws.scale import scale_down as _scale_down_aws

                _scale_down_aws(
                    cluster_name=name,
                    context_id=context_id,
                    master=master,
                    current_workers=current_workers,
                    registered_worker_names=registered_workers,
                    new_count=workers,
                    region=region,
                    key_path=key_path,
                    user=admin_username,
                    metadata=metadata,
                )
            else:
                from .azure.scale import scale_down as _scale_down_azure

                _scale_down_azure(
                    cluster_name=name,
                    context_id=context_id,
                    master=master,
                    current_workers=current_workers,
                    registered_worker_names=registered_workers,
                    new_count=workers,
                    resource_group=resource_group,
                    key_path=key_path,
                    user=user,
                    metadata=metadata,
                )


