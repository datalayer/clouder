"""Clouder CLI - kubeadm vm-create command."""

import subprocess

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from ..ctx import get_current_context
from ...util.utils import SSH_FOLDER
from ...util.wait import wait_with_spinner

from ._helpers import _save_cluster_metadata


def register(kubeadm_app: typer.Typer):
    """Register the vm-create command on the given Typer app."""

    @kubeadm_app.command("vm-create")
    def kubeadm_vm_create(
        name: str = typer.Argument(..., help="Cluster name (used as prefix for VMs)."),
        workers: int = typer.Option(3, "--workers", "-w", help="Number of worker nodes."),
        region: str = typer.Option(None, "--region", "-r", help="Cloud region (e.g. eastus, us-east-1)."),
        resource_group: str = typer.Option(None, "--resource-group", "-g", help="Resource group (Azure only)."),
        master_size: str = typer.Option("Standard_B4ms", "--master-size", help="VM size/type for the master node."),
        node_size: str = typer.Option("Standard_B4ms", "--node-size", help="VM size/type for worker nodes."),
        os_disk_size: int = typer.Option(100, "--os-disk-size", help="OS disk size in GB (default 100, min 30)."),
        admin_username: str = typer.Option("", "--admin-user", help="Admin username (default: azureuser on Azure, ubuntu on AWS)."),
        image: str = typer.Option("Ubuntu2204", "--image", help="Image: Ubuntu2204, Ubuntu2404, Debian12."),
    ):
        """Create VMs for a kubeadm Kubernetes cluster (1 master + N workers on the same subnet)."""
        (cloud, context_id) = get_current_context()
        if cloud not in {"azure", "aws"}:
            typer.echo("Kubeadm VM provisioning is currently supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        resolved_admin = admin_username or ("azureuser" if cloud == "azure" else "ubuntu")

        if cloud == "azure":
            _create_kubeadm_azure(
                sub_id=context_id,
                cluster_name=name,
                nodes=workers,
                region=region,
                resource_group=resource_group,
                master_size=master_size,
                node_size=node_size,
                os_disk_size_gb=os_disk_size,
                admin_username=resolved_admin,
                image=image,
            )
            return

        _create_kubeadm_aws(
            account_id=context_id,
            cluster_name=name,
            nodes=workers,
            region=region,
            master_size=master_size,
            node_size=node_size,
            os_disk_size_gb=os_disk_size,
            admin_username=resolved_admin,
        )


def _create_kubeadm_azure(
    sub_id, cluster_name, nodes, region, resource_group,
    master_size, node_size, os_disk_size_gb, admin_username, image,
):
    """Create Azure VMs for a kubeadm cluster: shared VNet/Subnet/NSG, 1 master + N workers."""
    from ...cloud.azure.api import (
        create_azure_vm,
        list_azure_locations,
        list_azure_resource_groups,
    )
    from ...cloud.local.api import get_local_ssh_keys

    # --- Resource group ---
    if not resource_group:
        rgs = list_azure_resource_groups(subscription_id=sub_id)
        new_rg = f"{cluster_name}-rg"
        print("\n[bold]Resource groups:[/bold]")
        for i, rg in enumerate(rgs, 1):
            typer.echo(f"  {i}. {rg['name']} ({rg['location']})")
        new_idx = len(rgs) + 1
        print(f"  {new_idx}. [green]Create new: {new_rg}[/green]")
        choice = Prompt.ask("Select resource group number or type name", default=str(new_idx))
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(rgs):
                resource_group = rgs[idx - 1]["name"]
                if not region:
                    region = rgs[idx - 1]["location"]
            else:
                resource_group = new_rg
        else:
            resource_group = choice

    # --- Region ---
    if not region:
        locations = list_azure_locations(sub_id)
        popular = ["eastus", "eastus2", "westus2", "westeurope", "northeurope", "francecentral"]
        print("\n[bold]Popular regions:[/bold]")
        for i, r in enumerate(popular, 1):
            display = next((loc["display_name"] for loc in locations if loc["name"] == r), r)
            typer.echo(f"  {i}. {r} ({display})")
        choice = Prompt.ask("Select region number or type region name", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(popular):
            region = popular[int(choice) - 1]
        else:
            region = choice

    # --- Image mapping ---
    image_map = {
        "Ubuntu2204": ("Canonical", "0001-com-ubuntu-server-jammy", "22_04-lts-gen2"),
        "Ubuntu2404": ("Canonical", "ubuntu-24_04-lts", "server"),
        "Debian12": ("Debian", "debian-12", "12-gen2"),
    }
    image_info = image_map.get(image, image_map["Ubuntu2204"])

    # --- SSH key ---
    ssh_public_key = None
    ssh_key_name = None
    local_keys = get_local_ssh_keys()
    print("\n[bold]SSH keys:[/bold]")
    for i, key_name in enumerate(local_keys, 1):
        typer.echo(f"  {i}. {key_name}")
    new_idx = len(local_keys) + 1
    print(f"  {new_idx}. [green]Generate new key pair: {cluster_name}-key[/green]")
    choice = Prompt.ask("Select SSH key number or type key name", default="1" if local_keys else str(new_idx))
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(local_keys):
            ssh_key_name = local_keys[idx - 1]
            pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
            ssh_public_key = pub_path.read_text().strip()
        else:
            ssh_key_name = f"{cluster_name}-key"
            key_path = SSH_FOLDER / ssh_key_name
            SSH_FOLDER.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", f"clouder-{cluster_name}"],
                check=True,
            )
            key_path.chmod(0o600)
            ssh_public_key = (SSH_FOLDER / f"{ssh_key_name}.pub").read_text().strip()
            print(f"[green]Generated key pair: {key_path}[/green]")
    else:
        ssh_key_name = choice
        pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
        if pub_path.exists():
            ssh_public_key = pub_path.read_text().strip()
        else:
            typer.echo(f"Public key {pub_path} not found.", err=True)
            raise typer.Exit(1)

    # --- Build VM names ---
    master_name = f"{cluster_name}-master"
    worker_names = [f"{cluster_name}-node-{i + 1}" for i in range(nodes)]
    all_vms = [("master", master_name, master_size)] + [
        ("node", wn, node_size) for wn in worker_names
    ]

    # --- Confirmation ---
    print(f"\n[bold]Kubeadm cluster VMs:[/bold]")
    typer.echo(f"  Cluster Name:   {cluster_name}")
    typer.echo(f"  Resource Group: {resource_group}")
    typer.echo(f"  Region:         {region}")
    typer.echo(f"  Image:          {image}")
    typer.echo(f"  OS Disk:        {os_disk_size_gb} GB")
    typer.echo(f"  Admin User:     {admin_username}")
    typer.echo(f"  SSH Key:        {ssh_key_name or 'None'}")
    typer.echo(f"  Masters:         {master_name} ({master_size})")
    for wn in worker_names:
        typer.echo(f"  Worker:         {wn} ({node_size})")

    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    # --- Step 1: Create shared infrastructure ---
    from ...cloud.azure.api import _get_network_client, _get_resource_client
    from azure.mgmt.resource.resources.models import ResourceGroup as RGModel

    network_client = _get_network_client(sub_id)
    resource_client = _get_resource_client(sub_id)

    # Ensure resource group
    resource_client.resource_groups.create_or_update(
        resource_group, RGModel(location=region),
    )

    # Shared VNet + Subnet
    vnet_name = f"{cluster_name}-vnet"
    subnet_name = f"{cluster_name}-subnet"
    typer.echo(f"\nCreating shared VNet: {vnet_name}...")
    vnet_poller = network_client.virtual_networks.begin_create_or_update(
        resource_group,
        vnet_name,
        {
            "location": region,
            "address_space": {"address_prefixes": ["10.0.0.0/16"]},
            "subnets": [{"name": subnet_name, "address_prefix": "10.0.0.0/24"}],
        },
    )
    vnet = wait_with_spinner(
        lambda: vnet_poller.result(),
        f"Creating shared virtual network {vnet_name}",
    )
    subnet_id = vnet.subnets[0].id
    typer.echo(f"  VNet created: {vnet_name}, Subnet: {subnet_name}")

    # Shared NSG with SSH + Kubernetes rules
    nsg_name = f"{cluster_name}-nsg"
    typer.echo(f"Creating shared NSG: {nsg_name}...")
    nsg_poller = network_client.network_security_groups.begin_create_or_update(
        resource_group,
        nsg_name,
        {
            "location": region,
            "security_rules": [
                {
                    "name": "AllowSSH",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "22",
                    "source_address_prefix": "*",
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1000,
                    "direction": "Inbound",
                },
                {
                    "name": "AllowK8sAPI",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "6443",
                    "source_address_prefix": "*",
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1100,
                    "direction": "Inbound",
                },
                {
                    "name": "AllowKubelet",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "10250",
                    "source_address_prefix": "10.0.0.0/16",
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1200,
                    "direction": "Inbound",
                },
                {
                    "name": "AllowNodePorts",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "30000-32767",
                    "source_address_prefix": "*",
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1300,
                    "direction": "Inbound",
                },
                {
                    "name": "AllowHTTP",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "80",
                    "source_address_prefix": "*",
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1400,
                    "direction": "Inbound",
                },
                {
                    "name": "AllowHTTPS",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "443",
                    "source_address_prefix": "*",
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1500,
                    "direction": "Inbound",
                },
            ],
        },
    )
    nsg = wait_with_spinner(
        lambda: nsg_poller.result(),
        f"Creating shared network security group {nsg_name}",
    )
    nsg_id = nsg.id
    typer.echo(f"  NSG created with SSH, K8s API (6443), Kubelet (10250), NodePort (30000-32767), HTTP (80), HTTPS (443) rules")

    # --- Step 2: Create VMs (reusing shared infra) ---
    results = []
    for role, vm_name, vm_size in all_vms:
        typer.echo(f"\nCreating {role}: {vm_name} ({vm_size})...")
        result = create_azure_vm(
            resource_group=resource_group,
            vm_name=vm_name,
            location=region,
            vm_size=vm_size,
            admin_username=admin_username,
            ssh_public_key=ssh_public_key,
            image_publisher=image_info[0],
            image_offer=image_info[1],
            image_sku=image_info[2],
            subnet_id=subnet_id,
            nsg_id=nsg_id,
            os_disk_size_gb=os_disk_size_gb,
            subscription_id=sub_id,
        )
        typer.echo(f"  {role.capitalize()} created: {result['name']} - IP: {result.get('public_ip', 'N/A')}")
        results.append((role, result))

    # --- Summary ---
    table = Table(title=f"Kubeadm Cluster: {cluster_name}")
    table.add_column("Role", style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Public IP", style="green")
    table.add_column("VM Size", style="dim")
    for role, res in results:
        table.add_row(role.capitalize(), res["name"], res.get("public_ip", "N/A"), res["vm_size"])
    print()
    print(table)

    print(f"\n[yellow]  clouder kubeadm setup {cluster_name}[/yellow]\n")

    # --- Save cluster metadata ---
    master_result = results[0][1]
    worker_results = [(role, res) for role, res in results if role == "node"]
    _save_cluster_metadata(cluster_name, {
        "name": cluster_name,
        "cloud": "azure",
        "subscription_id": sub_id,
        "resource_group": resource_group,
        "region": region,
        "image": image,
        "image_publisher": image_info[0],
        "image_offer": image_info[1],
        "image_sku": image_info[2],
        "os_disk_size_gb": os_disk_size_gb,
        "admin_username": admin_username,
        "ssh_key_name": ssh_key_name,
        "master": {
            "name": master_result["name"],
            "vm_size": master_size,
            "ip": master_result.get("public_ip"),
        },
        "workers": [
            {
                "name": res["name"],
                "vm_size": node_size,
                "ip": res.get("public_ip"),
            }
            for _, res in worker_results
        ],
        "networking": {
            "vnet_name": vnet_name,
            "subnet_name": subnet_name,
            "subnet_id": subnet_id,
            "nsg_name": nsg_name,
            "nsg_id": nsg_id,
        },
    })


def _create_kubeadm_aws(
    account_id, cluster_name, nodes, region,
    master_size, node_size, os_disk_size_gb, admin_username,
):
    """Create AWS EC2 VMs for a kubeadm cluster with shared VPC/Subnet/Security Group."""
    from ...cloud.aws.api import (
        _client,
        create_aws_kubeadm_network,
        create_aws_vm,
        resolve_ubuntu_ami,
    )

    if not region:
        region = "us-east-1"

    # AWS instance type defaults equivalent to Azure defaults
    if master_size == "Standard_B4ms":
        master_size = "t3.large"
    if node_size == "Standard_B4ms":
        node_size = "t3.large"

    ec2 = _client("ec2", region=region)

    # Key pair selection
    key_pairs = ec2.describe_key_pairs().get("KeyPairs", [])
    if not key_pairs:
        typer.echo("No EC2 key pair found in this region. Create one first with AWS CLI or console.", err=True)
        raise typer.Exit(1)

    print("\n[bold]AWS EC2 key pairs:[/bold]")
    for i, kp in enumerate(key_pairs, 1):
        typer.echo(f"  {i}. {kp['KeyName']}")
    choice = Prompt.ask("Select EC2 key pair number or type key name", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(key_pairs):
        ssh_key_name = key_pairs[int(choice) - 1]["KeyName"]
    else:
        ssh_key_name = choice

    vpc_cidr = "10.0.0.0/16"
    subnet_cidr = "10.0.0.0/24"
    ami_id = resolve_ubuntu_ami(region=region)

    master_name = f"{cluster_name}-master"
    worker_names = [f"{cluster_name}-node-{i + 1}" for i in range(nodes)]

    print("\n[bold]Kubeadm cluster VMs (AWS):[/bold]")
    typer.echo(f"  Account:        {account_id}")
    typer.echo(f"  Region:         {region}")
    typer.echo(f"  AMI:            {ami_id}")
    typer.echo(f"  VPC CIDR:       {vpc_cidr}")
    typer.echo(f"  Subnet CIDR:    {subnet_cidr}")
    typer.echo(f"  Admin User:     {admin_username}")
    typer.echo(f"  EC2 Key Pair:   {ssh_key_name}")
    typer.echo(f"  Masters:         {master_name} ({master_size})")
    for wn in worker_names:
        typer.echo(f"  Worker:         {wn} ({node_size})")

    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    network = create_aws_kubeadm_network(
        cluster_name=cluster_name,
        vpc_cidr=vpc_cidr,
        subnet_cidr=subnet_cidr,
        allowed_ssh_cidrs=["0.0.0.0/0"],
        region=region,
    )

    results = []
    for role, vm_name, vm_type in [("master", master_name, master_size)] + [("node", wn, node_size) for wn in worker_names]:
        typer.echo(f"\nCreating {role}: {vm_name} ({vm_type})...")
        result = create_aws_vm(
            vm_name=vm_name,
            instance_type=vm_type,
            key_name=ssh_key_name,
            subnet_id=network["subnet_id"],
            security_group_id=network["security_group_id"],
            ami_id=ami_id,
            root_volume_size_gb=os_disk_size_gb,
            tags={
                "datalayer.io/cluster": cluster_name,
                "datalayer.io/role": role,
                "datalayer.io/component": "kubeadm",
            },
            region=region,
        )
        typer.echo(f"  {role.capitalize()} created: {result['name']} - IP: {result.get('public_ip', 'N/A')}")
        results.append((role, result))

    table = Table(title=f"Kubeadm Cluster: {cluster_name} (AWS)")
    table.add_column("Role", style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Public IP", style="green")
    table.add_column("Instance Type", style="dim")
    for role, res in results:
        table.add_row(role.capitalize(), res["name"], res.get("public_ip", "N/A"), res["instance_type"])
    print()
    print(table)

    print(f"\n[yellow]  clouder kubeadm setup {cluster_name}[/yellow]\n")

    master_result = results[0][1]
    worker_results = [(role, res) for role, res in results if role == "node"]
    _save_cluster_metadata(cluster_name, {
        "name": cluster_name,
        "cloud": "aws",
        "account_id": account_id,
        "region": region,
        "ami_id": ami_id,
        "os_disk_size_gb": os_disk_size_gb,
        "admin_username": admin_username,
        "ssh_key_name": ssh_key_name,
        "master": {
            "name": master_result["name"],
            "vm_size": master_size,
            "ip": master_result.get("public_ip"),
            "instance_id": master_result.get("id"),
        },
        "workers": [
            {
                "name": res["name"],
                "vm_size": node_size,
                "ip": res.get("public_ip"),
                "instance_id": res.get("id"),
            }
            for _, res in worker_results
        ],
        "networking": {
            "vpc_id": network["vpc_id"],
            "subnet_id": network["subnet_id"],
            "security_group_id": network["security_group_id"],
            "internet_gateway_id": network["internet_gateway_id"],
            "route_table_id": network["route_table_id"],
        },
    })
