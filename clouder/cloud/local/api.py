"""Local API."""
from pathlib import Path


SSH_FOLDER = Path.home() / ".ssh"


def get_local_ssh_keys():
    """Get the local SSH keys."""
    keys = []
    for file in SSH_FOLDER.iterdir():
        if file.name.endswith(".pub"):
            keys.append(file.name.replace(".pub", ""))
    return keys
