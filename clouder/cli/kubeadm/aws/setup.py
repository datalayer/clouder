"""AWS-specific kubeadm setup helpers."""

from __future__ import annotations

from rich import print


def install_storage(
    cluster_name: str,
    metadata: dict,
    master: dict,
    resolved_user: str,
    key_path: str,
) -> bool:
    """Install AWS storage components on an initialized cluster."""
    from ....cloud.aws.api import (
        get_aws_session_credentials,
        get_aws_vm_instance_profile_arn,
    )
    from .._helpers import (
        _build_aws_ebs_csi_setup_script,
        _ssh_cmd_stream,
    )

    _ = cluster_name

    storage_ok = False

    aws_region = ""
    if metadata:
        aws_region = metadata.get("region", "")
    if not aws_region:
        aws_region = master.get("region", "")

    instance_profile_arn = None
    master_instance_id = master.get("instance_id")
    if master_instance_id:
        try:
            instance_profile_arn = get_aws_vm_instance_profile_arn(
                master_instance_id,
                region=aws_region or None,
            )
        except Exception as exc:
            print(f"[yellow]  Could not detect AWS instance profile: {exc}[/yellow]")

    aws_creds = get_aws_session_credentials(region=aws_region or None)
    access_key_id = aws_creds.get("access_key_id", "")
    secret_access_key = aws_creds.get("secret_access_key", "")

    use_instance_profile = bool(instance_profile_arn)
    if not aws_region:
        print("[yellow]  AWS region could not be resolved - skipping storage setup.[/yellow]")
        print("  Ensure cluster metadata has a region and re-run setup.")
        return storage_ok

    if not use_instance_profile and (not access_key_id or not secret_access_key):
        print("[yellow]  No instance profile detected and AWS credentials are unavailable - skipping storage setup.[/yellow]")
        print("  Attach an EC2 instance profile to cluster nodes or configure AWS credentials, then re-run setup.")
        return storage_ok

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
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, aws_storage_script)
    if rc != 0:
        print("[red]  AWS EBS CSI installation failed.[/red]")
    else:
        print("  [green]AWS EBS CSI installed (default StorageClass: gp3).[/green]")
        storage_ok = True

    return storage_ok


def install_loadbalancer(
    cluster_name: str,
    metadata: dict,
    master: dict,
    resolved_user: str,
    key_path: str,
) -> bool:
    """Install AWS load balancer components on an initialized cluster."""
    from ....cloud.aws.api import get_aws_vm_vpc_id
    from .._helpers import (
        _build_aws_load_balancer_setup_script,
        _ssh_cmd_stream,
    )

    loadbalancer_ok = False

    aws_region = ""
    if metadata:
        aws_region = metadata.get("region", "")
    if not aws_region:
        aws_region = master.get("region", "")

    vpc_id = ""
    master_instance_id = master.get("instance_id")
    if master_instance_id:
        try:
            vpc_id = get_aws_vm_vpc_id(
                master_instance_id,
                region=aws_region or None,
            ) or ""
        except Exception as exc:
            print(f"[yellow]  Could not resolve AWS VPC id: {exc}[/yellow]")
    if not vpc_id and metadata:
        vpc_id = metadata.get("networking", {}).get("vpc_id", "")

    if not vpc_id:
        print("[yellow]  AWS VPC id not available - skipping load balancer controller setup.[/yellow]")
        print("  Ensure cluster metadata includes networking.vpc_id or use an EC2-backed cluster context.")
        return loadbalancer_ok

    print("  Installing AWS Load Balancer Controller...")
    aws_lb_script = _build_aws_load_balancer_setup_script(
        region=aws_region,
        vpc_id=vpc_id,
        cluster_name=cluster_name,
    )
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, aws_lb_script)
    if rc != 0:
        print("[red]  AWS Load Balancer Controller installation failed.[/red]")
    else:
        print("  [green]AWS Load Balancer Controller installed.[/green]")
        loadbalancer_ok = True

    return loadbalancer_ok
