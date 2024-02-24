import subprocess

from pathlib import Path


HOME_FOLDER = Path.home()

SSH_FOLDER = HOME_FOLDER / ".ssh"

SSH_PUBLIC_KEY = SSH_FOLDER / "id_rsa.pub"

CLOUDER_CONFIG_FOLDER = HOME_FOLDER / ".clouder"

CLOUDER_CONTEXT_FILE = CLOUDER_CONFIG_FOLDER / "clouder.yaml"

OVH_CONFIG_FOLDER = CLOUDER_CONFIG_FOLDER / "ovh"

OVH_CONFIG_FILE = OVH_CONFIG_FOLDER / "ovh.conf"

OVH_K8S_FOLDER = OVH_CONFIG_FOLDER / "k8s"

HERE_FOLDER = Path(__file__).parent

DEFAULT_REGION = "BHS"


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
