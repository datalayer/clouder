"""Azure-specific kubeadm setup helpers."""

from __future__ import annotations

import base64

from rich import print


def install_storage(
    cluster_name: str,
    metadata: dict,
    all_nodes: list[dict],
    master: dict,
    context_id: str,
    resolved_user: str,
    key_path: str,
) -> bool:
    """Install Azure storage integration components on an initialized cluster."""
    from .._helpers import (
        _SCRIPT_INSTALL_AZURE_DISK_CSI,
        _SCRIPT_INSTALL_AZURE_FILE_CSI,
        _build_azure_cloud_config,
        _build_azure_nfs_storageclass_script,
        _get_or_create_azure_sp,
        _ssh_cmd_stream,
    )

    storage_ok = False

    if not metadata:
        print("[yellow]  No cluster metadata found - skipping storage setup.[/yellow]")
        print("  Run the storage setup manually. See: https://clouder.sh/cluster/cli/kubeadm")
        return storage_ok

    tenant_id, client_id, client_secret = _get_or_create_azure_sp(
        context_id,
        metadata.get("resource_group", ""),
        cluster_name,
    )
    if not all([tenant_id, client_id, client_secret]):
        print("[yellow]  Azure SP credentials not available - skipping storage setup.[/yellow]")
        print("  Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET and re-run,")
        print("  or install the Azure Disk CSI driver manually.")
        return storage_ok

    networking = metadata.get("networking", {})
    azure_config = _build_azure_cloud_config(
        tenant_id=tenant_id,
        subscription_id=context_id,
        resource_group=metadata.get("resource_group", ""),
        location=metadata.get("region", ""),
        client_id=client_id,
        client_secret=client_secret,
        vnet_name=networking.get("vnet_name", ""),
        subnet_name=networking.get("subnet_name", ""),
        nsg_name=networking.get("nsg_name", ""),
    )

    config_b64 = base64.b64encode(azure_config.encode()).decode()
    deploy_cmd = (
        f"echo '{config_b64}' | base64 -d "
        "| sudo tee /etc/kubernetes/azure.json > /dev/null "
        "&& sudo chmod 600 /etc/kubernetes/azure.json"
    )
    for node in all_nodes:
        print(f"  Deploying cloud config to [cyan]{node['name']}[/cyan]...")
        rc = _ssh_cmd_stream(node["ip"], resolved_user, key_path, deploy_cmd)
        if rc != 0:
            print(f"  [red]Failed to deploy cloud config on {node['name']}[/red]")

    secret_cmd = (
        "sudo cat /etc/kubernetes/azure.json | kubectl create secret generic azure-cloud-provider "
        "--from-file=cloud-config=/dev/stdin "
        "-n kube-system --dry-run=client -o yaml | kubectl apply -f -"
    )
    print("  Creating azure-cloud-provider secret...")
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, secret_cmd)
    if rc != 0:
        print("  [red]Failed to create cloud-provider secret.[/red]")
        return storage_ok

    print("  Installing Azure Disk CSI driver...")
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, _SCRIPT_INSTALL_AZURE_DISK_CSI)
    if rc != 0:
        print("[red]  Azure Disk CSI driver installation failed.[/red]")
        return storage_ok

    print("  [green]Azure Disk CSI installed (default StorageClass: managed-csi).[/green]")
    storage_ok = True

    print("  Installing Azure File CSI driver...")
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, _SCRIPT_INSTALL_AZURE_FILE_CSI)
    if rc != 0:
        print("[red]  Azure File CSI driver installation failed.[/red]")
        return storage_ok

    print("  [green]Azure File CSI installed (provisioner: file.csi.azure.com).[/green]")

    print("  Creating azure-nfs StorageClass...")
    sc_script = _build_azure_nfs_storageclass_script(
        subscription_id=context_id,
        resource_group=metadata.get("resource_group", ""),
        location=metadata.get("region", ""),
    )
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, sc_script)
    if rc != 0:
        print("[red]  azure-nfs StorageClass creation failed.[/red]")
    else:
        print("  [green]azure-nfs StorageClass created.[/green]")

    return storage_ok


def install_loadbalancer(
    cluster_name: str,
    metadata: dict,
    all_nodes: list[dict],
    master: dict,
    context_id: str,
    resolved_user: str,
    key_path: str,
) -> bool:
    """Install Azure load balancer provider components during kubeadm setup."""
    _ = (cluster_name, metadata, all_nodes, master, context_id, resolved_user, key_path)
    # Azure load balancer provisioning is handled by ingress commands.
    return False
