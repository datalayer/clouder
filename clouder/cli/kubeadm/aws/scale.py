"""AWS-specific kubeadm scale operations."""

from __future__ import annotations

import json
import re
import time

import typer
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from rich import print
from rich.panel import Panel

from ....cloud.aws.api import (
    create_aws_vm,
    list_aws_vms,
    terminate_aws_vm,
    wait_aws_instances_terminated,
)
from ....util.utils import kubeadm_kubeconfig_path
from .._helpers import (
    _SCRIPT_PREREQS,
    _SCRIPT_UPGRADE_KUBELET,
    _SCRIPT_WORKER_FEATURE_GATE,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _save_cluster_metadata,
    _ssh_cmd,
    _update_cluster_metadata,
)


def _build_k8s_api(cluster_name: str) -> k8s_client.CoreV1Api | None:
    kubeconfig_path = kubeadm_kubeconfig_path(cluster_name)
    if not kubeconfig_path.exists():
        return None
    try:
        k8s_config.load_kube_config(config_file=str(kubeconfig_path))
        return k8s_client.CoreV1Api()
    except Exception:
        return None


def _registered_worker_names(core_v1: k8s_client.CoreV1Api | None, cluster_name: str) -> set[str]:
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


def _registered_worker_vm_names(
    core_v1: k8s_client.CoreV1Api | None,
    cluster_name: str,
    workers: list[dict[str, str]],
) -> set[str]:
    """Resolve registered worker VM names from Kubernetes nodes.

    This matches by explicit node name, AWS provider instance id, and node IP
    addresses so clusters that currently expose IP-based node names are still
    counted correctly during scale decisions.
    """
    if core_v1 is None:
        return set()

    try:
        nodes = core_v1.list_node().items
    except Exception:
        return set()

    by_name = {
        str(worker.get("name") or ""): str(worker.get("name") or "")
        for worker in workers
        if str(worker.get("name") or "")
    }
    by_instance_id = {
        str(worker.get("instance_id") or ""): str(worker.get("name") or "")
        for worker in workers
        if str(worker.get("instance_id") or "") and str(worker.get("name") or "")
    }
    by_public_ip = {
        str(worker.get("ip") or ""): str(worker.get("name") or "")
        for worker in workers
        if str(worker.get("ip") or "") and str(worker.get("name") or "")
    }
    by_private_ip = {
        str(worker.get("private_ip") or ""): str(worker.get("name") or "")
        for worker in workers
        if str(worker.get("private_ip") or "") and str(worker.get("name") or "")
    }

    prefix = f"{cluster_name}-node-"
    resolved: set[str] = set()

    for node in nodes:
        node_name = str(getattr(getattr(node, "metadata", None), "name", "") or "")
        if node_name in by_name:
            resolved.add(by_name[node_name])
            continue

        if node_name.startswith(prefix) and node_name in by_name:
            resolved.add(by_name[node_name])
            continue

        provider_id = str(getattr(getattr(node, "spec", None), "provider_id", "") or "")
        if provider_id:
            instance_id = provider_id.rsplit("/", 1)[-1].strip()
            if instance_id in by_instance_id:
                resolved.add(by_instance_id[instance_id])
                continue

        addresses = getattr(getattr(node, "status", None), "addresses", None) or []
        for address in addresses:
            ip = str(getattr(address, "address", "") or "")
            if not ip:
                continue
            if ip in by_private_ip:
                resolved.add(by_private_ip[ip])
                break
            if ip in by_public_ip:
                resolved.add(by_public_ip[ip])
                break

    return resolved


def _wait_for_node_ready_api(core_v1: k8s_client.CoreV1Api, node_name: str, timeout_seconds: int = 300) -> bool:
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


def _apply_node_labels_with_retry_api(
    core_v1: k8s_client.CoreV1Api,
    node_name: str,
    labels: list[str],
    timeout_seconds: int = 300,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            patch_labels: dict[str, str] = {}
            for label in labels:
                key, value = label.split("=", 1)
                patch_labels[key] = value
            if patch_labels:
                core_v1.patch_node(node_name, {"metadata": {"labels": patch_labels}})
            return True
        except ApiException as exc:
            if exc.status != 404:
                pass
        except Exception:
            pass
        time.sleep(5)
    return False


def _worker_index_from_name(cluster_name: str, worker_name: str) -> int | None:
    pattern = rf"^{re.escape(cluster_name)}-node-(\d+)(?:-[a-z0-9]{{4}})?$"
    match = re.match(pattern, worker_name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _build_unique_worker_name(cluster_name: str, index: int, existing_names: set[str]) -> str:
    import uuid

    for _ in range(16):
        slug = uuid.uuid4().hex[:4]
        candidate = f"{cluster_name}-node-{index}-{slug}"
        if candidate not in existing_names:
            existing_names.add(candidate)
            return candidate
    candidate = f"{cluster_name}-node-{index}-{uuid.uuid4().hex[:8]}"
    existing_names.add(candidate)
    return candidate


def _get_join_command(master_ip: str, ssh_user: str, key_path: str) -> str:
    cmd = (
        "sudo kubeadm token create --print-join-command "
        "--ttl 30m 2>/dev/null || true"
    )
    result = _ssh_cmd(master_ip, ssh_user, key_path, cmd, check=False)
    value = (result.stdout or "").strip()
    if not value.startswith("kubeadm join "):
        raise RuntimeError("Unable to obtain kubeadm join command from master.")
    return value


def _run_script(ip: str, ssh_user: str, key_path: str, script: str) -> tuple[str, str, int]:
    result = _ssh_cmd(ip, ssh_user, key_path, script, check=False)
    return (
        str(result.stdout or ""),
        str(result.stderr or ""),
        int(result.returncode or 0),
    )


def _prepare_and_join_workers(
    cluster_name: str,
    core_v1: k8s_client.CoreV1Api,
    workers: list[dict[str, str]],
    master: dict,
    admin_username: str,
    key_path: str,
    node_labels: list[str],
) -> list[dict[str, str]]:
    if not workers:
        return []

    join_command = _get_join_command(master["ip"], admin_username, key_path)
    prepared: list[dict[str, str]] = []

    for worker in workers:
        worker_name = str(worker.get("name") or "")
        worker_ip = str(worker.get("ip") or "")
        if not worker_name or not worker_ip:
            print(f"  [yellow]Skipping worker with missing name/ip: {worker}[/yellow]")
            continue

        print(f"  [cyan]{worker_name}[/cyan] installing prerequisites...")
        prereq_out, prereq_err, prereq_code = _run_script(worker_ip, admin_username, key_path, _SCRIPT_PREREQS)
        if prereq_code != 0 or "__DATALAYER_PREREQS_OK__" not in prereq_out:
            print(f"  [yellow]Skipping {worker_name}: prerequisites failed.[/yellow]")
            if prereq_err.strip():
                print(f"    [dim]{prereq_err.strip()}[/dim]")
            continue

        print(f"  [cyan]{worker_name}[/cyan] upgrading kubelet stack...")
        up_out, up_err, up_code = _run_script(worker_ip, admin_username, key_path, _SCRIPT_UPGRADE_KUBELET)
        if up_code != 0 or "__DATALAYER_KUBELET_UPGRADE_OK__" not in up_out:
            print(f"  [yellow]Skipping {worker_name}: kubelet upgrade failed.[/yellow]")
            if up_err.strip():
                print(f"    [dim]{up_err.strip()}[/dim]")
            continue

        print(f"  [cyan]{worker_name}[/cyan] joining cluster...")
        join_out, join_err, join_code = _run_script(
            worker_ip,
            admin_username,
            key_path,
            f"sudo {join_command} --node-name {worker_name} --v=5",
        )
        join_combined = "\n".join(
            part for part in (join_out.lower(), join_err.lower()) if part
        )
        fatal_markers = (
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
        if join_code != 0 or any(marker in join_combined for marker in fatal_markers):
            print(f"  [yellow]Skipping {worker_name}: join failed.[/yellow]")
            if join_err.strip():
                print(f"    [dim]{join_err.strip()}[/dim]")
            continue

        _run_script(worker_ip, admin_username, key_path, _SCRIPT_WORKER_FEATURE_GATE)

        print(f"  [cyan]{worker_name}[/cyan] waiting for Ready...")
        if not _wait_for_node_ready_api(core_v1, worker_name):
            print(f"  [yellow]{worker_name} did not become Ready in time.[/yellow]")
            continue

        _apply_node_labels_with_retry_api(core_v1, worker_name, node_labels)
        print(f"  [green]{worker_name} joined and labeled.[/green]")
        prepared.append(worker)

    return prepared


def scale_up(
    cluster_name,
    context_id,
    master,
    current_workers,
    new_count,
    workers_to_reconcile,
    region,
    node_size,
    admin_username,
    ssh_key_name,
    subnet_id,
    security_group_id,
    ami_id,
    key_path,
    user,
    metadata,
    node_labels,
    os_disk_size_gb,
):
    """Scale up kubeadm AWS workers."""
    core_v1 = _build_k8s_api(cluster_name)
    if core_v1 is None:
        typer.echo(
            "Kubeconfig is required for kubeadm scaling validation. Run 'clouder kubeadm get-config <cluster>' first.",
            err=True,
        )
        raise typer.Exit(1)

    if not region:
        region = (metadata or {}).get("region")
    if not region:
        raise typer.BadParameter("AWS region is required for scaling. Set metadata.region or AWS_REGION.")

    resolved_metadata = metadata or _load_cluster_metadata(cluster_name) or {}
    networking = resolved_metadata.get("networking", {})
    subnet_id = subnet_id or networking.get("subnet_id")
    security_group_id = security_group_id or networking.get("security_group_id")
    ami_id = ami_id or resolved_metadata.get("ami_id")
    ssh_key_name = ssh_key_name or resolved_metadata.get("ssh_key_name")

    if not subnet_id or not security_group_id or not ami_id or not ssh_key_name:
        raise typer.BadParameter(
            "Missing AWS metadata for scaling (subnet_id, security_group_id, ami_id, ssh_key_name). "
            "Re-create metadata with `clouder kubeadm create` or provide complete cluster metadata."
        )

    discovered_registered_workers = _registered_worker_vm_names(
        core_v1,
        cluster_name,
        current_workers,
    )
    workers_to_reconcile = [
        worker
        for worker in current_workers
        if str(worker.get("name") or "") not in discovered_registered_workers
    ]

    reconciled_workers = _prepare_and_join_workers(
        cluster_name=cluster_name,
        core_v1=core_v1,
        workers=list(workers_to_reconcile or []),
        master=master,
        admin_username=admin_username,
        key_path=key_path,
        node_labels=node_labels,
    )

    refreshed_cluster = _resolve_cluster_vms(cluster_name, cloud="aws", context_id=context_id)
    refreshed_workers = refreshed_cluster["workers"]
    registered_after_reconcile = _registered_worker_vm_names(
        core_v1,
        cluster_name,
        refreshed_workers,
    )

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

    new_workers: list[dict[str, str]] = []
    if new_worker_names:
        print(f"\n[bold]Creating {len(new_worker_names)} AWS worker VM(s)...[/bold]")
        for vm_name in new_worker_names:
            result = create_aws_vm(
                vm_name=vm_name,
                instance_type=node_size,
                key_name=ssh_key_name,
                subnet_id=subnet_id,
                security_group_id=security_group_id,
                ami_id=ami_id,
                root_volume_size_gb=os_disk_size_gb or int(resolved_metadata.get("os_disk_size_gb") or 100),
                tags={
                    "datalayer.io/cluster": cluster_name,
                    "datalayer.io/role": "node",
                    "datalayer.io/component": "kubeadm",
                },
                region=region,
            )
            print(f"  [green]{vm_name} created - IP: {result.get('public_ip', 'N/A')}[/green]")
            new_workers.append(
                {
                    "name": str(result.get("name") or vm_name),
                    "ip": str(result.get("public_ip") or ""),
                    "instance_id": str(result.get("id") or ""),
                }
            )

        _prepare_and_join_workers(
            cluster_name=cluster_name,
            core_v1=core_v1,
            workers=new_workers,
            master=master,
            admin_username=admin_username,
            key_path=key_path,
            node_labels=node_labels,
        )

    latest_cluster = _resolve_cluster_vms(cluster_name, cloud="aws", context_id=context_id)
    worker_entries = [
        {
            "name": w.get("name"),
            "vm_size": node_size,
            "ip": w.get("ip"),
            "private_ip": w.get("private_ip"),
            "instance_id": w.get("instance_id"),
        }
        for w in latest_cluster["workers"]
    ]

    if resolved_metadata:
        _update_cluster_metadata(
            cluster_name,
            {
                "cloud": "aws",
                "account_id": context_id,
                "region": region,
                "node_size": node_size,
                "workers": worker_entries,
            },
        )
    else:
        _save_cluster_metadata(
            cluster_name,
            {
                "name": cluster_name,
                "cloud": "aws",
                "account_id": context_id,
                "region": region,
                "admin_username": user,
                "master": {
                    "name": master["name"],
                    "vm_size": "unknown",
                    "ip": master["ip"],
                    "instance_id": master.get("instance_id"),
                },
                "workers": worker_entries,
            },
        )

    created_names = [w["name"] for w in new_workers]
    print(
        Panel(
            f"[green]Scaled up cluster '{cluster_name}' to {new_count} workers.[/green]\n\n"
            f"  Reconciled: {', '.join(w.get('name') for w in reconciled_workers) or '-'}\n"
            f"  New nodes:  {', '.join(created_names) or '-'}\n"
            f"  Check:      clouder kubectl {cluster_name} get nodes",
            title="[bold bright_green]AWS Scale Up Complete[/bold bright_green]",
        )
    )


def scale_down(
    cluster_name,
    context_id,
    master,
    current_workers,
    registered_worker_names,
    new_count,
    region,
    key_path,
    user,
    metadata,
):
    """Scale down kubeadm AWS workers."""
    core_v1 = _build_k8s_api(cluster_name)
    if core_v1 is None:
        typer.echo(
            "Kubeconfig is required for node-first scale-down. Run 'clouder kubeadm get-config <cluster>' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Build Ready-only worker set.
    ready_workers: set[str] = set()
    try:
        nodes = core_v1.list_node().items
        prefix = f"{cluster_name}-node-"
        for node in nodes:
            node_name = str(getattr(getattr(node, "metadata", None), "name", "") or "")
            if not node_name.startswith(prefix):
                continue
            for condition in (getattr(getattr(node, "status", None), "conditions", None) or []):
                if condition.type == "Ready" and condition.status == "True":
                    ready_workers.add(node_name)
                    break
    except Exception:
        pass

    k8s_worker_names = set(ready_workers)
    if registered_worker_names:
        k8s_worker_names = k8s_worker_names or set(registered_worker_names)

    nodes_to_remove = len(k8s_worker_names) - int(new_count)
    if nodes_to_remove <= 0:
        print(
            f"\n[green]No Kubernetes workers to remove: current={len(k8s_worker_names)}, desired={new_count}.[/green]"
        )
        return

    current_worker_map = {
        str(w.get("name") or ""): {
            "instance_id": str(w.get("instance_id") or ""),
            "ip": str(w.get("ip") or ""),
        }
        for w in current_workers
    }

    if not region:
        region = (metadata or {}).get("region")

    remaining_nodes = sorted(k8s_worker_names)
    removed_workers: list[dict[str, str]] = []

    for iteration in range(1, nodes_to_remove + 1):
        if not remaining_nodes:
            break

        node_name = remaining_nodes[0]
        print(f"\n[bold]Removing node {iteration}/{nodes_to_remove}: {node_name}[/bold]")

        _ssh_cmd(master["ip"], user, key_path, f"kubectl cordon {node_name} --ignore-daemonsets=true", check=False)
        _ssh_cmd(
            master["ip"],
            user,
            key_path,
            (
                f"kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data "
                "--force --grace-period=30 --timeout=180s"
            ),
            check=False,
        )
        _ssh_cmd(master["ip"], user, key_path, f"kubectl delete node {node_name} --ignore-not-found=true", check=False)

        instance_id = current_worker_map.get(node_name, {}).get("instance_id")
        if not instance_id:
            # Fallback discover by name from live inventory.
            for vm in list_aws_vms(region=region):
                if str(vm.get("name") or "") == node_name:
                    instance_id = str(vm.get("id") or "")
                    break

        if instance_id:
            terminate_aws_vm(instance_id=instance_id, region=region)
            wait_aws_instances_terminated([instance_id], region=region)
            print(f"  [green]AWS instance terminated for {node_name}: {instance_id}[/green]")
        else:
            print(f"  [yellow]No AWS instance found for {node_name}; Kubernetes node removed only.[/yellow]")

        removed_workers.append({"name": node_name})
        remaining_nodes = [n for n in remaining_nodes if n != node_name]

    victim_names = {v["name"] for v in removed_workers}
    latest_cluster = _resolve_cluster_vms(cluster_name, cloud="aws", context_id=context_id)

    if metadata:
        remaining_workers = [
            {
                "name": w.get("name"),
                "vm_size": w.get("vm_size") or metadata.get("node_size") or "unknown",
                "ip": w.get("ip"),
                "instance_id": w.get("instance_id"),
            }
            for w in latest_cluster["workers"]
            if w.get("name") not in victim_names
        ]
        _update_cluster_metadata(cluster_name, {"workers": remaining_workers})
    else:
        _save_cluster_metadata(
            cluster_name,
            {
                "name": cluster_name,
                "cloud": "aws",
                "account_id": context_id,
                "region": region,
                "admin_username": user,
                "master": {
                    "name": master["name"],
                    "vm_size": "unknown",
                    "ip": master["ip"],
                    "instance_id": master.get("instance_id"),
                },
                "workers": [
                    {
                        "name": w.get("name"),
                        "vm_size": "unknown",
                        "ip": w.get("ip"),
                        "instance_id": w.get("instance_id"),
                    }
                    for w in latest_cluster["workers"]
                ],
            },
        )

    print(
        Panel(
            f"[green]Scaled down cluster '{cluster_name}' to {new_count} workers.[/green]\n\n"
            f"  Removed: {', '.join(v['name'] for v in removed_workers) or '-'}\n"
            f"  Check:   clouder kubectl {cluster_name} get nodes",
            title="[bold bright_yellow]AWS Scale Down Complete[/bold bright_yellow]",
        )
    )
