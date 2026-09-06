"""Shared CLI autocompletion helpers."""

from __future__ import annotations

from pathlib import Path

from ..util.utils import CLOUDER_KUBEADM_FOLDER, SSH_FOLDER


def deployment_name_completion(incomplete: str):
    """Complete kubeadm cluster names from ~/.clouder/kubeadm/*/kubeadm.json."""
    if not CLOUDER_KUBEADM_FOLDER.exists():
        return []

    prefix = (incomplete or "").strip()
    names = sorted(
        p.parent.name
        for p in CLOUDER_KUBEADM_FOLDER.glob("*/kubeadm.json")
        if p.is_file()
    )
    if not prefix:
        return names
    return [name for name in names if name.startswith(prefix)]


def ssh_key_name_completion(incomplete: str):
    """Complete local SSH private key file names from ~/.ssh/."""
    if not SSH_FOLDER.exists():
        return []

    prefix = (incomplete or "").strip()
    keys = []
    for p in sorted(SSH_FOLDER.iterdir()):
        if not p.is_file():
            continue
        if p.name.endswith(".pub"):
            continue
        if p.name.startswith("known_hosts"):
            continue
        keys.append(p.name)

    if not prefix:
        return keys
    return [name for name in keys if name.startswith(prefix)]
