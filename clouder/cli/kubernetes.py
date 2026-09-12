"""Clouder CLI - Kubernetes management commands."""

import yaml

import typer
from rich import print
from rich.table import Table
from rich.markdown import Markdown

from .ctx import get_current_context, set_default_kubeconfig_path
from ..util.utils import OVH_K8S_FOLDER

kubernetes_app = typer.Typer(no_args_is_help=True)


def _require_ovh(cloud: str):
    """Ensure OVH context is active for k8s commands."""
    if cloud != "ovh":
        typer.echo(f"Kubernetes management is currently only supported for OVH. Current context: {cloud}.")
        raise typer.Exit(1)


@kubernetes_app.callback(invoke_without_command=True)
def k8s_default(ctx: typer.Context):
    """List clusters if no subcommand given."""
    if ctx.invoked_subcommand is None:
        k8s_list()


@kubernetes_app.command("create")
def k8s_create(
    name: str = typer.Argument(..., help="Name for the Kubernetes cluster."),
):
    """Create a Kubernetes cluster."""
    (cloud, context_id) = get_current_context()
    _require_ovh(cloud)
    from ..cloud.ovh.api import create_ovh_kubernetes
    res = create_ovh_kubernetes(context_id, name)
    print(res)


@kubernetes_app.command("ls")
def k8s_list():
    """List Kubernetes clusters."""
    (cloud, context_id) = get_current_context()
    _require_ovh(cloud)
    from ..cloud.ovh.api import (
        get_ovh_project,
        get_ovh_kubernetess,
        get_ovh_kubernetes,
        get_ovh_kubernetes_nodepools,
        get_ovh_kubernetes_nodepool_nodes,
    )
    project = get_ovh_project(context_id)
    project_id = project["project_id"]
    project_name = project["description"]
    print(Markdown(f"# Kubernetes {cloud}:{project_name} ({project_id})"))
    kubernetess = get_ovh_kubernetess(project_id)
    for kubernetes_id in kubernetess:
        kubernetes = get_ovh_kubernetes(project_id, kubernetes_id)
        table = Table(title=f"Kubernetes {cloud}:{project_name}")
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Version", justify="left", style="green")
        table.add_column("Status", justify="left", style="green")
        table.add_row(
            kubernetes["id"],
            kubernetes["name"],
            kubernetes["region"],
            kubernetes["version"],
            kubernetes["status"],
        )
        print(table)
        nodepools = get_ovh_kubernetes_nodepools(project_id, kubernetes_id)
        for nodepool in nodepools:
            title = f"Nodepool {cloud}:{project_name}:{nodepool['name']}"
            print(Markdown("## " + title))
            table = Table(title=title)
            table.add_column("ID", justify="left", style="cyan", no_wrap=True)
            table.add_column("Name", justify="left", style="cyan")
            table.add_column("Flavor", justify="left", style="green")
            table.add_column("Current", justify="left", style="green")
            table.add_column("Available", justify="left", style="green")
            table.add_column("Min", justify="left", style="green")
            table.add_column("Desired", justify="left", style="green")
            table.add_column("Max", justify="left", style="green")
            table.add_column("Status", justify="left", style="green")
            table.add_row(
                nodepool["id"],
                nodepool["name"],
                nodepool["flavor"],
                str(nodepool["currentNodes"]),
                str(nodepool["availableNodes"]),
                str(nodepool["minNodes"]),
                str(nodepool["desiredNodes"]),
                str(nodepool["maxNodes"]),
                nodepool["status"],
            )
            print(table)
            nodes = get_ovh_kubernetes_nodepool_nodes(project_id, kubernetes_id, nodepool["id"])
            table = Table(title=f"Nodes {cloud}:{project_name}:{nodepool['name']}")
            table.add_column("ID", justify="left", style="cyan", no_wrap=True)
            table.add_column("Instance ID", justify="left", style="cyan")
            table.add_column("Name", justify="left", style="green")
            table.add_column("Status", justify="left", style="green")
            for node in nodes:
                table.add_row(
                    node["id"],
                    node["instanceId"],
                    node["name"],
                    node["status"],
                )
            print(table)
            print()


@kubernetes_app.command("kubeconfig")
def k8s_kubeconfig(
    name: str = typer.Argument(..., help="Name of the Kubernetes cluster."),
):
    """Download kubeconfig for a Kubernetes cluster."""
    (cloud, context_id) = get_current_context()
    _require_ovh(cloud)
    from ..cloud.ovh.api import get_ovh_kubernetess, get_ovh_kubernetes, get_ovh_kubernetes_kubeconfig
    kubernetess = get_ovh_kubernetess(context_id)
    for kubernetes_id in kubernetess:
        kubernetes = get_ovh_kubernetes(context_id, kubernetes_id)
        if kubernetes["name"] == name:
            config = get_ovh_kubernetes_kubeconfig(context_id, kubernetes_id)
            kubeconfig = config["content"]
            OVH_K8S_FOLDER.mkdir(parents=True, exist_ok=True)
            kubeconfig_file = OVH_K8S_FOLDER / f"{name}.yaml"
            with open(kubeconfig_file, "w") as out:
                k = yaml.safe_load(kubeconfig)
                yaml.dump(k, out, default_flow_style=False, sort_keys=False)
                kubeconfig_file.chmod(0o600)
                typer.echo(f"export KUBECONFIG={str(kubeconfig_file.absolute())}")
            return
    typer.echo(f"Cluster '{name}' not found.", err=True)
    raise typer.Exit(1)


@kubernetes_app.command("use")
def k8s_use(
    name: str = typer.Argument(..., help="Name of the Kubernetes cluster."),
):
    """Set the kubeconfig for a Kubernetes cluster as the default."""
    (cloud, context_id) = get_current_context()
    _require_ovh(cloud)
    from ..cloud.ovh.api import get_ovh_kubernetess, get_ovh_kubernetes
    kubernetess = get_ovh_kubernetess(context_id)
    for kubernetes_id in kubernetess:
        kubernetes = get_ovh_kubernetes(context_id, kubernetes_id)
        if kubernetes["name"] == name:
            kubeconfig_path = str((OVH_K8S_FOLDER / f"{name}.yaml").absolute())
            set_default_kubeconfig_path(kubeconfig_path)
            typer.echo(f"export KUBECONFIG={kubeconfig_path}")
            return
    typer.echo(f"Cluster '{name}' not found.", err=True)
    raise typer.Exit(1)


@kubernetes_app.command("create-nodepool")
def k8s_create_nodepool(
    cluster_name: str = typer.Argument(..., help="Name of the Kubernetes cluster."),
    nodepool_name: str = typer.Argument(..., help="Name for the node pool."),
    flavor: str = typer.Option("b2-15", "--flavor", "-f", help="Node flavor."),
    min_nodes: int = typer.Option(3, "--min", help="Minimum number of nodes."),
    desired: int = typer.Option(3, "--desired", help="Desired number of nodes."),
    max_nodes: int = typer.Option(10, "--max", help="Maximum number of nodes."),
    roles: str = typer.Option("", "--roles", help="Comma-separated role names."),
    variant: str = typer.Option("default", "--variant", help="Variant for the pool."),
    xpu: str = typer.Option("cpu", "--xpu", help="Compute type: cpu, gpu-cuda, qpu."),
):
    """Create a node pool in a Kubernetes cluster."""
    (cloud, context_id) = get_current_context()
    _require_ovh(cloud)
    from ..cloud.ovh.api import (
        get_ovh_kubernetess,
        get_ovh_kubernetes,
        create_ovh_kubernetes_nodepool,
    )
    if roles:
        labels = {f"role.datalayer.io/{role}": "true" for role in roles.split(",")}
    else:
        labels = {}
    labels["node.datalayer.io/variant"] = variant
    labels["node.datalayer.io/xpu"] = xpu
    kubernetess = get_ovh_kubernetess(context_id)
    for k in kubernetess:
        kubernetes = get_ovh_kubernetes(context_id, k)
        if kubernetes["name"] == cluster_name:
            kubernetes_id = kubernetes["id"]
            template = {
                "metadata": {
                    "annotations": {},
                    "finalizers": [],
                    "labels": labels,
                },
                "spec": {
                    "taints": [],
                    "unschedulable": False,
                },
            }
            if "gpu" in xpu:
                template["metadata"]["labels"]["nvidia.com/device-plugin.config"] = "gpu-nvidia-20"
            res = create_ovh_kubernetes_nodepool(
                context_id, kubernetes_id, nodepool_name,
                flavor, desired, min_nodes, max_nodes, template,
            )
            print(res)
            return
    typer.echo(f"Cluster '{cluster_name}' not found.", err=True)
    raise typer.Exit(1)


@kubernetes_app.command("update-nodepool")
def k8s_update_nodepool(
    cluster_name: str = typer.Argument(..., help="Name of the Kubernetes cluster."),
    nodepool_name: str = typer.Argument(..., help="Name of the node pool."),
    min_nodes: int = typer.Option(3, "--min", help="Minimum number of nodes."),
    desired: int = typer.Option(3, "--desired", help="Desired number of nodes."),
    max_nodes: int = typer.Option(10, "--max", help="Maximum number of nodes."),
):
    """Update a node pool in a Kubernetes cluster."""
    (cloud, context_id) = get_current_context()
    _require_ovh(cloud)
    from ..cloud.ovh.api import (
        get_ovh_kubernetess,
        get_ovh_kubernetes,
        get_ovh_kubernetes_nodepools,
        update_ovh_kubernetes_nodepool,
    )
    kubernetess = get_ovh_kubernetess(context_id)
    for k in kubernetess:
        kubernetes = get_ovh_kubernetes(context_id, k)
        if kubernetes["name"] == cluster_name:
            kubernetes_id = kubernetes["id"]
            nodepools = get_ovh_kubernetes_nodepools(context_id, kubernetes_id)
            for nodepool in nodepools:
                if nodepool["name"] == nodepool_name:
                    update_ovh_kubernetes_nodepool(
                        context_id, kubernetes_id, nodepool["id"],
                        desired, min_nodes, max_nodes,
                    )
                    typer.echo(f"Nodepool '{nodepool_name}' updated.")
                    return
    typer.echo(f"Cluster or nodepool not found.", err=True)
    raise typer.Exit(1)
