"""Clouder CLI - kubeadm repair command."""

from __future__ import annotations

import json
import shlex
import time

import typer
from rich import print
from rich.panel import Panel

from ...util.utils import SSH_FOLDER
from ._helpers import (
    DEFAULT_NODE_LABELS,
    resolve_kubeadm_cloud_context,
    _SCRIPT_PREREQS,
    _SCRIPT_UPGRADE_KUBELET,
    _SCRIPT_WORKER_FEATURE_GATE,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _ssh_cmd_stream,
    resolve_kubeadm_cluster_name,
)

def _resolve_node_labels(raw_labels: list[str] | None) -> list[str]:
    if not raw_labels:
        return list(DEFAULT_NODE_LABELS)

    labels: list[str] = []
    for value in raw_labels:
        for part in str(value).split(","):
            candidate = part.strip()
            if not candidate:
                continue
            if "=" not in candidate:
                typer.echo(f"Invalid --node-label '{candidate}'. Expected key=value.", err=True)
                raise typer.Exit(1)
            labels.append(candidate)
    return labels or list(DEFAULT_NODE_LABELS)


def _list_kubernetes_node_names(master_ip: str, ssh_user: str, key_path: str) -> list[str]:
    result = _ssh_cmd(
        master_ip,
        ssh_user,
        key_path,
        "kubectl get nodes -o json",
        check=False,
    )
    if result.returncode != 0:
        typer.echo("Failed to list kubectl nodes from master.", err=True)
        if result.stderr:
            typer.echo(result.stderr.strip(), err=True)
        raise typer.Exit(1)

    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        typer.echo("Could not parse kubectl node output as JSON.", err=True)
        raise typer.Exit(1)

    names: list[str] = []
    for item in payload.get("items", []):
        name = str(item.get("metadata", {}).get("name", "") or "").strip()
        if name:
            names.append(name)
    return names


def _wait_for_node_ready(
    master_ip: str,
    ssh_user: str,
    key_path: str,
    node_name: str,
    timeout_seconds: int = 300,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            (
                f"kubectl get node {shlex.quote(node_name)} "
                "-o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' "
                "2>/dev/null || true"
            ),
            check=False,
        ).stdout.strip()
        if status == "True":
            return True
        time.sleep(5)
    return False


def _apply_node_labels(
    master_ip: str,
    ssh_user: str,
    key_path: str,
    node_name: str,
    labels: list[str],
) -> None:
    for label in labels:
        _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            f"kubectl label node {shlex.quote(node_name)} {shlex.quote(label)} --overwrite",
            check=False,
        )


def _get_join_command(master_ip: str, ssh_user: str, key_path: str) -> str:
    result = _ssh_cmd(
        master_ip,
        ssh_user,
        key_path,
        "sudo kubeadm token create --print-join-command",
        check=False,
    )
    if result.returncode != 0:
        typer.echo("Failed to get kubeadm join command from master.", err=True)
        if result.stderr:
            typer.echo(result.stderr.strip(), err=True)
        raise typer.Exit(1)

    join_command = str(result.stdout or "").strip()
    if not join_command.startswith("kubeadm join "):
        typer.echo("Master did not return a valid kubeadm join command.", err=True)
        raise typer.Exit(1)
    return join_command


def _reconcile_worker(
    worker: dict[str, str],
    master_ip: str,
    ssh_user: str,
    key_path: str,
    node_labels: list[str],
) -> None:
    worker_name = str(worker.get("name") or "")
    worker_ip = str(worker.get("ip") or "")
    if not worker_name or not worker_ip:
        typer.echo("Worker metadata is incomplete (missing name/ip).", err=True)
        raise typer.Exit(1)

    print(f"\n[bold]Reconciling worker[/bold] [cyan]{worker_name}[/cyan] ({worker_ip})")

    rc = _ssh_cmd_stream(worker_ip, ssh_user, key_path, _SCRIPT_UPGRADE_KUBELET)
    if rc != 0:
        typer.echo(f"kubelet upgrade failed on {worker_name}", err=True)
        raise typer.Exit(1)

    rc = _ssh_cmd_stream(worker_ip, ssh_user, key_path, _SCRIPT_PREREQS)
    if rc != 0:
        typer.echo(f"Prerequisites setup failed on {worker_name}", err=True)
        raise typer.Exit(1)

    reset_cmd = (
        "sudo kubeadm reset -f --cri-socket unix:///var/run/containerd/containerd.sock 2>/dev/null || true; "
        "sudo rm -rf /etc/cni/net.d; "
        "sudo systemctl restart containerd; "
        "for i in $(seq 1 30); do "
        "  if [ -S /var/run/containerd/containerd.sock ] && sudo ctr --connect-timeout 2s version >/dev/null 2>&1; then break; fi; "
        "  sleep 1; "
        "done"
    )
    _ssh_cmd_stream(worker_ip, ssh_user, key_path, reset_cmd)

    join_command = _get_join_command(master_ip, ssh_user, key_path)
    rc = _ssh_cmd_stream(worker_ip, ssh_user, key_path, f"sudo {join_command}")
    if rc != 0:
        typer.echo(f"kubeadm join failed on {worker_name}", err=True)
        raise typer.Exit(1)

    rc = _ssh_cmd_stream(worker_ip, ssh_user, key_path, _SCRIPT_WORKER_FEATURE_GATE)
    if rc != 0:
        print(f"[yellow]Feature gate setup failed on {worker_name} (non-fatal).[/yellow]")

    if not _wait_for_node_ready(master_ip, ssh_user, key_path, worker_name):
        typer.echo(f"{worker_name} did not become Ready in time.", err=True)
        raise typer.Exit(1)

    _apply_node_labels(master_ip, ssh_user, key_path, worker_name, node_labels)
    print(f"[green]{worker_name} is Ready and labels were applied.[/green]")


def register(kubeadm_app: typer.Typer):
    """Register the repair command on the given Typer app."""

    @kubeadm_app.command("repair")
    def kubeadm_repair(
        name: str | None = typer.Argument(
            None,
            help="Cluster name. If omitted, uses default kubeadm cluster.",
        ),
        cloud: str | None = typer.Option(
            None,
            "--cloud",
            help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud.",
        ),
        user: str | None = typer.Option(
            None,
            "--admin-user",
            "-u",
            help="SSH username on the VMs (default: metadata value or azureuser on Azure, ubuntu on AWS).",
        ),
        key: str | None = typer.Option(
            None,
            "--key",
            "-i",
            help="SSH key name (from ~/.ssh/).",
        ),
        node_labels: list[str] | None = typer.Option(
            None,
            "--node-label",
            help=(
                "Node label key=value to apply when reconciled workers become Ready. "
                "Repeatable or comma-separated. Defaults to runtime labels."
            ),
        ),
    ):
        """Repair kubeadm workers missing from Kubernetes node registration.

        The command:
        1) lists cluster VMs,
        2) lists Kubernetes nodes via kubectl on master,
        3) reconciles worker VMs present in cloud but absent from Kubernetes nodes.
        """
        name = resolve_kubeadm_cluster_name(name)
        node_labels_resolved = _resolve_node_labels(node_labels)
        cloud, _ = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        cluster = _resolve_cluster_vms(name, cloud=cloud)
        metadata = _load_cluster_metadata(name) or {}

        key_path = str(SSH_FOLDER / key) if key else _resolve_ssh_key_for_cluster(name)
        ssh_user = user or metadata.get("admin_username") or ("azureuser" if cloud == "azure" else "ubuntu")

        master = cluster["master"]
        workers = cluster["workers"]

        vm_lines = [f"  master:  {master['name']} ({master['ip']})"]
        for worker in workers:
            vm_lines.append(f"  worker:  {worker['name']} ({worker['ip']})")

        k8s_nodes = _list_kubernetes_node_names(master["ip"], ssh_user, key_path)
        k8s_node_set = set(k8s_nodes)
        node_lines = [f"  {node_name}" for node_name in k8s_nodes] if k8s_nodes else ["  (none)"]

        missing_workers = [worker for worker in workers if worker["name"] not in k8s_node_set]
        missing_master = master["name"] not in k8s_node_set

        summary_lines = [
            f"[bold]Cluster:[/bold] {name}",
            f"[bold]Cloud:[/bold]   {cloud}",
            "",
            "[bold]VM inventory[/bold]",
            *vm_lines,
            "",
            "[bold]kubectl nodes[/bold]",
            *node_lines,
            "",
            f"[bold]Workers to reconcile:[/bold] {len(missing_workers)}",
        ]
        if missing_master:
            summary_lines.append("[yellow]Control-plane VM is missing from kubectl nodes.[/yellow]")

        print(Panel("\n".join(summary_lines), title="Kubeadm Repair"))

        if missing_master:
            print(
                "[yellow]Control-plane is not registered. This command only repairs workers. "
                "Run full control-plane setup manually if required.[/yellow]"
            )

        if not missing_workers:
            print("[green]No worker reconciliation needed.[/green]")
            return

        for worker in missing_workers:
            _reconcile_worker(
                worker=worker,
                master_ip=master["ip"],
                ssh_user=ssh_user,
                key_path=key_path,
                node_labels=node_labels_resolved,
            )

        refreshed_nodes = _list_kubernetes_node_names(master["ip"], ssh_user, key_path)
        refreshed_set = set(refreshed_nodes)
        still_missing = [worker["name"] for worker in missing_workers if worker["name"] not in refreshed_set]

        if still_missing:
            typer.echo(
                f"Workers still missing from kubectl nodes after repair: {', '.join(still_missing)}",
                err=True,
            )
            raise typer.Exit(1)

        print(
            Panel(
                "\n".join(
                    [
                        f"[green]Repair complete for cluster '{name}'.[/green]",
                        f"Reconciled workers: {', '.join(worker['name'] for worker in missing_workers)}",
                        f"Check: clouder kubectl {name} get nodes",
                    ]
                ),
                title="Repair Complete",
            )
        )
