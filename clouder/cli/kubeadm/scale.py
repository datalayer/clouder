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


DEFAULT_NODE_LABELS = [
    "role.datalayer.io/runtime=true",
    "node.datalayer.io/variant=medium",
    "xpu.datalayer.io/cpu=true",
]

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


def _resolve_node_labels(raw_labels: list[str] | None) -> list[str]:
    """Resolve node labels, supporting repeated flags and comma-separated values."""
    if not raw_labels:
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
        if cloud != "azure":
            typer.echo("Kubeadm scale is currently only supported for Azure. Use --cloud azure.", err=True)
            raise typer.Exit(1)

        # --- Load cluster metadata (or discover from Azure) ---
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
            from ...cloud.azure.api import list_azure_vms

            vm_inventory = list_azure_vms(
                resource_group=master["resource_group"],
                subscription_id=context_id,
            )
            vm_state_by_name = {
                str(item.get("name") or ""): str(item.get("provisioning_state") or "").lower()
                for item in vm_inventory
            }
            worker_names = {str(worker.get("name") or "") for worker in current_workers}
            vm_unhealthy_names = {
                worker_name
                for worker_name in worker_names
                if vm_state_by_name.get(worker_name, "") != "succeeded"
            }
            vm_unhealthy_count = len(vm_unhealthy_names)
            vm_ready_count = max(0, current_count - vm_unhealthy_count)
        except Exception:
            pass

        workers_to_reconcile = [
            worker for worker in current_workers if worker["name"] not in registered_workers
        ]

        # --- Resolve cluster parameters (from metadata or Azure discovery) ---
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

        admin_username = (admin_username or "azureuser").strip()
        if not admin_username:
            admin_username = "azureuser"

        if metadata and not metadata.get("admin_username"):
            _update_cluster_metadata(name, {"admin_username": admin_username})

        # --- Preflight unhealthy cleanup (K8s NotReady and/or VM unhealthy) ---
        preflight_cleanup_candidates_summary = "-"
        unhealthy_worker_names = set(not_ready_workers) | set(vm_unhealthy_names)
        if unhealthy_worker_names:
            preflight_cleanup_candidates_summary = ", ".join(sorted(unhealthy_worker_names))
        if unhealthy_worker_names:
            from ...cloud.azure.api import delete_azure_vm

            print(
                "\n[bold yellow]Preflight cleanup:[/bold yellow] "
                f"deleting {len(unhealthy_worker_names)} unhealthy worker(s) from Kubernetes and Azure..."
            )
            current_worker_names = {str(worker.get("name") or "") for worker in current_workers}
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
                        delete_azure_vm(
                            resource_group=resource_group,
                            vm_name=worker_name,
                            subscription_id=context_id,
                        )
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
                from ...cloud.azure.api import list_azure_vms

                vm_inventory = list_azure_vms(
                    resource_group=master["resource_group"],
                    subscription_id=context_id,
                )
                vm_state_by_name = {
                    str(item.get("name") or ""): str(item.get("provisioning_state") or "").lower()
                    for item in vm_inventory
                }
                worker_names = {str(worker.get("name") or "") for worker in current_workers}
                vm_unhealthy_count = len(
                    [
                        worker_name
                        for worker_name in worker_names
                        if vm_state_by_name.get(worker_name, "") != "succeeded"
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

        resolved_node_labels = _resolve_node_labels(node_labels)

        if direction == "up":
            _scale_up(
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
            _scale_down(
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


def _scale_up(
    cluster_name, context_id, master, current_workers, new_count,
    workers_to_reconcile,
    resource_group, region, node_size, admin_username, ssh_key_name,
    image_publisher, image_offer, image_sku, subnet_id, nsg_id,
    key_path, user, metadata, node_labels, os_disk_size_gb,
):
    """Add new worker nodes to the cluster."""
    from ...cloud.azure.api import (
        create_azure_vm,
        delete_azure_vm,
        get_kubeadm_join_command,
        list_azure_vms,
        run_azure_vm_shell_script,
    )

    def _clip(text: str, max_chars: int = 1600) -> str:
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        return "...\n" + value[-max_chars:]

    def _print_step_logs(worker_name: str, step: str, result: dict[str, object]) -> None:
        stdout = _clip(str(result.get("stdout") or ""))
        stderr = _clip(str(result.get("stderr") or ""))
        print(f"  [yellow]{worker_name} {step} diagnostics:[/yellow]")
        if stdout:
            print(f"    [dim]stdout:\n{stdout}[/dim]")
        else:
            print("    [dim]stdout: <empty>[/dim]")
        if stderr:
            print(f"    [dim]stderr:\n{stderr}[/dim]")
        else:
            print("    [dim]stderr: <empty>[/dim]")

    def _print_worker_runtime_diagnostics(worker: dict[str, str]) -> None:
        try:
            diagnostics = run_azure_vm_shell_script(
                resource_group=resource_group,
                vm_name=worker["name"],
                script=(
                    "set +e\n"
                    "echo '__kubelet_active__'\n"
                    "sudo systemctl is-active kubelet\n"
                    "echo '__containerd_active__'\n"
                    "sudo systemctl is-active containerd\n"
                    "echo '__kubelet_status__'\n"
                    "sudo systemctl status kubelet --no-pager -l\n"
                    "echo '__kubelet_journal__'\n"
                    "sudo journalctl -u kubelet -n 120 --no-pager\n"
                ),
                subscription_id=context_id,
            )
            _print_step_logs(worker["name"], "runtime", diagnostics)
        except Exception as exc:
            print(
                f"  [yellow]{worker['name']} runtime diagnostics unavailable: {type(exc).__name__}: {exc}[/yellow]"
            )

    def _is_vm_marked_for_deletion_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "operationnotallowed" in text
            and "vmextensionoperation" in text
            and "marked for deletion" in text
        )

    def _is_vm_operation_preempted_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "operationpreempted" in text and "more recent operation" in text

    def _delete_worker_and_skip(worker: dict[str, str], reason: str) -> None:
        print(f"  [yellow]{worker['name']} unhealthy: {reason}. Deleting and skipping.[/yellow]")
        try:
            delete_azure_vm(
                resource_group=resource_group,
                vm_name=worker["name"],
                subscription_id=context_id,
            )
            print(f"  [green]{worker['name']} delete requested.[/green]")
        except Exception as exc:
            print(f"  [yellow]Failed to delete {worker['name']}: {type(exc).__name__}: {exc}[/yellow]")

    def _is_vm_healthy(worker: dict[str, str]) -> tuple[bool, str]:
        try:
            vms = list_azure_vms(resource_group=resource_group, subscription_id=context_id)
        except Exception as exc:
            return False, f"vm_list_failed:{type(exc).__name__}"

        vm = next((item for item in vms if item.get("name") == worker["name"]), None)
        if vm is None:
            return False, "vm_not_found"

        state = str(vm.get("provisioning_state") or "").strip().lower()
        if state != "succeeded":
            return False, f"provisioning_state={state or 'unknown'}"
        return True, state or "unknown"

    def _log_relevant_output(worker_name: str, step_name: str, result: dict[str, object]) -> None:
        """Print a concise, relevant summary from Azure RunCommand output."""
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            return

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return

        preferred = [
            line
            for line in lines
            if any(
                token in line.lower()
                for token in (
                    "version",
                    "installed",
                    "complete",
                    "joined",
                    "ready",
                    "done",
                )
            )
        ]
        selected = preferred[-2:] if preferred else lines[-1:]
        snippet = " | ".join(selected)
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        print(f"    [dim]{worker_name} {step_name}: {snippet}[/dim]")

    core_v1 = _build_k8s_api(cluster_name)
    if core_v1 is None:
        typer.echo(
            (
                "Kubeconfig is required for API-based kubeadm scaling validation. "
                "Run 'clouder kubeadm get-config <cluster>' first."
            ),
            err=True,
        )
        raise typer.Exit(1)

    current_count = len(current_workers)
    new_workers: list[dict[str, str]] = []

    # --- Read SSH public key ---
    ssh_public_key = None
    if ssh_key_name:
        pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
        if pub_path.exists():
            ssh_public_key = pub_path.read_text().strip()

    def _prepare_and_register_workers(target_workers: list[dict[str, str]], phase_title: str) -> None:
        if not target_workers:
            return

        workers = list(target_workers)
        healthy_workers: list[dict[str, str]] = []
        print(f"\n[bold]{phase_title}: Checking VM health on {len(workers)} worker(s)...[/bold]")
        for worker in workers:
            healthy, status = _is_vm_healthy(worker)
            if not healthy:
                _delete_worker_and_skip(worker, status)
                continue
            healthy_workers.append(worker)

        workers = healthy_workers
        if not workers:
            print(f"  [yellow]{phase_title}: no healthy workers to process.[/yellow]")
            return

        print(
            f"\n[bold]{phase_title}: Installing prerequisites on {len(workers)} worker(s) (cloud API)...[/bold]"
        )
        prereq_ready_workers: list[dict[str, str]] = []
        for worker in workers:
            print(f"  [cyan]{worker['name']}[/cyan] ({worker['ip']})...")
            try:
                result = run_azure_vm_shell_script(
                    resource_group=resource_group,
                    vm_name=worker["name"],
                    script=_SCRIPT_PREREQS,
                    subscription_id=context_id,
                )
            except Exception as exc:
                if _is_vm_marked_for_deletion_error(exc) or _is_vm_operation_preempted_error(exc):
                    _delete_worker_and_skip(worker, "azure_operation_conflict")
                    continue
                print(f"  [red]Failed on {worker['name']}[/red]")
                print(f"  [dim]{type(exc).__name__}: {exc}[/dim]")
                raise typer.Exit(1)
            stdout = (result.get("stdout") or "").strip()
            stderr = (result.get("stderr") or "").strip()
            if stderr or "__DATALAYER_PREREQS_OK__" not in stdout:
                print(f"  [red]Failed on {worker['name']}[/red]")
                _print_step_logs(worker["name"], "prereqs", result)
                _delete_worker_and_skip(worker, "prereqs_failed")
                continue
            print(f"  [green]{worker['name']} prerequisites done.[/green]")
            _log_relevant_output(worker["name"], "prereqs", result)
            prereq_ready_workers.append(worker)

        workers = prereq_ready_workers
        if not workers:
            print(f"  [yellow]{phase_title}: no workers left after prerequisites.[/yellow]")
            return

        print(
            f"\n[bold]{phase_title}: Upgrading kubelet/kubeadm/kubectl on {len(workers)} worker(s) (cloud API)...[/bold]"
        )
        upgraded_workers: list[dict[str, str]] = []
        for worker in workers:
            print(f"  [cyan]{worker['name']}[/cyan] upgrading kubelet stack...")
            try:
                upgrade_result = run_azure_vm_shell_script(
                    resource_group=resource_group,
                    vm_name=worker["name"],
                    script=_SCRIPT_UPGRADE_KUBELET,
                    subscription_id=context_id,
                )
            except Exception as exc:
                if _is_vm_marked_for_deletion_error(exc) or _is_vm_operation_preempted_error(exc):
                    _delete_worker_and_skip(worker, "azure_operation_conflict")
                    continue
                print(f"  [red]Kubelet upgrade failed on {worker['name']}[/red]")
                print(f"  [dim]{type(exc).__name__}: {exc}[/dim]")
                raise typer.Exit(1)
            upgrade_stdout = (upgrade_result.get("stdout") or "").strip()
            upgrade_stderr = (upgrade_result.get("stderr") or "").strip()
            if upgrade_stderr or "__DATALAYER_KUBELET_UPGRADE_OK__" not in upgrade_stdout:
                print(f"  [red]Kubelet upgrade failed on {worker['name']}[/red]")
                _print_step_logs(worker["name"], "upgrade", upgrade_result)
                _delete_worker_and_skip(worker, "upgrade_failed")
                continue
            print(f"  [green]{worker['name']} kubelet stack upgraded.[/green]")
            _log_relevant_output(worker["name"], "upgrade", upgrade_result)
            upgraded_workers.append(worker)

        workers = upgraded_workers
        if not workers:
            print(f"  [yellow]{phase_title}: no workers left after upgrade.[/yellow]")
            return

        print(f"\n[bold]{phase_title}: Getting join command from master (Python cloud API)...[/bold]")
        try:
            join_command = get_kubeadm_join_command(
                resource_group=resource_group,
                master_vm_name=master["name"],
                subscription_id=context_id,
            )
        except ValueError as exc:
            print("[red]Could not get a valid join command from master.[/red]")
            typer.echo(str(exc))
            raise typer.Exit(1)
        print(f"  [dim]Join command: {join_command}[/dim]")

        print(
            f"\n[bold]{phase_title}: Joining workers, enabling feature gates, and applying labels (cloud API + k8s API)...[/bold]"
        )
        for worker in workers:
            print(f"  [cyan]{worker['name']}[/cyan] joining...")
            try:
                join_worker_result = run_azure_vm_shell_script(
                    resource_group=resource_group,
                    vm_name=worker["name"],
                    script=f"sudo {join_command} --v=5",
                    subscription_id=context_id,
                )
            except Exception as exc:
                if _is_vm_marked_for_deletion_error(exc) or _is_vm_operation_preempted_error(exc):
                    _delete_worker_and_skip(worker, "azure_operation_conflict")
                    continue
                print(f"  [red]Join failed on {worker['name']}[/red]")
                print(f"  [dim]{type(exc).__name__}: {exc}[/dim]")
                raise typer.Exit(1)
            join_worker_stdout = (join_worker_result.get("stdout") or "").strip()
            join_worker_stderr = (join_worker_result.get("stderr") or "").strip()
            join_output_lower = "\n".join(
                part for part in (join_worker_stdout.lower(), join_worker_stderr.lower()) if part
            )
            join_worker_fatal_markers = (
                "error execution phase",
                "timed out waiting for the condition",
                "unable to fetch the kubeadm-config configmap",
                "token is invalid",
                "token has expired",
                "certificate signed by unknown authority",
                "connection refused",
                "context deadline exceeded",
                "run 'kubeadm reset'",
            )
            join_worker_fatal_signature = any(marker in join_output_lower for marker in join_worker_fatal_markers)
            if re.search(r"token\s+id\s+.*\s+is\s+invalid", join_output_lower):
                join_worker_fatal_signature = True

            if join_worker_fatal_signature:
                print(f"  [red]Join failed on {worker['name']}[/red]")
                _print_step_logs(worker["name"], "join", join_worker_result)
                _print_worker_runtime_diagnostics(worker)
                _delete_worker_and_skip(worker, "join_failed")
                continue

            if join_worker_stderr:
                print(
                    f"  [yellow]{worker['name']} join reported stderr but no fatal markers; continuing.[/yellow]"
                )
            print(f"  [green]{worker['name']} joined.[/green]")
            _log_relevant_output(worker["name"], "join", join_worker_result)

            print(f"  [cyan]{worker['name']}[/cyan] enabling feature gates...")
            feature_result = run_azure_vm_shell_script(
                resource_group=resource_group,
                vm_name=worker["name"],
                script=_SCRIPT_WORKER_FEATURE_GATE,
                subscription_id=context_id,
            )
            feature_stderr = (feature_result.get("stderr") or "").strip()
            if feature_stderr:
                print(f"  [yellow]Feature gate setup failed on {worker['name']} (non-fatal)[/yellow]")
                print(f"  [dim]{feature_stderr[-300:]}[/dim]")
            else:
                print(f"  [green]{worker['name']} feature gates enabled.[/green]")
            _log_relevant_output(worker["name"], "feature-gates", feature_result)

            kubelet_check = run_azure_vm_shell_script(
                resource_group=resource_group,
                vm_name=worker["name"],
                script="sudo systemctl is-active kubelet; sudo systemctl is-active containerd",
                subscription_id=context_id,
            )
            kubelet_state = (kubelet_check.get("stdout") or "").strip().splitlines()
            kubelet_active = any(str(line).strip() == "active" for line in kubelet_state)
            if not kubelet_active:
                print(f"  [red]{worker['name']} kubelet is not active right after join.[/red]")
                _print_step_logs(worker["name"], "service-check", kubelet_check)
                _print_step_logs(worker["name"], "join", join_worker_result)
                _print_worker_runtime_diagnostics(worker)
                _delete_worker_and_skip(worker, "kubelet_not_active")
                continue

            print(f"  [cyan]{worker['name']}[/cyan] waiting for node Ready via Kubernetes API...")
            ready = _wait_for_node_ready_api(core_v1, worker["name"])
            if not ready:
                exists, ready_status, ready_reason, ready_message = _read_node_ready_condition_api(
                    core_v1,
                    worker["name"],
                )
                transient_not_ready_reasons = {
                    "KubeletNotReady",
                    "NetworkPluginNotReady",
                }
                transient_not_ready_signals = (
                    "cni",
                    "network plugin",
                    "container runtime network",
                    "runtime network",
                )
                ready_message_l = ready_message.lower()
                is_transient_not_ready = (
                    exists
                    and ready_status != "True"
                    and (
                        ready_reason in transient_not_ready_reasons
                        or any(signal in ready_message_l for signal in transient_not_ready_signals)
                    )
                )
                if is_transient_not_ready:
                    print(
                        f"  [yellow]{worker['name']} is still converging ({ready_reason or 'NotReady'}). "
                        "Keeping node for background recovery.[/yellow]"
                    )
                    if ready_message:
                        clipped = ready_message if len(ready_message) < 240 else (ready_message[:237] + "...")
                        print(f"    [dim]{clipped}[/dim]")

                    print(f"  [cyan]{worker['name']}[/cyan] applying labels (node registered, not yet Ready)...")
                    labels_applied = _apply_node_labels_with_retry_api(
                        core_v1,
                        worker["name"],
                        node_labels,
                    )
                    if labels_applied:
                        print(f"  [green]{worker['name']} labels applied.[/green]")
                    else:
                        print(
                            f"  [yellow]Could not apply labels on {worker['name']} yet; "
                            "node may still be registering.[/yellow]"
                        )
                    continue
                print(f"  [red]{worker['name']} did not become Ready in time.[/red]")
                _print_step_logs(worker["name"], "join", join_worker_result)
                _print_step_logs(worker["name"], "feature-gates", feature_result)
                _print_worker_runtime_diagnostics(worker)
                _delete_worker_and_skip(worker, "node_not_ready")
                continue
            print(f"  [green]{worker['name']} is Ready.[/green]")

            print(f"  [cyan]{worker['name']}[/cyan] applying labels...")
            labels_applied = _apply_node_labels_with_retry_api(
                core_v1,
                worker["name"],
                node_labels,
            )
            if labels_applied:
                print(f"  [green]{worker['name']} labels applied.[/green]")
            else:
                print(
                    f"  [yellow]Could not apply labels on {worker['name']} in time; "
                    "please retry labeling.[/yellow]"
                )

    reconciling_workers = list(workers_to_reconcile or [])
    if reconciling_workers:
        _prepare_and_register_workers(reconciling_workers, "Phase A (reconcile)")

    refreshed_cluster = _resolve_cluster_vms(cluster_name)
    refreshed_workers = refreshed_cluster["workers"]
    registered_after_reconcile = _registered_worker_names(core_v1, cluster_name)

    desired_missing_workers = max(0, int(new_count) - len(registered_after_reconcile))

    existing_numbers = []
    existing_names = {str(worker.get("name") or "") for worker in refreshed_workers}
    for worker in refreshed_workers:
        index = _worker_index_from_name(cluster_name, str(worker.get("name") or ""))
        if index is not None:
            existing_numbers.append(index)
    next_start = max(existing_numbers) + 1 if existing_numbers else 1

    new_worker_names = [
        _build_unique_worker_name(cluster_name, next_start + i, existing_names)
        for i in range(desired_missing_workers)
    ]

    if new_worker_names:
        print(
            f"\n[bold]Phase B (scale): Creating {len(new_worker_names)} new worker VM(s) to reach desired registered workers...[/bold]"
        )
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
                os_disk_size_gb=os_disk_size_gb,
                subscription_id=context_id,
            )
            print(f"  [green]{vm_name} created - IP: {result.get('public_ip', 'N/A')}[/green]")
            new_workers.append({"name": vm_name, "ip": result.get("public_ip"), "resource_group": resource_group})

        _prepare_and_register_workers(new_workers, "Phase B (scale)")

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
        title="[bold bright_green]Scale Up Complete[/bold bright_green]",
    ))


def _scale_down(
    cluster_name, context_id, master, current_workers, new_count,
    registered_worker_names,
    resource_group, key_path, user, metadata,
):
    """Remove worker nodes from the cluster (least running pods first, one by one)."""
    from ...cloud.azure.api import delete_azure_vm, list_azure_vms

    core_v1 = _build_k8s_api(cluster_name)
    if core_v1 is None:
        typer.echo(
            (
                "Kubeconfig is required for node-first scale-down. "
                "Run 'clouder kubeadm get-config <cluster>' first."
            ),
            err=True,
        )
        raise typer.Exit(1)

    ready_workers, _ = _k8s_worker_names_by_readiness(core_v1, cluster_name)
    k8s_worker_names = set(ready_workers)
    if registered_worker_names:
        # Use latest Kubernetes Ready view as source of truth while preserving caller context.
        k8s_worker_names = k8s_worker_names or set(registered_worker_names)

    nodes_to_remove = len(k8s_worker_names) - int(new_count)
    if nodes_to_remove <= 0:
        print(
            f"\n[green]No Kubernetes workers to remove: "
            f"current={len(k8s_worker_names)}, desired={new_count}.[/green]"
        )
        return

    def _worker_number(worker_name: str):
        parts = worker_name.rsplit("-", 1)
        return int(parts[-1]) if parts[-1].isdigit() else 0

    def _running_pod_counts(node_names: list[str]) -> dict[str, int]:
        """Return workload running pod counts per node using kubectl on master.

        DaemonSet and StatefulSet pods are ignored so empty worker nodes can be
        prioritized for removal even when infrastructure controllers still run.
        """
        cmd = (
            "kubectl get pods -A --field-selector=status.phase=Running "
            "-o json 2>/dev/null || true"
        )
        result = _ssh_cmd(master["ip"], user, key_path, cmd, check=False)
        counts = {node: 0 for node in node_names}
        try:
            payload = json.loads(result.stdout or "{}")
            items = payload.get("items", []) if isinstance(payload, dict) else []
        except Exception:
            items = []
        for item in items:
            spec = item.get("spec") or {}
            node = str(spec.get("nodeName") or "")
            if node not in counts:
                continue
            metadata = item.get("metadata") or {}
            owners = metadata.get("ownerReferences") or []
            owner_kind = str((owners[0].get("kind") if owners else "") or "")
            if owner_kind in {"DaemonSet", "StatefulSet"}:
                continue
            counts[node] += 1
        return counts

    protected_namespaces = _protected_namespaces()

    remaining_nodes = sorted(k8s_worker_names, key=_worker_number)
    removed_workers = []

    print(
        "\n[bold]Scale-down source:[/bold] Kubernetes Ready worker nodes only "
        f"({len(remaining_nodes)} candidate(s))."
    )
    if remaining_nodes:
        preview = ", ".join(remaining_nodes[:12])
        if len(remaining_nodes) > 12:
            preview += ", ..."
        print(f"  [dim]Candidates: {preview}[/dim]")

    print(f"\n[bold]Scale-down plan: remove {nodes_to_remove} worker node(s), one by one.[/bold]")

    for iteration in range(1, nodes_to_remove + 1):
        if not remaining_nodes:
            print("  [yellow]No Kubernetes worker nodes left to remove.[/yellow]")
            break

        candidate_names = list(remaining_nodes)
        pod_counts = _running_pod_counts(candidate_names)

        victims_sorted = sorted(
            candidate_names,
            key=lambda node_name: (pod_counts.get(node_name, 0), -_worker_number(node_name)),
        )
        k8s_node_name = victims_sorted[0]
        running_pods = pod_counts.get(k8s_node_name, 0)

        print(f"\n[bold]Node removal {iteration}/{nodes_to_remove}[/bold]")
        print("  Candidate running pod counts:")
        for node_name in sorted(candidate_names, key=_worker_number):
            print(f"    - {node_name}: {pod_counts.get(node_name, 0)} pod(s)")
        print(
            f"  [cyan]Selected node:[/cyan] {k8s_node_name} "
            f"([cyan]{running_pods}[/cyan] running pod(s), least-loaded priority)"
        )

        node_exists = True

        # --- Step 1: Mark unschedulable ---
        print(f"\n  [bold]Step 1/4:[/bold] Mark node as unschedulable ({k8s_node_name})")
        cordon_result = _ssh_cmd(
            master["ip"], user, key_path,
            f"kubectl cordon {k8s_node_name}",
            check=False,
        )
        cordon_output = (cordon_result.stdout + cordon_result.stderr).lower()
        if (
            cordon_result.returncode != 0
            and "already cordoned" not in cordon_output
            and "notfound" not in cordon_output
        ):
            print(f"  [red]Failed to cordon {k8s_node_name}.[/red]")
            if cordon_result.stderr.strip():
                print(f"  [dim]{cordon_result.stderr.strip()}[/dim]")
            raise typer.Exit(1)
        if "notfound" in cordon_output:
            node_exists = False
            print(f"  [yellow]Node {k8s_node_name} no longer exists in Kubernetes. Skipping node steps.[/yellow]")

        if node_exists:
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

        if node_exists:
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

        if node_exists:
            # --- Step 3: Wait until evictable pods are gone, then remove K8s node object ---
            print(f"\n  [bold]Step 3/4:[/bold] Wait for pod termination and remove Kubernetes node object")
            pods_gone = False
            last_other_pods_signature: tuple[str, ...] = tuple()
            unchanged_polls = 0
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
                    current_signature = tuple(sorted(other_pods))
                    if current_signature == last_other_pods_signature:
                        unchanged_polls += 1
                    else:
                        unchanged_polls = 0
                        last_other_pods_signature = current_signature

                    print(
                        f"  Waiting for evictable pods to terminate on {k8s_node_name}: "
                        f"{evictable_remaining} remaining"
                        f" ({len(daemonset_pods)} DaemonSet pod(s) ignored)..."
                    )
                    for pod_ref in other_pods[:5]:
                        print(f"    [dim]- {pod_ref}[/dim]")

                    # If the same evictable pods remain for too long, force delete and clear finalizers.
                    if unchanged_polls in {6, 12}:
                        print(
                            f"  [yellow]Detected stuck evictable pods on {k8s_node_name}; "
                            "attempting force-delete + finalizer cleanup.[/yellow]"
                        )
                        skipped_protected: set[str] = set()
                        for pod_ref in other_pods:
                            if "/" not in pod_ref:
                                continue
                            namespace, pod_name = pod_ref.split("/", 1)
                            if namespace in protected_namespaces:
                                skipped_protected.add(pod_ref)
                                continue
                            _ssh_cmd(
                                master["ip"],
                                user,
                                key_path,
                                (
                                    f"kubectl delete pod -n {namespace} {pod_name} "
                                    "--grace-period=0 --force --wait=false 2>/dev/null || true"
                                ),
                                check=False,
                            )

                        if skipped_protected:
                            preview = ", ".join(sorted(skipped_protected)[:5])
                            if len(skipped_protected) > 5:
                                preview += ", ..."
                            print(
                                "  [dim]Skipped force finalizer cleanup for protected namespaces: "
                                f"{preview}[/dim]"
                            )
                            _ssh_cmd(
                                master["ip"],
                                user,
                                key_path,
                                (
                                    f"kubectl patch pod -n {namespace} {pod_name} "
                                    "--type=merge -p '{\"metadata\":{\"finalizers\":[]}}' "
                                    "2>/dev/null || true"
                                ),
                                check=False,
                            )

                    # Last-resort escape hatch: keep going with node/VM deletion after prolonged stalling.
                    if unchanged_polls >= 18:
                        print(
                            f"  [yellow]Evictable pods are still stuck on {k8s_node_name}. "
                            "Proceeding with node deletion to avoid indefinite drain loop.[/yellow]"
                        )
                        pods_gone = True
                        break
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
        vm_names = {
            vm["name"]
            for vm in list_azure_vms(resource_group=resource_group, subscription_id=context_id)
            if str(vm.get("name") or "").startswith(f"{cluster_name}-node-")
        }
        if k8s_node_name in vm_names:
            try:
                delete_azure_vm(resource_group, k8s_node_name, subscription_id=context_id)
            except Exception as e:
                print(f"  [red]Failed to delete VM {k8s_node_name}: {e}[/red]")
                raise typer.Exit(1)
        else:
            print(f"  [yellow]No matching VM found for Kubernetes node {k8s_node_name}. Skipping VM deletion.[/yellow]")

        if k8s_node_name in vm_names:
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

        removed_workers.append({"name": k8s_node_name})
        remaining_nodes = [node_name for node_name in remaining_nodes if node_name != k8s_node_name]

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
        title="[bold bright_yellow]Scale Down Complete[/bold bright_yellow]",
    ))
