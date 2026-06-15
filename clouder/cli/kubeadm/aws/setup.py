"""AWS-specific kubeadm setup helpers."""

from __future__ import annotations

import time

from botocore.exceptions import ClientError
from rich import print


def _ensure_aws_efs_ready(
    cluster_name: str,
    region: str,
    subnet_id: str,
    security_group_id: str,
) -> str | None:
    """Create or reuse an EFS filesystem and mount target for kubeadm shared storage."""
    from ....cloud.aws.api import _client

    efs = _client("efs", region=region)
    ec2 = _client("ec2", region=region)

    creation_token = f"clouder-{cluster_name}-kubeadm-efs"
    file_system_id = None

    try:
        response = efs.describe_file_systems(CreationToken=creation_token)
        file_systems = response.get("FileSystems", [])
        if file_systems:
            file_system_id = file_systems[0].get("FileSystemId")
    except ClientError:
        file_system_id = None

    if not file_system_id:
        create_response = efs.create_file_system(
            CreationToken=creation_token,
            PerformanceMode="generalPurpose",
            ThroughputMode="bursting",
            Encrypted=True,
            Tags=[
                {"Key": "Name", "Value": f"{cluster_name}-efs"},
                {"Key": "datalayer.io/cluster", "Value": cluster_name},
                {"Key": "datalayer.io/component", "Value": "kubeadm"},
            ],
        )
        file_system_id = create_response["FileSystemId"]

    # Wait until the filesystem is available.
    for _ in range(60):
        fs = efs.describe_file_systems(FileSystemId=file_system_id)["FileSystems"][0]
        state = fs.get("LifeCycleState", "")
        if state == "available":
            break
        time.sleep(2)
    else:
        print("[yellow]  EFS filesystem did not become available in time.[/yellow]")
        return None

    # Ensure NFS ingress exists in the cluster security group (self-reference).
    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 2049,
                    "ToPort": 2049,
                    "UserIdGroupPairs": [{"GroupId": security_group_id}],
                }
            ],
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code != "InvalidPermission.Duplicate":
            print(f"[yellow]  Could not ensure NFS ingress rule on SG {security_group_id}: {exc}[/yellow]")

    # Ensure a mount target exists in the cluster subnet.
    mount_targets = efs.describe_mount_targets(FileSystemId=file_system_id).get("MountTargets", [])
    target_for_subnet = next((mt for mt in mount_targets if mt.get("SubnetId") == subnet_id), None)
    if not target_for_subnet:
        try:
            target_for_subnet = efs.create_mount_target(
                FileSystemId=file_system_id,
                SubnetId=subnet_id,
                SecurityGroups=[security_group_id],
            )
        except ClientError as exc:
            print(f"[yellow]  Could not create EFS mount target in subnet {subnet_id}: {exc}[/yellow]")
            return None

    mount_target_id = target_for_subnet.get("MountTargetId")
    if mount_target_id:
        for _ in range(60):
            mt_response = efs.describe_mount_targets(MountTargetId=mount_target_id)
            mount_targets = mt_response.get("MountTargets", [])
            if mount_targets and mount_targets[0].get("LifeCycleState") == "available":
                break
            time.sleep(2)

    return file_system_id


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
        _build_aws_efs_storageclass_script,
        _ssh_cmd_stream,
        _update_cluster_metadata,
    )

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

    networking = metadata.get("networking", {}) if metadata else {}
    subnet_id = networking.get("subnet_id", "")
    security_group_id = networking.get("security_group_id", "")

    efs_ready = False

    if not subnet_id or not security_group_id:
        print("[yellow]  Cluster subnet/security group metadata missing - skipping aws-efs StorageClass setup.[/yellow]")
        print("  Ensure metadata has networking.subnet_id and networking.security_group_id, then re-run setup.")
    else:
        print("  Ensuring AWS EFS filesystem and mount target for shared storage...")
        efs_file_system_id = _ensure_aws_efs_ready(
            cluster_name=cluster_name,
            region=aws_region,
            subnet_id=subnet_id,
            security_group_id=security_group_id,
        )

        if efs_file_system_id:
            print(f"  [green]EFS ready:[/green] {efs_file_system_id}")
            _update_cluster_metadata(
                cluster_name,
                {
                    "networking": {
                        **networking,
                        "efs_file_system_id": efs_file_system_id,
                    }
                },
            )

            print("  Creating aws-efs StorageClass...")
            efs_sc_script = _build_aws_efs_storageclass_script(efs_file_system_id)
            efs_sc_rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, efs_sc_script)
            if efs_sc_rc != 0:
                print("[red]  aws-efs StorageClass creation failed.[/red]")
            else:
                print("  [green]aws-efs StorageClass created.[/green]")
                efs_ready = True
        else:
            print("[yellow]  EFS setup did not complete - aws-efs StorageClass was not created.[/yellow]")

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
        storage_ok = efs_ready

    if not efs_ready:
        print("[yellow]  AWS storage setup is incomplete: missing aws-efs StorageClass.[/yellow]")

    return storage_ok


def install_loadbalancer(
    cluster_name: str,
    metadata: dict,
    master: dict,
    resolved_user: str,
    key_path: str,
) -> bool:
    """Install AWS load balancer components on an initialized cluster."""
    from ....cloud.aws.api import (
        get_aws_session_credentials,
        get_aws_vm_instance_profile_arn,
        get_aws_vm_vpc_id,
    )
    from .._helpers import (
        _build_aws_load_balancer_setup_script,
        _ssh_cmd,
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
    instance_profile_arn = None
    if master_instance_id:
        try:
            vpc_id = get_aws_vm_vpc_id(
                master_instance_id,
                region=aws_region or None,
            ) or ""
        except Exception as exc:
            print(f"[yellow]  Could not resolve AWS VPC id: {exc}[/yellow]")
        try:
            instance_profile_arn = get_aws_vm_instance_profile_arn(
                master_instance_id,
                region=aws_region or None,
            )
        except Exception as exc:
            print(f"[yellow]  Could not detect AWS instance profile: {exc}[/yellow]")
    if not vpc_id and metadata:
        vpc_id = metadata.get("networking", {}).get("vpc_id", "")

    if not vpc_id:
        print("[yellow]  AWS VPC id not available - skipping load balancer controller setup.[/yellow]")
        print("  Ensure cluster metadata includes networking.vpc_id or use an EC2-backed cluster context.")
        return loadbalancer_ok

    aws_creds = get_aws_session_credentials(region=aws_region or None)
    use_instance_profile = bool(instance_profile_arn)
    if use_instance_profile:
        print(f"  Using EC2 instance profile for ALB controller auth: [dim]{instance_profile_arn}[/dim]")
    else:
        print("  No instance profile detected. Falling back to static AWS credentials for ALB controller bootstrap.")

    print("  Installing AWS Load Balancer Controller...")
    aws_lb_script = _build_aws_load_balancer_setup_script(
        region=aws_region,
        vpc_id=vpc_id,
        cluster_name=cluster_name,
        use_instance_profile=use_instance_profile,
        access_key_id=aws_creds.get("access_key_id") or None,
        secret_access_key=aws_creds.get("secret_access_key") or None,
        session_token=aws_creds.get("session_token") or None,
    )
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, aws_lb_script)
    if rc != 0:
        print("[red]  AWS Load Balancer Controller installation failed.[/red]")
    else:
        deployment_status = _ssh_cmd(
            master["ip"],
            resolved_user,
            key_path,
            (
                "kubectl -n kube-system get deployment aws-load-balancer-controller "
                "-o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null || true"
            ),
            check=False,
        ).stdout.strip().strip("'")
        controller_image = _ssh_cmd(
            master["ip"],
            resolved_user,
            key_path,
            (
                "kubectl -n kube-system get deployment aws-load-balancer-controller "
                "-o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true"
            ),
            check=False,
        ).stdout.strip().strip("'")

        print("  [green]AWS Load Balancer Controller installed.[/green]")
        if deployment_status:
            print(f"  [bold green]Ready replicas:[/bold green] {deployment_status}")
        if controller_image:
            print(f"  [bold green]Controller image:[/bold green] {controller_image}")
        loadbalancer_ok = True

    return loadbalancer_ok
