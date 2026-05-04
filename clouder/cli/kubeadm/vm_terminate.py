"""Clouder CLI - kubeadm vm-terminate command."""

import typer
from rich import print
from rich.prompt import Confirm

from ..ctx import get_current_context
from ...util.utils import CLOUDER_KUBECONFIGS_FOLDER

from ._helpers import (
    _delete_cluster_metadata,
    _load_cluster_metadata,
    _resolve_cluster_vms,
)


def register(kubeadm_app: typer.Typer):
    """Register the vm-terminate command on the given Typer app."""

    @kubeadm_app.command("vm-terminate")
    def kubeadm_vm_terminate(
        name: str = typer.Argument(..., help="Cluster name."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
        delete_rg: bool = typer.Option(False, "--delete-rg", help="Also delete the resource group."),
    ):
        """Terminate all VMs and networking for a kubeadm cluster.

        Deletes VMs, NICs, public IPs, OS disks, NSG, and VNet for the cluster.
        """
        (cloud, context_id) = get_current_context()
        if cloud not in {"azure", "aws"}:
            typer.echo("Kubeadm commands are currently supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        if cloud == "aws":
            from ...cloud.aws.api import delete_aws_kubeadm_network, list_aws_vms, terminate_aws_vm

            vms = list_aws_vms()
            master_name = f"{name}-master"
            cluster_vms = [
                vm for vm in vms
                if vm["name"] == master_name or vm["name"].startswith(f"{name}-node-")
            ]
            if not cluster_vms:
                typer.echo(f"No VMs found for cluster '{name}'.", err=True)
                raise typer.Exit(1)

            metadata = _load_cluster_metadata(name) or {}

            print(f"\n[bold]Resources to delete for cluster '{name}' (AWS):[/bold]")
            for vm in cluster_vms:
                typer.echo(f"  EC2:  {vm['name']} ({vm['id']})")
            if metadata.get("networking"):
                typer.echo(f"  VPC:  {metadata['networking'].get('vpc_id', 'n/a')}")
                typer.echo(f"  SG:   {metadata['networking'].get('security_group_id', 'n/a')}")
                typer.echo(f"  Subnet: {metadata['networking'].get('subnet_id', 'n/a')}")

            if not force:
                if not Confirm.ask(f"\nDelete all resources for cluster '{name}'?", default=False):
                    raise typer.Abort()

            print("\n[bold]Terminating EC2 instances...[/bold]")
            for vm in cluster_vms:
                try:
                    terminate_aws_vm(vm["id"])
                    print(f"  [green]Terminated: {vm['name']} ({vm['id']})[/green]")
                except Exception as e:
                    print(f"  [red]Failed to terminate {vm['name']}: {e}[/red]")

            if metadata.get("networking"):
                print("[bold]Deleting VPC networking...[/bold]")
                try:
                    delete_aws_kubeadm_network(
                        vpc_id=metadata["networking"].get("vpc_id"),
                        subnet_id=metadata["networking"].get("subnet_id"),
                        internet_gateway_id=metadata["networking"].get("internet_gateway_id"),
                        route_table_id=metadata["networking"].get("route_table_id"),
                        security_group_id=metadata["networking"].get("security_group_id"),
                        region=metadata.get("region"),
                    )
                    print("  [green]Deleted AWS networking resources.[/green]")
                except Exception as e:
                    print(f"  [yellow]Could not fully delete networking resources: {e}[/yellow]")

            kubeconfig_path = CLOUDER_KUBECONFIGS_FOLDER / f"kubeconfig-{name}"
            if kubeconfig_path.exists():
                kubeconfig_path.unlink()
                typer.echo(f"  Removed kubeconfig: {kubeconfig_path}")

            _delete_cluster_metadata(name)
            print(f"\n[green]Cluster '{name}' terminated.[/green]")
            return

        from ...cloud.azure.api import (
            list_azure_vms,
            delete_azure_vm,
            delete_azure_nsg,
            delete_azure_vnet,
        )

        # Find cluster VMs by naming convention
        vms = list_azure_vms(subscription_id=context_id)
        master_name = f"{name}-master"
        cluster_vms = [
            vm for vm in vms
            if vm["name"] == master_name or vm["name"].startswith(f"{name}-node-")
        ]

        if not cluster_vms:
            typer.echo(f"No VMs found for cluster '{name}'.", err=True)
            raise typer.Exit(1)

        rg = cluster_vms[0]["resource_group"]

        # Show what will be deleted
        print(f"\n[bold]Resources to delete for cluster '{name}':[/bold]")
        for vm in cluster_vms:
            typer.echo(f"  VM:   {vm['name']} (+ attached disks, NIC, IP)")
        typer.echo(f"  LB:   {name}-lb (if exists)")
        typer.echo(f"  LB IP:{name}-lb-ip (if exists)")
        typer.echo(f"  NSG:  {name}-nsg")
        typer.echo(f"  VNet: {name}-vnet")
        if delete_rg:
            typer.echo(f"  RG:   {rg}")

        if not force:
            if not Confirm.ask(f"\nDelete all resources for cluster '{name}'?", default=False):
                raise typer.Abort()

        # Step 1: Delete VMs (with automatic cleanup of disks, NICs, IPs)
        print("\n[bold]Deleting VMs (with disks, NICs, IPs)...[/bold]")
        for vm in cluster_vms:
            typer.echo(f"  Deleting VM: {vm['name']}...")
            try:
                delete_azure_vm(rg, vm["name"], subscription_id=context_id)
                print(f"  [green]Deleted: {vm['name']} + associated resources[/green]")
            except Exception as e:
                print(f"  [red]Failed to delete VM {vm['name']}: {e}[/red]")

        # Step 2: Delete Load Balancer and LB public IP (if they exist)
        print("[bold]Deleting Load Balancer...[/bold]")
        try:
            from ...cloud.azure.api import delete_azure_load_balancer
            delete_azure_load_balancer(rg, f"{name}-lb", subscription_id=context_id)
            print(f"  [green]Deleted: {name}-lb[/green]")
        except Exception:
            print(f"  [dim]LB {name}-lb not found or already deleted.[/dim]")
        try:
            delete_azure_public_ip(rg, f"{name}-lb-ip", subscription_id=context_id)
            print(f"  [green]Deleted: {name}-lb-ip[/green]")
        except Exception:
            print(f"  [dim]LB IP {name}-lb-ip not found or already deleted.[/dim]")

        # Step 3: Delete NSG
        print("[bold]Deleting NSG...[/bold]")
        try:
            delete_azure_nsg(rg, f"{name}-nsg", subscription_id=context_id)
            print(f"  [green]Deleted: {name}-nsg[/green]")
        except Exception:
            print(f"  [dim]NSG {name}-nsg not found or already deleted.[/dim]")

        # Step 4: Delete VNet (includes subnets)
        print("[bold]Deleting VNet...[/bold]")
        try:
            delete_azure_vnet(rg, f"{name}-vnet", subscription_id=context_id)
            print(f"  [green]Deleted: {name}-vnet[/green]")
        except Exception:
            print(f"  [dim]VNet {name}-vnet not found or already deleted.[/dim]")

        # Step 5: Optionally delete resource group
        if delete_rg:
            print("[bold]Deleting resource group...[/bold]")
            try:
                from ...cloud.azure.api import _get_resource_client
                resource_client = _get_resource_client(context_id)
                poller = resource_client.resource_groups.begin_delete(rg)
                poller.result()
                print(f"  [green]Deleted: {rg}[/green]")
            except Exception as e:
                print(f"  [red]Failed to delete resource group {rg}: {e}[/red]")

        # Remove kubeconfig if it exists
        kubeconfig_path = CLOUDER_KUBECONFIGS_FOLDER / f"kubeconfig-{name}"
        if kubeconfig_path.exists():
            kubeconfig_path.unlink()
            typer.echo(f"  Removed kubeconfig: {kubeconfig_path}")

        # Remove cluster metadata if it exists
        _delete_cluster_metadata(name)

        print(f"\n[green]Cluster '{name}' terminated.[/green]")
