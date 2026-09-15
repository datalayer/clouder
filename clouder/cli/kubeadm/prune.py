"""Clouder CLI - kubeadm prune command."""

import typer
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from rich import print
from rich.prompt import Confirm

from ...util.utils import kubeadm_kubeconfig_path

from ._helpers import (
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
    _load_cluster_metadata,
    _resolve_cluster_vms,
)


def _build_k8s_api(cluster_name: str) -> k8s_client.CoreV1Api | None:
    """Build Kubernetes CoreV1 API client from cluster kubeconfig if available."""
    kubeconfig_path = kubeadm_kubeconfig_path(cluster_name)
    if not kubeconfig_path.exists():
        return None
    try:
        k8s_config.load_kube_config(config_file=str(kubeconfig_path))
        return k8s_client.CoreV1Api()
    except Exception:
        return None


def _ready_condition(node: k8s_client.V1Node) -> tuple[str, str, str]:
    """Return (status, reason, message) for the Node Ready condition."""
    for condition in (getattr(getattr(node, "status", None), "conditions", None) or []):
        if condition.type == "Ready":
            return (
                str(condition.status or "Unknown"),
                str(condition.reason or ""),
                str(condition.message or ""),
            )
    return ("Unknown", "", "Ready condition missing")


def register(kubeadm_app: typer.Typer):
    """Register the prune command on the given Typer app."""

    @kubeadm_app.command("prune")
    def kubeadm_prune(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    ):
        """Identify unhealthy worker VMs and Kubernetes nodes, then force-delete them."""
        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        if cloud not in {"azure", "aws"}:
            typer.echo("Kubeadm prune is currently only supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        metadata = _load_cluster_metadata(name) or {}
        resource_group = master.get("resource_group", "")

        prefix = f"{name}-node-"

        core_v1 = _build_k8s_api(name)
        unhealthy_k8s_nodes: list[dict[str, str]] = []
        if core_v1 is None:
            print(
                "[yellow]Kubeconfig not available; skipping Kubernetes node health detection. "
                "Run 'clouder kubeadm get-config <cluster>' to enable it.[/yellow]"
            )
        else:
            try:
                for node in core_v1.list_node().items:
                    node_name = str(getattr(getattr(node, "metadata", None), "name", "") or "")
                    if not node_name.startswith(prefix):
                        continue
                    status, reason, message = _ready_condition(node)
                    if status != "True":
                        unhealthy_k8s_nodes.append(
                            {
                                "name": node_name,
                                "status": status,
                                "reason": reason,
                                "message": message,
                            }
                        )
            except Exception as exc:
                print(f"[yellow]Failed to inspect Kubernetes nodes: {type(exc).__name__}: {exc}[/yellow]")

        unhealthy_vms: list[dict[str, str]] = []
        if cloud == "azure":
            from ...cloud.azure.api import list_azure_vms

            try:
                vms = list_azure_vms(resource_group=resource_group, subscription_id=context_id)
                for vm in vms:
                    vm_name = str(vm.get("name") or "")
                    if not vm_name.startswith(prefix):
                        continue
                    provisioning_state = str(vm.get("provisioning_state") or "").strip()
                    if provisioning_state.lower() != "succeeded":
                        unhealthy_vms.append(
                            {
                                "name": vm_name,
                                "provisioning_state": provisioning_state or "unknown",
                            }
                        )
            except Exception as exc:
                print(f"[yellow]Failed to inspect Azure VMs: {type(exc).__name__}: {exc}[/yellow]")
        else:
            from ...cloud.aws.api import list_aws_vms

            try:
                vms = list_aws_vms(region=metadata.get("region"))
                for vm in vms:
                    vm_name = str(vm.get("name") or "")
                    if not vm_name.startswith(prefix):
                        continue
                    state = str(vm.get("state") or "").strip().lower()
                    if state != "running":
                        unhealthy_vms.append(
                            {
                                "name": vm_name,
                                "state": state or "unknown",
                                "instance_id": str(vm.get("id") or ""),
                            }
                        )
            except Exception as exc:
                print(f"[yellow]Failed to inspect AWS VMs: {type(exc).__name__}: {exc}[/yellow]")

        if not unhealthy_k8s_nodes and not unhealthy_vms:
            print(f"[green]No unhealthy worker Kubernetes nodes or VMs found for cluster '{name}'.[/green]")
            raise typer.Exit(0)

        print(f"\n[bold]Unhealthy resources for cluster '{name}':[/bold]")

        if unhealthy_k8s_nodes:
            print("  [bold]Kubernetes nodes (NotReady):[/bold]")
            for node in sorted(unhealthy_k8s_nodes, key=lambda n: n["name"]):
                reason = node["reason"] or "unknown"
                message = (node["message"] or "").strip().replace("\n", " ")
                if len(message) > 180:
                    message = message[:177] + "..."
                if message:
                    print(
                        f"    - {node['name']} (status={node['status']}, reason={reason}) "
                        f"{message}"
                    )
                else:
                    print(f"    - {node['name']} (status={node['status']}, reason={reason})")
        else:
            print("  [dim]Kubernetes nodes (NotReady): none[/dim]")

        if unhealthy_vms:
            if cloud == "azure":
                print("  [bold]Azure VMs (provisioning state != Succeeded):[/bold]")
                for vm in sorted(unhealthy_vms, key=lambda v: v["name"]):
                    print(f"    - {vm['name']} (provisioning_state={vm['provisioning_state']})")
            else:
                print("  [bold]AWS EC2 worker VMs (state != running):[/bold]")
                for vm in sorted(unhealthy_vms, key=lambda v: v["name"]):
                    print(f"    - {vm['name']} (instance_id={vm['instance_id']}, state={vm['state']})")
        else:
            provider_label = "Azure VMs" if cloud == "azure" else "AWS VMs"
            print(f"  [dim]{provider_label} (unhealthy): none[/dim]")

        if not force:
            if not Confirm.ask("\nForce delete the unhealthy resources listed above?", default=False):
                raise typer.Abort()

        if unhealthy_k8s_nodes and core_v1 is not None:
            print("\n[bold]Deleting unhealthy Kubernetes nodes...[/bold]")
            for node in sorted(unhealthy_k8s_nodes, key=lambda n: n["name"]):
                node_name = node["name"]
                try:
                    core_v1.delete_node(
                        node_name,
                        body=k8s_client.V1DeleteOptions(grace_period_seconds=0),
                    )
                    print(f"  [green]Deleted k8s node: {node_name}[/green]")
                except ApiException as exc:
                    if exc.status == 404:
                        print(f"  [dim]K8s node already absent: {node_name}[/dim]")
                    else:
                        print(
                            f"  [yellow]Failed to delete k8s node {node_name}: "
                            f"{type(exc).__name__}: {exc}[/yellow]"
                        )
                except Exception as exc:
                    print(
                        f"  [yellow]Failed to delete k8s node {node_name}: "
                        f"{type(exc).__name__}: {exc}[/yellow]"
                    )

        if unhealthy_vms:
            if cloud == "azure":
                from ...cloud.azure.api import delete_azure_vm

                print("\n[bold]Deleting unhealthy Azure VMs...[/bold]")
                for vm in sorted(unhealthy_vms, key=lambda v: v["name"]):
                    vm_name = vm["name"]
                    try:
                        delete_azure_vm(
                            resource_group=resource_group,
                            vm_name=vm_name,
                            subscription_id=context_id,
                        )
                        print(f"  [green]Delete requested for VM: {vm_name}[/green]")
                    except Exception as exc:
                        print(
                            f"  [yellow]Failed to delete VM {vm_name}: "
                            f"{type(exc).__name__}: {exc}[/yellow]"
                        )
            else:
                from ...cloud.aws.api import terminate_aws_vm

                print("\n[bold]Terminating unhealthy AWS EC2 VMs...[/bold]")
                for vm in sorted(unhealthy_vms, key=lambda v: v["name"]):
                    instance_id = vm.get("instance_id", "")
                    try:
                        if instance_id:
                            terminate_aws_vm(instance_id, region=metadata.get("region"))
                            print(f"  [green]Terminate requested: {vm['name']} ({instance_id})[/green]")
                    except Exception as exc:
                        print(
                            f"  [yellow]Failed to terminate VM {vm['name']}: "
                            f"{type(exc).__name__}: {exc}[/yellow]"
                        )

        print(f"\n[green]Prune complete for cluster '{name}'.[/green]")
