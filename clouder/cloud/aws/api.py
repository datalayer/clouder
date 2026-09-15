"""AWS cloud provider API helpers."""

from __future__ import annotations

import json
import os
import re
import time

from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ...util.wait import wait_with_spinner


def _session(region: Optional[str] = None):
    """Create a boto3 session using current env/profile config."""
    kwargs = {}
    env_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if region or env_region:
        kwargs["region_name"] = region or env_region
    profile = os.getenv("AWS_PROFILE") or os.getenv("AWS_DEFAULT_PROFILE")
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def _client(service: str, region: Optional[str] = None):
    """Create a boto3 service client."""
    return _session(region=region).client(service)


def get_aws_session_credentials(region: Optional[str] = None) -> dict:
    """Return active AWS session credentials for API consumers that must run in-cluster setup."""
    session = _session(region=region)
    creds = session.get_credentials()
    if not creds:
        return {}
    frozen = creds.get_frozen_credentials()
    return {
        "access_key_id": frozen.access_key or "",
        "secret_access_key": frozen.secret_key or "",
        "session_token": frozen.token or "",
        "region": session.region_name or region or "",
    }


def get_aws_identity() -> dict:
    """Return STS caller identity for current credentials."""
    sts = _client("sts")
    ident = sts.get_caller_identity()
    return {
        "account_id": ident.get("Account", ""),
        "arn": ident.get("Arn", ""),
        "user_id": ident.get("UserId", ""),
    }


def list_aws_accounts() -> list:
    """Return the current AWS account as the available context list."""
    ident = get_aws_identity()
    return [
        {
            "id": ident["account_id"],
            "name": ident["arn"],
        }
    ]


def list_aws_regions() -> list:
    """List enabled AWS regions."""
    ec2 = _client("ec2", region="us-east-1")
    regions = ec2.describe_regions(AllRegions=False).get("Regions", [])
    return [
        {
            "name": r.get("RegionName", ""),
            "endpoint": r.get("Endpoint", ""),
            "opt_in_status": r.get("OptInStatus", ""),
        }
        for r in regions
    ]


def list_aws_vms(region: Optional[str] = None) -> list:
    """List non-terminated EC2 instances in a region."""
    ec2 = _client("ec2", region=region)
    paginator = ec2.get_paginator("describe_instances")
    instances = []
    for page in paginator.paginate(
        Filters=[
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            }
        ]
    ):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                name = ""
                for tag in instance.get("Tags", []):
                    if tag.get("Key") == "Name":
                        name = tag.get("Value", "")
                        break
                instances.append(
                    {
                        "id": instance.get("InstanceId", ""),
                        "name": name,
                        "key_name": instance.get("KeyName", ""),
                        "instance_type": instance.get("InstanceType", ""),
                        "state": instance.get("State", {}).get("Name", ""),
                        "public_ip": instance.get("PublicIpAddress"),
                        "private_ip": instance.get("PrivateIpAddress"),
                        "vpc_id": instance.get("VpcId"),
                        "subnet_id": instance.get("SubnetId"),
                        "region": ec2.meta.region_name,
                    }
                )
    return instances


def get_aws_vm_public_ip(instance_id: str, region: Optional[str] = None) -> Optional[str]:
    """Get an EC2 instance public IP by id."""
    ec2 = _client("ec2", region=region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        return None
    return reservations[0]["Instances"][0].get("PublicIpAddress")


def get_aws_vm_elastic_ip(instance_id: str, region: Optional[str] = None) -> Optional[str]:
    """Return Elastic IP associated with an EC2 instance, if any."""
    ec2 = _client("ec2", region=region)
    response = ec2.describe_addresses(
        Filters=[
            {"Name": "instance-id", "Values": [instance_id]},
        ]
    )
    addresses = response.get("Addresses", [])
    if not addresses:
        return None
    # In typical setups there is one EIP per instance primary interface.
    return addresses[0].get("PublicIp")


def list_aws_acm_certificates(region: Optional[str] = None) -> list[dict]:
    """List ACM certificates for a region with basic metadata."""
    acm = _client("acm", region=region)
    certificates: list[dict] = []
    paginator = acm.get_paginator("list_certificates")
    for page in paginator.paginate():
        for summary in page.get("CertificateSummaryList", []) or []:
            arn = summary.get("CertificateArn") or ""
            if not arn:
                continue
            certificates.append(
                {
                    "arn": arn,
                    "domain_name": summary.get("DomainName") or "",
                    "status": summary.get("Status") or "",
                    "type": summary.get("Type") or "",
                    "key_algorithm": summary.get("KeyAlgorithm") or "",
                }
            )

    return sorted(certificates, key=lambda c: (c.get("status") != "ISSUED", c.get("domain_name") or c.get("arn") or ""))


def get_aws_vm_instance_profile_arn(instance_id: str, region: Optional[str] = None) -> Optional[str]:
    """Return IAM instance profile ARN attached to an EC2 instance, if any."""
    ec2 = _client("ec2", region=region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        return None
    profile = reservations[0]["Instances"][0].get("IamInstanceProfile") or {}
    return profile.get("Arn")


def get_aws_vm_vpc_id(instance_id: str, region: Optional[str] = None) -> Optional[str]:
    """Return VPC ID of an EC2 instance."""
    ec2 = _client("ec2", region=region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        return None
    return reservations[0]["Instances"][0].get("VpcId")


def get_aws_instances_details(instance_ids: list[str], region: Optional[str] = None) -> dict[str, dict]:
    """Return selected EC2 details indexed by instance id."""
    if not instance_ids:
        return {}

    ec2 = _client("ec2", region=region)
    response = ec2.describe_instances(InstanceIds=instance_ids)

    details: dict[str, dict] = {}
    for reservation in response.get("Reservations", []) or []:
        for instance in reservation.get("Instances", []) or []:
            instance_id = instance.get("InstanceId")
            if not instance_id:
                continue
            details[instance_id] = {
                "private_ip": instance.get("PrivateIpAddress") or "",
                "availability_zone": ((instance.get("Placement") or {}).get("AvailabilityZone") or ""),
            }
    return details


def ensure_aws_instance_security_group_ingress(
    instance_ids: list[str],
    ports: list[int],
    cidr: str = "0.0.0.0/0",
    region: Optional[str] = None,
) -> dict:
    """Ensure inbound TCP ingress rules exist on SGs attached to instances.

    Returns a summary with touched SG IDs and ensured ports.
    """
    if not instance_ids or not ports:
        return {"security_group_ids": [], "ports": []}

    ec2 = _client("ec2", region=region)
    response = ec2.describe_instances(InstanceIds=instance_ids)

    security_group_ids: set[str] = set()
    for reservation in response.get("Reservations", []) or []:
        for instance in reservation.get("Instances", []) or []:
            for sg in instance.get("SecurityGroups", []) or []:
                group_id = sg.get("GroupId")
                if group_id:
                    security_group_ids.add(group_id)

    for group_id in security_group_ids:
        for port in ports:
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=group_id,
                    IpPermissions=[
                        {
                            "IpProtocol": "tcp",
                            "FromPort": int(port),
                            "ToPort": int(port),
                            "IpRanges": [{"CidrIp": cidr}],
                        }
                    ],
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "InvalidPermission.Duplicate":
                    raise RuntimeError(
                        f"Unable to authorize security group '{group_id}' ingress on port {port}: {exc}"
                    ) from exc

    return {
        "security_group_ids": sorted(security_group_ids),
        "ports": sorted({int(p) for p in ports}),
        "cidr": cidr,
    }


def create_aws_vm(
    vm_name: str,
    instance_type: str,
    key_name: str,
    subnet_id: str,
    security_group_id: str,
    ami_id: str,
    root_volume_size_gb: int = 100,
    tags: Optional[dict] = None,
    region: Optional[str] = None,
) -> dict:
    """Create a single EC2 VM and wait until running."""
    ec2 = _client("ec2", region=region)

    tag_list = [{"Key": "Name", "Value": vm_name}]
    for key, value in (tags or {}).items():
        tag_list.append({"Key": key, "Value": str(value)})

    response = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        KeyName=key_name,
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet_id,
        SecurityGroupIds=[security_group_id],
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": root_volume_size_gb,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": tag_list,
            }
        ],
    )

    instance_id = response["Instances"][0]["InstanceId"]
    waiter = ec2.get_waiter("instance_running")
    wait_with_spinner(
        lambda: waiter.wait(InstanceIds=[instance_id]),
        f"Waiting for EC2 instance {vm_name} to be running",
    )

    desc = ec2.describe_instances(InstanceIds=[instance_id])
    inst = desc["Reservations"][0]["Instances"][0]
    return {
        "id": instance_id,
        "name": vm_name,
        "instance_type": inst.get("InstanceType", ""),
        "public_ip": inst.get("PublicIpAddress"),
        "private_ip": inst.get("PrivateIpAddress"),
        "state": inst.get("State", {}).get("Name", ""),
        "region": ec2.meta.region_name,
    }


def terminate_aws_vm(instance_id: str, region: Optional[str] = None):
    """Terminate one EC2 instance."""
    ec2 = _client("ec2", region=region)
    ec2.terminate_instances(InstanceIds=[instance_id])


def wait_aws_instances_terminated(instance_ids: list[str], region: Optional[str] = None):
    """Wait until the given EC2 instances are terminated."""
    if not instance_ids:
        return

    ec2 = _client("ec2", region=region)
    waiter = ec2.get_waiter("instance_terminated")
    wait_with_spinner(
        lambda: waiter.wait(InstanceIds=instance_ids),
        f"Waiting for {len(instance_ids)} EC2 instance(s) to terminate",
    )


def list_aws_albs_for_instance(instance_id: str, region: Optional[str] = None) -> list[dict]:
    """List ALBs associated to an EC2 instance via target groups in a region."""
    elbv2 = _client("elbv2", region=region)
    found: dict[str, dict] = {}

    paginator = elbv2.get_paginator("describe_target_groups")
    for page in paginator.paginate():
        for tg in page.get("TargetGroups", []):
            tg_arn = tg.get("TargetGroupArn")
            if not tg_arn:
                continue

            try:
                health = elbv2.describe_target_health(TargetGroupArn=tg_arn)
            except ClientError:
                continue

            targets = health.get("TargetHealthDescriptions", [])
            has_instance = any(
                (desc.get("Target") or {}).get("Id") == instance_id
                for desc in targets
            )
            if not has_instance:
                continue

            for lb_arn in tg.get("LoadBalancerArns", []) or []:
                entry = found.setdefault(
                    lb_arn,
                    {
                        "load_balancer_arn": lb_arn,
                        "load_balancer_name": "",
                        "dns_name": "",
                        "target_group_arns": set(),
                    },
                )
                entry["target_group_arns"].add(tg_arn)

    if not found:
        return []

    lb_arns = list(found.keys())
    for idx in range(0, len(lb_arns), 20):
        chunk = lb_arns[idx : idx + 20]
        response = elbv2.describe_load_balancers(LoadBalancerArns=chunk)
        for lb in response.get("LoadBalancers", []):
            lb_arn = lb.get("LoadBalancerArn")
            if lb_arn in found:
                found[lb_arn]["load_balancer_name"] = lb.get("LoadBalancerName", "")
                found[lb_arn]["dns_name"] = lb.get("DNSName", "")

    results = []
    for value in found.values():
        value["target_group_arns"] = sorted(value["target_group_arns"])
        results.append(value)

    return sorted(results, key=lambda item: item.get("load_balancer_name") or item.get("load_balancer_arn") or "")


def delete_aws_alb(load_balancer_arn: str, region: Optional[str] = None):
    """Delete an ALB and its listeners/target groups in the given region."""
    elbv2 = _client("elbv2", region=region)

    try:
        listeners = elbv2.describe_listeners(LoadBalancerArn=load_balancer_arn).get("Listeners", [])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"LoadBalancerNotFound", "LoadBalancerNotFoundException"}:
            return
        raise

    target_group_arns: set[str] = set()
    for listener in listeners:
        for action in listener.get("DefaultActions", []) or []:
            tg_arn = action.get("TargetGroupArn")
            if tg_arn:
                target_group_arns.add(tg_arn)
            fwd_cfg = action.get("ForwardConfig") or {}
            for tg_ref in fwd_cfg.get("TargetGroups", []) or []:
                tg_ref_arn = tg_ref.get("TargetGroupArn")
                if tg_ref_arn:
                    target_group_arns.add(tg_ref_arn)
        try:
            elbv2.delete_listener(ListenerArn=listener.get("ListenerArn"))
        except ClientError:
            continue

    # Include any additional TGs bound to the LB.
    paginator = elbv2.get_paginator("describe_target_groups")
    for page in paginator.paginate(LoadBalancerArn=load_balancer_arn):
        for tg in page.get("TargetGroups", []):
            tg_arn = tg.get("TargetGroupArn")
            if tg_arn:
                target_group_arns.add(tg_arn)

    try:
        elbv2.delete_load_balancer(LoadBalancerArn=load_balancer_arn)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"LoadBalancerNotFound", "LoadBalancerNotFoundException"}:
            raise

    waiter = elbv2.get_waiter("load_balancers_deleted")
    try:
        waiter.wait(LoadBalancerArns=[load_balancer_arn])
    except Exception:
        pass

    for tg_arn in sorted(target_group_arns):
        try:
            elbv2.delete_target_group(TargetGroupArn=tg_arn)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"TargetGroupNotFound", "TargetGroupNotFoundException", "ResourceInUse"}:
                continue
            raise


def create_aws_kubeadm_network(
    cluster_name: str,
    vpc_cidr: str,
    subnet_cidr: str,
    allowed_ssh_cidrs: list[str],
    availability_zone: Optional[str] = None,
    region: Optional[str] = None,
) -> dict:
    """Create VPC, subnet, IGW, route table, and SG for a kubeadm cluster."""
    ec2 = _client("ec2", region=region)

    vpc = ec2.create_vpc(CidrBlock=vpc_cidr)
    vpc_id = vpc["Vpc"]["VpcId"]

    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

    ec2.create_tags(
        Resources=[vpc_id],
        Tags=[{"Key": "Name", "Value": f"{cluster_name}-vpc"}],
    )

    subnet_kwargs = {
        "VpcId": vpc_id,
        "CidrBlock": subnet_cidr,
    }
    if availability_zone:
        subnet_kwargs["AvailabilityZone"] = availability_zone

    subnet = ec2.create_subnet(**subnet_kwargs)
    subnet_id = subnet["Subnet"]["SubnetId"]
    subnet_az = subnet["Subnet"].get("AvailabilityZone", availability_zone or "")

    ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={"Value": True})
    ec2.create_tags(
        Resources=[subnet_id],
        Tags=[{"Key": "Name", "Value": f"{cluster_name}-subnet"}],
    )

    igw = ec2.create_internet_gateway()
    igw_id = igw["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

    route_table = ec2.create_route_table(VpcId=vpc_id)
    route_table_id = route_table["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=route_table_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    ec2.associate_route_table(RouteTableId=route_table_id, SubnetId=subnet_id)

    sg = ec2.create_security_group(
        GroupName=f"{cluster_name}-kubeadm-sg",
        Description="Security group for Clouder kubeadm cluster",
        VpcId=vpc_id,
    )
    security_group_id = sg["GroupId"]

    ingress_rules = [
        {"from": 22, "to": 22, "proto": "tcp", "cidrs": allowed_ssh_cidrs},
        {"from": 6443, "to": 6443, "proto": "tcp", "cidrs": allowed_ssh_cidrs},
        {"from": 80, "to": 80, "proto": "tcp", "cidrs": ["0.0.0.0/0"]},
        {"from": 443, "to": 443, "proto": "tcp", "cidrs": ["0.0.0.0/0"]},
        {"from": 30000, "to": 32767, "proto": "tcp", "cidrs": ["0.0.0.0/0"]},
        {"from": 2379, "to": 2380, "proto": "tcp", "self": True},
        {"from": 10250, "to": 10259, "proto": "tcp", "self": True},
        {"from": 8472, "to": 8472, "proto": "udp", "self": True},
    ]

    permissions = []
    for rule in ingress_rules:
        perm = {
            "IpProtocol": rule["proto"],
            "FromPort": rule["from"],
            "ToPort": rule["to"],
        }
        if rule.get("self"):
            perm["UserIdGroupPairs"] = [{"GroupId": security_group_id}]
        else:
            perm["IpRanges"] = [{"CidrIp": cidr} for cidr in rule["cidrs"]]
        permissions.append(perm)

    ec2.authorize_security_group_ingress(GroupId=security_group_id, IpPermissions=permissions)

    return {
        "vpc_id": vpc_id,
        "subnet_id": subnet_id,
        "subnet_availability_zone": subnet_az,
        "internet_gateway_id": igw_id,
        "route_table_id": route_table_id,
        "security_group_id": security_group_id,
        "region": ec2.meta.region_name,
    }


def delete_aws_kubeadm_network(
    vpc_id: str,
    subnet_id: str,
    internet_gateway_id: str,
    route_table_id: str,
    security_group_id: str,
    region: Optional[str] = None,
):
    """Delete kubeadm network resources in reverse dependency order."""
    ec2 = _client("ec2", region=region)
    elbv2 = _client("elbv2", region=region)
    efs = _client("efs", region=region)

    def _cleanup_vpc_public_addresses() -> None:
        # Unmap ENI public IP associations in this VPC (includes non-EIP public IPv4 mappings).
        try:
            eni_paginator = ec2.get_paginator("describe_network_interfaces")
            eni_pages = eni_paginator.paginate(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
        except ClientError:
            eni_pages = []

        for page in eni_pages:
            for eni in page.get("NetworkInterfaces", []) or []:
                association = eni.get("Association") or {}
                association_id = association.get("AssociationId")
                allocation_id = association.get("AllocationId")
                if association_id:
                    try:
                        ec2.disassociate_address(AssociationId=association_id)
                    except ClientError:
                        pass
                if allocation_id:
                    try:
                        ec2.release_address(AllocationId=allocation_id)
                    except ClientError:
                        pass

        # Release Elastic IPs still allocated in this VPC.
        addresses = ec2.describe_addresses().get("Addresses", [])
        for address in addresses:
            allocation_id = address.get("AllocationId")
            network_interface_id = address.get("NetworkInterfaceId")
            association_id = address.get("AssociationId")
            if not allocation_id:
                continue
            if network_interface_id:
                try:
                    eni = ec2.describe_network_interfaces(NetworkInterfaceIds=[network_interface_id]).get(
                        "NetworkInterfaces", []
                    )
                except ClientError:
                    eni = []
                if not eni or eni[0].get("VpcId") != vpc_id:
                    continue
            if association_id:
                try:
                    ec2.disassociate_address(AssociationId=association_id)
                except ClientError:
                    pass
            try:
                ec2.release_address(AllocationId=allocation_id)
            except ClientError:
                pass

    def _has_mapped_public_addresses() -> bool:
        # Any ENI in the VPC with a public association can block IGW detachment.
        enis = ec2.describe_network_interfaces(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("NetworkInterfaces", [])
        return any((eni.get("Association") or {}).get("PublicIp") for eni in enis)

    def _delete_vpc_load_balancers() -> None:
        # Delete ELBv2 load balancers in this VPC first: they own service ENIs/public mappings.
        try:
            lbs = elbv2.describe_load_balancers().get("LoadBalancers", [])
        except ClientError:
            lbs = []

        lb_arns: list[str] = []
        for lb in lbs:
            if lb.get("VpcId") != vpc_id:
                continue
            lb_arn = lb.get("LoadBalancerArn")
            if not lb_arn:
                continue
            try:
                elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
                lb_arns.append(lb_arn)
            except ClientError:
                pass

        for lb_arn in lb_arns:
            try:
                waiter = elbv2.get_waiter("load_balancers_deleted")
                waiter.wait(LoadBalancerArns=[lb_arn], WaiterConfig={"Delay": 5, "MaxAttempts": 24})
            except Exception:
                pass

        # Best-effort cleanup of orphan target groups in the same VPC.
        try:
            paginator = elbv2.get_paginator("describe_target_groups")
            for page in paginator.paginate():
                for tg in page.get("TargetGroups", []) or []:
                    if tg.get("VpcId") != vpc_id:
                        continue
                    if tg.get("LoadBalancerArns"):
                        continue
                    tg_arn = tg.get("TargetGroupArn")
                    if tg_arn:
                        try:
                            elbv2.delete_target_group(TargetGroupArn=tg_arn)
                        except ClientError:
                            pass
        except ClientError:
            pass

    def _delete_vpc_efs_mount_targets() -> None:
        # EFS mount targets create requester-managed ENIs that block subnet/SG/VPC deletion.
        try:
            subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
        except ClientError:
            subnets = []

        vpc_subnet_ids = {subnet.get("SubnetId") for subnet in subnets if subnet.get("SubnetId")}
        if not vpc_subnet_ids:
            return

        try:
            file_systems = efs.describe_file_systems().get("FileSystems", [])
        except ClientError:
            file_systems = []

        mount_target_ids: list[str] = []
        for fs in file_systems:
            fs_id = fs.get("FileSystemId")
            if not fs_id:
                continue
            try:
                paginator = efs.get_paginator("describe_mount_targets")
                for page in paginator.paginate(FileSystemId=fs_id):
                    for mt in page.get("MountTargets", []) or []:
                        if mt.get("SubnetId") in vpc_subnet_ids:
                            mt_id = mt.get("MountTargetId")
                            if mt_id:
                                mount_target_ids.append(mt_id)
            except ClientError:
                continue

        for mt_id in mount_target_ids:
            try:
                efs.delete_mount_target(MountTargetId=mt_id)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "MountTargetNotFound":
                    pass

        if not mount_target_ids:
            return

        # Wait briefly for deletion propagation so dependent ENIs can disappear.
        deadline = time.time() + 180
        pending = set(mount_target_ids)
        while pending and time.time() < deadline:
            done: set[str] = set()
            for mt_id in pending:
                try:
                    state = efs.describe_mount_targets(MountTargetId=mt_id).get("MountTargets", [{}])[0].get("LifeCycleState")
                    if state == "deleted":
                        done.add(mt_id)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") == "MountTargetNotFound":
                        done.add(mt_id)
            pending -= done
            if pending:
                time.sleep(5)

    def _force_cleanup_vpc_dependencies() -> None:
        print("  AWS teardown: running force dependency cleanup for VPC resources...")
        _delete_vpc_load_balancers()
        _delete_vpc_efs_mount_targets()

        # 2) Release public address associations and EIPs in this VPC.
        _cleanup_vpc_public_addresses()

        # 3) Remove non-main route table associations/tables in this VPC.
        try:
            route_tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
                "RouteTables", []
            )
        except ClientError:
            route_tables = []
        for rt in route_tables:
            for assoc in rt.get("Associations", []) or []:
                assoc_id = assoc.get("RouteTableAssociationId")
                if assoc_id and not assoc.get("Main"):
                    try:
                        ec2.disassociate_route_table(AssociationId=assoc_id)
                    except ClientError:
                        pass
            if any(assoc.get("Main") for assoc in rt.get("Associations", []) or []):
                continue
            rt_id = rt.get("RouteTableId")
            if rt_id:
                try:
                    ec2.delete_route_table(RouteTableId=rt_id)
                except ClientError:
                    pass

        # 4) Delete available ENIs in this VPC.
        try:
            enis = ec2.describe_network_interfaces(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
                "NetworkInterfaces", []
            )
        except ClientError:
            enis = []
        for eni in enis:
            if eni.get("Status") != "available":
                continue
            eni_id = eni.get("NetworkInterfaceId")
            if eni_id:
                try:
                    ec2.delete_network_interface(NetworkInterfaceId=eni_id)
                except ClientError:
                    pass

        # 5) Best-effort subnet deletion in this VPC.
        try:
            subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
        except ClientError:
            subnets = []
        for subnet in subnets:
            subnet_id_local = subnet.get("SubnetId")
            if subnet_id_local:
                try:
                    ec2.delete_subnet(SubnetId=subnet_id_local)
                except ClientError:
                    pass

        # 6) Best-effort non-default SG deletion in this VPC.
        try:
            security_groups = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
                "SecurityGroups", []
            )
        except ClientError:
            security_groups = []
        for sg in security_groups:
            if sg.get("GroupName") == "default":
                continue
            sg_id_local = sg.get("GroupId")
            if sg_id_local:
                try:
                    ec2.delete_security_group(GroupId=sg_id_local)
                except ClientError:
                    pass

    # Delete load balancers first to release ELB-managed ENIs/public mappings.
    _delete_vpc_load_balancers()
    _delete_vpc_efs_mount_targets()

    # Delete route table associations/tables (except main). If a specific id is stale,
    # fall back to VPC discovery so termination remains retryable.
    route_tables: list[dict] = []
    if route_table_id:
        try:
            route_tables = ec2.describe_route_tables(RouteTableIds=[route_table_id]).get("RouteTables", [])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "InvalidRouteTableID.NotFound":
                raise
    if not route_tables:
        route_tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
            "RouteTables", []
        )

    for route_table in route_tables:
        for assoc in route_table.get("Associations", []):
            assoc_id = assoc.get("RouteTableAssociationId")
            if assoc_id and not assoc.get("Main"):
                try:
                    ec2.disassociate_route_table(AssociationId=assoc_id)
                except ClientError:
                    pass
        if any(a.get("Main") for a in route_table.get("Associations", []) or []):
            continue
        rt_id = route_table.get("RouteTableId")
        if rt_id:
            try:
                ec2.delete_route_table(RouteTableId=rt_id)
            except ClientError:
                pass

    # Security group and IGW detach can race with ENI/public-IP cleanup right after instance termination.
    security_group_targets: list[str] = []
    if security_group_id:
        security_group_targets.append(security_group_id)
    else:
        try:
            discovered_sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
                "SecurityGroups", []
            )
        except ClientError:
            discovered_sgs = []
        security_group_targets.extend(
            sg.get("GroupId")
            for sg in discovered_sgs
            if sg.get("GroupId") and sg.get("GroupName") != "default"
        )

    security_group_deleted = False
    for sg_target in security_group_targets:
        for attempt in range(1, 13):
            try:
                ec2.delete_security_group(GroupId=sg_target)
                security_group_deleted = True
                break
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code == "InvalidGroup.NotFound":
                    security_group_deleted = True
                    break
                if code != "DependencyViolation":
                    raise
                print(
                    f"  AWS teardown: security group still in use (attempt {attempt}/12); retrying in 5s..."
                )
                time.sleep(5)
        if not security_group_deleted:
            break

    if not security_group_deleted and security_group_targets:
        _force_cleanup_vpc_dependencies()
        try:
            ec2.delete_security_group(GroupId=security_group_targets[0])
            security_group_deleted = True
        except ClientError:
            pass

    _cleanup_vpc_public_addresses()
    igw_ids: list[str] = []
    if internet_gateway_id:
        igw_ids.append(internet_gateway_id)
    attached_igws = ec2.describe_internet_gateways(
        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
    ).get("InternetGateways", [])
    for igw in attached_igws:
        igw_id = igw.get("InternetGatewayId")
        if igw_id and igw_id not in igw_ids:
            igw_ids.append(igw_id)

    igw_detached = False
    detached_igw_ids: list[str] = []
    for igw_id in igw_ids:
        for attempt in range(1, 19):
            try:
                ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                igw_detached = True
                detached_igw_ids.append(igw_id)
                break
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in {"Gateway.NotAttached", "InvalidInternetGatewayID.NotFound"}:
                    igw_detached = True
                    break
                if code != "DependencyViolation":
                    raise
                _cleanup_vpc_public_addresses()
                if not _has_mapped_public_addresses():
                    print(
                        f"  AWS teardown: waiting for ENI/public-IP propagation before IGW detach (attempt {attempt}/18)..."
                    )
                    time.sleep(3)
                else:
                    print(
                        f"  AWS teardown: mapped public addresses still present in VPC (attempt {attempt}/18); retrying in 5s..."
                    )
                    time.sleep(5)

    if not igw_detached:
        _force_cleanup_vpc_dependencies()
        _cleanup_vpc_public_addresses()
        for attempt in range(1, 7):
            try:
                ec2.detach_internet_gateway(InternetGatewayId=igw_ids[0], VpcId=vpc_id)
                igw_detached = True
                detached_igw_ids.append(igw_ids[0])
                break
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "DependencyViolation":
                    raise
                print(
                    f"  AWS teardown: force cleanup done but IGW still attached (attempt {attempt}/6); retrying in 5s..."
                )
                time.sleep(5)

    if not igw_detached:
        raise RuntimeError(
            f"Unable to detach internet gateway from VPC {vpc_id} after force cleanup."
        )

    for igw_id in set(detached_igw_ids or igw_ids):
        try:
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {
                "InvalidInternetGatewayID.NotFound",
                "DependencyViolation",
            }:
                raise

    subnet_ids: list[str] = []
    if subnet_id:
        subnet_ids.append(subnet_id)
    discovered_subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
    for subnet in discovered_subnets:
        sid = subnet.get("SubnetId")
        if sid and sid not in subnet_ids:
            subnet_ids.append(sid)
    for sid in subnet_ids:
        try:
            ec2.delete_subnet(SubnetId=sid)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {
                "InvalidSubnetID.NotFound",
                "DependencyViolation",
            }:
                raise

    for attempt in range(1, 7):
        try:
            ec2.delete_vpc(VpcId=vpc_id)
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "InvalidVpcID.NotFound":
                return
            if code != "DependencyViolation":
                raise
            _force_cleanup_vpc_dependencies()
            if attempt < 6:
                print(
                    f"  AWS teardown: VPC still has dependencies after force cleanup (attempt {attempt}/6); retrying in 5s..."
                )
                time.sleep(5)

    raise RuntimeError(
        f"Unable to delete VPC {vpc_id} after retries. Remaining dependencies still exist."
    )


def resolve_ubuntu_ami(region: Optional[str] = None) -> str:
    """Resolve latest Ubuntu 22.04 LTS AMI ID."""
    ec2 = _client("ec2", region=region)
    response = ec2.describe_images(
        Owners=["099720109477"],
        Filters=[
            {
                "Name": "name",
                "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"],
            },
            {
                "Name": "virtualization-type",
                "Values": ["hvm"],
            },
        ],
    )
    images = sorted(response.get("Images", []), key=lambda x: x.get("CreationDate", ""), reverse=True)
    if not images:
        raise RuntimeError("No Ubuntu 22.04 AMI found in current region")
    return images[0]["ImageId"]


def get_aws_ec2_ondemand_hourly_prices(region: str) -> dict[str, float]:
    """Return On-Demand Linux shared hourly prices by EC2 instance type.

    Uses AWS Pricing API and returns a mapping:
      {"m7i.large": 0.1008, ...}
    """
    pricing = _client("pricing", region="us-east-1")

    paginator = pricing.get_paginator("get_products")
    pages = paginator.paginate(
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
            {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            {"Type": "TERM_MATCH", "Field": "licenseModel", "Value": "No License required"},
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Compute Instance"},
        ],
    )

    prices: dict[str, float] = {}
    for page in pages:
        for raw in page.get("PriceList", []):
            try:
                entry = json.loads(raw)
            except Exception:
                continue

            product = entry.get("product") or {}
            attrs = product.get("attributes") or {}
            instance_type = attrs.get("instanceType")
            if not instance_type:
                continue

            terms = (entry.get("terms") or {}).get("OnDemand") or {}
            for term in terms.values():
                dims = term.get("priceDimensions") or {}
                for dim in dims.values():
                    if (dim.get("unit") or "") != "Hrs":
                        continue
                    description = str(dim.get("description") or "")
                    # Keep actual instance usage dimensions and skip add-on/zero dimensions.
                    if "Instance Hour" not in description and "BoxUsage" not in description:
                        continue
                    usd = ((dim.get("pricePerUnit") or {}).get("USD") or "").strip()
                    if not usd:
                        continue
                    try:
                        price = float(usd)
                    except ValueError:
                        continue
                    if price <= 0:
                        continue
                    existing = prices.get(instance_type)
                    if existing is None or price < existing:
                        prices[instance_type] = price

    return prices


def _normalize_aws_resource_name(name: str, suffix: str, max_length: int = 32) -> str:
    """Normalize a resource name for ELBv2 constraints.

    Keeps lowercase alphanumeric and hyphen, trims duplicate hyphens,
    and ensures a stable suffix.
    """
    base = re.sub(r"[^a-zA-Z0-9-]", "-", (name or "").strip().lower())
    base = re.sub(r"-+", "-", base).strip("-")
    if not base:
        base = "vm"
    normalized_suffix = suffix.strip("-")
    reserved = len(normalized_suffix) + 1
    if len(base) > max_length - reserved:
        base = base[: max_length - reserved].rstrip("-")
    if not base:
        base = "vm"
    return f"{base}-{normalized_suffix}"


def _validate_acm_certificate_arn(certificate_arn: str, region: str) -> str:
    """Validate that a provided ACM certificate ARN exists in the target region."""
    cert_value = (certificate_arn or "").strip()
    if not cert_value:
        raise RuntimeError("ACM certificate ARN is required.")
    if not cert_value.startswith("arn:aws:acm:"):
        raise RuntimeError(
            "Invalid ACM certificate value. Use --certificate-arn with a full ACM certificate ARN."
        )

    acm = _client("acm", region=region)
    try:
        acm.describe_certificate(CertificateArn=cert_value)
        return cert_value
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            raise RuntimeError(
                f"ACM certificate ARN '{cert_value}' was not found in region '{region}'. "
                "ALB certificates must exist in the same region as the load balancer."
            ) from exc
        raise RuntimeError(f"Unable to validate ACM certificate ARN '{cert_value}': {exc}") from exc


def ensure_aws_alb_for_vm(
    vm_name: str,
    instance_id: str,
    vpc_id: str,
    certificate_arn: str,
    region: str,
    target_port: int = 80,
) -> dict:
    """Create or reuse an AWS ALB to expose HTTPS for a VM.

    Creates (or reuses):
    - ALB named <vm-name>-alb
    - target group named <vm-name>-tg (HTTP to instance target_port)
    - HTTPS listener on 443 forwarding to target group with ACM cert
    - HTTP listener on 80 redirecting to HTTPS 443
    """
    ec2 = _client("ec2", region=region)
    elbv2 = _client("elbv2", region=region)
    certificate_arn = _validate_acm_certificate_arn(certificate_arn, region)

    alb_name = _normalize_aws_resource_name(vm_name, "alb", max_length=32)
    tg_name = _normalize_aws_resource_name(vm_name, "tg", max_length=32)
    sg_name = _normalize_aws_resource_name(vm_name, "alb-sg", max_length=255)

    # 1) Security group for ALB
    try:
        existing_sgs = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [sg_name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("SecurityGroups", [])
        if existing_sgs:
            alb_sg_id = existing_sgs[0]["GroupId"]
        else:
            sg = ec2.create_security_group(
                GroupName=sg_name,
                Description=f"ALB security group for {vm_name}",
                VpcId=vpc_id,
            )
            alb_sg_id = sg["GroupId"]
    except ClientError as exc:
        raise RuntimeError(f"Unable to prepare ALB security group: {exc}") from exc

    try:
        ec2.authorize_security_group_ingress(
            GroupId=alb_sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
            ],
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "InvalidPermission.Duplicate":
            raise RuntimeError(f"Unable to authorize ALB security group ingress: {exc}") from exc

    # 2) Allow traffic from ALB SG to instance on target port.
    instance_desc = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = instance_desc.get("Reservations", [])
    instances = reservations[0].get("Instances", []) if reservations else []
    if not instances:
        raise RuntimeError(f"EC2 instance '{instance_id}' not found in region '{region}'.")
    instance = instances[0]
    instance_sgs = [sg.get("GroupId") for sg in instance.get("SecurityGroups", []) if sg.get("GroupId")]
    if not instance_sgs:
        raise RuntimeError(f"EC2 instance '{instance_id}' has no security groups attached.")

    for instance_sg_id in instance_sgs:
        try:
            ec2.authorize_security_group_ingress(
                GroupId=instance_sg_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": target_port,
                        "ToPort": target_port,
                        "UserIdGroupPairs": [{"GroupId": alb_sg_id}],
                    }
                ],
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "InvalidPermission.Duplicate":
                raise RuntimeError(
                    f"Unable to open instance security group '{instance_sg_id}' for ALB traffic: {exc}"
                ) from exc

    # 3) Create/reuse ALB in at least two subnets of the VPC.
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
    subnet_ids = [s.get("SubnetId") for s in subnets if s.get("SubnetId")]
    if len(subnet_ids) < 2:
        raise RuntimeError(
            f"VPC '{vpc_id}' needs at least two subnets to create an ALB (found {len(subnet_ids)})."
        )

    alb = None
    try:
        response = elbv2.describe_load_balancers(Names=[alb_name])
        lbs = response.get("LoadBalancers", [])
        if lbs:
            alb = lbs[0]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "LoadBalancerNotFound":
            raise RuntimeError(f"Unable to lookup ALB '{alb_name}': {exc}") from exc

    if not alb:
        created = elbv2.create_load_balancer(
            Name=alb_name,
            Subnets=subnet_ids[:2],
            SecurityGroups=[alb_sg_id],
            Scheme="internet-facing",
            Type="application",
            IpAddressType="ipv4",
            Tags=[{"Key": "Name", "Value": alb_name}, {"Key": "datalayer.io/component", "Value": "alb"}],
        )
        alb = created["LoadBalancers"][0]

    alb_arn = alb["LoadBalancerArn"]
    alb_dns_name = alb.get("DNSName", "")

    # 4) Create/reuse target group.
    tg_arn = None
    try:
        response = elbv2.describe_target_groups(Names=[tg_name])
        tgs = response.get("TargetGroups", [])
        if tgs:
            tg_arn = tgs[0]["TargetGroupArn"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TargetGroupNotFound":
            raise RuntimeError(f"Unable to lookup target group '{tg_name}': {exc}") from exc

    if not tg_arn:
        created_tg = elbv2.create_target_group(
            Name=tg_name,
            Protocol="HTTP",
            Port=target_port,
            VpcId=vpc_id,
            TargetType="instance",
            HealthCheckProtocol="HTTP",
            HealthCheckPath="/",
            HealthCheckPort="traffic-port",
            Matcher={"HttpCode": "200-399"},
            Tags=[{"Key": "Name", "Value": tg_name}],
        )
        tg_arn = created_tg["TargetGroups"][0]["TargetGroupArn"]

    elbv2.register_targets(
        TargetGroupArn=tg_arn,
        Targets=[{"Id": instance_id, "Port": target_port}],
    )

    # 5) Ensure listeners.
    listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn).get("Listeners", [])
    has_443 = any(l.get("Port") == 443 for l in listeners)
    has_80 = any(l.get("Port") == 80 for l in listeners)

    if not has_443:
        elbv2.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol="HTTPS",
            Port=443,
            Certificates=[{"CertificateArn": certificate_arn}],
            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
            SslPolicy="ELBSecurityPolicy-2016-08",
        )

    if not has_80:
        elbv2.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol="HTTP",
            Port=80,
            DefaultActions=[
                {
                    "Type": "redirect",
                    "RedirectConfig": {
                        "Protocol": "HTTPS",
                        "Port": "443",
                        "StatusCode": "HTTP_301",
                    },
                }
            ],
        )

    return {
        "alb_name": alb_name,
        "alb_arn": alb_arn,
        "alb_dns_name": alb_dns_name,
        "target_group_name": tg_name,
        "target_group_arn": tg_arn,
        "region": region,
        "instance_id": instance_id,
        "instance_security_groups": instance_sgs,
        "alb_security_group_id": alb_sg_id,
        "target_port": target_port,
        "certificate_arn": certificate_arn,
    }
