import subprocess

from pathlib import Path


HOME_FOLDER = Path.home()

SSH_FOLDER = HOME_FOLDER / ".ssh"

CLOUDER_CONFIG_FOLDER = HOME_FOLDER / ".clouder"

CLOUDER_CONTEXT_FILE = CLOUDER_CONFIG_FOLDER / "clouder.yaml"

OVH_CONFIG_FILE = CLOUDER_CONFIG_FOLDER / "ovh" / "ovh.conf"

HERE_FOLDER = Path(__file__).parent


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
