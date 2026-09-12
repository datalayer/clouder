"""Clouder CLI - Info commands."""

import typer
from rich import print
from rich.table import Table

from .ctx import get_current_context
from ..util.utils import DEFAULT_REGION

info_app = typer.Typer(no_args_is_help=True)


@info_app.callback(invoke_without_command=True)
def info_default(ctx: typer.Context):
    """Show context info if no subcommand given."""
    if ctx.invoked_subcommand is None:
        info_ctx()


@info_app.command("ctx")
def info_ctx():
    """Show detailed info about the current context."""
    (cloud, context_id) = get_current_context()

    if cloud == "azure":
        _info_ctx_azure(context_id)
    elif cloud == "aws":
        _info_ctx_aws(context_id)
    else:
        _info_ctx_ovh(cloud, context_id)


def _info_ctx_aws(account_id: str):
    """Show detailed info for an AWS context."""
    from ..cloud.aws.api import list_aws_regions, list_aws_vms

    regions = list_aws_regions()
    table = Table(title=f"AWS Regions (account {account_id})")
    table.add_column("Name", justify="left", style="cyan", no_wrap=True)
    table.add_column("Opt-in", justify="left", style="green")
    for region in regions:
        table.add_row(region["name"], region.get("opt_in_status", ""))
    print(table)

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
            vm.get("name") or "N/A",
            vm.get("id", "N/A"),
            vm.get("instance_type") or "N/A",
            vm.get("state") or "N/A",
            vm.get("public_ip") or "N/A",
            vm.get("region") or "N/A",
        )
    print(table)

    print(f"\n[dim]Total: {len(regions)} regions, {len(vms)} instances[/dim]")


def _info_ctx_azure(subscription_id: str):
    """Show detailed info for an Azure context."""
    from ..cloud.azure.api import (
        list_azure_vms,
        list_azure_resource_groups,
    )

    # Resource groups
    rgs = list_azure_resource_groups(subscription_id=subscription_id)
    table = Table(title=f"Azure Resource Groups (subscription {subscription_id[:8]}...)")
    table.add_column("Name", justify="left", style="cyan", no_wrap=True)
    table.add_column("Location", justify="left", style="green")
    table.add_column("Provisioning State", justify="left", style="green")
    for rg in rgs:
        table.add_row(rg["name"], rg["location"], rg.get("provisioning_state", ""))
    print(table)

    # Virtual machines
    vms = list_azure_vms(subscription_id=subscription_id)
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

    print(f"\n[dim]Total: {len(rgs)} resource groups, {len(vms)} VMs[/dim]")


def _info_ctx_ovh(cloud: str, context_id: str):
    """Show detailed info for an OVH context."""
    from ..cloud.ovh.api import (
        get_ovh_project,
        get_ovh_vm,
        get_ovh_s3,
        get_ovh_ssh_keys,
        get_ovh_kubernetess,
        get_ovh_kubernetes,
        get_ovh_kubernetes_nodepools,
        get_ovh_kubernetes_nodepool_nodes,
    )
    project = get_ovh_project(context_id)
    project_name = project["description"]

    # Kubernetes
    kubernetess = get_ovh_kubernetess(context_id)
    table = Table(title=f"Kubernetes {cloud}:{project_name}")
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("Region", justify="left", style="green")
    table.add_column("Version", justify="left", style="green")
    table.add_column("Status", justify="left", style="green")
    table.add_column("Nodepool", justify="left", style="green")
    table.add_column("Nodes", justify="left", style="green")
    for kubernetes_id in kubernetess:
        kubernetes = get_ovh_kubernetes(context_id, kubernetes_id)
        table.add_row(
            kubernetes["name"],
            kubernetes["region"],
            kubernetes["version"],
            kubernetes["status"],
        )
        nodepools = get_ovh_kubernetes_nodepools(context_id, kubernetes_id)
        for nodepool in nodepools:
            nodes = get_ovh_kubernetes_nodepool_nodes(context_id, kubernetes_id, nodepool["id"])
            table.add_row("", "", "", "", nodepool["name"], str(len(nodes)))
    print(table)

    # Virtual machines
    vms = get_ovh_vm(context_id)
    table = Table(title=f"Virtual Machines {cloud}:{project_name}")
    table.add_column("ID", justify="left", style="cyan", no_wrap=True)
    table.add_column("Name", justify="left", style="green")
    table.add_column("Flavor ID", justify="left", style="green")
    table.add_column("Region", justify="left", style="green")
    table.add_column("Status", justify="left", style="green")
    for vm in vms:
        table.add_row(
            vm["id"], vm["name"], vm["flavorId"], vm["region"], vm["status"],
        )
    print(table)

    # S3
    s3s = get_ovh_s3(context_id, DEFAULT_REGION)
    table = Table(title=f"S3 {cloud}:{project_name}")
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("Virtual Host", justify="left", style="green")
    table.add_column("Objects Count", justify="left", style="green")
    table.add_column("Objects Size", justify="left", style="green")
    table.add_column("Region", justify="left", style="green")
    table.add_column("Created At", justify="left", style="green")
    for s3 in s3s:
        table.add_row(
            s3["name"], s3["virtualHost"],
            str(s3["objectsCount"]), str(s3["objectsSize"]),
            s3["region"], s3["createdAt"],
        )
    print(table)

    # SSH keys
    ssh_keys = get_ovh_ssh_keys(context_id)
    table = Table(title=f"SSH Keys {cloud}:{project_name}")
    table.add_column("ID", justify="left", style="cyan", no_wrap=True)
    table.add_column("Name", justify="left", style="cyan")
    table.add_column("Fingerprint", justify="left", style="green")
    table.add_column("Public Key", justify="left", style="green")
    for ssh_key in ssh_keys:
        table.add_row(
            ssh_key["id"], ssh_key["name"],
            ssh_key.get("fingerprint", ""), ssh_key.get("publicKey", ""),
        )
    print(table)


@info_app.command("me")
def info_me():
    """Show current user information."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        from ..cloud.azure.api import list_azure_subscriptions
        subs = list_azure_subscriptions()
        sub = next(
            (
                s
                for s in subs
                if s.get("id") == context_id or s.get("subscription_id") == context_id
            ),
            None,
        )
        table = Table(title="Azure Account")
        table.add_column("Subscription ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="green")
        table.add_column("State", justify="left", style="green")
        table.add_column("Tenant ID", justify="left", style="dim")
        if sub:
            table.add_row(
                sub.get("id", sub.get("subscription_id", "N/A")),
                sub.get("name", sub.get("display_name", "N/A")),
                sub["state"], sub.get("tenant_id", "N/A"),
            )
        else:
            table.add_row(context_id, "N/A", "N/A", "N/A")
        print(table)
    elif cloud == "aws":
        from ..cloud.aws.api import get_aws_identity
        me = get_aws_identity()
        table = Table(title="AWS Caller Identity")
        table.add_column("Account ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("ARN", justify="left", style="green")
        table.add_column("User ID", justify="left", style="dim")
        table.add_row(me.get("account_id", "N/A"), me.get("arn", "N/A"), me.get("user_id", "N/A"))
        print(table)
    else:
        from ..cloud.ovh.api import get_ovh_me
        me = get_ovh_me()
        table = Table(title="OVHcloud Me")
        table.add_column("Legal Form", justify="left", style="cyan", no_wrap=True)
        table.add_column("Organisation", justify="left", style="magenta")
        table.add_column("First Name", justify="left", style="green")
        table.add_column("Name", justify="left", style="green")
        table.add_column("Country", justify="left", style="green")
        table.add_row(
            me["legalform"], me["organisation"],
            me["firstname"], me["name"], me["country"],
        )
        print(table)
