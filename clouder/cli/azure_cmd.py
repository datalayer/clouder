"""Clouder CLI - Azure cloud commands with interactive prompts."""

from typing import Optional

import typer
from rich import print
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from ..cloud.azure.config import (
    load_azure_config,
    save_azure_config,
    get_azure_subscription_id,
)

azure_app = typer.Typer(no_args_is_help=True)


def _ensure_azure_configured() -> str:
    """Ensure Azure is configured and return subscription_id.

    If not configured, interactively prompt the user.
    """
    config = load_azure_config()
    sub_id = config.get("subscription_id", "")
    if sub_id:
        return sub_id

    typer.echo("Azure is not configured. Let's set it up.")
    typer.echo("")
    typer.echo("You need an Azure Service Principal or to be logged in with `az login`.")
    typer.echo("See: clouder docs or run `clouder azure configure`")
    typer.echo("")
    raise typer.Exit(1)


@azure_app.callback(invoke_without_command=True)
def azure_default(ctx: typer.Context):
    """Azure cloud operations."""
    if ctx.invoked_subcommand is None:
        config = load_azure_config()
        if config:
            print(Panel(
                f"[green]Subscription ID:[/green] {config.get('subscription_id', 'N/A')}\n"
                f"[green]Tenant ID:[/green] {config.get('tenant_id', 'N/A')}\n"
                f"[green]Client ID:[/green] {config.get('client_id', 'N/A')}\n"
                f"[green]Auth method:[/green] {'Service Principal' if config.get('client_secret') else 'Default Credential (az login)'}",
                title="Azure Configuration",
            ))
        else:
            typer.echo("Azure not configured. Run `clouder azure configure`.")


@azure_app.command("configure")
def azure_configure(
    subscription_id: Optional[str] = typer.Option(None, "--subscription-id", "-s", help="Azure subscription ID."),
    tenant_id: Optional[str] = typer.Option(None, "--tenant-id", "-t", help="Azure tenant ID."),
    client_id: Optional[str] = typer.Option(None, "--client-id", "-c", help="Azure app (client) ID."),
    client_secret: Optional[str] = typer.Option(None, "--client-secret", help="Azure client secret."),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", help="Enable interactive prompts."),
):
    """Configure Azure credentials for Clouder.

    If no options provided, prompts interactively for the values.
    You can use either a Service Principal (client_id + client_secret) or
    rely on `az login` (DefaultAzureCredential).
    """
    config = load_azure_config()

    if interactive and not subscription_id:
        # Try to list subscriptions first using DefaultAzureCredential
        typer.echo("")
        use_az_login = Confirm.ask(
            "Do you want to use `az login` credentials (DefaultAzureCredential)?",
            default=True,
        )
        if use_az_login:
            try:
                from ..cloud.azure.api import list_azure_subscriptions
                typer.echo("\nFetching subscriptions from Azure...")
                subs = list_azure_subscriptions()
                if subs:
                    table = Table(title="Available Azure Subscriptions")
                    table.add_column("#", justify="right", style="cyan")
                    table.add_column("Subscription ID", justify="left", style="green")
                    table.add_column("Name", justify="left", style="green")
                    table.add_column("State", justify="left", style="yellow")
                    for i, sub in enumerate(subs, 1):
                        table.add_row(str(i), sub["id"], sub["name"], sub["state"])
                    print(table)
                    choice = Prompt.ask(
                        "Select subscription number",
                        default="1",
                    )
                    idx = int(choice) - 1
                    selected = subs[idx]
                    config["subscription_id"] = selected["id"]
                    config["tenant_id"] = selected.get("tenant_id", "")
                    config.pop("client_id", None)
                    config.pop("client_secret", None)
                    save_azure_config(config)
                    print(f"\n[green]Configured Azure with subscription:[/green] {selected['name']} ({selected['id']})")
                    return
                else:
                    typer.echo("No subscriptions found. Please check your credentials.")
            except Exception as e:
                typer.echo(f"Could not list subscriptions via az login: {e}")
                typer.echo("Falling back to Service Principal configuration.\n")

        # Service Principal flow
        typer.echo("\n[bold]Service Principal Configuration[/bold]")
        typer.echo("Create one in Azure Portal → Microsoft Entra ID → App registrations → New registration\n")
        subscription_id = Prompt.ask("Subscription ID", default=config.get("subscription_id", ""))
        tenant_id = Prompt.ask("Tenant ID", default=config.get("tenant_id", ""))
        client_id = Prompt.ask("Client (Application) ID", default=config.get("client_id", ""))
        client_secret = Prompt.ask("Client Secret", password=True)

    if subscription_id:
        config["subscription_id"] = subscription_id
    if tenant_id:
        config["tenant_id"] = tenant_id
    if client_id:
        config["client_id"] = client_id
    if client_secret:
        config["client_secret"] = client_secret

    if not config.get("subscription_id"):
        typer.echo("Subscription ID is required.", err=True)
        raise typer.Exit(1)

    save_azure_config(config)
    print(f"\n[green]Azure configuration saved.[/green]")


@azure_app.command("subscriptions")
def azure_subscriptions():
    """List Azure subscriptions."""
    from ..cloud.azure.api import list_azure_subscriptions
    subs = list_azure_subscriptions()
    table = Table(title="Azure Subscriptions")
    table.add_column("Subscription ID", justify="left", style="cyan")
    table.add_column("Name", justify="left", style="green")
    table.add_column("State", justify="left", style="yellow")
    table.add_column("Tenant ID", justify="left", style="dim")
    for sub in subs:
        table.add_row(sub["id"], sub["name"], sub["state"], sub["tenant_id"])
    print(table)


@azure_app.command("regions")
def azure_regions():
    """List available Azure regions/locations."""
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import list_azure_locations
    locations = list_azure_locations(sub_id)
    table = Table(title="Azure Regions")
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("Display Name", justify="left", style="green")
    table.add_column("Regional Name", justify="left", style="green")
    for loc in sorted(locations, key=lambda x: x["name"]):
        table.add_row(loc["name"], loc["display_name"], loc["regional_display_name"])
    print(table)
    print(f"\n[dim]Total: {len(locations)} regions[/dim]")


@azure_app.command("resources")
def azure_resources(
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Filter by region. If not provided, asks interactively."),
    resource_group: Optional[str] = typer.Option(None, "--resource-group", "-g", help="Filter by resource group."),
):
    """List resources per region.

    If no region is given, interactively prompts for one.
    """
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import list_azure_locations, list_azure_resources_by_region, list_azure_resources

    # Interactive region selection if not provided
    if not region:
        locations = list_azure_locations(sub_id)
        location_names = sorted([loc["name"] for loc in locations])

        # Show popular regions first
        popular = ["eastus", "eastus2", "westus2", "westeurope", "northeurope",
                    "southeastasia", "centralus", "uksouth", "francecentral"]
        typer.echo("\n[bold]Popular regions:[/bold]")
        for i, r in enumerate(popular, 1):
            display = next((loc["display_name"] for loc in locations if loc["name"] == r), r)
            typer.echo(f"  {i}. {r} ({display})")
        typer.echo(f"  a. [All regions]")

        choice = Prompt.ask(
            "\nSelect region number, type a region name, or 'a' for all",
            default="a",
        )
        if choice.lower() == "a":
            region = None
        elif choice.isdigit() and 1 <= int(choice) <= len(popular):
            region = popular[int(choice) - 1]
        elif choice in location_names:
            region = choice
        else:
            typer.echo(f"Unknown region: {choice}", err=True)
            raise typer.Exit(1)

    if resource_group:
        resources = list_azure_resources(resource_group=resource_group, subscription_id=sub_id)
        table = Table(title=f"Azure Resources in {resource_group}")
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Type", justify="left", style="green")
        table.add_column("Location", justify="left", style="yellow")
        table.add_column("Tags", justify="left", style="dim")
        for r in resources:
            if region and r["location"] != region:
                continue
            tags_str = ", ".join(f"{k}={v}" for k, v in (r["tags"] or {}).items())
            table.add_row(r["name"], r["type"], r["location"], tags_str)
        print(table)
    else:
        resources_by_region = list_azure_resources_by_region(region=region, subscription_id=sub_id)
        if not resources_by_region:
            if region:
                typer.echo(f"No resources found in region '{region}'.")
            else:
                typer.echo("No resources found.")
            return
        for reg, resources in sorted(resources_by_region.items()):
            table = Table(title=f"Azure Resources in {reg}")
            table.add_column("Name", justify="left", style="cyan")
            table.add_column("Type", justify="left", style="green")
            table.add_column("Resource Group", justify="left", style="yellow")
            table.add_column("Tags", justify="left", style="dim")
            for r in resources:
                tags_str = ", ".join(f"{k}={v}" for k, v in (r["tags"] or {}).items())
                table.add_row(r["name"], r["type"], r["resource_group"], tags_str)
            print(table)
            print()

    total = sum(len(v) for v in resources_by_region.items()) if not resource_group else len(resources)
    print(f"[dim]Total resources: {total}[/dim]")


@azure_app.command("vm-sizes")
def azure_vm_sizes(
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Region to list VM sizes for."),
):
    """List available VM sizes in a region.

    If no region provided, prompts interactively.
    """
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import list_azure_vm_sizes, list_azure_locations

    if not region:
        region = Prompt.ask("Enter Azure region", default="eastus")

    sizes = list_azure_vm_sizes(region, sub_id)
    table = Table(title=f"VM Sizes in {region}")
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("vCPUs", justify="right", style="green")
    table.add_column("Memory (GB)", justify="right", style="green")
    table.add_column("Max Data Disks", justify="right", style="yellow")
    table.add_column("OS Disk (GB)", justify="right", style="dim")
    for size in sorted(sizes, key=lambda x: (x["vcpus"], x["memory_gb"])):
        table.add_row(
            size["name"],
            str(size["vcpus"]),
            str(size["memory_gb"]),
            str(size["max_data_disks"]),
            str(size["os_disk_size_gb"] or "N/A"),
        )
    print(table)
    print(f"\n[dim]Total: {len(sizes)} sizes available[/dim]")


@azure_app.command("vm-ls")
def azure_vm_list(
    resource_group: Optional[str] = typer.Option(None, "--resource-group", "-g", help="Resource group to list VMs from."),
):
    """List Azure virtual machines."""
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import list_azure_vms

    vms = list_azure_vms(resource_group=resource_group, subscription_id=sub_id)
    table = Table(title="Azure Virtual Machines")
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("Location", justify="left", style="green")
    table.add_column("VM Size", justify="left", style="green")
    table.add_column("State", justify="left", style="yellow")
    table.add_column("OS", justify="left", style="dim")
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


@azure_app.command("vm-create")
def azure_vm_create(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="VM name."),
    resource_group: Optional[str] = typer.Option(None, "--resource-group", "-g", help="Resource group."),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Azure region."),
    vm_size: Optional[str] = typer.Option(None, "--vm-size", help="VM size (e.g., Standard_B2s)."),
    admin_username: str = typer.Option("azureuser", "--admin-user", help="Admin username."),
    ssh_key_file: Optional[str] = typer.Option(None, "--ssh-key", help="Path to SSH public key file."),
    image: str = typer.Option("Ubuntu2204", "--image", help="Image: Ubuntu2204, Ubuntu2404, Debian12."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags (key=value,...)."),
):
    """Create an Azure virtual machine.

    If required options are not provided, prompts interactively.
    """
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import (
        create_azure_vm, list_azure_locations, list_azure_vm_sizes,
    )
    from ..util.utils import SSH_PUBLIC_KEY

    # Interactive prompts for missing values
    if not name:
        name = Prompt.ask("VM name")

    if not resource_group:
        resource_group = Prompt.ask("Resource group", default=f"{name}-rg")

    if not region:
        from ..cloud.azure.api import list_azure_locations
        locations = list_azure_locations(sub_id)
        popular = ["eastus", "eastus2", "westus2", "westeurope", "northeurope", "francecentral"]
        typer.echo("\n[bold]Popular regions:[/bold]")
        for i, r in enumerate(popular, 1):
            display = next((loc["display_name"] for loc in locations if loc["name"] == r), r)
            typer.echo(f"  {i}. {r} ({display})")
        choice = Prompt.ask("Select region number or type region name", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(popular):
            region = popular[int(choice) - 1]
        else:
            region = choice

    if not vm_size:
        typer.echo(f"\n[bold]Common VM sizes in {region}:[/bold]")
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
    if ssh_key_file:
        with open(ssh_key_file, "r") as f:
            ssh_public_key = f.read().strip()
    elif SSH_PUBLIC_KEY.exists():
        use_default = Confirm.ask(
            f"Use default SSH key ({SSH_PUBLIC_KEY})?",
            default=True,
        )
        if use_default:
            ssh_public_key = SSH_PUBLIC_KEY.read_text().strip()

    # Tags
    tag_dict = {}
    if tags:
        for pair in tags.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                tag_dict[k.strip()] = v.strip()

    # Confirmation
    typer.echo(f"\n[bold]Creating VM:[/bold]")
    typer.echo(f"  Name:           {name}")
    typer.echo(f"  Resource Group: {resource_group}")
    typer.echo(f"  Region:         {region}")
    typer.echo(f"  VM Size:        {vm_size}")
    typer.echo(f"  Image:          {image}")
    typer.echo(f"  Admin User:     {admin_username}")
    typer.echo(f"  SSH Key:        {'Yes' if ssh_public_key else 'No (password)'}")
    if tag_dict:
        typer.echo(f"  Tags:           {tag_dict}")

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
        tags=tag_dict,
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
        f"  SSH:  ssh {admin_username}@{result.get('public_ip', '<ip>')}",
        title="VM Created",
    ))


@azure_app.command("vm-delete")
def azure_vm_delete(
    name: str = typer.Argument(..., help="VM name to delete."),
    resource_group: str = typer.Option(..., "--resource-group", "-g", help="Resource group containing the VM."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Delete an Azure virtual machine."""
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import delete_azure_vm

    if not force:
        if not Confirm.ask(f"Delete VM '{name}' and its disks/NIC/IP in resource group '{resource_group}'?", default=False):
            raise typer.Abort()

    typer.echo(f"Deleting VM '{name}' (with disks, NIC, IP)...")
    delete_azure_vm(resource_group, name, subscription_id=sub_id)
    print(f"[green]VM '{name}' deleted (with disks, NIC, IP).[/green]")


@azure_app.command("resource-groups")
def azure_resource_groups():
    """List Azure resource groups."""
    sub_id = _ensure_azure_configured()
    from ..cloud.azure.api import list_azure_resource_groups

    rgs = list_azure_resource_groups(subscription_id=sub_id)
    table = Table(title="Azure Resource Groups")
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("Location", justify="left", style="green")
    table.add_column("State", justify="left", style="yellow")
    table.add_column("Tags", justify="left", style="dim")
    for rg in rgs:
        tags_str = ", ".join(f"{k}={v}" for k, v in (rg["tags"] or {}).items())
        table.add_row(rg["name"], rg["location"], rg["provisioning_state"] or "", tags_str)
    print(table)
    print(f"\n[dim]Total: {len(rgs)} resource groups[/dim]")
