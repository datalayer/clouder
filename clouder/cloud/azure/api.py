"""Azure cloud provider API."""

from typing import Optional

from .config import get_azure_credential, get_azure_subscription_id
from ...util.wait import wait_with_spinner


def _wait_poller(poller, description: str):
    """Wait on Azure SDK pollers with delayed spinner + elapsed time."""
    return wait_with_spinner(lambda: poller.result(), description)


def _get_subscription_client():
    """Get Azure SubscriptionClient."""
    from azure.mgmt.subscription import SubscriptionClient
    credential = get_azure_credential()
    return SubscriptionClient(credential)


def _get_resource_client(subscription_id: Optional[str] = None):
    """Get Azure ResourceManagementClient."""
    from azure.mgmt.resource import ResourceManagementClient
    credential = get_azure_credential()
    sub_id = subscription_id or get_azure_subscription_id()
    return ResourceManagementClient(credential, sub_id)


def _get_compute_client(subscription_id: Optional[str] = None):
    """Get Azure ComputeManagementClient."""
    from azure.mgmt.compute import ComputeManagementClient
    credential = get_azure_credential()
    sub_id = subscription_id or get_azure_subscription_id()
    return ComputeManagementClient(credential, sub_id)


def _get_network_client(subscription_id: Optional[str] = None):
    """Get Azure NetworkManagementClient."""
    from azure.mgmt.network import NetworkManagementClient
    credential = get_azure_credential()
    sub_id = subscription_id or get_azure_subscription_id()
    return NetworkManagementClient(credential, sub_id)


# --- Subscriptions ---

def list_azure_subscriptions() -> list:
    """List all Azure subscriptions accessible by the credential."""
    client = _get_subscription_client()
    # Resolve tenant_id from the tenants API (Subscription objects don't carry it)
    tenant_ids = [t.tenant_id for t in client.tenants.list()]
    default_tenant = tenant_ids[0] if tenant_ids else ""
    return [
        {
            "id": sub.subscription_id,
            "name": sub.display_name,
            "state": sub.state,
            "tenant_id": getattr(sub, "tenant_id", default_tenant),
        }
        for sub in client.subscriptions.list()
    ]


# --- Regions/Locations ---

def list_azure_locations(subscription_id: Optional[str] = None) -> list:
    """List all available Azure locations/regions."""
    client = _get_subscription_client()
    sub_id = subscription_id or get_azure_subscription_id()
    return [
        {
            "name": loc.name,
            "display_name": loc.display_name,
            "regional_display_name": getattr(loc, "regional_display_name", loc.display_name),
            "latitude": getattr(getattr(loc, "metadata", None), "latitude", None),
            "longitude": getattr(getattr(loc, "metadata", None), "longitude", None),
        }
        for loc in client.subscriptions.list_locations(sub_id)
    ]


# --- Resource Groups ---

def list_azure_resource_groups(subscription_id: Optional[str] = None) -> list:
    """List resource groups in the subscription."""
    client = _get_resource_client(subscription_id)
    return [
        {
            "name": rg.name,
            "location": rg.location,
            "provisioning_state": rg.properties.provisioning_state if rg.properties else None,
            "tags": rg.tags or {},
        }
        for rg in client.resource_groups.list()
    ]


def create_azure_resource_group(name: str, location: str, tags: Optional[dict] = None,
                                 subscription_id: Optional[str] = None) -> dict:
    """Create an Azure resource group."""
    client = _get_resource_client(subscription_id)
    from azure.mgmt.resource.resources.models import ResourceGroup
    rg = client.resource_groups.create_or_update(
        name,
        ResourceGroup(location=location, tags=tags or {}),
    )
    return {
        "name": rg.name,
        "location": rg.location,
        "provisioning_state": rg.properties.provisioning_state if rg.properties else None,
    }


# --- Resources per Region ---

def list_azure_resources(resource_group: Optional[str] = None,
                         subscription_id: Optional[str] = None) -> list:
    """List all resources, optionally filtered by resource group."""
    client = _get_resource_client(subscription_id)
    if resource_group:
        resources = client.resources.list_by_resource_group(resource_group)
    else:
        resources = client.resources.list()
    return [
        {
            "name": r.name,
            "type": r.type,
            "location": r.location,
            "resource_group": r.id.split("/")[4] if r.id and len(r.id.split("/")) > 4 else None,
            "provisioning_state": getattr(r, "provisioning_state", None),
            "tags": r.tags or {},
        }
        for r in resources
    ]


def list_azure_resources_by_region(region: Optional[str] = None,
                                    subscription_id: Optional[str] = None) -> dict:
    """List resources grouped by region. Optionally filter to a single region.

    Returns:
        dict mapping region name -> list of resources.
    """
    all_resources = list_azure_resources(subscription_id=subscription_id)
    by_region = {}
    for r in all_resources:
        loc = r["location"]
        if region and loc != region:
            continue
        by_region.setdefault(loc, []).append(r)
    return by_region


# --- VM Sizes ---

def list_azure_vm_sizes(location: str, subscription_id: Optional[str] = None) -> list:
    """List available VM sizes in a location."""
    client = _get_compute_client(subscription_id)
    return [
        {
            "name": size.name,
            "vcpus": size.number_of_cores,
            "memory_gb": round(size.memory_in_mb / 1024, 1),
            "max_data_disks": size.max_data_disk_count,
            "os_disk_size_gb": size.os_disk_size_in_mb // 1024 if size.os_disk_size_in_mb else None,
        }
        for size in client.virtual_machine_sizes.list(location)
    ]


# --- Virtual Machines ---

def list_azure_vms(resource_group: Optional[str] = None,
                   subscription_id: Optional[str] = None) -> list:
    """List virtual machines, optionally filtered by resource group."""
    client = _get_compute_client(subscription_id)
    if resource_group:
        vms = client.virtual_machines.list(resource_group)
    else:
        vms = client.virtual_machines.list_all()
    return [
        {
            "name": vm.name,
            "location": vm.location,
            "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
            "provisioning_state": vm.provisioning_state,
            "os_type": vm.storage_profile.os_disk.os_type if vm.storage_profile and vm.storage_profile.os_disk else None,
            "resource_group": vm.id.split("/")[4] if vm.id and len(vm.id.split("/")) > 4 else None,
            "tags": vm.tags or {},
        }
        for vm in vms
    ]


def create_azure_vm(
    resource_group: str,
    vm_name: str,
    location: str,
    vm_size: str = "Standard_B2s",
    admin_username: str = "azureuser",
    ssh_public_key: Optional[str] = None,
    image_publisher: str = "Canonical",
    image_offer: str = "0001-com-ubuntu-server-jammy",
    image_sku: str = "22_04-lts-gen2",
    image_version: str = "latest",
    subnet_id: Optional[str] = None,
    nsg_id: Optional[str] = None,
    os_disk_size_gb: Optional[int] = None,
    tags: Optional[dict] = None,
    subscription_id: Optional[str] = None,
) -> dict:
    """Create an Azure virtual machine with networking.

    This creates:
    1. A virtual network + subnet (if subnet_id not provided)
    2. An NSG with SSH rule (if nsg_id not provided)
    3. A public IP address
    4. A network interface (with NSG attached)
    5. The virtual machine

    Returns:
        dict with VM details.
    """
    compute_client = _get_compute_client(subscription_id)
    network_client = _get_network_client(subscription_id)

    # Ensure resource group exists
    resource_client = _get_resource_client(subscription_id)
    from azure.mgmt.resource.resources.models import ResourceGroup
    resource_client.resource_groups.create_or_update(
        resource_group,
        ResourceGroup(location=location, tags=tags or {}),
    )

    # 1. Create VNet + Subnet if not provided
    if not subnet_id:
        vnet_name = f"{vm_name}-vnet"
        subnet_name = f"{vm_name}-subnet"
        vnet_poller = network_client.virtual_networks.begin_create_or_update(
            resource_group,
            vnet_name,
            {
                "location": location,
                "address_space": {"address_prefixes": ["10.0.0.0/16"]},
                "subnets": [{"name": subnet_name, "address_prefix": "10.0.0.0/24"}],
            },
        )
        vnet = _wait_poller(vnet_poller, f"Creating virtual network {vnet_name}")
        subnet_id = vnet.subnets[0].id

    # 2. Create or reuse NSG with SSH rule
    if not nsg_id:
        nsg_name = f"{vm_name}-nsg"
        nsg_poller = network_client.network_security_groups.begin_create_or_update(
            resource_group,
            nsg_name,
            {
                "location": location,
                "security_rules": [
                    {
                        "name": "AllowSSH",
                        "protocol": "Tcp",
                        "source_port_range": "*",
                        "destination_port_range": "22",
                        "source_address_prefix": "*",
                        "destination_address_prefix": "*",
                        "access": "Allow",
                        "priority": 1000,
                        "direction": "Inbound",
                    },
                ],
            },
        )
        nsg = _wait_poller(nsg_poller, f"Creating network security group {nsg_name}")
        nsg_id = nsg.id

    # 3. Create Public IP
    ip_name = f"{vm_name}-ip"
    ip_poller = network_client.public_ip_addresses.begin_create_or_update(
        resource_group,
        ip_name,
        {
            "location": location,
            "sku": {"name": "Standard"},
            "public_ip_allocation_method": "Static",
        },
    )
    public_ip = _wait_poller(ip_poller, f"Creating public IP {ip_name}")

    # 4. Create NIC with NSG
    nic_name = f"{vm_name}-nic"
    nic_poller = network_client.network_interfaces.begin_create_or_update(
        resource_group,
        nic_name,
        {
            "location": location,
            "network_security_group": {"id": nsg_id},
            "enable_ip_forwarding": True,
            "ip_configurations": [
                {
                    "name": f"{vm_name}-ipconfig",
                    "subnet": {"id": subnet_id},
                    "public_ip_address": {"id": public_ip.id},
                }
            ],
        },
    )
    nic = _wait_poller(nic_poller, f"Creating network interface {nic_name}")

    # 4. Build VM parameters
    vm_params = {
        "location": location,
        "tags": tags or {},
        "hardware_profile": {"vm_size": vm_size},
        "storage_profile": {
            "image_reference": {
                "publisher": image_publisher,
                "offer": image_offer,
                "sku": image_sku,
                "version": image_version,
            },
            "os_disk": {
                "create_option": "FromImage",
                "managed_disk": {"storage_account_type": "Standard_LRS"},
                **({"disk_size_gb": os_disk_size_gb} if os_disk_size_gb else {}),
            },
        },
        "os_profile": {
            "computer_name": vm_name,
            "admin_username": admin_username,
        },
        "network_profile": {
            "network_interfaces": [{"id": nic.id}],
        },
    }

    # SSH key auth
    if ssh_public_key:
        vm_params["os_profile"]["linux_configuration"] = {
            "disable_password_authentication": True,
            "ssh": {
                "public_keys": [
                    {
                        "path": f"/home/{admin_username}/.ssh/authorized_keys",
                        "key_data": ssh_public_key,
                    }
                ]
            },
        }
    else:
        # Fall back to password (user should provide SSH key in production)
        import secrets
        password = secrets.token_urlsafe(20)
        vm_params["os_profile"]["admin_password"] = password

    # 5. Create VM
    vm_poller = compute_client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm_params,
    )
    vm = _wait_poller(vm_poller, f"Creating Azure VM {vm_name}")

    return {
        "name": vm.name,
        "location": vm.location,
        "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
        "provisioning_state": vm.provisioning_state,
        "public_ip": public_ip.ip_address,
        "resource_group": resource_group,
    }


def delete_azure_vm(resource_group: str, vm_name: str,
                     subscription_id: Optional[str] = None,
                     cleanup_resources: bool = True):
    """Delete an Azure virtual machine and optionally its associated resources.

    When cleanup_resources is True (default), discovers and deletes the VM's
    OS disk, data disks, NICs, and public IPs after the VM is deleted.
    Disks must be deleted after the VM since Azure won't allow deleting
    attached disks.
    """
    # Discover associated resources before deleting the VM
    resources = None
    if cleanup_resources:
        try:
            resources = get_azure_vm_associated_resources(
                resource_group, vm_name, subscription_id=subscription_id,
            )
        except Exception:
            resources = None

    # Delete the VM first (releases disk attachments)
    client = _get_compute_client(subscription_id)
    poller = client.virtual_machines.begin_delete(resource_group, vm_name)
    _wait_poller(poller, f"Deleting Azure VM {vm_name}")

    # Clean up associated resources
    if resources:
        network_client = _get_network_client(subscription_id)
        # Delete NICs (must happen before public IPs can be deleted)
        for nic_name in resources.get("nic_names", []):
            try:
                p = network_client.network_interfaces.begin_delete(resource_group, nic_name)
                _wait_poller(p, f"Deleting network interface {nic_name}")
            except Exception:
                pass
        # Delete public IPs
        for ip_name in resources.get("ip_names", []):
            try:
                p = network_client.public_ip_addresses.begin_delete(resource_group, ip_name)
                _wait_poller(p, f"Deleting public IP {ip_name}")
            except Exception:
                pass
        # Delete OS disk
        if resources.get("os_disk_name"):
            try:
                p = client.disks.begin_delete(resource_group, resources["os_disk_name"])
                _wait_poller(p, f"Deleting OS disk {resources['os_disk_name']}")
            except Exception:
                pass
        # Delete data disks
        for disk_name in resources.get("data_disk_names", []):
            try:
                p = client.disks.begin_delete(resource_group, disk_name)
                _wait_poller(p, f"Deleting data disk {disk_name}")
            except Exception:
                pass

    return {"deleted": True, "name": vm_name}


def get_azure_vm_public_ip(resource_group: str, vm_name: str,
                           subscription_id: Optional[str] = None) -> Optional[str]:
    """Get the public IP address of an Azure VM."""
    compute_client = _get_compute_client(subscription_id)
    network_client = _get_network_client(subscription_id)
    vm = compute_client.virtual_machines.get(resource_group, vm_name)
    if not vm.network_profile or not vm.network_profile.network_interfaces:
        return None
    nic_id = vm.network_profile.network_interfaces[0].id
    nic_name = nic_id.split("/")[-1]
    nic_rg = nic_id.split("/")[4]
    nic = network_client.network_interfaces.get(nic_rg, nic_name)
    for ip_config in nic.ip_configurations or []:
        if ip_config.public_ip_address:
            pip_id = ip_config.public_ip_address.id
            pip_name = pip_id.split("/")[-1]
            pip_rg = pip_id.split("/")[4]
            pip = network_client.public_ip_addresses.get(pip_rg, pip_name)
            if pip.ip_address:
                return pip.ip_address
    return None


def get_azure_vm_associated_resources(resource_group: str, vm_name: str,
                                       subscription_id: Optional[str] = None) -> dict:
    """Get names of resources associated with a VM (OS disk, data disks, NICs, public IPs).

    Returns:
        dict with keys: os_disk_name, data_disk_names, nic_names, ip_names.
    """
    compute_client = _get_compute_client(subscription_id)
    network_client = _get_network_client(subscription_id)

    vm = compute_client.virtual_machines.get(resource_group, vm_name)

    os_disk_name = None
    if vm.storage_profile and vm.storage_profile.os_disk:
        os_disk_name = vm.storage_profile.os_disk.name

    data_disk_names = []
    if vm.storage_profile and vm.storage_profile.data_disks:
        for dd in vm.storage_profile.data_disks:
            if dd.name:
                data_disk_names.append(dd.name)

    nic_names = []
    ip_names = []
    if vm.network_profile and vm.network_profile.network_interfaces:
        for nic_ref in vm.network_profile.network_interfaces:
            nic_name = nic_ref.id.split("/")[-1]
            nic_names.append(nic_name)
            nic_rg = nic_ref.id.split("/")[4]
            nic = network_client.network_interfaces.get(nic_rg, nic_name)
            for ip_config in (nic.ip_configurations or []):
                if ip_config.public_ip_address:
                    ip_names.append(ip_config.public_ip_address.id.split("/")[-1])

    return {
        "os_disk_name": os_disk_name,
        "data_disk_names": data_disk_names,
        "nic_names": nic_names,
        "ip_names": ip_names,
    }


def delete_azure_nic(resource_group: str, nic_name: str,
                     subscription_id: Optional[str] = None):
    """Delete a network interface."""
    client = _get_network_client(subscription_id)
    poller = client.network_interfaces.begin_delete(resource_group, nic_name)
    _wait_poller(poller, f"Deleting network interface {nic_name}")


def delete_azure_public_ip(resource_group: str, ip_name: str,
                            subscription_id: Optional[str] = None):
    """Delete a public IP address."""
    client = _get_network_client(subscription_id)
    poller = client.public_ip_addresses.begin_delete(resource_group, ip_name)
    _wait_poller(poller, f"Deleting public IP {ip_name}")


def delete_azure_disk(resource_group: str, disk_name: str,
                      subscription_id: Optional[str] = None):
    """Delete a managed disk."""
    client = _get_compute_client(subscription_id)
    poller = client.disks.begin_delete(resource_group, disk_name)
    _wait_poller(poller, f"Deleting disk {disk_name}")


def delete_azure_nsg(resource_group: str, nsg_name: str,
                     subscription_id: Optional[str] = None):
    """Delete a network security group."""
    client = _get_network_client(subscription_id)
    poller = client.network_security_groups.begin_delete(resource_group, nsg_name)
    _wait_poller(poller, f"Deleting network security group {nsg_name}")


def delete_azure_vnet(resource_group: str, vnet_name: str,
                      subscription_id: Optional[str] = None):
    """Delete a virtual network (and all its subnets)."""
    client = _get_network_client(subscription_id)
    poller = client.virtual_networks.begin_delete(resource_group, vnet_name)
    _wait_poller(poller, f"Deleting virtual network {vnet_name}")


# --- Load Balancer ---

def create_azure_load_balancer(
    resource_group: str,
    lb_name: str,
    location: str,
    public_ip_name: str,
    frontend_name: str = "lb-frontend",
    backend_pool_name: str = "lb-backend-pool",
    subscription_id: Optional[str] = None,
) -> dict:
    """Create an Azure Load Balancer with a public IP, frontend, and backend pool.

    Returns:
        dict with lb_id, public_ip, frontend_id, backend_pool_id.
    """
    network_client = _get_network_client(subscription_id)

    # Create a public IP for the load balancer
    ip_poller = network_client.public_ip_addresses.begin_create_or_update(
        resource_group,
        public_ip_name,
        {
            "location": location,
            "sku": {"name": "Standard"},
            "public_ip_allocation_method": "Static",
        },
    )
    public_ip = _wait_poller(ip_poller, f"Creating load balancer IP {public_ip_name}")

    # Build LB parameters
    frontend_id = (
        f"/subscriptions/{subscription_id or get_azure_subscription_id()}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.Network"
        f"/loadBalancers/{lb_name}/frontendIPConfigurations/{frontend_name}"
    )

    lb_params = {
        "location": location,
        "sku": {"name": "Standard"},
        "frontend_ip_configurations": [
            {
                "name": frontend_name,
                "public_ip_address": {"id": public_ip.id},
            }
        ],
        "backend_address_pools": [
            {"name": backend_pool_name}
        ],
        "probes": [
            {
                "name": "http-probe",
                "protocol": "Tcp",
                "port": 80,
                "interval_in_seconds": 15,
                "number_of_probes": 2,
            },
            {
                "name": "https-probe",
                "protocol": "Tcp",
                "port": 443,
                "interval_in_seconds": 15,
                "number_of_probes": 2,
            },
        ],
        "load_balancing_rules": [
            {
                "name": "http-rule",
                "protocol": "Tcp",
                "frontend_port": 80,
                "backend_port": 80,
                "frontend_ip_configuration": {"id": frontend_id},
                "backend_address_pool": {
                    "id": (
                        f"/subscriptions/{subscription_id or get_azure_subscription_id()}"
                        f"/resourceGroups/{resource_group}/providers/Microsoft.Network"
                        f"/loadBalancers/{lb_name}/backendAddressPools/{backend_pool_name}"
                    )
                },
                "probe": {
                    "id": (
                        f"/subscriptions/{subscription_id or get_azure_subscription_id()}"
                        f"/resourceGroups/{resource_group}/providers/Microsoft.Network"
                        f"/loadBalancers/{lb_name}/probes/http-probe"
                    )
                },
                "idle_timeout_in_minutes": 15,
                "enable_tcp_reset": True,
            },
            {
                "name": "https-rule",
                "protocol": "Tcp",
                "frontend_port": 443,
                "backend_port": 443,
                "frontend_ip_configuration": {"id": frontend_id},
                "backend_address_pool": {
                    "id": (
                        f"/subscriptions/{subscription_id or get_azure_subscription_id()}"
                        f"/resourceGroups/{resource_group}/providers/Microsoft.Network"
                        f"/loadBalancers/{lb_name}/backendAddressPools/{backend_pool_name}"
                    )
                },
                "probe": {
                    "id": (
                        f"/subscriptions/{subscription_id or get_azure_subscription_id()}"
                        f"/resourceGroups/{resource_group}/providers/Microsoft.Network"
                        f"/loadBalancers/{lb_name}/probes/https-probe"
                    )
                },
                "idle_timeout_in_minutes": 15,
                "enable_tcp_reset": True,
            },
        ],
    }

    lb_poller = network_client.load_balancers.begin_create_or_update(
        resource_group, lb_name, lb_params,
    )
    lb = _wait_poller(lb_poller, f"Creating load balancer {lb_name}")

    # Extract backend pool ID from created LB
    backend_pool_id = None
    for pool in (lb.backend_address_pools or []):
        if pool.name == backend_pool_name:
            backend_pool_id = pool.id
            break

    return {
        "lb_id": lb.id,
        "lb_name": lb.name,
        "public_ip": public_ip.ip_address,
        "public_ip_id": public_ip.id,
        "frontend_id": lb.frontend_ip_configurations[0].id if lb.frontend_ip_configurations else None,
        "backend_pool_id": backend_pool_id,
    }


def add_nic_to_lb_backend_pool(
    resource_group: str,
    nic_name: str,
    backend_pool_id: str,
    subscription_id: Optional[str] = None,
):
    """Add a NIC's primary IP configuration to a load balancer backend pool."""
    network_client = _get_network_client(subscription_id)
    nic = network_client.network_interfaces.get(resource_group, nic_name)

    nic_params = {
        "location": nic.location,
        "ip_configurations": [],
    }
    if nic.network_security_group:
        nic_params["network_security_group"] = {"id": nic.network_security_group.id}

    for ip_config in (nic.ip_configurations or []):
        ip_conf_dict = {
            "name": ip_config.name,
            "private_ip_allocation_method": ip_config.private_ip_allocation_method,
        }
        if ip_config.subnet:
            ip_conf_dict["subnet"] = {"id": ip_config.subnet.id}
        if ip_config.public_ip_address:
            ip_conf_dict["public_ip_address"] = {"id": ip_config.public_ip_address.id}

        # Collect existing backend pools
        pool_ids = [p.id for p in (ip_config.load_balancer_backend_address_pools or [])]
        # Add our pool if this is the primary IP config and not already present
        if (ip_config.primary or len(nic.ip_configurations) == 1) and backend_pool_id not in pool_ids:
            pool_ids.append(backend_pool_id)
        if pool_ids:
            ip_conf_dict["load_balancer_backend_address_pools"] = [{"id": pid} for pid in pool_ids]

        nic_params["ip_configurations"].append(ip_conf_dict)

    poller = network_client.network_interfaces.begin_create_or_update(
        resource_group, nic_name, nic_params,
    )
    _wait_poller(poller, f"Updating network interface {nic_name}")


def delete_azure_load_balancer(resource_group: str, lb_name: str,
                                subscription_id: Optional[str] = None):
    """Delete a load balancer."""
    client = _get_network_client(subscription_id)
    poller = client.load_balancers.begin_delete(resource_group, lb_name)
    _wait_poller(poller, f"Deleting load balancer {lb_name}")
