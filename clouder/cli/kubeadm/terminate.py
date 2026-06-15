"""Clouder CLI - kubeadm terminate command."""

import shutil

import typer
from rich import print
from rich.prompt import Confirm, Prompt

from ...util.utils import kubeadm_cluster_folder
from ...util.wait import wait_with_spinner

from ._helpers import (
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
    _delete_cluster_metadata,
    _load_cluster_metadata,
    _resolve_cluster_vms,
)


def register(kubeadm_app: typer.Typer):
    """Register the terminate command on the given Typer app."""

    @kubeadm_app.command("terminate")
    def kubeadm_terminate(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
        delete_rg: bool = typer.Option(False, "--delete-rg", help="Also delete the resource group (auto-enabled when RG is <cluster>-rg)."),
    ):
        """Terminate all VMs and networking for a kubeadm cluster.

        Deletes VMs, NICs, public IPs, OS disks, NSG, and VNet for the cluster.
        """
        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        if cloud not in {"azure", "aws"}:
            typer.echo("Kubeadm commands are currently supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        def _require_cluster_name_confirmation() -> None:
            typed_name = Prompt.ask(f"Type the cluster name to confirm deletion ({name})")
            if typed_name.strip() != name:
                print("[red]Cluster name mismatch. Aborting deletion.[/red]")
                raise typer.Abort()

        if cloud == "aws":
            from ...cloud.aws.api import (
                delete_aws_kubeadm_network,
                list_aws_vms,
                terminate_aws_vm,
                wait_aws_instances_terminated,
            )

            metadata = _load_cluster_metadata(name) or {}
            aws_region = str(metadata.get("region") or "").strip() or None
            failures: list[str] = []

            vms = list_aws_vms(region=aws_region)
            master_prefix = f"{name}-master"
            cluster_vms = [
                vm for vm in vms
                if vm["name"] == master_prefix
                or vm["name"].startswith(f"{master_prefix}-")
                or vm["name"].startswith(f"{name}-node-")
            ]

            has_networking = bool(metadata.get("networking"))
            if not cluster_vms and not has_networking:
                region_hint = aws_region or "current-default"
                typer.echo(
                    f"No VMs found for cluster '{name}' in AWS region '{region_hint}' and no networking metadata to clean up.",
                    err=True,
                )
                raise typer.Exit(1)

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
                _require_cluster_name_confirmation()

            print("\n[bold]Terminating EC2 instances...[/bold]")
            terminated_ids: list[str] = []
            if not cluster_vms:
                print("  [dim]No EC2 instances found for this cluster; continuing with networking cleanup.[/dim]")
            for vm in cluster_vms:
                try:
                    terminate_aws_vm(vm["id"], region=aws_region or vm.get("region"))
                    terminated_ids.append(vm["id"])
                    print(f"  [green]Terminated: {vm['name']} ({vm['id']})[/green]")
                except Exception as e:
                    print(f"  [red]Failed to terminate {vm['name']}: {e}[/red]")
                    failures.append(f"terminate:{vm['id']}")

            if terminated_ids:
                try:
                    wait_aws_instances_terminated(terminated_ids, region=aws_region)
                except Exception as e:
                    print(f"  [yellow]Instance termination waiter did not fully complete: {e}[/yellow]")
                    failures.append("waiter:instance_terminated")

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
                    net = metadata.get("networking", {}) or {}
                    cmd_region = str(metadata.get("region") or aws_region or "")
                    vpc_id = net.get("vpc_id") or "<vpc-id>"
                    subnet_id = net.get("subnet_id") or "<subnet-id>"
                    igw_id = net.get("internet_gateway_id") or "<igw-id>"
                    route_table_id = net.get("route_table_id") or "<route-table-id>"
                    sg_id = net.get("security_group_id") or "<security-group-id>"
                    print("  [yellow]Manual cleanup commands (run if retries keep failing):[/yellow]")
                    typer.echo(f"    aws elbv2 describe-load-balancers --region {cmd_region} --query \"LoadBalancers[?VpcId=='{vpc_id}'].LoadBalancerArn\" --output text")
                    typer.echo(f"    aws elbv2 describe-target-groups --region {cmd_region} --query \"TargetGroups[?VpcId=='{vpc_id}'].TargetGroupArn\" --output text")
                    typer.echo(f"    aws ec2 describe-network-interfaces --region {cmd_region} --filters Name=vpc-id,Values={vpc_id}")
                    typer.echo(f"    aws ec2 describe-addresses --region {cmd_region}")
                    typer.echo(f"    aws ec2 detach-internet-gateway --region {cmd_region} --internet-gateway-id {igw_id} --vpc-id {vpc_id}")
                    typer.echo(f"    aws ec2 delete-internet-gateway --region {cmd_region} --internet-gateway-id {igw_id}")
                    typer.echo(f"    aws ec2 delete-route-table --region {cmd_region} --route-table-id {route_table_id}")
                    typer.echo(f"    aws ec2 delete-security-group --region {cmd_region} --group-id {sg_id}")
                    typer.echo(f"    aws ec2 delete-subnet --region {cmd_region} --subnet-id {subnet_id}")
                    typer.echo(f"    aws ec2 delete-vpc --region {cmd_region} --vpc-id {vpc_id}")
                    failures.append("networking")

            if failures:
                print(
                    f"\n[yellow]Cluster '{name}' termination is incomplete ({len(failures)} failure(s)).[/yellow]"
                )
                print("[yellow]Keeping local kubeadm files/metadata so you can retry termination.[/yellow]")
                raise typer.Exit(1)

            cluster_folder = kubeadm_cluster_folder(name)
            if cluster_folder.exists():
                shutil.rmtree(cluster_folder)
                typer.echo(f"  Removed local kubeadm files: {cluster_folder}")

            _delete_cluster_metadata(name)
            print(f"\n[green]Cluster '{name}' terminated.[/green]")
            return

        from ...cloud.azure.api import (
            list_azure_vms,
            delete_azure_vm,
            delete_azure_nsg,
            delete_azure_public_ip,
            delete_azure_vnet,
        )

        # Find cluster VMs by naming convention
        vms = list_azure_vms(subscription_id=context_id)
        master_prefix = f"{name}-master"
        cluster_vms = [
            vm for vm in vms
            if vm["name"] == master_prefix
            or vm["name"].startswith(f"{master_prefix}-")
            or vm["name"].startswith(f"{name}-node-")
        ]

        if not cluster_vms:
            typer.echo(f"No VMs found for cluster '{name}'.", err=True)
            raise typer.Exit(1)

        rg = cluster_vms[0]["resource_group"]
        default_rg_name = f"{name}-rg"
        delete_rg_effective = delete_rg or (rg == default_rg_name)

        # Show what will be deleted
        print(f"\n[bold]Resources to delete for cluster '{name}':[/bold]")
        for vm in cluster_vms:
            typer.echo(f"  VM:   {vm['name']} (+ attached disks, NIC, IP)")
        typer.echo(f"  LB:   {name}-lb (if exists)")
        typer.echo(f"  LB IP:{name}-lb-ip (if exists)")
        typer.echo(f"  NSG:  {name}-nsg")
        typer.echo(f"  VNet: {name}-vnet")
        if delete_rg_effective:
            typer.echo(f"  RG:   {rg}")
            if not delete_rg and rg == default_rg_name:
                typer.echo("  Note: auto-deleting default cluster resource group.")

        if not force:
            if not Confirm.ask(f"\nDelete all resources for cluster '{name}'?", default=False):
                raise typer.Abort()
            _require_cluster_name_confirmation()

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

        # Step 5: Delete resource group when explicitly requested or when it's the default cluster RG.
        if delete_rg_effective:
            print("[bold]Deleting resource group...[/bold]")
            try:
                from ...cloud.azure.api import _get_resource_client
                resource_client = _get_resource_client(context_id)
                poller = resource_client.resource_groups.begin_delete(rg)
                wait_with_spinner(
                    lambda: poller.result(),
                    f"Deleting resource group {rg}",
                )
                print(f"  [green]Deleted: {rg}[/green]")
            except Exception as e:
                print(f"  [red]Failed to delete resource group {rg}: {e}[/red]")
        else:
            # Best-effort orphan sweep in shared RGs: remove any leftover cluster public IPs.
            print("[bold]Final orphan cleanup (shared resource group)...[/bold]")
            try:
                from ...cloud.azure.api import _get_network_client, delete_azure_public_ip

                network_client = _get_network_client(context_id)
                for pip in network_client.public_ip_addresses.list(rg):
                    pip_name = str(getattr(pip, "name", "") or "")
                    if not pip_name.startswith(f"{name}-"):
                        continue
                    try:
                        delete_azure_public_ip(rg, pip_name, subscription_id=context_id)
                        print(f"  [green]Deleted orphan public IP: {pip_name}[/green]")
                    except Exception:
                        print(f"  [dim]Could not delete orphan public IP: {pip_name}[/dim]")
            except Exception:
                print("  [dim]Could not run orphan public IP cleanup.[/dim]")

        # Remove kubeconfig if it exists
        cluster_folder = kubeadm_cluster_folder(name)
        if cluster_folder.exists():
            shutil.rmtree(cluster_folder)
            typer.echo(f"  Removed local kubeadm files: {cluster_folder}")

        # Remove cluster metadata if it exists
        _delete_cluster_metadata(name)

        print(f"\n[green]Cluster '{name}' terminated.[/green]")
