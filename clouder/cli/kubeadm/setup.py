"""Clouder CLI - kubeadm setup command."""

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm

from ..ctx import get_current_context
from ...util.utils import SSH_FOLDER

from ._helpers import (
    K8S_VERSION,
    _build_aws_ebs_csi_setup_script,
    _build_aws_load_balancer_setup_script,
    _SCRIPT_INSTALL_AZURE_DISK_CSI,
    _SCRIPT_INSTALL_AZURE_FILE_CSI,
    _SCRIPT_INSTALL_CNI,
    _SCRIPT_KUBEADM_INIT,
    _SCRIPT_PREREQS,
    _SCRIPT_WORKER_FEATURE_GATE,
    _build_azure_cloud_config,
    _build_azure_nfs_storageclass_script,
    _get_or_create_azure_sp,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _ssh_cmd_stream,
    _update_cluster_metadata,
)


def register(kubeadm_app: typer.Typer):
    """Register the setup command on the given Typer app."""

    @kubeadm_app.command("setup")
    def kubeadm_setup(
        name: str = typer.Argument(..., help="Cluster name (must match vm-create name)."),
        user: str = typer.Option("ubuntu", "--admin-user", "-u", help="SSH username on the VMs (ubuntu for AWS, azureuser for Azure)."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        k8s_version: str = typer.Option(K8S_VERSION, "--k8s-version", help="Kubernetes version to install."),
    ):
        """Set up a kubeadm cluster on previously created VMs.

        Steps: install prerequisites → kubeadm init (master) → install CNI →
        kubeadm join (workers) → enable CRIU feature gates (all nodes).
        """
        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        workers = cluster["workers"]
        cloud, _ = get_current_context()

        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        all_nodes = [master] + workers

        print(Panel(
            f"[bold]Cluster:[/bold] {name}\n"
            f"[bold]Master:[/bold]  {master['name']} ({master['ip']})\n"
            f"[bold]Workers:[/bold] {', '.join(w['name'] for w in workers)}\n"
            f"[bold]Key:[/bold]     {key_path}\n"
            f"[bold]K8s:[/bold]     v{k8s_version}",
            title="Kubeadm Setup",
        ))

        if not Confirm.ask("\nProceed with cluster setup?", default=True):
            raise typer.Abort()

        # ----- Step 1: Install prerequisites on ALL nodes -----
        print("\n[bold]Step 1/5: Installing prerequisites on all nodes...[/bold]")
        for node in all_nodes:
            print(f"  [cyan]{node['name']}[/cyan] ({node['ip']})...")
            rc = _ssh_cmd_stream(node["ip"], user, key_path, _SCRIPT_PREREQS)
            if rc != 0:
                print(f"  [red]Failed on {node['name']}[/red]")
                raise typer.Exit(1)
            print(f"  [green]{node['name']} done.[/green]")

        # ----- Step 2: kubeadm init on master -----
        print("\n[bold]Step 2/5: Initializing control plane on master...[/bold]")
        init_script = _SCRIPT_KUBEADM_INIT.replace("PUBLIC_IP_PLACEHOLDER", master["ip"])
        result = _ssh_cmd(master["ip"], user, key_path, init_script, check=False)
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

        # ----- Step 3: Install CNI on master -----
        print("\n[bold]Step 3/5: Installing Calico CNI...[/bold]")
        rc = _ssh_cmd_stream(master["ip"], user, key_path, _SCRIPT_INSTALL_CNI)
        if rc != 0:
            print("[red]CNI installation failed.[/red]")
            raise typer.Exit(1)
        print("  [green]CNI installed.[/green]")

        # ----- Step 4: Join workers -----
        print("\n[bold]Step 4/5: Joining worker nodes...[/bold]")
        for worker in workers:
            print(f"  [cyan]{worker['name']}[/cyan] ({worker['ip']})...")
            # Reset any previous kubeadm state and ensure containerd is ready (idempotent re-runs).
            _ssh_cmd_stream(worker["ip"], user, key_path,
                "sudo kubeadm reset -f --cri-socket unix:///var/run/containerd/containerd.sock 2>/dev/null || true; "
                "sudo rm -rf /etc/cni/net.d; "
                "sudo systemctl restart containerd; "
                "for i in $(seq 1 30); do "
                "  if [ -S /var/run/containerd/containerd.sock ] && sudo ctr --connect-timeout 2s version >/dev/null 2>&1; then break; fi; "
                "  sleep 1; "
                "done"
            )
            rc = _ssh_cmd_stream(worker["ip"], user, key_path, f"sudo {join_command}")
            if rc != 0:
                print(f"  [red]Join failed on {worker['name']}[/red]")
                raise typer.Exit(1)
            print(f"  [green]{worker['name']} joined.[/green]")

        # ----- Step 5: Enable CRIU feature gates on all nodes -----
        print("\n[bold]Step 5/6: Enabling CRIU feature gates on all nodes...[/bold]")
        for node in all_nodes:
            print(f"  [cyan]{node['name']}[/cyan]...")
            rc = _ssh_cmd_stream(node["ip"], user, key_path, _SCRIPT_WORKER_FEATURE_GATE)
            if rc != 0:
                print(f"  [red]Feature gate setup failed on {node['name']}[/red]")
                # Non-fatal — continue

        # ----- Step 6: Install cloud-specific storage and load balancer providers -----
        print("\n[bold]Step 6/6: Installing cloud storage and load balancer providers...[/bold]")

        metadata = _load_cluster_metadata(name)
        storage_ok = False
        loadbalancer_ok = False
        if cloud == "aws":
            from ...cloud.aws.api import (
                get_aws_session_credentials,
                get_aws_vm_instance_profile_arn,
                get_aws_vm_vpc_id,
            )

            aws_region = ""
            if metadata:
                aws_region = metadata.get("region", "")
            if not aws_region:
                aws_region = master.get("region", "")

            instance_profile_arn = None
            vpc_id = ""
            master_instance_id = master.get("instance_id")
            if master_instance_id:
                try:
                    instance_profile_arn = get_aws_vm_instance_profile_arn(
                        master_instance_id,
                        region=aws_region or None,
                    )
                except Exception as exc:
                    print(f"[yellow]  Could not detect AWS instance profile: {exc}[/yellow]")
                try:
                    vpc_id = get_aws_vm_vpc_id(
                        master_instance_id,
                        region=aws_region or None,
                    ) or ""
                except Exception as exc:
                    print(f"[yellow]  Could not resolve AWS VPC id: {exc}[/yellow]")
            if not vpc_id and metadata:
                vpc_id = metadata.get("networking", {}).get("vpc_id", "")

            aws_creds = get_aws_session_credentials(region=aws_region or None)
            access_key_id = aws_creds.get("access_key_id", "")
            secret_access_key = aws_creds.get("secret_access_key", "")

            use_instance_profile = bool(instance_profile_arn)
            if not aws_region:
                print("[yellow]  AWS region could not be resolved — skipping storage setup.[/yellow]")
                print("  Ensure cluster metadata has a region and re-run setup.")
            elif not use_instance_profile and (not access_key_id or not secret_access_key):
                print("[yellow]  No instance profile detected and AWS credentials are unavailable — skipping storage setup.[/yellow]")
                print("  Attach an EC2 instance profile to cluster nodes or configure AWS credentials, then re-run setup.")
            else:
                if use_instance_profile:
                    print(f"  Using EC2 instance profile for EBS CSI auth: [dim]{instance_profile_arn}[/dim]")
                else:
                    print("  No instance profile detected. Falling back to static AWS credentials for EBS CSI bootstrap.")

                print("  Installing AWS EBS CSI driver and default gp3 StorageClass...")
                aws_storage_script = _build_aws_ebs_csi_setup_script(
                    region=aws_region,
                    use_instance_profile=use_instance_profile,
                    access_key_id=access_key_id or None,
                    secret_access_key=secret_access_key or None,
                    session_token=aws_creds.get("session_token") or None,
                )
                rc = _ssh_cmd_stream(master["ip"], user, key_path, aws_storage_script)
                if rc != 0:
                    print("[red]  AWS EBS CSI installation failed.[/red]")
                else:
                    print("  [green]AWS EBS CSI installed (default StorageClass: gp3).[/green]")
                    storage_ok = True

                if not vpc_id:
                    print("[yellow]  AWS VPC id not available — skipping load balancer controller setup.[/yellow]")
                    print("  Ensure cluster metadata includes networking.vpc_id or use an EC2-backed cluster context.")
                else:
                    print("  Installing AWS Load Balancer Controller...")
                    aws_lb_script = _build_aws_load_balancer_setup_script(
                        region=aws_region,
                        vpc_id=vpc_id,
                        cluster_name=name,
                    )
                    rc = _ssh_cmd_stream(master["ip"], user, key_path, aws_lb_script)
                    if rc != 0:
                        print("[red]  AWS Load Balancer Controller installation failed.[/red]")
                    else:
                        print("  [green]AWS Load Balancer Controller installed.[/green]")
                        loadbalancer_ok = True
        elif not metadata:
            print("[yellow]  No cluster metadata found — skipping storage setup.[/yellow]")
            print("  Run the storage setup manually. See: https://clouder.sh/cluster/cli/kubeadm")
        else:
            tenant_id, client_id, client_secret = _get_or_create_azure_sp(
                cluster["context_id"],
                metadata.get("resource_group", ""),
                name,
            )
            if not all([tenant_id, client_id, client_secret]):
                print("[yellow]  Azure SP credentials not available — skipping storage setup.[/yellow]")
                print("  Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET and re-run,")
                print("  or install the Azure Disk CSI driver manually.")
            else:
                networking = metadata.get("networking", {})
                azure_config = _build_azure_cloud_config(
                    tenant_id=tenant_id,
                    subscription_id=cluster["context_id"],
                    resource_group=metadata.get("resource_group", ""),
                    location=metadata.get("region", ""),
                    client_id=client_id,
                    client_secret=client_secret,
                    vnet_name=networking.get("vnet_name", ""),
                    subnet_name=networking.get("subnet_name", ""),
                    nsg_name=networking.get("nsg_name", ""),
                )

                # Deploy /etc/kubernetes/azure.json to ALL nodes (CSI node pods mount it).
                import base64
                config_b64 = base64.b64encode(azure_config.encode()).decode()
                deploy_cmd = (
                    f"echo '{config_b64}' | base64 -d "
                    "| sudo tee /etc/kubernetes/azure.json > /dev/null "
                    "&& sudo chmod 600 /etc/kubernetes/azure.json"
                )
                for node in all_nodes:
                    print(f"  Deploying cloud config to [cyan]{node['name']}[/cyan]...")
                    rc = _ssh_cmd_stream(node["ip"], user, key_path, deploy_cmd)
                    if rc != 0:
                        print(f"  [red]Failed to deploy cloud config on {node['name']}[/red]")

                # Create the azure-cloud-provider secret in kube-system (for the CSI controller).
                secret_cmd = (
                    "sudo cat /etc/kubernetes/azure.json | kubectl create secret generic azure-cloud-provider "
                    "--from-file=cloud-config=/dev/stdin "
                    "-n kube-system --dry-run=client -o yaml | kubectl apply -f -"
                )
                print("  Creating azure-cloud-provider secret...")
                rc = _ssh_cmd_stream(master["ip"], user, key_path, secret_cmd)
                if rc != 0:
                    print("  [red]Failed to create cloud-provider secret.[/red]")
                else:
                    # Install the Azure Disk CSI driver and create StorageClass.
                    print("  Installing Azure Disk CSI driver...")
                    rc = _ssh_cmd_stream(master["ip"], user, key_path, _SCRIPT_INSTALL_AZURE_DISK_CSI)
                    if rc != 0:
                        print("[red]  Azure Disk CSI driver installation failed.[/red]")
                    else:
                        print("  [green]Azure Disk CSI installed (default StorageClass: managed-csi).[/green]")
                        storage_ok = True

                        # Install the Azure File CSI driver (required for azure-nfs shared filesystem).
                        print("  Installing Azure File CSI driver...")
                        rc = _ssh_cmd_stream(master["ip"], user, key_path, _SCRIPT_INSTALL_AZURE_FILE_CSI)
                        if rc != 0:
                            print("[red]  Azure File CSI driver installation failed.[/red]")
                        else:
                            print("  [green]Azure File CSI installed (provisioner: file.csi.azure.com).[/green]")

                            # Create the azure-nfs StorageClass with Azure params baked in.
                            # On kubeadm these must be explicit (AKS auto-detects from IMDS).
                            print("  Creating azure-nfs StorageClass...")
                            sc_script = _build_azure_nfs_storageclass_script(
                                subscription_id=cluster["context_id"],
                                resource_group=metadata.get("resource_group", ""),
                                location=metadata.get("region", ""),
                            )
                            rc = _ssh_cmd_stream(master["ip"], user, key_path, sc_script)
                            if rc != 0:
                                print("[red]  azure-nfs StorageClass creation failed.[/red]")
                            else:
                                print("  [green]azure-nfs StorageClass created.[/green]")

        # ----- Done -----
        print(Panel(
            f"[green]Cluster '{name}' is ready![/green]\n\n"
            f"  Run [bold]clouder kubeadm info {name}[/bold] to see cluster details and next steps.",
            title="Setup Complete",
        ))

        # Update cluster metadata with setup info
        _update_cluster_metadata(name, {
            "k8s_version": k8s_version,
            "setup_complete": True,
            "admin_username": user,
            "storage_ready": storage_ok,
            "loadbalancer_ready": loadbalancer_ok,
        })
