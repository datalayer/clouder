"""Clouder CLI - Virtual machine management commands."""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .ctx import get_current_context
from ..util.utils import DEFAULT_REGION

vm_app = typer.Typer(no_args_is_help=True)


@vm_app.callback(invoke_without_command=True)
def vm_default(ctx: typer.Context):
    """List VMs if no subcommand given."""
    if ctx.invoked_subcommand is None:
        vm_list()


@vm_app.command("create")
def vm_create(
    name: str = typer.Argument(..., help="Name for the virtual machine."),
    region: str = typer.Option(None, "--region", "-r", help="Region to create the VM in."),
    resource_group: str = typer.Option(None, "--resource-group", "-g", help="Resource group (Azure only)."),
    vm_size: str = typer.Option(None, "--vm-size", help="VM size (Azure only, e.g. Standard_B2s)."),
    admin_username: str = typer.Option("azureuser", "--admin-user", help="Admin username (Azure only)."),
    image: str = typer.Option("Ubuntu2204", "--image", help="Image: Ubuntu2204, Ubuntu2404, Debian12 (Azure only)."),
):
    """Create a virtual machine."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        _create_azure_vm(context_id, name, region, resource_group, vm_size, admin_username, image)
    elif cloud == "aws":
        _create_aws_vm(name, region, vm_size)
    else:
        if not region:
            region = DEFAULT_REGION
        from ..cloud.ovh.api import create_ovh_vm
        res = create_ovh_vm(context_id, name, region)
        print(res)


def _create_aws_vm(name: str, region: str | None, vm_size: str | None):
    """Create an AWS EC2 VM with interactive prompts for missing values."""
    from ..cloud.aws.api import (
        _client,
        create_aws_vm,
        list_aws_regions,
        resolve_ubuntu_ami,
    )

    if not region:
        regions = list_aws_regions()
        popular = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1"]
        print("\n[bold]Popular AWS regions:[/bold]")
        for i, r in enumerate(popular, 1):
            typer.echo(f"  {i}. {r}")
        typer.echo(f"  {len(popular) + 1}. Type another region")
        choice = Prompt.ask("Select region number or type region", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(popular):
            region = popular[int(choice) - 1]
        elif choice.isdigit() and int(choice) == len(popular) + 1:
            region = Prompt.ask("Region")
        else:
            region = choice

    if not vm_size:
        common_sizes = ["t3.large", "m5.xlarge", "m6i.xlarge"]
        print("\n[bold]Common AWS instance sizes:[/bold]")
        for i, size_name in enumerate(common_sizes, 1):
            typer.echo(f"  {i}. {size_name}")
        choice = Prompt.ask("Select size number or type instance type", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(common_sizes):
            vm_size = common_sizes[int(choice) - 1]
        else:
            vm_size = choice

    ec2 = _client("ec2", region=region)

    # Key pair selection
    key_pairs = ec2.describe_key_pairs().get("KeyPairs", [])
    if not key_pairs:
        typer.echo("No EC2 key pair found in region. Create one first: aws ec2 create-key-pair ...", err=True)
        raise typer.Exit(1)
    print("\n[bold]AWS EC2 key pairs:[/bold]")
    for i, kp in enumerate(key_pairs, 1):
        typer.echo(f"  {i}. {kp['KeyName']}")
    choice = Prompt.ask("Select key pair number or type key name", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(key_pairs):
        key_name = key_pairs[int(choice) - 1]["KeyName"]
    else:
        key_name = choice

    # Networking: default VPC + first subnet
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
    if not vpcs:
        typer.echo("No default VPC found. Use kubeadm create for managed network provisioning.", err=True)
        raise typer.Exit(1)
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
    if not subnets:
        typer.echo("No subnet found in default VPC.", err=True)
        raise typer.Exit(1)
    subnet_id = subnets[0]["SubnetId"]

    # Reuse default security group
    sgs = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["default"]}, {"Name": "vpc-id", "Values": [vpc_id]}]).get("SecurityGroups", [])
    if not sgs:
        typer.echo("Default security group not found.", err=True)
        raise typer.Exit(1)
    security_group_id = sgs[0]["GroupId"]

    ami_id = resolve_ubuntu_ami(region=region)

    print("\n[bold]Creating VM:[/bold]")
    typer.echo(f"  Name:         {name}")
    typer.echo(f"  Region:       {region}")
    typer.echo(f"  InstanceType: {vm_size}")
    typer.echo(f"  Key Pair:     {key_name}")
    typer.echo(f"  Subnet:       {subnet_id}")
    typer.echo(f"  AMI:          {ami_id}")

    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    result = create_aws_vm(
        vm_name=name,
        instance_type=vm_size,
        key_name=key_name,
        subnet_id=subnet_id,
        security_group_id=security_group_id,
        ami_id=ami_id,
        tags={"datalayer.io/component": "vm"},
        region=region,
    )

    print(Panel(
        f"[green]VM created successfully![/green]\n\n"
        f"  Name:         {result['name']}\n"
        f"  Instance ID:  {result['id']}\n"
        f"  State:        {result['state']}\n"
        f"  Public IP:    {result.get('public_ip', 'N/A')}\n"
        f"  Region:       {result['region']}",
        title="AWS EC2 VM Created",
    ))


def _create_azure_vm(sub_id, name, region, resource_group, vm_size, admin_username, image):
    """Create an Azure VM with interactive prompts for missing values."""
    from ..cloud.azure.api import create_azure_vm, list_azure_locations, list_azure_resource_groups
    from ..util.utils import SSH_PUBLIC_KEY

    if not resource_group:
        rgs = list_azure_resource_groups(subscription_id=sub_id)
        new_rg = f"{name}-rg"
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
                # Use the existing resource group's location as region
                if not region:
                    region = rgs[idx - 1]["location"]
            else:
                resource_group = new_rg
        else:
            resource_group = choice

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

    if not vm_size:
        print(f"\n[bold]Common VM sizes in {region}:[/bold]")
        common_sizes = [
            ("Standard_B1s", "1 vCPU, 1 GB RAM - Free tier eligible"),
            ("Standard_B2s", "2 vCPUs, 4 GB RAM - General purpose"),
            ("Standard_B4ms", "4 vCPUs, 16 GB RAM - General purpose"),
            ("Standard_D4s_v5", "4 vCPUs, 16 GB RAM - Compute optimized"),
            ("Standard_D8s_v5", "8 vCPUs, 32 GB RAM - Compute optimized"),
            ("Standard_NC6s_v3", "6 vCPUs, 112 GB RAM, 1 GPU V100 - GPU"),
        ]
        for i, (size_name, desc) in enumerate(common_sizes, 1):
            typer.echo(f"  {i}. {size_name} - {desc}")
        choice = Prompt.ask("Select VM size number or type size name", default="2")
        if choice.isdigit() and 1 <= int(choice) <= len(common_sizes):
            vm_size = common_sizes[int(choice) - 1][0]
        else:
            vm_size = choice

    # Image mapping
    image_map = {
        "Ubuntu2204": ("Canonical", "0001-com-ubuntu-server-jammy", "22_04-lts-gen2"),
        "Ubuntu2404": ("Canonical", "ubuntu-24_04-lts", "server"),
        "Debian12": ("Debian", "debian-12", "12-gen2"),
    }
    image_info = image_map.get(image, image_map["Ubuntu2204"])

    # SSH key
    ssh_public_key = None
    ssh_key_name = None
    from ..cloud.local.api import get_local_ssh_keys
    from ..util.utils import SSH_FOLDER
    local_keys = get_local_ssh_keys()
    print("\n[bold]SSH keys:[/bold]")
    for i, key_name in enumerate(local_keys, 1):
        typer.echo(f"  {i}. {key_name}")
    new_idx = len(local_keys) + 1
    print(f"  {new_idx}. [green]Generate new key pair: {name}-key[/green]")
    choice = Prompt.ask("Select SSH key number or type key name", default="1" if local_keys else str(new_idx))
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(local_keys):
            ssh_key_name = local_keys[idx - 1]
            pub_path = SSH_FOLDER / f"{ssh_key_name}.pub"
            ssh_public_key = pub_path.read_text().strip()
        else:
            # Generate new key pair
            import subprocess
            ssh_key_name = f"{name}-key"
            key_path = SSH_FOLDER / ssh_key_name
            SSH_FOLDER.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", f"clouder-{name}"],
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

    # Confirmation
    print(f"\n[bold]Creating VM:[/bold]")
    typer.echo(f"  Name:           {name}")
    typer.echo(f"  Resource Group: {resource_group}")
    typer.echo(f"  Region:         {region}")
    typer.echo(f"  VM Size:        {vm_size}")
    typer.echo(f"  Image:          {image}")
    typer.echo(f"  Admin User:     {admin_username}")
    typer.echo(f"  SSH Key:        {ssh_key_name or 'None (password)'}")

    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    typer.echo("\nCreating VM (this may take a few minutes)...")
    result = create_azure_vm(
        resource_group=resource_group,
        vm_name=name,
        location=region,
        vm_size=vm_size,
        admin_username=admin_username,
        ssh_public_key=ssh_public_key,
        image_publisher=image_info[0],
        image_offer=image_info[1],
        image_sku=image_info[2],
        subscription_id=sub_id,
    )

    print(Panel(
        f"[green]VM created successfully![/green]\n\n"
        f"  Name:             {result['name']}\n"
        f"  Location:         {result['location']}\n"
        f"  VM Size:          {result['vm_size']}\n"
        f"  State:            {result['provisioning_state']}\n"
        f"  Public IP:        {result.get('public_ip', 'N/A')}\n"
        f"  Resource Group:   {result['resource_group']}\n\n"
        f"  SSH:  ssh -i ~/.ssh/{ssh_key_name or 'id_rsa'} {admin_username}@{result.get('public_ip', '<ip>')}",
        title="VM Created",
    ))


@vm_app.command("ls")
def vm_list():
    """List virtual machines."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        from ..cloud.azure.api import list_azure_vms
        vms = list_azure_vms(subscription_id=context_id)
        table = Table(title="Azure Virtual Machines")
        table.add_column("Name", justify="left", style="cyan", no_wrap=True)
        table.add_column("Location", justify="left", style="green")
        table.add_column("VM Size", justify="left", style="green")
        table.add_column("State", justify="left", style="yellow")
        table.add_column("OS", justify="left", style="green")
        table.add_column("Resource Group", justify="left", style="dim")
        for vm in vms:
            table.add_row(
                vm["name"],
                vm["location"],
                vm["vm_size"] or "N/A",
                vm["provisioning_state"] or "N/A",
                vm["os_type"] or "N/A",
                vm["resource_group"] or "N/A",
            )
        print(table)
        print(f"\n[dim]Total: {len(vms)} VMs[/dim]")
    elif cloud == "aws":
        from ..cloud.aws.api import list_aws_vms
        vms = list_aws_vms()
        table = Table(title="AWS EC2 Instances")
        table.add_column("Name", justify="left", style="cyan", no_wrap=True)
        table.add_column("Instance ID", justify="left", style="green")
        table.add_column("Type", justify="left", style="green")
        table.add_column("State", justify="left", style="yellow")
        table.add_column("Public IP", justify="left", style="green")
        table.add_column("Region", justify="left", style="dim")
        for vm in vms:
            table.add_row(
                vm["name"] or "N/A",
                vm["id"],
                vm["instance_type"] or "N/A",
                vm["state"] or "N/A",
                vm.get("public_ip") or "N/A",
                vm.get("region") or "N/A",
            )
        print(table)
        print(f"\n[dim]Total: {len(vms)} VMs[/dim]")
    else:
        from ..cloud.ovh.api import get_ovh_vm, get_ovh_project
        project = get_ovh_project(context_id)
        vms = get_ovh_vm(context_id)
        table = Table(title=f"Virtual Machines {cloud}:{project['description']}")
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="green")
        table.add_column("Flavor ID", justify="left", style="green")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Status", justify="left", style="green")
        for vm in vms:
            table.add_row(
                vm["id"],
                vm["name"],
                vm["flavorId"],
                vm["region"],
                vm["status"],
            )
        print(table)


@vm_app.command("delete")
def vm_delete(
    name: str = typer.Argument(..., help="Name of the virtual machine to delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
):
    """Delete a virtual machine by name."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        from ..cloud.azure.api import list_azure_vms, delete_azure_vm
        vms = list_azure_vms(subscription_id=context_id)
        match = [vm for vm in vms if vm["name"] == name]
        if not match:
            typer.echo(f"VM '{name}' not found.", err=True)
            raise typer.Exit(1)
        vm = match[0]
        if not force:
            typer.confirm(f"Delete Azure VM '{name}' and its disks/NIC/IP in resource group '{vm['resource_group']}'?", abort=True)
        delete_azure_vm(vm["resource_group"], name, subscription_id=context_id)
        typer.echo(f"VM '{name}' deleted (with disks, NIC, IP).")
    elif cloud == "aws":
        from ..cloud.aws.api import list_aws_vms, terminate_aws_vm
        vms = list_aws_vms()
        match = [vm for vm in vms if vm["name"] == name]
        if not match:
            typer.echo(f"VM '{name}' not found.", err=True)
            raise typer.Exit(1)
        vm = match[0]
        if not force:
            typer.confirm(f"Terminate AWS instance '{name}' (id: {vm['id']})?", abort=True)
        terminate_aws_vm(vm["id"])
        typer.echo(f"VM '{name}' terminated.")
    else:
        from ..cloud.ovh.api import get_ovh_vm, delete_ovh_vm
        vms = get_ovh_vm(context_id)
        match = [vm for vm in vms if vm["name"] == name]
        if not match:
            typer.echo(f"VM '{name}' not found.", err=True)
            raise typer.Exit(1)
        vm = match[0]
        if not force:
            typer.confirm(f"Delete OVH VM '{name}' (id: {vm['id']})?", abort=True)
        delete_ovh_vm(context_id, vm["id"])
        typer.echo(f"VM '{name}' deleted.")
