"""Clouder CLI - OVHcloud commands."""

from typing import Optional

import typer
from rich import print
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .ctx import get_current_context

ovh_app = typer.Typer(no_args_is_help=True)


def _require_ovh_context() -> str:
    """Ensure current context is OVH and return project id."""
    cloud, context_id = get_current_context()
    if cloud != "ovh":
        typer.echo(
            "Current context is not OVH. Run `clouder ctx set ovh <project_id>` first.",
            err=True,
        )
        raise typer.Exit(1)
    return context_id


@ovh_app.callback(invoke_without_command=True)
def ovh_default(ctx: typer.Context):
    """Show OVH project if no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        ovh_info()


@ovh_app.command("info")
def ovh_info():
    """Show OVH project information for current context."""
    from ..cloud.ovh.api import get_ovh_project

    project_id = _require_ovh_context()
    project = get_ovh_project(project_id)

    table = Table(title="OVH Project")
    table.add_column("Project ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Description", style="green")
    table.add_column("Status", style="yellow")
    table.add_row(
        project.get("project_id", project_id),
        project.get("description", "N/A"),
        project.get("description", "N/A"),
        project.get("status", "N/A"),
    )
    print(table)


@ovh_app.command("regions")
def ovh_regions():
    """List OVH regions available to the current project."""
    from ..cloud.ovh.api import list_ovh_regions

    project_id = _require_ovh_context()
    regions = list_ovh_regions(project_id)

    table = Table(title="OVH Regions")
    table.add_column("Region", style="cyan")
    for region in regions:
        table.add_row(str(region))
    print(table)
    print(f"\n[dim]Total: {len(regions)} regions[/dim]")


@ovh_app.command("resources")
def ovh_resources():
    """Show a high-level OVH resource inventory for current project."""
    from ..cloud.ovh.api import (
        get_ovh_kubernetess,
        get_ovh_s3,
        get_ovh_ssh_keys,
        get_ovh_vm,
        list_ovh_regions,
    )

    project_id = _require_ovh_context()

    vms = get_ovh_vm(project_id)
    ssh_keys = get_ovh_ssh_keys(project_id)
    kubes = get_ovh_kubernetess(project_id)

    bucket_count = 0
    for region in list_ovh_regions(project_id):
        try:
            bucket_count += len(get_ovh_s3(project_id, str(region)))
        except Exception:
            continue

    table = Table(title="OVH Resources")
    table.add_column("Resource", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Instances", str(len(vms)))
    table.add_row("SSH Keys", str(len(ssh_keys)))
    table.add_row("Kubernetes Clusters", str(len(kubes)))
    table.add_row("S3 Buckets", str(bucket_count))
    print(table)


@ovh_app.command("vm-sizes")
def ovh_vm_sizes(
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Optional region filter."),
):
    """List OVH flavors (instance sizes) for current project."""
    from ..cloud.ovh.api import list_ovh_flavors

    project_id = _require_ovh_context()
    flavors = list_ovh_flavors(project_id, region=region)

    table = Table(title=f"OVH Flavors{f' ({region})' if region else ''}")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("vCPUs", justify="right", style="green")
    table.add_column("RAM (GB)", justify="right", style="green")
    table.add_column("Disk (GB)", justify="right", style="yellow")
    table.add_column("Available", style="yellow")
    table.add_column("Type", style="dim")

    def _ram_gb(value):
        try:
            return round(float(value) / 1024, 2)
        except Exception:
            return None

    sorted_flavors = sorted(
        flavors,
        key=lambda f: (
            int(f.get("vcpus") or 0),
            float(f.get("ram") or 0),
            str(f.get("name") or ""),
        ),
    )

    for flavor in sorted_flavors:
        table.add_row(
            str(flavor.get("id") or "N/A"),
            str(flavor.get("name") or "N/A"),
            str(flavor.get("vcpus") or "N/A"),
            str(_ram_gb(flavor.get("ram")) or "N/A"),
            str(flavor.get("disk") or "N/A"),
            "Yes" if flavor.get("available") else "No",
            str(flavor.get("type") or "N/A"),
        )
    print(table)
    print(f"\n[dim]Total: {len(sorted_flavors)} flavors[/dim]")


@ovh_app.command("vm-ls")
def ovh_vm_list():
    """List OVH instances in current project."""
    from ..cloud.ovh.api import get_ovh_vm

    project_id = _require_ovh_context()
    vms = get_ovh_vm(project_id)

    table = Table(title="OVH Virtual Machines")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Flavor ID", style="green")
    table.add_column("Region", style="green")
    table.add_column("Status", style="yellow")
    for vm in vms:
        table.add_row(
            str(vm.get("id") or "N/A"),
            str(vm.get("name") or "N/A"),
            str(vm.get("flavorId") or "N/A"),
            str(vm.get("region") or "N/A"),
            str(vm.get("status") or "N/A"),
        )
    print(table)
    print(f"\n[dim]Total: {len(vms)} VMs[/dim]")


@ovh_app.command("vm-create")
def ovh_vm_create(
    name: str = typer.Option(..., "--name", "-n", help="VM name."),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="OVH region."),
    flavor: Optional[str] = typer.Option(None, "--flavor", "-f", help="Flavor ID (e.g. b2-15)."),
):
    """Create an OVH VM in the current project."""
    from ..cloud.ovh.api import create_ovh_vm, list_ovh_flavors, list_ovh_regions

    project_id = _require_ovh_context()

    if not region:
        regions = list_ovh_regions(project_id)
        if not regions:
            typer.echo("No OVH regions available for this project.", err=True)
            raise typer.Exit(1)
        print("\n[bold]OVH regions:[/bold]")
        for i, item in enumerate(regions, 1):
            typer.echo(f"  {i}. {item}")
        choice = Prompt.ask("Select region number or type region", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(regions):
            region = str(regions[int(choice) - 1])
        else:
            region = choice

    if not flavor:
        flavors = [f for f in list_ovh_flavors(project_id, region=region) if f.get("available")]
        if not flavors:
            typer.echo(f"No available flavors found in region '{region}'.", err=True)
            raise typer.Exit(1)

        print(f"\n[bold]Common OVH flavors in {region}:[/bold]")
        for i, f in enumerate(flavors[:12], 1):
            ram_gb = round(float(f.get("ram") or 0) / 1024, 2)
            typer.echo(f"  {i}. {f.get('id')} ({f.get('vcpus')} vCPU, {ram_gb} GB RAM)")

        choice = Prompt.ask("Select flavor number or type flavor id", default="1")
        if choice.isdigit() and 1 <= int(choice) <= min(12, len(flavors)):
            flavor = str(flavors[int(choice) - 1].get("id"))
        else:
            flavor = choice

    print("\n[bold]Creating OVH VM:[/bold]")
    typer.echo(f"  Name:   {name}")
    typer.echo(f"  Region: {region}")
    typer.echo(f"  Flavor: {flavor}")
    if not Confirm.ask("\nProceed?", default=True):
        raise typer.Abort()

    create_ovh_vm(project_id, name, region, flavor_id=flavor)
    print(f"[green]OVH VM '{name}' creation requested.[/green]")


@ovh_app.command("vm-delete")
def ovh_vm_delete(
    name: str = typer.Argument(..., help="VM name to delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Delete an OVH VM by name."""
    from ..cloud.ovh.api import delete_ovh_vm, get_ovh_vm

    project_id = _require_ovh_context()
    vms = get_ovh_vm(project_id)
    match = [vm for vm in vms if (vm.get("name") or "") == name]
    if not match:
        typer.echo(f"VM '{name}' not found.", err=True)
        raise typer.Exit(1)

    vm = match[0]
    if not force:
        if not Confirm.ask(f"Delete OVH VM '{name}' (id: {vm.get('id')})?", default=False):
            raise typer.Abort()

    delete_ovh_vm(project_id, vm.get("id"))
    print(f"[green]VM '{name}' deleted.[/green]")


@ovh_app.command("vm-ssh")
def ovh_vm_ssh(
    vm_name: str = typer.Argument(..., help="Name of the OVH VM to SSH into."),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="SSH username."),
    key: Optional[str] = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
    port: int = typer.Option(22, "--port", "-p", help="SSH port."),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="Command to run on remote host (non-interactive)."),
):
    """SSH into an OVH VM by name (same behavior as `clouder ssh`)."""
    from .ssh import ssh_to_vm

    project_id = _require_ovh_context()
    ssh_to_vm(
        vm_name=vm_name,
        user=user,
        key=key,
        port=port,
        command=command,
        cloud="ovh",
        context_id=project_id,
    )
