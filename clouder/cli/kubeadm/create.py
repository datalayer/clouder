"""Clouder CLI - kubeadm create command."""

import subprocess
import uuid

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from ...util.utils import SSH_FOLDER
from ...util.wait import wait_with_spinner

from ._helpers import _save_cluster_metadata, resolve_kubeadm_cloud_context


def _ensure_local_ssh_keypair(key_name: str, comment: str) -> tuple[str, str]:
    """Ensure a local SSH key pair exists in ~/.ssh with secure permissions.

    Returns (private_key_name, public_key_data).
    """

    SSH_FOLDER.mkdir(parents=True, exist_ok=True)
    SSH_FOLDER.chmod(0o700)

    key_path = SSH_FOLDER / key_name
    pub_path = SSH_FOLDER / f"{key_name}.pub"

    if not key_path.exists() or not pub_path.exists():
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                str(key_path),
                "-N",
                "",
                "-C",
                comment,
            ],
            check=True,
        )

    key_path.chmod(0o600)
    pub_path.chmod(0o644)

    return key_name, pub_path.read_text().strip()


def _create_aws_key_pair_locally(ec2_client, key_name: str) -> str:
    """Create an EC2 key pair and save private key under ~/.ssh as .pem."""
    SSH_FOLDER.mkdir(parents=True, exist_ok=True)
    SSH_FOLDER.chmod(0o700)

    response = ec2_client.create_key_pair(KeyName=key_name)
    key_material = (response.get("KeyMaterial") or "").strip()
    if not key_material:
        raise RuntimeError("AWS did not return private key material for the new key pair.")

    file_stem = key_name[:-4] if key_name.endswith(".pem") else key_name
    key_path = SSH_FOLDER / f"{file_stem}.pem"
    if key_path.exists():
        suffix = 2
        while (SSH_FOLDER / f"{file_stem}-{suffix}.pem").exists():
            suffix += 1
        key_path = SSH_FOLDER / f"{file_stem}-{suffix}.pem"

    key_path.write_text(key_material + "\n")
    key_path.chmod(0o600)
    return str(key_path)


def _build_name_with_slug(base_name: str, existing_names: set[str]) -> str:
    """Build a unique VM name by appending a short slug suffix."""
    for _ in range(16):
        candidate = f"{base_name}-{uuid.uuid4().hex[:4]}"
        if candidate not in existing_names:
            existing_names.add(candidate)
            return candidate
    candidate = f"{base_name}-{uuid.uuid4().hex[:8]}"
    existing_names.add(candidate)
    return candidate


def _select_supported_aws_availability_zone(ec2_client, instance_types: list[str]) -> str | None:
    """Return an AZ in the current region that supports all requested instance types."""
    if not instance_types:
        return None

    az_response = ec2_client.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )
    available_azs = {
        az.get("ZoneName")
        for az in az_response.get("AvailabilityZones", [])
        if az.get("ZoneName") and az.get("ZoneType", "availability-zone") == "availability-zone"
    }
    if not available_azs:
        return None

    common_azs: set[str] | None = None
    for instance_type in instance_types:
        pager = ec2_client.get_paginator("describe_instance_type_offerings")
        supported: set[str] = set()
        for page in pager.paginate(
            LocationType="availability-zone",
            Filters=[
                {"Name": "instance-type", "Values": [instance_type]},
            ],
        ):
            for offering in page.get("InstanceTypeOfferings", []):
                location = offering.get("Location")
                if location in available_azs:
                    supported.add(location)

        if not supported:
            return None

        common_azs = supported if common_azs is None else (common_azs & supported)
        if not common_azs:
            return None

    return sorted(common_azs)[0] if common_azs else None


def _cluster_exists(cloud: str, context_id: str, cluster_name: str, region: str | None = None) -> bool:
    """Return True when VMs for the cluster already exist."""
    master_prefix = f"{cluster_name}-master"
    worker_prefix = f"{cluster_name}-node-"

    if cloud == "azure":
        from ...cloud.azure.api import list_azure_vms

        vm_names = [str(vm.get("name") or "") for vm in list_azure_vms(subscription_id=context_id)]
    elif cloud == "aws":
        from ...cloud.aws.api import list_aws_vms

        vm_names = [str(vm.get("name") or "") for vm in list_aws_vms(region=region)]
    else:
        return False

    return any(
        name == master_prefix
        or name.startswith(f"{master_prefix}-")
        or name.startswith(worker_prefix)
        for name in vm_names
    )


def register(kubeadm_app: typer.Typer):
    """Register the create command on the given Typer app."""

    @kubeadm_app.command("create")
    def kubeadm_create(
        name: str = typer.Argument(..., help="Cluster name (used as prefix for VMs)."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to current context cloud."),
        workers: int = typer.Option(3, "--workers", "-w", help="Number of worker nodes."),
        region: str = typer.Option(None, "--region", "-r", help="Cloud region (e.g. eastus, us-east-1)."),
        resource_group: str = typer.Option(None, "--resource-group", "-g", help="Resource group (Azure only)."),
        master_size: str | None = typer.Option(None, "--master-size", help="VM size/type for the master node (default: prompt, fallback Standard_B4ms)."),
        node_size: str | None = typer.Option(None, "--node-size", help="VM size/type for worker nodes (default: prompt, fallback Standard_B4ms)."),
        os_disk_size: int = typer.Option(100, "--os-disk-size", help="OS disk size in GB (default 100, min 30)."),
        admin_username: str = typer.Option("", "--admin-user", help="Admin username (default: azureuser on Azure, ubuntu on AWS)."),
        image: str | None = typer.Option(None, "--image", help="Image: Ubuntu2204, Ubuntu2404, Debian12."),
    ):
        """Create VMs for a kubeadm Kubernetes cluster (1 master + N workers on the same subnet)."""
        if os_disk_size < 30:
            typer.echo("--os-disk-size must be at least 30 GB.", err=True)
            raise typer.Exit(1)

        (cloud, context_id) = resolve_kubeadm_cloud_context(cloud=cloud)
        if cloud not in {"azure", "aws"}:
            typer.echo("Kubeadm VM provisioning is currently supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        # Keep AWS region/account scoping explicit and deterministic for create.
        resolved_region = region
        if cloud == "aws" and not resolved_region:
            resolved_region = "us-east-1"

        if cloud == "aws":
            from ...cloud.aws.api import get_aws_identity

            current_account = str(get_aws_identity().get("account_id") or "")
            if current_account and current_account != context_id:
                typer.echo(
                    (
                        "AWS context/account mismatch: "
                        f"current credentials are for account '{current_account}' "
                        f"but selected context is '{context_id}'."
                    ),
                    err=True,
                )
                typer.echo(
                    "Switch AWS credentials/profile or run `clouder ctx set aws <account_id>` to align context.",
                    err=True,
                )
                raise typer.Exit(1)

        if _cluster_exists(cloud=cloud, context_id=context_id, cluster_name=name, region=resolved_region):
            print(f"[red]Cluster '{name}' already exists.[/red]")
            print("[yellow]Use another name, terminate the existing cluster first, or run setup/info on the existing cluster.[/yellow]")
            raise typer.Exit(1)

        resolved_admin = admin_username or ("azureuser" if cloud == "azure" else "ubuntu")

        if cloud == "azure":
            _create_kubeadm_azure(
                sub_id=context_id,
                cluster_name=name,
                nodes=workers,
                region=resolved_region,
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
            region=resolved_region,
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
    from azure.core.exceptions import HttpResponseError
    from ...cloud.azure.api import (
        create_azure_vm,
        list_azure_locations,
        list_azure_popular_vm_images,
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
        print(f"  {new_idx}. Create new: {new_rg} [green](default)[/green]")
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
            default_suffix = " [green](default)[/green]" if i == 1 else ""
            print(f"  {i}. {r} ({display}){default_suffix}")
        choice = Prompt.ask("Select region number or type region name", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(popular):
            region = popular[int(choice) - 1]
        else:
            region = choice

    # --- Image proposal based on selected region ---
    if not image:
        popular_images = list_azure_popular_vm_images(location=region, subscription_id=sub_id)
        available_images = [img for img in popular_images if img.get("available")]
        proposed_images = available_images or popular_images

        default_image_name = "Ubuntu2204"
        if not any(img["name"] == default_image_name for img in proposed_images):
            default_image_name = proposed_images[0]["name"]

        print(f"\n[bold]Popular VM images in {region}:[/bold]")
        for i, img in enumerate(proposed_images, 1):
            availability_note = "" if img.get("available") else " [yellow](not available)[/yellow]"
            default_suffix = " [green](default)[/green]" if img["name"] == default_image_name else ""
            print(f"  {i}. {img['name']}{availability_note}{default_suffix}")

        default_idx = next(
            (i for i, img in enumerate(proposed_images, 1) if img["name"] == default_image_name),
            1,
        )
        choice = Prompt.ask("Select image number or type image name", default=str(default_idx))
        if choice.isdigit() and 1 <= int(choice) <= len(proposed_images):
            image = proposed_images[int(choice) - 1]["name"]
        else:
            image = choice

    # --- Image mapping ---
    image_map = {
        "Ubuntu2204": ("Canonical", "0001-com-ubuntu-server-jammy", "22_04-lts-gen2"),
        "Ubuntu2404": ("Canonical", "ubuntu-24_04-lts", "server"),
        "Debian12": ("Debian", "debian-12", "12-gen2"),
    }
    image_info = image_map.get(image, image_map["Ubuntu2204"])

    # --- VM size proposal ---
    vm_sizes = [
        "Standard_B2s",
        "Standard_B4ms",
        "Standard_D4s_v5",
        "Standard_D8s_v5",
    ]

    if not master_size:
        default_master_size = "Standard_B4ms"
        print("\n[bold]Master VM size options:[/bold]")
        for i, size in enumerate(vm_sizes, 1):
            default_suffix = " [green](default)[/green]" if size == default_master_size else ""
            print(f"  {i}. {size}{default_suffix}")
        default_idx = next((i for i, size in enumerate(vm_sizes, 1) if size == default_master_size), 1)
        choice = Prompt.ask("Select master VM size number or type VM size", default=str(default_idx))
        if choice.isdigit() and 1 <= int(choice) <= len(vm_sizes):
            master_size = vm_sizes[int(choice) - 1]
        else:
            master_size = choice

    if not node_size:
        default_node_size = "Standard_B4ms"
        print("\n[bold]Worker VM size options:[/bold]")
        for i, size in enumerate(vm_sizes, 1):
            default_suffix = " [green](default)[/green]" if size == default_node_size else ""
            print(f"  {i}. {size}{default_suffix}")
        default_idx = next((i for i, size in enumerate(vm_sizes, 1) if size == default_node_size), 1)
        choice = Prompt.ask("Select worker VM size number or type VM size", default=str(default_idx))
        if choice.isdigit() and 1 <= int(choice) <= len(vm_sizes):
            node_size = vm_sizes[int(choice) - 1]
        else:
            node_size = choice

    # --- SSH key ---
    ssh_public_key = None
    ssh_key_name = None
    generate_ssh_key = False
    local_keys = get_local_ssh_keys()

    base_new_key_name = f"{cluster_name}-key"
    existing_key_names = set(local_keys)
    generated_key_name = base_new_key_name
    if generated_key_name in existing_key_names:
        suffix = 2
        while f"{base_new_key_name}-{suffix}" in existing_key_names:
            suffix += 1
        generated_key_name = f"{base_new_key_name}-{suffix}"

    print("\n[bold]SSH keys:[/bold]")
    for i, key_name in enumerate(local_keys, 1):
        print(f"  {i}. {key_name}")
    new_idx = len(local_keys) + 1
    print(f"  {new_idx}. Generate new key pair: {generated_key_name} [green](default)[/green]")
    choice = Prompt.ask("Select SSH key number or type key name", default=str(new_idx))
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(local_keys):
            ssh_key_name = local_keys[idx - 1]
            pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
            ssh_public_key = pub_path.read_text().strip()
        else:
            ssh_key_name = generated_key_name
            generate_ssh_key = True
    else:
        ssh_key_name = choice
        pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
        if pub_path.exists():
            ssh_public_key = pub_path.read_text().strip()
        else:
            generate_ssh_key = True

    # --- Build VM names ---
    reserved_names: set[str] = set()
    master_name = _build_name_with_slug(f"{cluster_name}-master", reserved_names)
    worker_names = [
        _build_name_with_slug(f"{cluster_name}-node-{i + 1}", reserved_names)
        for i in range(nodes)
    ]
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
    typer.echo(f"  Master:         {master_name} ({master_size})")
    typer.echo(f"  Worker Size:    {node_size}")
    typer.echo(f"  Workers:        {len(worker_names)}")

    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    # --- Step 1: Create shared infrastructure ---
    from ...cloud.azure.api import _get_network_client, _get_resource_client
    from azure.mgmt.resource.resources.models import ResourceGroup as RGModel

    network_client = _get_network_client(sub_id)
    resource_client = _get_resource_client(sub_id)

    # Ensure resource group
    try:
        resource_client.resource_groups.create_or_update(
            resource_group, RGModel(location=region),
        )
    except HttpResponseError as exc:
        if "AuthorizationFailed" in str(exc):
            print("[red]Authorization failed for resource group creation/update.[/red]")
            print(f"[yellow]Resource group:[/yellow] {resource_group}")
            print(f"[yellow]Subscription:[/yellow] {sub_id}")
            print("[yellow]Required permission:[/yellow] Microsoft.Resources/subscriptions/resourcegroups/write")
            print("[yellow]Hint:[/yellow] Ask for Contributor/Owner on the subscription or resource group, then refresh credentials.")
            raise typer.Exit(1)
        raise

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

    # Generate keypair only after confirmation and successful infra setup.
    if generate_ssh_key and ssh_key_name:
        ssh_key_name, ssh_public_key = _ensure_local_ssh_keypair(
            key_name=ssh_key_name,
            comment=f"clouder-{cluster_name}",
        )
        print(f"[green]SSH key pair ready in {SSH_FOLDER}/{ssh_key_name}[/green]")

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

    print(
        Panel.fit(
            f"[bold cyan]clouder kubeadm setup {cluster_name}[/bold cyan]",
            title="Next Step",
            border_style="yellow",
        )
    )

    # --- Save cluster metadata ---
    master_result = results[0][1]
    worker_results = [(role, res) for role, res in results if role == "node"]
    _save_cluster_metadata(cluster_name, {
        "name": cluster_name,
        "cluster_type": "kubeadm",
        "cloud": "azure",
        "context": {
            "cloud": "azure",
            "subscription_id": sub_id,
        },
        "subscription_id": sub_id,
        "resource_group": resource_group,
        "region": region,
        "requested_workers": nodes,
        "master_size": master_size,
        "node_size": node_size,
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

    # AWS instance type selection
    common_sizes = ["t3.large", "m5.xlarge", "m6i.xlarge"]

    if not master_size or master_size == "Standard_B4ms":
        print("\n[bold]Master instance type options:[/bold]")
        for i, size_name in enumerate(common_sizes, 1):
            default_suffix = " (default)" if size_name == "t3.large" else ""
            typer.echo(f"  {i}. {size_name}{default_suffix}")
        choice = Prompt.ask("Select master instance type number or type value", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(common_sizes):
            master_size = common_sizes[int(choice) - 1]
        else:
            master_size = choice

    if not node_size or node_size == "Standard_B4ms":
        print("\n[bold]Worker instance type options:[/bold]")
        for i, size_name in enumerate(common_sizes, 1):
            default_suffix = " (default)" if size_name == "t3.large" else ""
            typer.echo(f"  {i}. {size_name}{default_suffix}")
        choice = Prompt.ask("Select worker instance type number or type value", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(common_sizes):
            node_size = common_sizes[int(choice) - 1]
        else:
            node_size = choice

    ec2 = _client("ec2", region=region)

    # Key pair selection
    key_pairs = ec2.describe_key_pairs().get("KeyPairs", [])
    key_pair_names = {kp["KeyName"] for kp in key_pairs}

    if not key_pairs:
        default_key_name = f"{cluster_name}-key"
        print("\n[yellow]No EC2 key pair found in this region.[/yellow]")
        ssh_key_name = Prompt.ask("Key pair name to create", default=default_key_name)
        if not Confirm.ask(f"Create EC2 key pair '{ssh_key_name}' and save private key locally?", default=True):
            raise typer.Abort()
        try:
            local_key_path = _create_aws_key_pair_locally(ec2, ssh_key_name)
            print(f"[green]Created EC2 key pair '{ssh_key_name}'.[/green]")
            print(f"[green]Private key saved:[/green] {local_key_path}")
        except Exception as exc:
            typer.echo(f"Failed to create EC2 key pair '{ssh_key_name}': {exc}", err=True)
            raise typer.Exit(1)
    else:
        print("\n[bold]AWS EC2 key pairs:[/bold]")
        for i, kp in enumerate(key_pairs, 1):
            typer.echo(f"  {i}. {kp['KeyName']}")
        choice = Prompt.ask("Select EC2 key pair number or type key name", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(key_pairs):
            ssh_key_name = key_pairs[int(choice) - 1]["KeyName"]
        else:
            ssh_key_name = choice

        if ssh_key_name not in key_pair_names:
            print(f"\n[yellow]EC2 key pair '{ssh_key_name}' was not found in region '{region}'.[/yellow]")
            if Confirm.ask(f"Create EC2 key pair '{ssh_key_name}' and save private key locally?", default=True):
                try:
                    local_key_path = _create_aws_key_pair_locally(ec2, ssh_key_name)
                    print(f"[green]Created EC2 key pair '{ssh_key_name}'.[/green]")
                    print(f"[green]Private key saved:[/green] {local_key_path}")
                except Exception as exc:
                    typer.echo(f"Failed to create EC2 key pair '{ssh_key_name}': {exc}", err=True)
                    raise typer.Exit(1)
            else:
                raise typer.Abort()

    vpc_cidr = "10.0.0.0/16"
    subnet_cidr = "10.0.0.0/24"
    ami_id = resolve_ubuntu_ami(region=region)

    reserved_names: set[str] = set()
    master_name = _build_name_with_slug(f"{cluster_name}-master", reserved_names)
    worker_names = [
        _build_name_with_slug(f"{cluster_name}-node-{i + 1}", reserved_names)
        for i in range(nodes)
    ]

    print("\n[bold]Kubeadm cluster VMs (AWS):[/bold]")
    typer.echo(f"  Account:        {account_id}")
    typer.echo(f"  Region:         {region}")
    typer.echo(f"  AMI:            {ami_id}")
    typer.echo(f"  VPC CIDR:       {vpc_cidr}")
    typer.echo(f"  Subnet CIDR:    {subnet_cidr}")
    typer.echo(f"  Admin User:     {admin_username}")
    typer.echo(f"  EC2 Key Pair:   {ssh_key_name}")
    typer.echo(f"  Master:         {master_name} ({master_size})")
    for wn in worker_names:
        typer.echo(f"  Worker:         {wn} ({node_size})")

    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    preferred_az = _select_supported_aws_availability_zone(
        ec2,
        [master_size, node_size],
    )
    if preferred_az:
        typer.echo(f"Selected availability zone for subnet: {preferred_az}")
    else:
        typer.echo(
            "Could not determine a common availability zone for selected instance types; using AWS default subnet placement.",
            err=True,
        )

    network = create_aws_kubeadm_network(
        cluster_name=cluster_name,
        vpc_cidr=vpc_cidr,
        subnet_cidr=subnet_cidr,
        allowed_ssh_cidrs=["0.0.0.0/0"],
        availability_zone=preferred_az,
        region=region,
    )

    results = []
    try:
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
    except Exception as exc:
        print(f"[red]Failed while creating AWS kubeadm VMs: {exc}[/red]")
        print("[yellow]Rolling back created AWS networking resources...[/yellow]")
        try:
            from ...cloud.aws.api import delete_aws_kubeadm_network

            delete_aws_kubeadm_network(
                vpc_id=network["vpc_id"],
                subnet_id=network["subnet_id"],
                internet_gateway_id=network["internet_gateway_id"],
                route_table_id=network["route_table_id"],
                security_group_id=network["security_group_id"],
                region=region,
            )
            print("[green]AWS networking rollback complete.[/green]")
        except Exception as rollback_exc:
            print(f"[red]Rollback failed: {rollback_exc}[/red]")
            print("[yellow]You may need to manually delete the partially created VPC resources.[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Kubeadm Cluster: {cluster_name} (AWS)")
    table.add_column("Role", style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Public IP", style="green")
    table.add_column("Instance Type", style="dim")
    for role, res in results:
        table.add_row(role.capitalize(), res["name"], res.get("public_ip", "N/A"), res["instance_type"])
    print()
    print(table)

    print(
        Panel.fit(
            f"[bold cyan]clouder kubeadm setup {cluster_name}[/bold cyan]",
            title="Next Step",
            border_style="yellow",
        )
    )

    master_result = results[0][1]
    worker_results = [(role, res) for role, res in results if role == "node"]
    _save_cluster_metadata(cluster_name, {
        "name": cluster_name,
        "cluster_type": "kubeadm",
        "cloud": "aws",
        "context": {
            "cloud": "aws",
            "account_id": account_id,
        },
        "account_id": account_id,
        "region": region,
        "requested_workers": nodes,
        "master_size": master_size,
        "node_size": node_size,
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
            "subnet_availability_zone": network.get("subnet_availability_zone", ""),
            "security_group_id": network["security_group_id"],
            "internet_gateway_id": network["internet_gateway_id"],
            "route_table_id": network["route_table_id"],
        },
    })
