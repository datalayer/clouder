"""Clouder CLI - kubeadm remove-node command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ...util.utils import kubeadm_kubeconfig_path
from ._helpers import (
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _update_cluster_metadata,
)


def _run_kubectl(kubeconfig_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run kubectl with an explicit kubeconfig and capture output."""
    cmd = ["kubectl", "--kubeconfig", str(kubeconfig_path), *args]
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _remove_k8s_node(kubeconfig_path: Path, node_name: str) -> None:
    """Cordon, drain, and delete a Kubernetes node."""
    cordon = _run_kubectl(kubeconfig_path, ["cordon", node_name])
    if cordon.returncode == 0:
        print(f"  [green]Cordoned Kubernetes node: {node_name}[/green]")
    else:
        msg = (cordon.stderr or cordon.stdout or "").strip()
        print(f"  [yellow]Could not cordon node {node_name}: {msg or 'unknown error'}[/yellow]")

    drain = _run_kubectl(
        kubeconfig_path,
        [
            "drain",
            node_name,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--force",
            "--grace-period=30",
            "--timeout=180s",
        ],
    )
    if drain.returncode == 0:
        print(f"  [green]Drained Kubernetes node: {node_name}[/green]")
    else:
        msg = (drain.stderr or drain.stdout or "").strip()
        print(f"  [yellow]Could not fully drain node {node_name}: {msg or 'unknown error'}[/yellow]")

    delete = _run_kubectl(kubeconfig_path, ["delete", "node", node_name, "--ignore-not-found=true"])
    if delete.returncode == 0:
        print(f"  [green]Deleted Kubernetes node object: {node_name}[/green]")
    else:
        msg = (delete.stderr or delete.stdout or "").strip()
        print(f"  [yellow]Could not delete node object {node_name}: {msg or 'unknown error'}[/yellow]")


def register(kubeadm_app: typer.Typer):
    """Register the remove-node command on the given Typer app."""

    @kubeadm_app.command("remove-node")
    def kubeadm_remove_node(
        name: str | None = typer.Argument(
            None,
            help="Cluster name. If omitted, uses default kubeadm cluster.",
        ),
        node_name: str = typer.Argument(
            ...,
            help="Worker node name to remove (Kubernetes node and cloud VM).",
        ),
        cloud: str | None = typer.Option(
            None,
            "--cloud",
            help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            "-f",
            help="Skip confirmation prompt.",
        ),
    ):
        """Remove a single worker node from a kubeadm cluster.

        The command removes the Kubernetes node object (cordon/drain/delete)
        and then removes the corresponding cloud VM.
        """
        name = resolve_kubeadm_cluster_name(name)
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        if cloud not in {"azure", "aws"}:
            typer.echo("Kubeadm commands are currently only supported for Azure and AWS.", err=True)
            raise typer.Exit(1)

        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        workers = cluster["workers"]
        metadata = _load_cluster_metadata(name) or {}

        if node_name == master.get("name"):
            typer.echo("Refusing to remove the control-plane node with remove-node.", err=True)
            raise typer.Exit(1)

        worker_by_name = {
            str(worker.get("name") or ""): worker
            for worker in workers
            if str(worker.get("name") or "")
        }
        worker = worker_by_name.get(node_name)

        plan_lines = [
            f"[bold]Cluster:[/bold] {name}",
            f"[bold]Cloud:[/bold]   {cloud}",
            f"[bold]Node:[/bold]    {node_name}",
            f"[bold]K8s kubeconfig:[/bold] {kubeadm_kubeconfig_path(name)}",
            f"[bold]Cloud VM match:[/bold] {'yes' if worker else 'no'}",
        ]
        if worker:
            vm_ip = str(worker.get("ip") or "")
            vm_private_ip = str(worker.get("private_ip") or "")
            vm_instance_id = str(worker.get("instance_id") or "")
            if vm_ip:
                plan_lines.append(f"[bold]VM public IP:[/bold] {vm_ip}")
            if vm_private_ip:
                plan_lines.append(f"[bold]VM private IP:[/bold] {vm_private_ip}")
            if vm_instance_id:
                plan_lines.append(f"[bold]VM instance id:[/bold] {vm_instance_id}")

        print(Panel("\n".join(plan_lines), title="Remove Node Plan"))

        if not force:
            if not Confirm.ask(f"\nProceed to remove node '{node_name}' from cluster '{name}'?", default=False):
                raise typer.Abort()

        kubeconfig_path = kubeadm_kubeconfig_path(name)
        if kubeconfig_path.exists():
            _remove_k8s_node(kubeconfig_path, node_name)
        else:
            print(
                "[yellow]Kubeconfig not found. Skipping Kubernetes drain/delete step. "
                "Run 'clouder kubeadm get-config <cluster>' if you need to remove the K8s node object.[/yellow]"
            )

        if cloud == "azure":
            from ...cloud.azure.api import delete_azure_vm

            if worker is None:
                print(
                    f"[yellow]No Azure VM matched worker name '{node_name}'. "
                    "Kubernetes node object step completed (if kubeconfig was available).[/yellow]"
                )
            else:
                resource_group = str(worker.get("resource_group") or "")
                if not resource_group:
                    resource_group = str(master.get("resource_group") or "")
                try:
                    delete_azure_vm(
                        resource_group=resource_group,
                        vm_name=node_name,
                        subscription_id=context_id,
                    )
                    print(f"  [green]Delete requested for Azure VM: {node_name}[/green]")
                except Exception as exc:
                    typer.echo(
                        f"Failed to delete Azure VM '{node_name}': {type(exc).__name__}: {exc}",
                        err=True,
                    )
                    raise typer.Exit(1)
        else:
            from ...cloud.aws.api import terminate_aws_vm, wait_aws_instances_terminated

            instance_id = ""
            region = str(metadata.get("region") or "")
            if worker is not None:
                instance_id = str(worker.get("instance_id") or "")
                if not region:
                    region = str(worker.get("region") or "")

            if not instance_id:
                typer.echo(
                    f"Could not find AWS instance ID for worker '{node_name}'.",
                    err=True,
                )
                raise typer.Exit(1)

            try:
                terminate_aws_vm(instance_id=instance_id, region=region or None)
                wait_aws_instances_terminated([instance_id], region=region or None)
                print(f"  [green]AWS instance terminated: {node_name} ({instance_id})[/green]")
            except Exception as exc:
                typer.echo(
                    f"Failed to terminate AWS instance '{instance_id}': {type(exc).__name__}: {exc}",
                    err=True,
                )
                raise typer.Exit(1)

        metadata_workers = metadata.get("workers")
        if isinstance(metadata_workers, list):
            updated_workers = [
                item for item in metadata_workers
                if str((item or {}).get("name") or "") != node_name
            ]
            if len(updated_workers) != len(metadata_workers):
                _update_cluster_metadata(name, {"workers": updated_workers})

        print(
            Panel(
                "\n".join(
                    [
                        f"[green]Node '{node_name}' removed from cluster '{name}'.[/green]",
                        f"Check: clouder kubectl {name} get nodes",
                    ]
                ),
                title="Remove Node Complete",
            )
        )
