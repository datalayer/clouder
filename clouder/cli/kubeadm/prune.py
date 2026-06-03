"""Clouder CLI - kubeadm prune command."""

import typer
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from rich import print
from rich.prompt import Confirm

from ..ctx import get_current_context
from ...util.utils import kubeadm_kubeconfig_path

from ._helpers import resolve_kubeadm_cluster_name, _resolve_cluster_vms


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
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    ):
        """Identify unhealthy worker VMs and Kubernetes nodes, then force-delete them."""
        name = resolve_kubeadm_cluster_name(name)
        (cloud, context_id) = get_current_context()
        if cloud != "azure":
            typer.echo("Kubeadm prune is currently only supported for Azure.", err=True)
            raise typer.Exit(1)

        from ...cloud.azure.api import list_azure_vms, delete_azure_vm

        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        resource_group = master["resource_group"]

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
            print("  [bold]Azure VMs (provisioning state != Succeeded):[/bold]")
            for vm in sorted(unhealthy_vms, key=lambda v: v["name"]):
                print(f"    - {vm['name']} (provisioning_state={vm['provisioning_state']})")
        else:
            print("  [dim]Azure VMs (unhealthy): none[/dim]")

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

        print(f"\n[green]Prune complete for cluster '{name}'.[/green]")
