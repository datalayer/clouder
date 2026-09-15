"""Clouder CLI - S3 bucket management commands."""

import typer
from rich import print
from rich.table import Table

from .ctx import get_current_context
from ..util.utils import DEFAULT_REGION

s3_app = typer.Typer(no_args_is_help=True)


@s3_app.callback(invoke_without_command=True)
def s3_default(ctx: typer.Context):
    """List S3 buckets if no subcommand given."""
    if ctx.invoked_subcommand is None:
        s3_list()


@s3_app.command("create")
def s3_create(
    name: str = typer.Argument(..., help="Name for the S3 bucket."),
    region: str | None = typer.Option(None, "--region", "-r", help="Region for the S3 bucket."),
):
    """Create an S3 bucket."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        typer.echo("S3 buckets are not supported on Azure. Use Azure Blob Storage instead.")
        raise typer.Exit(0)
    if cloud == "aws":
        from ..cloud.aws.api import _client

        s3 = _client("s3", region=region)
        resolved_region = region or s3.meta.region_name or "us-east-1"
        params = {"Bucket": name}
        # us-east-1 must omit CreateBucketConfiguration.
        if resolved_region != "us-east-1":
            params["CreateBucketConfiguration"] = {"LocationConstraint": resolved_region}
        s3.create_bucket(**params)
        typer.echo(f"AWS S3 bucket '{name}' created in region '{resolved_region}'.")
        return
    from ..cloud.ovh.api import create_ovh_s3
    if not region:
        region = DEFAULT_REGION
    res = create_ovh_s3(context_id, name, region)
    print(res)


@s3_app.command("ls")
def s3_list(
    region: str | None = typer.Option(None, "--region", "-r", help="Region to list S3 buckets from."),
):
    """List S3 buckets."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        typer.echo("S3 buckets are not supported on Azure. Use `clouder azure resources` to list Azure resources.")
        raise typer.Exit(0)
    if cloud == "aws":
        from ..cloud.aws.api import _client

        s3 = _client("s3", region=region)
        buckets = s3.list_buckets().get("Buckets", [])

        table = Table(title=f"AWS S3 Buckets (region filter: {region or 'all'})")
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Created At", justify="left", style="green")

        for bucket in sorted(buckets, key=lambda b: b.get("Name", "")):
            bname = bucket.get("Name", "")
            location = s3.get_bucket_location(Bucket=bname).get("LocationConstraint") or "us-east-1"
            if region and location != region:
                continue
            created = bucket.get("CreationDate")
            table.add_row(
                bname,
                location,
                str(created) if created else "",
            )
        print(table)
        return
    from ..cloud.ovh.api import get_ovh_s3, get_ovh_project
    project = get_ovh_project(context_id)
    if not region:
        region = DEFAULT_REGION
    s3s = get_ovh_s3(context_id, region)
    for s3 in s3s:
        table = Table(title=f"S3 {cloud}:{project['description']}:{s3['name']}")
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Virtual Host", justify="left", style="green")
        table.add_column("Objects Count", justify="left", style="green")
        table.add_column("Objects Size", justify="left", style="green")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Created At", justify="left", style="green")
        table.add_row(
            s3["name"],
            s3["virtualHost"],
            str(s3["objectsCount"]),
            str(s3["objectsSize"]),
            s3["region"],
            s3["createdAt"],
        )
        print(table)
