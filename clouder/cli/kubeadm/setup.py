"""Clouder CLI - kubeadm setup command."""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ...util.utils import SSH_FOLDER

from ._helpers import (
    K8S_VERSION,
    resolve_kubeadm_cluster_name,
    _SCRIPT_INSTALL_CNI,
    _SCRIPT_KUBEADM_INIT,
    _SCRIPT_PREREQS,
    _SCRIPT_UPGRADE_KUBELET,
    _SCRIPT_WORKER_FEATURE_GATE,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    resolve_kubeadm_cloud_context,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _ssh_cmd_stream,
    _update_cluster_metadata,
)


DEFAULT_NODE_LABELS = [
    "role.datalayer.io/runtime=true",
    "node.datalayer.io/variant=medium",
    "xpu.datalayer.io/cpu=true",
]


def _resolve_node_labels(raw_labels: list[str] | None) -> list[str]:
    """Resolve node labels, supporting repeated flags and comma-separated values."""
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
    if not labels:
        return list(DEFAULT_NODE_LABELS)
    return labels


def _wait_for_node_ready(master_ip: str, ssh_user: str, key_path: str, node_name: str, timeout_seconds: int = 300) -> bool:
    """Wait until the Kubernetes node is registered and Ready."""
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            (
                f"kubectl get node {node_name} "
                "-o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' "
                "2>/dev/null || true"
            ),
            check=False,
        ).stdout.strip()
        if status == "True":
            return True
        time.sleep(5)
    return False


def _detect_kubernetes_node_name(worker_ip: str, ssh_user: str, key_path: str, fallback: str) -> str:
    """Detect the node name kubelet will register with Kubernetes."""
    result = _ssh_cmd(
        worker_ip,
        ssh_user,
        key_path,
        "hostname -s 2>/dev/null || hostname 2>/dev/null || true",
        check=False,
    )
    detected = (result.stdout or "").strip()
    return detected or fallback


def _print_node_ready_timeout_diagnostics(master_ip: str, ssh_user: str, key_path: str, node_name: str) -> None:
    """Print Kubernetes diagnostics when node readiness times out."""
    print("  [yellow]Node readiness timed out. Collecting diagnostics from control-plane...[/yellow]")

    nodes_result = _ssh_cmd(
        master_ip,
        ssh_user,
        key_path,
        "kubectl get nodes -o wide 2>/dev/null || true",
        check=False,
    )
    if nodes_result.stdout.strip():
        typer.echo("\n[kubectl get nodes -o wide]")
        typer.echo(nodes_result.stdout.rstrip())

    describe_result = _ssh_cmd(
        master_ip,
        ssh_user,
        key_path,
        f"kubectl describe node {node_name} 2>/dev/null || true",
        check=False,
    )
    if describe_result.stdout.strip():
        typer.echo(f"\n[kubectl describe node {node_name}]")
        typer.echo(describe_result.stdout.rstrip())


def _apply_node_labels(master_ip: str, ssh_user: str, key_path: str, node_name: str, labels: list[str]) -> None:
    """Apply labels to a Kubernetes node using kubectl on the master."""
    for label in labels:
        _ssh_cmd(
            master_ip,
            ssh_user,
            key_path,
            f"kubectl label node {node_name} {label} --overwrite",
            check=False,
        )


def register(kubeadm_app: typer.Typer):
    """Register the setup command on the given Typer app."""

    @kubeadm_app.command("setup")
    def kubeadm_setup(
        name: str | None = typer.Argument(None, help="Cluster name (must match create name). If omitted, uses default kubeadm cluster."),
        cloud: str | None = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws). Defaults to cluster metadata or current context cloud."),
        user: str | None = typer.Option(None, "--admin-user", "-u", help="SSH username on the VMs (default: metadata value or azureuser on Azure, ubuntu on AWS)."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        k8s_version: str = typer.Option(K8S_VERSION, "--k8s-version", help="Kubernetes version to install."),
        node_labels: list[str] | None = typer.Option(
            None,
            "--node-label",
            help=(
                "Node label key=value to apply once each worker becomes Ready. "
                "Repeatable or comma-separated. Defaults to runtime labels."
            ),
        ),
    ):
        """Set up a kubeadm cluster on previously created VMs.

        Steps: install prerequisites → kubeadm init (master) → install CNI →
        kubeadm join (workers) → enable CRIU feature gates (all nodes).
        """
        name = resolve_kubeadm_cluster_name(name)
        cloud, _ = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        resolved_node_labels = _resolve_node_labels(node_labels)
        cluster = _resolve_cluster_vms(name, cloud=cloud)
        master = cluster["master"]
        workers = cluster["workers"]
        metadata = _load_cluster_metadata(name) or {}

        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)
        resolved_user = user or metadata.get("admin_username") or ("azureuser" if cloud == "azure" else "ubuntu")

        all_nodes = [master] + workers

        print(Panel(
            f"[bold]Cluster:[/bold] {name}\n"
            f"[bold]Masters:[/bold] {master['name']} ({master['ip']})\n"
            f"[bold]Workers:[/bold] {', '.join(w['name'] for w in workers)}\n"
            f"[bold]Key:[/bold]     {key_path}\n"
            f"[bold]User:[/bold]    {resolved_user}\n"
            f"[bold]K8s:[/bold]     v{k8s_version}",
            title="Kubeadm Setup",
        ))

        print(
            "[dim]This setup upgrades kubelet/kubeadm/kubectl, installs node prerequisites, "
            "initializes the control plane, joins workers, then enables runtime features and cloud integrations.[/dim]"
        )

        if not Confirm.ask("\nProceed with cluster setup?", default=True):
            raise typer.Abort()

        # ----- Step 1: Upgrade kubelet on ALL nodes (master first) -----
        print("\n[bold]Step 1/7: Upgrading kubelet/kubeadm/kubectl on all nodes (master first)...[/bold]")
        for node in all_nodes:
            print(f"  [cyan]{node['name']}[/cyan] ({node['ip']})...")
            rc = _ssh_cmd_stream(node["ip"], resolved_user, key_path, _SCRIPT_UPGRADE_KUBELET)
            if rc != 0:
                print(f"  [red]kubelet upgrade failed on {node['name']}[/red]")
                raise typer.Exit(1)
            print(f"  [green]{node['name']} kubelet upgraded.[/green]")

        # ----- Step 2: Install prerequisites on ALL nodes -----
        print("\n[bold]Step 2/7: Installing prerequisites on all nodes...[/bold]")
        for node in all_nodes:
            print(f"  [cyan]{node['name']}[/cyan] ({node['ip']})...")
            rc = _ssh_cmd_stream(node["ip"], resolved_user, key_path, _SCRIPT_PREREQS)
            if rc != 0:
                print(f"  [red]Failed on {node['name']}[/red]")
                raise typer.Exit(1)
            print(f"  [green]{node['name']} done.[/green]")

        # ----- Step 3: kubeadm init on master -----
        print("\n[bold]Step 3/7: Initializing control plane on master...[/bold]")
        init_script = _SCRIPT_KUBEADM_INIT.replace("PUBLIC_IP_PLACEHOLDER", master["ip"])
        result = _ssh_cmd(master["ip"], resolved_user, key_path, init_script, check=False)
        if result.returncode != 0:
            typer.echo(result.stderr)
            print(f"[red]kubeadm init failed on {master['name']}[/red]")
            raise typer.Exit(1)

        # Extract join command from output.
        join_command = ""
        lines = result.stdout.strip().split("\n")
        for line in reversed(lines):
            stripped = line.strip()
            if "kubeadm join" in stripped:
                join_command = stripped.rstrip("\\").strip()
                break

        if not join_command:
            for i, line in enumerate(lines):
                if "kubeadm join" in line:
                    join_command = line.strip().rstrip("\\").strip()
                    while i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line.startswith("--"):
                            join_command += " " + next_line.rstrip("\\").strip()
                            i += 1
                        else:
                            break
                    break

        if not join_command:
            print("[red]Could not extract join command from kubeadm init output.[/red]")
            typer.echo("Master stdout:")
            typer.echo(result.stdout)
            raise typer.Exit(1)

        print(f"  [green]Control plane initialized.[/green]")
        print(f"  [dim]Join command: {join_command}[/dim]")

        # ----- Step 4: Install CNI on master -----
        print("\n[bold]Step 4/7: Installing Calico CNI...[/bold]")
        rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, _SCRIPT_INSTALL_CNI)
        if rc != 0:
            print("[red]CNI installation failed.[/red]")
            raise typer.Exit(1)
        print("  [green]CNI installed.[/green]")

        # ----- Step 5: Join workers -----
        print("\n[bold]Step 5/7: Joining worker nodes...[/bold]")
        for worker in workers:
            print(f"  [cyan]{worker['name']}[/cyan] ({worker['ip']})...")
            worker_node_name = _detect_kubernetes_node_name(
                worker["ip"],
                resolved_user,
                key_path,
                worker["name"],
            )
            if worker_node_name != worker["name"]:
                print(
                    f"  [dim]Kubernetes node name detected as '{worker_node_name}' "
                    f"(VM name: '{worker['name']}')[/dim]"
                )
            # Reset any previous kubeadm state and ensure containerd is ready (idempotent re-runs).
            _ssh_cmd_stream(worker["ip"], resolved_user, key_path,
                "sudo kubeadm reset -f --cri-socket unix:///var/run/containerd/containerd.sock 2>/dev/null || true; "
                "sudo rm -rf /etc/cni/net.d; "
                "sudo systemctl restart containerd; "
                "for i in $(seq 1 30); do "
                "  if [ -S /var/run/containerd/containerd.sock ] && sudo ctr --connect-timeout 2s version >/dev/null 2>&1; then break; fi; "
                "  sleep 1; "
                "done"
            )
            rc = _ssh_cmd_stream(worker["ip"], resolved_user, key_path, f"sudo {join_command}")
            if rc != 0:
                print(f"  [red]Join failed on {worker['name']}[/red]")
                raise typer.Exit(1)
            print(f"  [green]{worker['name']} joined.[/green]")

            print(f"  [cyan]{worker['name']}[/cyan] waiting for node Ready...")
            if not _wait_for_node_ready(master["ip"], resolved_user, key_path, worker_node_name):
                _print_node_ready_timeout_diagnostics(
                    master["ip"],
                    resolved_user,
                    key_path,
                    worker_node_name,
                )
                print(f"  [red]{worker['name']} did not become Ready in time.[/red]")
                raise typer.Exit(1)
            print(f"  [green]{worker['name']} is Ready.[/green]")

            print(f"  [cyan]{worker['name']}[/cyan] applying labels...")
            _apply_node_labels(master["ip"], resolved_user, key_path, worker_node_name, resolved_node_labels)
            print(f"  [green]{worker['name']} labels applied.[/green]")

        # ----- Step 6: Enable CRIU feature gates on all nodes -----
        print("\n[bold]Step 6/7: Enabling CRIU feature gates on all nodes...[/bold]")
        for node in all_nodes:
            print(f"  [cyan]{node['name']}[/cyan]...")
            rc = _ssh_cmd_stream(node["ip"], resolved_user, key_path, _SCRIPT_WORKER_FEATURE_GATE)
            if rc != 0:
                print(f"  [red]Feature gate setup failed on {node['name']}[/red]")
                # Non-fatal — continue

        # ----- Step 7: Install cloud-specific storage and load balancer providers -----
        print("\n[bold]Step 7/7: Installing cloud storage and load balancer providers...[/bold]")

        if cloud == "aws":
            from .aws.setup import install_loadbalancer, install_storage

            storage_ok = install_storage(
                cluster_name=name,
                metadata=metadata,
                master=master,
                resolved_user=resolved_user,
                key_path=key_path,
            )
            loadbalancer_ok = install_loadbalancer(
                cluster_name=name,
                metadata=metadata,
                master=master,
                resolved_user=resolved_user,
                key_path=key_path,
            )
        else:
            from .azure.setup import install_loadbalancer, install_storage

            storage_ok = install_storage(
                cluster_name=name,
                metadata=metadata,
                all_nodes=all_nodes,
                master=master,
                context_id=cluster["context_id"],
                resolved_user=resolved_user,
                key_path=key_path,
            )
            loadbalancer_ok = install_loadbalancer(
                cluster_name=name,
                metadata=metadata,
                all_nodes=all_nodes,
                master=master,
                context_id=cluster["context_id"],
                resolved_user=resolved_user,
                key_path=key_path,
            )

        # ----- Done -----
        print(Panel(
            f"[green]Cluster '{name}' is ready![/green]\n\n"
            f"  Next: [bold cyan]clouder kubeadm info {name}[/bold cyan]",
            title="Next Step",
            border_style="yellow",
        ))

        # Update cluster metadata with setup info
        _update_cluster_metadata(name, {
            "k8s_version": k8s_version,
            "setup_complete": True,
            "admin_username": resolved_user,
            "storage_ready": storage_ok,
            "loadbalancer_ready": loadbalancer_ok,
        })
