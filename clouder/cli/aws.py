"""Clouder CLI - AWS commands."""

from typing import Optional

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from ..cloud.aws.api import (
    _client,
    get_aws_identity,
    list_aws_regions,
    list_aws_vms,
)

aws_app = typer.Typer(no_args_is_help=True)


@aws_app.callback(invoke_without_command=True)
def aws_default(ctx: typer.Context):
    """Show AWS identity if no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        aws_info()


@aws_app.command("info")
def aws_info():
    """Show current AWS caller identity."""
    ident = get_aws_identity()
    table = Table(title="AWS Caller Identity")
    table.add_column("Account ID", justify="left", style="cyan", no_wrap=True)
    table.add_column("ARN", justify="left", style="green")
    table.add_column("User ID", justify="left", style="dim")
    table.add_row(
        ident.get("account_id", "N/A"),
        ident.get("arn", "N/A"),
        ident.get("user_id", "N/A"),
    )
    print(table)


@aws_app.command("regions")
def aws_regions():
    """List available AWS regions."""
    regions = list_aws_regions()
    table = Table(title="AWS Regions")
    table.add_column("Region", justify="left", style="cyan", no_wrap=True)
    table.add_column("Endpoint", justify="left", style="green")
    table.add_column("Opt-in", justify="left", style="green")
    for region in regions:
        table.add_row(
            region.get("name", ""),
            region.get("endpoint", ""),
            region.get("opt_in_status", ""),
        )
    print(table)


@aws_app.command("configure")
def aws_configure():
    """Show AWS credential setup guidance."""
    print(
        Panel(
            "Use one of the following methods to configure AWS credentials:\n\n"
            "  1) aws configure\n"
            "  2) Export env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n"
            "  3) Use an IAM role/instance profile\n\n"
            "Then validate with: clouder aws info",
            title="AWS Credential Setup",
        )
    )


@aws_app.command("resources")
def aws_resources(
    region: Optional[str] = typer.Option(None, "--region", "-r", help="AWS region to inspect."),
):
    """List high-level AWS resource inventory for a region."""
    ec2 = _client("ec2", region=region)
    resolved_region = ec2.meta.region_name

    vpcs = ec2.describe_vpcs().get("Vpcs", [])
    subnets = ec2.describe_subnets().get("Subnets", [])
    security_groups = ec2.describe_security_groups().get("SecurityGroups", [])
    instances = list_aws_vms(region=resolved_region)

    table = Table(title=f"AWS Resources ({resolved_region})")
    table.add_column("Resource", justify="left", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("EC2 Instances", str(len(instances)))
    table.add_row("VPCs", str(len(vpcs)))
    table.add_row("Subnets", str(len(subnets)))
    table.add_row("Security Groups", str(len(security_groups)))
    print(table)


@aws_app.command("vm-sizes")
def aws_vm_sizes(
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Region to list EC2 instance types for."),
):
    """List common EC2 instance types available in a region."""
    ec2 = _client("ec2", region=region)
    resolved_region = ec2.meta.region_name
    paginator = ec2.get_paginator("describe_instance_types")

    rows: list[dict] = []
    for page in paginator.paginate(
        Filters=[
            {"Name": "current-generation", "Values": ["true"]},
            {"Name": "supported-virtualization-type", "Values": ["hvm"]},
        ]
    ):
        for item in page.get("InstanceTypes", []):
            memory_mib = int((item.get("MemoryInfo") or {}).get("SizeInMiB") or 0)
            gpu_info = item.get("GpuInfo") or {}
            gpu_cards = gpu_info.get("Gpus") or []
            gpu_count = 0
            gpu_type_parts: list[str] = []
            gpu_memory_mib = 0
            for gpu in gpu_cards:
                count = int(gpu.get("Count") or 0)
                gpu_count += count
                manufacturer = gpu.get("Manufacturer") or ""
                name = gpu.get("Name") or ""
                mem_info = gpu.get("MemoryInfo") or {}
                size_mib = int(mem_info.get("SizeInMiB") or 0)
                if count > 0 and size_mib > 0:
                    gpu_memory_mib += size_mib * count
                if manufacturer and name:
                    gpu_type_parts.append(f"{manufacturer} {name}")
                elif name:
                    gpu_type_parts.append(name)
                elif manufacturer:
                    gpu_type_parts.append(manufacturer)

            accelerator_info = item.get("InferenceAcceleratorInfo") or {}
            accel_items = accelerator_info.get("Accelerators") or []
            if accel_items and not gpu_type_parts:
                gpu_type_parts = [a.get("Name") or "Accelerator" for a in accel_items]

            rows.append(
                {
                    "instance_type": item.get("InstanceType", ""),
                    "family": item.get("InstanceType", "").split(".")[0],
                    "vcpus": int((item.get("VCpuInfo") or {}).get("DefaultVCpus") or 0),
                    "memory_gib": round(memory_mib / 1024, 2),
                    "network": (item.get("NetworkInfo") or {}).get("NetworkPerformance") or "N/A",
                    "architecture": ",".join((item.get("ProcessorInfo") or {}).get("SupportedArchitectures") or []) or "N/A",
                    "gpu_available": gpu_count > 0 or bool(accel_items),
                    "gpu_count": gpu_count,
                    "gpu_type": ", ".join(dict.fromkeys(gpu_type_parts)) if gpu_type_parts else "N/A",
                    "gpu_memory_gib": round(gpu_memory_mib / 1024, 2) if gpu_memory_mib > 0 else None,
                }
            )
        if len(rows) >= 150:
            break

    rows = sorted(rows, key=lambda x: (x["vcpus"], x["memory_gib"], x["instance_type"]))[:100]

    table = Table(title=f"AWS Instance Types ({resolved_region})")
    table.add_column("Instance Type", justify="left", style="cyan")
    table.add_column("Family", justify="left", style="dim")
    table.add_column("vCPUs", justify="right", style="green")
    table.add_column("Memory (GiB)", justify="right", style="green")
    table.add_column("Network", justify="left", style="yellow")
    table.add_column("Arch", justify="left", style="dim")
    table.add_column("GPU", justify="center", style="magenta")
    table.add_column("GPU Count", justify="right", style="magenta")
    table.add_column("GPU Type", justify="left", style="magenta")
    table.add_column("GPU Mem (GiB)", justify="right", style="magenta")
    for row in rows:
        gpu_mem = row.get("gpu_memory_gib")
        table.add_row(
            row["instance_type"],
            row.get("family") or "N/A",
            str(row["vcpus"]),
            str(row["memory_gib"]),
            row.get("network") or "N/A",
            row.get("architecture") or "N/A",
            "Yes" if row.get("gpu_available") else "No",
            str(row.get("gpu_count") or 0),
            row.get("gpu_type") or "N/A",
            str(gpu_mem) if gpu_mem is not None else "N/A",
        )
    print(table)
    print(f"\n[dim]Showing {len(rows)} instance types.[/dim]")


@aws_app.command("vm-ls")
def aws_vm_list(
    region: str = typer.Option(None, "--region", "-r", help="Optional AWS region override."),
):
    """List AWS EC2 instances."""
    vms = list_aws_vms(region=region)
    table = Table(title="AWS EC2 Instances")
    table.add_column("Name", justify="left", style="cyan", no_wrap=True)
    table.add_column("Instance ID", justify="left", style="green")
    table.add_column("Type", justify="left", style="green")
    table.add_column("State", justify="left", style="yellow")
    table.add_column("Public IP", justify="left", style="green")
    table.add_column("Region", justify="left", style="dim")
    for vm in vms:
        table.add_row(
            vm.get("name") or "N/A",
            vm.get("id", "N/A"),
            vm.get("instance_type") or "N/A",
            vm.get("state") or "N/A",
            vm.get("public_ip") or "N/A",
            vm.get("region") or "N/A",
        )
    print(table)


@aws_app.command("vm-create")
def aws_vm_create(
    name: str = typer.Option(..., "--name", "-n", help="VM name."),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Optional AWS region override."),
    vm_size: Optional[str] = typer.Option(None, "--vm-size", help="EC2 instance type (e.g., t3.large)."),
):
    """Create an AWS EC2 VM using the same flow as `clouder vm create` on AWS."""
    from .vm import _create_aws_vm

    _create_aws_vm(name=name, region=region, vm_size=vm_size)


@aws_app.command("vm-delete")
def aws_vm_delete(
    name: str = typer.Argument(..., help="VM name to delete."),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Optional AWS region override."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Terminate an AWS EC2 instance by VM name tag."""
    from ..cloud.aws.api import terminate_aws_vm

    vms = list_aws_vms(region=region)
    match = [vm for vm in vms if (vm.get("name") or "") == name]
    if not match:
        typer.echo(f"VM '{name}' not found.", err=True)
        raise typer.Exit(1)

    vm = match[0]
    if not force:
        if not Confirm.ask(f"Terminate AWS instance '{name}' (id: {vm.get('id')})?", default=False):
            raise typer.Abort()

    terminate_aws_vm(vm.get("id"), region=region or vm.get("region"))
    print(f"[green]VM '{name}' termination requested.[/green]")
