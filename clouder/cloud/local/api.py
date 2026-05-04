"""Local API."""

from ...util.utils import SSH_FOLDER


def get_local_ssh_keys():
    """Get the local SSH Keys."""
    ssh_keys = []
    for file in SSH_FOLDER.iterdir():
        if file.name.endswith(".pub"):
            ssh_keys.append(file.name.replace(".pub", ""))
    return ssh_keys
