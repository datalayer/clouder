"""AWS cloud provider API helpers."""

from __future__ import annotations

import json
import os

from typing import Optional

import boto3

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
