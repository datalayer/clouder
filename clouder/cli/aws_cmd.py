"""Clouder CLI - AWS commands."""

import typer
from rich import print
from rich.table import Table

from ..cloud.aws.api import get_aws_identity, list_aws_regions, list_aws_vms

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
    table.add_column("Opt-in", justify="left", style="green")
    for region in regions:
        table.add_row(region.get("name", ""), region.get("opt_in_status", ""))
    print(table)


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
