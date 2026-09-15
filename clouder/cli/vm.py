"""Clouder CLI - Virtual machine management commands."""

import sys

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .ctx import get_current_context
from ..util.utils import DEFAULT_REGION, SSH_FOLDER

vm_app = typer.Typer(no_args_is_help=True)


def _select_running_aws_vm(candidates: list[dict], vm_name: str) -> dict:
    """Select one running AWS VM from possibly ambiguous name matches."""
    if len(candidates) == 1:
        return candidates[0]

    if not sys.stdin.isatty():
        chosen = candidates[0]
        print(
            "[yellow]Multiple running AWS instances share this name. "
            f"Using first match: {chosen.get('id')} ({chosen.get('region')}).[/yellow]"
        )
        return chosen

    table = Table(title=f"Multiple running AWS VMs named '{vm_name}'")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Instance ID", style="green")
    table.add_column("Region", style="green")
    table.add_column("State", style="yellow")
    table.add_column("Public IP", style="yellow")
    for idx, vm in enumerate(candidates, 1):
        table.add_row(
            str(idx),
            vm.get("id") or "N/A",
            vm.get("region") or "N/A",
            vm.get("state") or "N/A",
            vm.get("public_ip") or "N/A",
        )
    print(table)

    choice = Prompt.ask("Select VM number", default="1")
    if not choice.isdigit() or not (1 <= int(choice) <= len(candidates)):
        typer.echo(f"Invalid selection: {choice}", err=True)
        raise typer.Exit(1)
    return candidates[int(choice) - 1]


def _find_aws_vm_by_name(name: str) -> dict | None:
    """Find an AWS VM by Name tag across current and enabled regions."""
    from ..cloud.aws.api import list_aws_regions, list_aws_vms

    # First check current/default session region.
    vms = list_aws_vms()
    running = [vm for vm in vms if vm.get("name") == name and vm.get("state") == "running"]
    if running:
        return _select_running_aws_vm(running, name)

    # Fallback to all enabled regions.
    running_all_regions: list[dict] = []
    for region in list_aws_regions():
        region_name = region.get("name")
        if not region_name:
            continue
        try:
            region_vms = list_aws_vms(region=region_name)
        except Exception:
            continue
        running = [vm for vm in region_vms if vm.get("name") == name and vm.get("state") == "running"]
        running_all_regions.extend(running)

    if running_all_regions:
        return _select_running_aws_vm(running_all_regions, name)

    return None


def _delete_aws_vm_with_alb_cleanup(name: str, region: str | None = None, force: bool = False):
    """Delete an AWS VM, optionally deleting associated ALBs first."""
    from ..cloud.aws.api import (
        delete_aws_alb,
        list_aws_albs_for_instance,
        list_aws_vms,
        terminate_aws_vm,
    )

    vms = list_aws_vms(region=region)
    match = [vm for vm in vms if (vm.get("name") or "") == name and vm.get("state") == "running"]
    if not match:
        typer.echo(f"Running VM '{name}' not found.", err=True)
        raise typer.Exit(1)

    vm = _select_running_aws_vm(match, name)
    vm_region = region or vm.get("region")
    associated_albs = list_aws_albs_for_instance(vm.get("id"), region=vm_region)

    if associated_albs:
        table = Table(title=f"ALBs Associated with {name}")
        table.add_column("ALB Name", style="cyan")
        table.add_column("DNS", style="green")
        table.add_column("ALB ARN", style="dim")
        for alb in associated_albs:
            table.add_row(
                alb.get("load_balancer_name") or "N/A",
                alb.get("dns_name") or "N/A",
                alb.get("load_balancer_arn") or "N/A",
            )
        print(table)

        if not Confirm.ask(
            "Associated ALB(s) found. Delete ALB(s) first, then terminate the EC2 instance?",
            default=False,
        ):
            raise typer.Abort()

        for alb in associated_albs:
            alb_name = alb.get("load_balancer_name") or alb.get("load_balancer_arn") or "ALB"
            typer.echo(f"Deleting ALB '{alb_name}'...")
            delete_aws_alb(alb.get("load_balancer_arn"), region=vm_region)

    if not force:
        typer.confirm(f"Terminate AWS instance '{name}' (id: {vm.get('id')})?", abort=True)

    terminate_aws_vm(vm.get("id"), region=vm_region)
    if associated_albs:
        typer.echo(f"ALB cleanup completed. VM '{name}' termination requested.")
    else:
        typer.echo(f"VM '{name}' termination requested.")


def _show_aws_vm_info(name: str, region: str | None = None):
    """Print detailed AWS VM information including associated ALBs."""
    from ..cloud.aws.api import list_aws_albs_for_instance, list_aws_vms

    if region:
        vms = list_aws_vms(region=region)
        vm = next(
            (
                item
                for item in vms
                if (item.get("name") or "") == name and item.get("state") == "running"
            ),
            None,
        )
    else:
        vm = _find_aws_vm_by_name(name)

    if not vm:
        typer.echo(f"Running VM '{name}' not found in AWS account/regions.", err=True)
        raise typer.Exit(1)

    vm_region = region or vm.get("region")
    associated_albs = list_aws_albs_for_instance(vm.get("id"), region=vm_region)

    details = Table(title=f"AWS VM Info: {name}")
    details.add_column("Field", style="cyan", no_wrap=True)
    details.add_column("Value", style="green")
    details.add_row("Name", vm.get("name") or "N/A")
    details.add_row("Instance ID", vm.get("id") or "N/A")
    details.add_row("Region", vm.get("region") or "N/A")
    details.add_row("State", vm.get("state") or "N/A")
    details.add_row("Instance Type", vm.get("instance_type") or "N/A")
    details.add_row("Public IP", vm.get("public_ip") or "N/A")
    details.add_row("Private IP", vm.get("private_ip") or "N/A")
    details.add_row("VPC ID", vm.get("vpc_id") or "N/A")
    details.add_row("Subnet ID", vm.get("subnet_id") or "N/A")
    details.add_row("Key Pair", vm.get("key_name") or "N/A")
    print(details)

    if associated_albs:
        albs = Table(title=f"Associated AWS ALBs ({len(associated_albs)})")
        albs.add_column("ALB Name", style="cyan")
        albs.add_column("DNS", style="green")
        albs.add_column("ALB ARN", style="dim")
        albs.add_column("Target Groups", style="yellow")
        for alb in associated_albs:
            albs.add_row(
                alb.get("load_balancer_name") or "N/A",
                alb.get("dns_name") or "N/A",
                alb.get("load_balancer_arn") or "N/A",
                "\n".join(alb.get("target_group_arns") or []) or "N/A",
            )
        print(albs)
    else:
        print("[dim]No associated ALB found for this instance.[/dim]")


def _create_aws_key_pair_locally(ec2_client, key_name: str) -> str:
    """Create an EC2 key pair and store private key under ~/.ssh.

    Returns the local private key path.
    """
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
        list_aws_acm_certificates,
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
    key_pair_names = {kp.get("KeyName", "") for kp in key_pairs}
    if not key_pairs:
        proposed_key_name = f"{name}-key"
        print("\n[yellow]No EC2 key pair found in this region.[/yellow]")
        key_name = Prompt.ask("Key pair name to create", default=proposed_key_name)
        if not Confirm.ask(f"Create EC2 key pair '{key_name}' and save private key locally?", default=True):
            raise typer.Abort()
        try:
            local_key_path = _create_aws_key_pair_locally(ec2, key_name)
            print(f"[green]Created EC2 key pair '{key_name}'.[/green]")
            print(f"[green]Private key saved:[/green] {local_key_path}")
        except Exception as exc:
            typer.echo(f"Failed to create EC2 key pair '{key_name}': {exc}", err=True)
            raise typer.Exit(1)
    else:
        print("\n[bold]AWS EC2 key pairs:[/bold]")
        for i, kp in enumerate(key_pairs, 1):
            typer.echo(f"  {i}. {kp['KeyName']}")
        choice = Prompt.ask("Select key pair number or type key name", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(key_pairs):
            key_name = key_pairs[int(choice) - 1]["KeyName"]
        else:
            key_name = choice

        if key_name not in key_pair_names:
            print(f"\n[yellow]EC2 key pair '{key_name}' does not exist in {region}.[/yellow]")
            if Confirm.ask(f"Create EC2 key pair '{key_name}' and save private key locally?", default=True):
                try:
                    local_key_path = _create_aws_key_pair_locally(ec2, key_name)
                    print(f"[green]Created EC2 key pair '{key_name}'.[/green]")
                    print(f"[green]Private key saved:[/green] {local_key_path}")
                except Exception as exc:
                    typer.echo(f"Failed to create EC2 key pair '{key_name}': {exc}", err=True)
                    raise typer.Exit(1)
            else:
                raise typer.Abort()

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

    vm_name = result.get("name") or name
    vm_region = result.get("region") or region
    if vm_region:
        try:
            certs = list_aws_acm_certificates(region=vm_region)
        except Exception as exc:
            print(
                Panel(
                    "[yellow]Could not list ACM certificates for recommended ALB setup.[/yellow]\n"
                    f"Reason: {exc}\n\n"
                    "Manual command template:\n"
                    f"  clouder vm add-alb {vm_name} --certificate-arn <acm-certificate-arn>",
                    title="Recommended Next Steps",
                )
            )
            return

        if not certs:
            print(
                Panel(
                    "No ACM certificate found in this region.\n\n"
                    "Create/import a certificate in ACM first, then run:\n"
                    f"  clouder vm add-alb {vm_name} --certificate-arn <acm-certificate-arn>",
                    title="Recommended Next Steps",
                )
            )
            return

        commands = []
        lines = [
            f"Found {len(certs)} ACM certificate(s) in region {vm_region}.",
            "Use one of the exact commands below to attach an HTTPS ALB:",
            "",
        ]
        for cert in certs:
            arn = cert.get("arn") or ""
            domain = cert.get("domain_name") or "N/A"
            status = cert.get("status") or "N/A"
            cmd = f"clouder vm add-alb {vm_name} --certificate-arn {arn}"
            commands.append(cmd)
            lines.append(f"- {domain} [{status}]")
            lines.append(f"  {cmd}")
            lines.append("")

        print(Panel("\n".join(lines).rstrip(), title="Recommended Next Steps"))


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


@vm_app.command("info")
def vm_info(
    name: str = typer.Argument(..., help="Name of the virtual machine."),
):
    """Show detailed VM information for the current context."""
    cloud, context_id = get_current_context()
    _ = context_id
    if cloud == "aws":
        _show_aws_vm_info(name=name, region=None)
        return

    typer.echo("`vm info` is currently implemented for AWS contexts only.", err=True)
    raise typer.Exit(1)


@vm_app.command("terminate")
def vm_terminate(
    name: str = typer.Argument(..., help="Name of the virtual machine to delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
):
    """Terminate a virtual machine by name."""
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
        _delete_aws_vm_with_alb_cleanup(name=name, region=None, force=force)
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


@vm_app.command("add-alb")
def vm_add_alb(
    vm_name: str = typer.Argument(..., help="Name of the AWS VM to attach behind an ALB."),
    certificate_arn: str = typer.Option(
        ..., "--certificate-arn", help="ACM certificate ARN for HTTPS listener."
    ),
    target_port: int = typer.Option(80, "--target-port", help="Backend VM HTTP port for ALB forwarding."),
):
    """Create/reuse an internet-facing AWS ALB for a VM with HTTPS termination.

    Creates ALB named '<vm-name>-alb' and configures:
    - HTTPS 443 (ACM cert) -> HTTP target group -> EC2 instance
    - HTTP 80 -> HTTPS redirect
    """
    cloud, _context_id = get_current_context()
    if cloud != "aws":
        typer.echo("`vm add-alb` is currently supported only for AWS context.", err=True)
        raise typer.Exit(1)

    vm = _find_aws_vm_by_name(vm_name)
    if not vm:
        typer.echo(f"VM '{vm_name}' not found in AWS account/regions.", err=True)
        raise typer.Exit(1)

    if not vm.get("vpc_id"):
        typer.echo(f"VM '{vm_name}' has no VPC information.", err=True)
        raise typer.Exit(1)

    if target_port < 1 or target_port > 65535:
        typer.echo("Target port must be between 1 and 65535.", err=True)
        raise typer.Exit(1)

    from ..cloud.aws.api import ensure_aws_alb_for_vm

    try:
        result = ensure_aws_alb_for_vm(
            vm_name=vm_name,
            instance_id=vm["id"],
            vpc_id=vm["vpc_id"],
            certificate_arn=certificate_arn,
            region=vm.get("region") or "",
            target_port=target_port,
        )
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    print(
        Panel(
            f"[green]ALB is ready for VM '{vm_name}'.[/green]\n\n"
            f"  ALB Name:         {result['alb_name']}\n"
            f"  ALB DNS:          {result['alb_dns_name']}\n"
            f"  Region:           {result['region']}\n"
            f"  Certificate:      {result['certificate_arn']}\n"
            f"  Backend Target:   {result['instance_id']}:{result['target_port']}\n"
            f"  Target Group:     {result['target_group_name']}\n\n"
            f"  HTTPS URL:        https://{result['alb_dns_name']}",
            title="AWS ALB Attached",
        )
    )
