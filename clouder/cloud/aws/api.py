"""AWS cloud provider API helpers."""

from __future__ import annotations

import json
import os
import re

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

    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock=subnet_cidr)
    subnet_id = subnet["Subnet"]["SubnetId"]

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

    # Delete route table associations (except main)
    route_table = ec2.describe_route_tables(RouteTableIds=[route_table_id])["RouteTables"][0]
    for assoc in route_table.get("Associations", []):
        assoc_id = assoc.get("RouteTableAssociationId")
        if assoc_id and not assoc.get("Main"):
            ec2.disassociate_route_table(AssociationId=assoc_id)

    ec2.delete_route_table(RouteTableId=route_table_id)
    ec2.delete_security_group(GroupId=security_group_id)
    ec2.detach_internet_gateway(InternetGatewayId=internet_gateway_id, VpcId=vpc_id)
    ec2.delete_internet_gateway(InternetGatewayId=internet_gateway_id)
    ec2.delete_subnet(SubnetId=subnet_id)
    ec2.delete_vpc(VpcId=vpc_id)


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
