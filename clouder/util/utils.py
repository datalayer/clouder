import subprocess

from pathlib import Path


HOME_FOLDER = Path.home()

SSH_FOLDER = HOME_FOLDER / ".ssh"

SSH_PUBLIC_KEY = SSH_FOLDER / "id_rsa.pub"

CLOUDER_CONFIG_FOLDER = HOME_FOLDER / ".clouder"

CLOUDER_KUBEADM_FOLDER = CLOUDER_CONFIG_FOLDER / "kubeadm"

CLOUDER_CONTEXT_FILE = CLOUDER_CONFIG_FOLDER / "clouder.yaml"

CLOUDER_CLOUDS_FOLDER = CLOUDER_CONFIG_FOLDER / "clouds"

OVH_CONFIG_FOLDER = CLOUDER_CLOUDS_FOLDER / "ovh"

OVH_CONFIG_FILE = OVH_CONFIG_FOLDER / "ovh.conf"

OVH_K8S_FOLDER = OVH_CONFIG_FOLDER / "k8s"

HERE_FOLDER = Path(__file__).parent

DEFAULT_REGION = "BHS"


def kubeadm_cluster_folder(cluster_name: str) -> Path:
    """Return ~/.clouder/kubeadm/<cluster_name>."""
    return CLOUDER_KUBEADM_FOLDER / cluster_name


def kubeadm_metadata_path(cluster_name: str) -> Path:
    """Return ~/.clouder/kubeadm/<cluster_name>/kubeadm.json."""
    return kubeadm_cluster_folder(cluster_name) / "kubeadm.json"


def kubeadm_kubeconfig_path(cluster_name: str) -> Path:
    """Return ~/.clouder/kubeadm/<cluster_name>/kubeconfig."""
    return kubeadm_cluster_folder(cluster_name) / "kubeconfig"


def kubeadm_azure_operator_values_path(cluster_name: str) -> Path:
    """Return ~/.clouder/kubeadm/<cluster_name>/datalayer-operator-azure.json."""
    return kubeadm_cluster_folder(cluster_name) / "datalayer-operator-azure.json"


def kubeadm_kubelet_client_cert_path(cluster_name: str) -> Path:
    """Return ~/.clouder/kubeadm/<cluster_name>/apiserver-kubelet-client.crt."""
    return kubeadm_cluster_folder(cluster_name) / "apiserver-kubelet-client.crt"


def kubeadm_kubelet_client_key_path(cluster_name: str) -> Path:
    """Return ~/.clouder/kubeadm/<cluster_name>/apiserver-kubelet-client.key."""
    return kubeadm_cluster_folder(cluster_name) / "apiserver-kubelet-client.key"


def ensure_kubeadm_cluster_folder(cluster_name: str) -> Path:
    """Ensure ~/.clouder/kubeadm/<cluster_name> exists and return it."""
    folder = kubeadm_cluster_folder(cluster_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run_shell(args):
    """Run a shell command."""

    subprocess.run(args[2:])


def run_sbin(args):
    """Run a sbin command."""

    args[2] = args[2] + ".sh"

    cmd = ["bash", str(HERE_FOLDER / ".." / "sbin" / "clouder.sh")]
    cmd.extend(args[2:])

    subprocess.run(cmd)


def run_sbin_direct(args):
    """Run directly a sbin command."""

    args[2] = ["", "", ] + args[2] + ".sh"

    run_sbin(args)
