"""Shared helpers, constants, and script fragments for kubeadm commands."""

import json
import subprocess

import typer
from rich import print
from rich.prompt import Prompt

from ..ctx import get_current_context, get_default_ssh_key
from ...util.utils import CLOUDER_CLUSTERS_FOLDER, CLOUDER_KUBECONFIGS_FOLDER, SSH_FOLDER

# Kubernetes version to install
K8S_VERSION = "1.32"


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

# Preamble injected into every SSH command to ensure binaries and kubeconfig
# are found in non-interactive / non-login shells.
_SSH_PREAMBLE = (
    'export PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin:$PATH"; '
    'export KUBECONFIG="$HOME/.kube/config"; '
)


def _ssh_cmd(ip: str, user: str, key_path: str, command: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command on a remote host via SSH."""
    ssh_args = [
        "ssh", "-i", key_path,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        f"{user}@{ip}",
        _SSH_PREAMBLE + command,
    ]
    return subprocess.run(ssh_args, check=check, capture_output=True, text=True)


def _ssh_cmd_stream(ip: str, user: str, key_path: str, command: str) -> int:
    """Run a command on a remote host via SSH, streaming output to stdout/stderr."""
    ssh_args = [
        "ssh", "-i", key_path,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        f"{user}@{ip}",
        _SSH_PREAMBLE + command,
    ]
    result = subprocess.run(ssh_args)
    return result.returncode


# ---------------------------------------------------------------------------
# Cluster VM resolution
# ---------------------------------------------------------------------------

def _resolve_cluster_vms(cluster_name: str):
    """Resolve all VMs belonging to a kubeadm cluster, returning (master, workers) with IPs."""
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        from ...cloud.azure.api import list_azure_vms, get_azure_vm_public_ip
        vms = list_azure_vms(subscription_id=context_id)

        master_name = f"{cluster_name}-master"
        master_vm = next((vm for vm in vms if vm["name"] == master_name), None)
        if not master_vm:
            typer.echo(f"Master VM '{master_name}' not found.", err=True)
            raise typer.Exit(1)

        worker_vms = sorted(
            [vm for vm in vms if vm["name"].startswith(f"{cluster_name}-node-")],
            key=lambda v: v["name"],
        )

        master_ip = get_azure_vm_public_ip(
            master_vm["resource_group"], master_name, subscription_id=context_id
        )
        workers = []
        for wvm in worker_vms:
            wip = get_azure_vm_public_ip(
                wvm["resource_group"], wvm["name"], subscription_id=context_id
            )
            workers.append({"name": wvm["name"], "ip": wip, "resource_group": wvm["resource_group"]})

        return {
            "master": {"name": master_name, "ip": master_ip, "resource_group": master_vm["resource_group"]},
            "workers": workers,
            "context_id": context_id,
        }

    if cloud == "aws":
        from ...cloud.aws.api import list_aws_vms

        vms = list_aws_vms()
        master_name = f"{cluster_name}-master"
        master_vm = next((vm for vm in vms if vm["name"] == master_name), None)
        if not master_vm:
            typer.echo(f"Master VM '{master_name}' not found.", err=True)
            raise typer.Exit(1)

        worker_vms = sorted(
            [vm for vm in vms if vm["name"].startswith(f"{cluster_name}-node-")],
            key=lambda v: v["name"],
        )

        workers = []
        for wvm in worker_vms:
            workers.append({
                "name": wvm["name"],
                "ip": wvm.get("public_ip"),
                "instance_id": wvm.get("id"),
                "region": wvm.get("region"),
            })

        return {
            "master": {
                "name": master_name,
                "ip": master_vm.get("public_ip"),
                "instance_id": master_vm.get("id"),
                "region": master_vm.get("region"),
            },
            "workers": workers,
            "context_id": context_id,
        }

    typer.echo("Kubeadm commands are currently only supported for Azure and AWS.", err=True)
    raise typer.Exit(1)


def _resolve_ssh_key_for_cluster(cluster_name: str) -> str:
    """Find the SSH key for a cluster."""
    # Try cluster-specific key first
    cluster_key = SSH_FOLDER / f"{cluster_name}-key"
    if cluster_key.exists():
        return str(cluster_key)
    # Try configured default key
    default_key = get_default_ssh_key()
    if default_key:
        default_path = SSH_FOLDER / default_key
        if default_path.exists():
            print(f"[dim]Using default SSH key: {default_path}[/dim]")
            return str(default_path)
    # Try common keys
    from ...cloud.local.api import get_local_ssh_keys
    local_keys = get_local_ssh_keys()
    if len(local_keys) == 1:
        return str(SSH_FOLDER / local_keys[0])
    if local_keys:
        print("\n[bold]SSH keys:[/bold]")
        for i, kn in enumerate(local_keys, 1):
            typer.echo(f"  {i}. {kn}")
        choice = Prompt.ask("Select SSH key for cluster access", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(local_keys):
            return str(SSH_FOLDER / local_keys[int(choice) - 1])
    typer.echo("No SSH key found. Cannot connect to cluster VMs.", err=True)
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Cluster metadata persistence (~/.clouder/clusters/<name>.json)
# ---------------------------------------------------------------------------

def _cluster_metadata_path(cluster_name: str):
    """Return the path to the cluster metadata JSON file."""
    return CLOUDER_CLUSTERS_FOLDER / f"{cluster_name}.json"


def _save_cluster_metadata(cluster_name: str, metadata: dict):
    """Save cluster metadata to disk."""
    from datetime import datetime, timezone
    CLOUDER_CLUSTERS_FOLDER.mkdir(parents=True, exist_ok=True)
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "created_at" not in metadata:
        metadata["created_at"] = metadata["updated_at"]
    path = _cluster_metadata_path(cluster_name)
    path.write_text(json.dumps(metadata, indent=2))
    path.chmod(0o600)
    print(f"[dim]Cluster metadata saved to {path}[/dim]")


def _load_cluster_metadata(cluster_name: str) -> dict | None:
    """Load cluster metadata from disk. Returns None if not found."""
    path = _cluster_metadata_path(cluster_name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _update_cluster_metadata(cluster_name: str, updates: dict):
    """Merge updates into existing cluster metadata."""
    metadata = _load_cluster_metadata(cluster_name) or {}
    metadata.update(updates)
    _save_cluster_metadata(cluster_name, metadata)


def _delete_cluster_metadata(cluster_name: str):
    """Remove cluster metadata file if it exists."""
    path = _cluster_metadata_path(cluster_name)
    if path.exists():
        path.unlink()
        typer.echo(f"  Removed cluster metadata: {path}")


# ---------------------------------------------------------------------------
# Script fragments for remote setup
# ---------------------------------------------------------------------------

_SCRIPT_PREREQS = f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# --- Disable swap ---
sudo swapoff -a
sudo sed -i '/swap/d' /etc/fstab

# --- Kernel modules for containerd ---
cat <<MEOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
MEOF
sudo modprobe overlay
sudo modprobe br_netfilter

# --- Sysctl for Kubernetes networking ---
cat <<SEOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
SEOF
sudo sysctl --system > /dev/null 2>&1

# --- Install containerd 2.x from Docker official repo ---
# Remove old Ubuntu containerd if present
sudo apt-get remove -y -qq containerd 2>/dev/null || true
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apt-transport-https ca-certificates curl gpg > /dev/null
# Add Docker GPG key and repo
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --yes --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq containerd.io > /dev/null
echo "containerd version: $(containerd --version)"

# --- Configure containerd with SystemdCgroup + CRIU checkpoint support ---
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl restart containerd
sudo systemctl enable containerd

# --- Install CRIU 4.x from PPA (pidfd + pidfd_store support) ---
sudo add-apt-repository -y ppa:criu/ppa > /dev/null 2>&1
sudo apt-get update -qq > /dev/null 2>&1
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq criu > /dev/null
echo "CRIU installed: $(sudo criu --version 2>&1 | head -1)"
sudo criu check && echo "CRIU check: OK" || echo "CRIU check: WARNING - some features may not work (this is expected in unprivileged containers)"

# --- Install runc wrapper for CRIU --tcp-established ---
# CRIU in swrk (RPC) mode ignores /etc/criu/default.conf, so the
# --tcp-established flag must be injected at the runc level.
# We replace /usr/bin/runc with a thin wrapper that adds the flag
# only for checkpoint commands; all other runc invocations pass through.
if [ ! -f /usr/bin/runc.real ]; then
    sudo cp /usr/bin/runc /usr/bin/runc.real
fi
cat <<'RUNC_WRAPPER' | sudo tee /usr/bin/runc > /dev/null
#!/bin/bash
REAL_RUNC=/usr/bin/runc.real
ARGS=()
INJECT=false
for arg in "$@"; do
  ARGS+=("$arg")
  if [ "$arg" = "checkpoint" ]; then
    ARGS+=("--tcp-established")
    INJECT=true
  fi
done
if $INJECT; then
  exec "$REAL_RUNC" "${{ARGS[@]}}"
fi
exec "$REAL_RUNC" "$@"
RUNC_WRAPPER
sudo chmod +x /usr/bin/runc
echo "runc wrapper installed (--tcp-established for checkpoint)."

# --- Disable io_uring for unprivileged processes ---
# CRIU cannot checkpoint io_uring rings. Setting io_uring_disabled=2 forces
# all userspace (Node.js libuv, Go netpoller, etc.) to fall back to epoll.
sudo sysctl -w kernel.io_uring_disabled=2 > /dev/null 2>&1 || true
echo 'kernel.io_uring_disabled = 2' | sudo tee /etc/sysctl.d/99-disable-io-uring.conf > /dev/null
echo "io_uring disabled for unprivileged processes."

# --- Install buildah (for CRIU checkpoint-to-image conversion) ---
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq buildah > /dev/null
echo "buildah installed: $(buildah --version 2>&1 | head -1)"

# --- Install kubeadm, kubelet, kubectl ---
sudo mkdir -p /etc/apt/keyrings
sudo rm -f /etc/apt/keyrings/kubernetes-apt-keyring.gpg
curl -fsSL https://pkgs.k8s.io/core:/stable:/v{K8S_VERSION}/deb/Release.key | \\
    sudo gpg --yes --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg 2>/dev/null
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v{K8S_VERSION}/deb/ /' | \\
    sudo tee /etc/apt/sources.list.d/kubernetes.list > /dev/null
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq kubelet kubeadm kubectl > /dev/null
sudo apt-mark hold kubelet kubeadm kubectl containerd.io

echo "Prerequisites installed successfully."
"""

_SCRIPT_KUBEADM_INIT = """
set -euo pipefail

PRIVATE_IP=$(hostname -I | awk '{print $1}')

# --- Ensure containerd is running and socket is ready ---
sudo systemctl restart containerd
echo "Waiting for containerd socket..."
for i in $(seq 1 30); do
    if [ -S /var/run/containerd/containerd.sock ] && sudo ctr --connect-timeout 2s version >/dev/null 2>&1; then
        echo "containerd is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: containerd did not become ready after 30s"
        sudo systemctl status containerd || true
        exit 1
    fi
    sleep 1
done

# --- Reset any previous kubeadm state (idempotent re-runs) ---
if [ -f /etc/kubernetes/manifests/kube-apiserver.yaml ]; then
    echo "Previous kubeadm state detected, resetting..."
    sudo kubeadm reset -f --cri-socket unix:///var/run/containerd/containerd.sock 2>/dev/null || true
    sudo rm -rf /etc/cni/net.d $HOME/.kube/config
    # Wait for containerd to recover after reset.
    sleep 3
    sudo systemctl restart containerd
    for i in $(seq 1 15); do
        if [ -S /var/run/containerd/containerd.sock ] && sudo ctr --connect-timeout 2s version >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
fi

# --- kubeadm init ---
sudo kubeadm init \
    --pod-network-cidr=10.244.0.0/16 \
    --apiserver-advertise-address=$PRIVATE_IP \
    --apiserver-cert-extra-sans=$PRIVATE_IP,PUBLIC_IP_PLACEHOLDER

# --- Setup kubeconfig for the admin user ---
mkdir -p $HOME/.kube
sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config

# --- Enable ContainerCheckpoint feature gate on kubelet ---
KUBELET_CONF=/var/lib/kubelet/config.yaml
if sudo grep -q "featureGates:" $KUBELET_CONF; then
    if ! sudo grep -q "ContainerCheckpoint" $KUBELET_CONF; then
        sudo sed -i '/featureGates:/a\\  ContainerCheckpoint: true' $KUBELET_CONF
    fi
else
    echo -e "featureGates:\\n  ContainerCheckpoint: true" | sudo tee -a $KUBELET_CONF > /dev/null
fi
sudo systemctl restart kubelet

# --- Generate join command ---
kubeadm token create --print-join-command 2>/dev/null
"""

# NOTE: Calico CNI code preserved below — currently broken on Azure same-subnet
# VNets because VXLANCrossSubnet uses direct routing when all nodes share a
# subnet, and Azure drops packets unless IP-forwarding is enabled on every NIC.
# To re-enable Calico, uncomment the block below and change
# --pod-network-cidr back to 192.168.0.0/16 in _SCRIPT_KUBEADM_INIT.
#
# _SCRIPT_INSTALL_CNI_CALICO = """
# set -euo pipefail
#
# # Install Calico CNI (Tigera operator)
# kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.29.3/manifests/tigera-operator.yaml 2>/dev/null || \
#     kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.29.3/manifests/tigera-operator.yaml
# kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.29.3/manifests/custom-resources.yaml 2>/dev/null || \
#     kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.29.3/manifests/custom-resources.yaml
# echo "Calico CNI installed (Tigera operator + custom resources)."
# echo "Waiting for Calico pods to start..."
# kubectl -n calico-system wait --for=condition=Ready pod -l k8s-app=calico-node --timeout=120s 2>/dev/null || \
#     echo "Calico pods not ready yet (may take a few minutes)."
# """

_SCRIPT_INSTALL_CNI = """
set -euo pipefail

# Install Flannel CNI
kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
echo "Flannel CNI installed."
echo "Waiting for Flannel pods to start..."
kubectl -n kube-flannel wait --for=condition=Ready pod -l app=flannel --timeout=120s 2>/dev/null || \
    echo "Flannel pods not ready yet (may take a few minutes)."
"""

_SCRIPT_WORKER_FEATURE_GATE = """
set -euo pipefail
# Enable ContainerCheckpoint feature gate on worker kubelet
KUBELET_CONF=/var/lib/kubelet/config.yaml
if sudo grep -q "featureGates:" $KUBELET_CONF; then
    if ! sudo grep -q "ContainerCheckpoint" $KUBELET_CONF; then
        sudo sed -i '/featureGates:/a\\  ContainerCheckpoint: true' $KUBELET_CONF
    fi
else
    echo -e "featureGates:\\n  ContainerCheckpoint: true" | sudo tee -a $KUBELET_CONF > /dev/null
fi
sudo systemctl restart kubelet
echo "ContainerCheckpoint feature gate enabled."

# Install runc wrapper for CRIU --tcp-established (idempotent)
if [ ! -f /usr/bin/runc.real ]; then
    sudo cp /usr/bin/runc /usr/bin/runc.real
fi
cat <<'RUNC_WRAPPER' | sudo tee /usr/bin/runc > /dev/null
#!/bin/bash
REAL_RUNC=/usr/bin/runc.real
ARGS=()
INJECT=false
for arg in "$@"; do
  ARGS+=("$arg")
  if [ "$arg" = "checkpoint" ]; then
    ARGS+=("--tcp-established")
    INJECT=true
  fi
done
if $INJECT; then
  exec "$REAL_RUNC" "${ARGS[@]}"
fi
exec "$REAL_RUNC" "$@"
RUNC_WRAPPER
sudo chmod +x /usr/bin/runc
echo "runc wrapper installed (--tcp-established for checkpoint)."

# Disable io_uring for unprivileged processes (CRIU cannot checkpoint io_uring)
sudo sysctl -w kernel.io_uring_disabled=2 > /dev/null 2>&1 || true
echo 'kernel.io_uring_disabled = 2' | sudo tee /etc/sysctl.d/99-disable-io-uring.conf > /dev/null
echo "io_uring disabled for unprivileged processes."
"""

# ---------------------------------------------------------------------------
# Azure Disk CSI driver helpers
# ---------------------------------------------------------------------------

def _build_azure_cloud_config(
    tenant_id: str, subscription_id: str, resource_group: str, location: str,
    client_id: str, client_secret: str,
    vnet_name: str, subnet_name: str, nsg_name: str,
) -> str:
    """Build the /etc/kubernetes/azure.json cloud-provider config."""
    return json.dumps({
        "cloud": "AzurePublicCloud",
        "tenantId": tenant_id,
        "subscriptionId": subscription_id,
        "resourceGroup": resource_group,
        "location": location,
        "aadClientId": client_id,
        "aadClientSecret": client_secret,
        "vnetName": vnet_name,
        "vnetResourceGroup": resource_group,
        "subnetName": subnet_name,
        "securityGroupName": nsg_name,
        "vmType": "standard",
    }, indent=2)


def _get_or_create_azure_sp(subscription_id: str, resource_group: str, cluster_name: str):
    """Get Azure SP credentials from env vars or create a new SP scoped to the resource group.

    Returns (tenant_id, client_id, client_secret).  Any element may be None on failure.
    """
    import os

    tenant_id = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")

    # Try to get tenant ID from Azure CLI if not in env.
    if not tenant_id:
        result = subprocess.run(
            ["az", "account", "show", "--query", "tenantId", "-o", "tsv"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            tenant_id = result.stdout.strip()

    # If all three are already available, return them.
    if tenant_id and client_id and client_secret:
        return tenant_id, client_id, client_secret

    if not tenant_id:
        return None, None, None

    # Create a new service principal scoped to the cluster resource group.
    print(f"  [dim]Creating Azure service principal for disk provisioning (scoped to {resource_group})...[/dim]")
    result = subprocess.run(
        [
            "az", "ad", "sp", "create-for-rbac",
            "--name", f"clouder-{cluster_name}-csi",
            "--role", "Contributor",
            "--scopes", f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}",
            "-o", "json",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  [red]Failed to create service principal: {result.stderr.strip()}[/red]")
        return tenant_id, None, None

    sp_data = json.loads(result.stdout)
    return sp_data.get("tenant", tenant_id), sp_data["appId"], sp_data["password"]


_SCRIPT_INSTALL_AZURE_DISK_CSI = """
set -euo pipefail

# --- Install Azure Disk CSI driver (with snapshot support) ---
curl -skSL https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/v1.30.3/deploy/install-driver.sh | bash -s v1.30.3 snapshot --

echo "Waiting for Azure Disk CSI controller to start..."
kubectl -n kube-system wait --for=condition=Ready pod -l app=csi-azuredisk-controller --timeout=180s 2>/dev/null || \
    echo "CSI controller pods not ready yet (may take a few minutes)."

# --- Create default StorageClass using Azure Managed Disk (StandardSSD) ---
cat <<'SCEOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: managed-csi
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: disk.csi.azure.com
parameters:
  skuName: StandardSSD_LRS
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
SCEOF

echo "Azure Disk CSI driver installed."
echo "StorageClass 'managed-csi' set as default."
kubectl get storageclass
"""


_SCRIPT_INSTALL_AZURE_FILE_CSI = """
set -euo pipefail

# --- Install Azure File CSI driver ---
curl -skSL https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/v1.30.6/deploy/install-driver.sh | bash -s v1.30.6 --

echo "Waiting for Azure File CSI controller to start..."
kubectl -n kube-system wait --for=condition=Ready pod -l app=csi-azurefile-controller --timeout=180s 2>/dev/null || \
    echo "CSI controller pods not ready yet (may take a few minutes)."

echo "Azure File CSI driver installed."
kubectl get storageclass
"""


def _build_azure_nfs_storageclass_script(
    subscription_id: str, resource_group: str, location: str,
) -> str:
    """Return a bash script that creates the azure-nfs StorageClass with baked-in Azure params."""
    return f"""set -euo pipefail
cat <<'SCEOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: azure-nfs
provisioner: file.csi.azure.com
allowVolumeExpansion: true
parameters:
  shareName: datalayer-shared-filesystem
  skuName: Premium_LRS
  protocol: nfs
  subscriptionID: {subscription_id}
  resourceGroup: {resource_group}
  location: {location}
SCEOF
echo "StorageClass 'azure-nfs' created."
kubectl get storageclass
"""


def _build_aws_ebs_csi_setup_script(
    region: str,
    use_instance_profile: bool = False,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
) -> str:
    """Return a bash script that installs AWS EBS CSI and configures default gp3 StorageClass."""
    import base64

    access_key_b64 = base64.b64encode((access_key_id or "").encode()).decode()
    secret_key_b64 = base64.b64encode((secret_access_key or "").encode()).decode()
    session_token_b64 = base64.b64encode((session_token or "").encode()).decode()

    # Use kustomize install for the driver. Prefer node IAM role (instance profile)
    # and only inject static credentials when explicit fallback is required.
    credential_block = """
kubectl -n kube-system create secret generic aws-cloud-credentials \\
    --from-literal=AWS_ACCESS_KEY_ID=\"$AWS_ACCESS_KEY_ID\" \\
    --from-literal=AWS_SECRET_ACCESS_KEY=\"$AWS_SECRET_ACCESS_KEY\" \\
    --from-literal=AWS_SESSION_TOKEN=\"$AWS_SESSION_TOKEN\" \\
    --dry-run=client -o yaml | kubectl apply -f -

kubectl -n kube-system set env deployment/ebs-csi-controller \\
    --containers=ebs-plugin --from=secret/aws-cloud-credentials
"""
    if use_instance_profile:
        credential_block = """
# Ensure static credentials are not forced when using instance profile auth.
kubectl -n kube-system set env deployment/ebs-csi-controller \\
    --containers=ebs-plugin AWS_ACCESS_KEY_ID- AWS_SECRET_ACCESS_KEY- AWS_SESSION_TOKEN- || true
"""

    return f"""set -euo pipefail

kubectl apply -k \"github.com/kubernetes-sigs/aws-ebs-csi-driver/deploy/kubernetes/overlays/stable/?ref=release-1.39\"

AWS_ACCESS_KEY_ID=\"$(echo '{access_key_b64}' | base64 -d)\"
AWS_SECRET_ACCESS_KEY=\"$(echo '{secret_key_b64}' | base64 -d)\"
AWS_SESSION_TOKEN=\"$(echo '{session_token_b64}' | base64 -d)\"

{credential_block}

kubectl -n kube-system set env deployment/ebs-csi-controller \\
    --containers=ebs-plugin AWS_REGION={region}

kubectl -n kube-system rollout status deployment/ebs-csi-controller --timeout=240s || true

cat <<'SCEOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
    name: gp3
    annotations:
        storageclass.kubernetes.io/is-default-class: \"true\"
provisioner: ebs.csi.aws.com
parameters:
    type: gp3
    fsType: ext4
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
SCEOF

echo \"AWS EBS CSI driver installed.\"
echo \"StorageClass 'gp3' set as default.\"
kubectl get storageclass
"""


def _build_aws_load_balancer_setup_script(
        region: str,
        vpc_id: str,
        cluster_name: str,
) -> str:
        """Return a bash script that installs AWS Load Balancer Controller for kubeadm clusters."""
        return f"""set -euo pipefail

# Install Helm if missing
if ! command -v helm >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# Install cert-manager (required by aws-load-balancer-controller webhook)
helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
helm repo update
kubectl create namespace cert-manager 2>/dev/null || true
helm upgrade --install cert-manager jetstack/cert-manager \\
    --namespace cert-manager \\
    --version v1.16.1 \\
    --set crds.enabled=true \\
    --wait --timeout 240s

# Install AWS Load Balancer Controller (node IAM role / instance profile auth)
helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
helm repo update
kubectl create namespace kube-system 2>/dev/null || true
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \\
    --namespace kube-system \\
    --set clusterName={cluster_name} \\
    --set serviceAccount.create=true \\
    --set region={region} \\
    --set vpcId={vpc_id} \\
    --wait --timeout 300s

kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=240s || true
echo \"AWS Load Balancer Controller installed.\"
"""


def _build_aws_load_balancer_setup_script(
        region: str,
        vpc_id: str,
        cluster_name: str,
) -> str:
        """Return a bash script that installs AWS Load Balancer Controller for kubeadm clusters."""
        return f"""set -euo pipefail

if ! command -v helm >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

helm repo add eks https://aws.github.io/eks-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

kubectl apply -k "github.com/aws/eks-charts/stable/aws-load-balancer-controller//crds?ref=master"

helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \\
    -n kube-system \\
    --set clusterName={cluster_name} \\
    --set serviceAccount.create=true \\
    --set serviceAccount.name=aws-load-balancer-controller \\
    --set region={region} \\
    --set vpcId={vpc_id} \\
    --set replicaCount=1 \\
    --wait --timeout 10m

kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=300s || true

echo "AWS Load Balancer Controller installed."
echo "Use Service annotations for NLB when needed:"
echo "  service.beta.kubernetes.io/aws-load-balancer-type: external"
echo "  service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip"
"""
