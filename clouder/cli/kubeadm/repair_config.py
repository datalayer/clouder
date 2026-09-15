"""Clouder CLI - kubeadm repair-config command.

Rebuild a damaged, empty, or missing cluster metadata file
(``~/.clouder/kubeadm/<name>/kubeadm.json``).

As much information as possible is recovered from the live cloud provider
(VM inventory) and from the running cluster over SSH (Kubernetes version,
node labels, admin user).  Anything that cannot be discovered automatically —
most notably the SSH key name — is requested interactively from the user.
"""

from __future__ import annotations

import json
import re

import typer
from rich import print
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from ...cloud.local.api import get_local_ssh_keys
from ...util.utils import SSH_FOLDER
from ..ctx import get_default_ssh_key
from ._helpers import (
    DEFAULT_NODE_LABELS,
    _cluster_metadata_path,
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _save_cluster_metadata,
    _ssh_cmd,
    resolve_kubeadm_cloud_context,
    resolve_kubeadm_cluster_name,
)

# Label prefixes that Datalayer manages on cluster nodes and that we try to
# recover when rebuilding the metadata.
_DATALAYER_LABEL_PREFIXES = (
    "role.datalayer.io/",
    "node.datalayer.io/",
    "xpu.datalayer.io/",
)


def _is_metadata_damaged(cluster_name: str) -> tuple[bool, str]:
    """Return (damaged, reason) for the current metadata file."""
    path = _cluster_metadata_path(cluster_name)
    if not path.exists():
        return True, "metadata file does not exist"
    content = path.read_text().strip()
    if not content:
        return True, "metadata file is empty"
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        return True, f"metadata file is not valid JSON ({exc})"
    return False, "metadata file is valid"


def _candidate_ssh_users(cloud: str) -> list[str]:
    """Likely SSH usernames for the given cloud, in priority order."""
    if cloud == "azure":
        return ["azureuser", "ubuntu"]
    # aws
    return ["ec2-user", "ubuntu"]


def _list_cloud_key_names(
    cloud: str, context_id: str | None, region: str | None
) -> list[str]:
    """Best-effort list of SSH key names registered with the cloud provider."""
    try:
        if cloud == "aws":
            from ...cloud.aws.api import _client

            ec2 = _client("ec2", region=region)
            key_pairs = ec2.describe_key_pairs().get("KeyPairs", [])
            return sorted(
                str(kp.get("KeyName", "")).strip()
                for kp in key_pairs
                if kp.get("KeyName")
            )
        if cloud not in {"azure", "aws"} and context_id:
            from ...cloud.ovh.api import get_ovh_ssh_keys

            ssh_keys = get_ovh_ssh_keys(context_id)
            return sorted(
                str(k.get("name", "")).strip() for k in ssh_keys if k.get("name")
            )
    except Exception as exc:  # noqa: BLE001 — discovery is best effort only.
        print(f"[dim]Could not list {cloud} cloud SSH keys: {exc}[/dim]")
    return []


def _prompt_ssh_key(
    cloud: str, context_id: str | None = None, region: str | None = None
) -> tuple[str, str]:
    """Ask the user which SSH key to use, listing local and cloud keys.

    Returns ``(ssh_key_name, key_path)``. The key name is what gets persisted
    to the cluster metadata; the path points at a local private key when one
    can be found in ``~/.ssh``.
    """
    default_key = get_default_ssh_key()
    local_keys = get_local_ssh_keys()
    cloud_keys = _list_cloud_key_names(cloud, context_id, region)

    # Build a merged, de-duplicated choice list, remembering each key's source.
    sources: dict[str, set[str]] = {}
    ordered: list[str] = []
    for source, names in (("local", local_keys), (cloud, cloud_keys)):
        for name in names:
            if name not in sources:
                sources[name] = set()
                ordered.append(name)
            sources[name].add(source)

    if ordered:
        print("\n[bold]Available SSH keys:[/bold]")
        for index, key_name in enumerate(ordered, 1):
            tags = sorted(sources[key_name])
            if key_name == default_key:
                tags.append("default")
            print(f"  {index}. [cyan]{key_name}[/cyan] [dim]({', '.join(tags)})[/dim]")
        default_choice = "1"
        if default_key in ordered:
            default_choice = str(ordered.index(default_key) + 1)
        choice = Prompt.ask(
            "Select the SSH key used to access the cluster (number or name)",
            default=default_choice,
        )
        if choice.isdigit() and 1 <= int(choice) <= len(ordered):
            key_name = ordered[int(choice) - 1]
        else:
            key_name = choice
    else:
        key_name = Prompt.ask(
            "No SSH keys found locally or in the cloud. Enter the SSH key name to use"
        )

    # Resolve the private key file path, accepting plain or .pem variants.
    candidates = [key_name]
    if not key_name.endswith(".pem"):
        candidates.append(f"{key_name}.pem")
    key_path = ""
    for candidate in candidates:
        candidate_path = SSH_FOLDER / candidate
        if candidate_path.exists():
            key_path = str(candidate_path)
            break
    if not key_path:
        # Fall back to the first candidate; SSH probing will report failures.
        key_path = str(SSH_FOLDER / candidates[0])
        print(
            f"[yellow]Warning:[/yellow] private key file not found at {key_path}. "
            "Live-cluster discovery over SSH may be skipped."
        )

    return key_name, key_path


def _detect_ssh_user(master_ip: str, key_path: str, cloud: str) -> str | None:
    """Probe candidate SSH users and return the first that connects."""
    for user in _candidate_ssh_users(cloud):
        result = _ssh_cmd(master_ip, user, key_path, "true", check=False)
        if result.returncode == 0:
            return user
    return None


def _extract_k8s_version(master_ip: str, user: str, key_path: str) -> str | None:
    """Best-effort extraction of the Kubernetes minor version (e.g. ``1.32``)."""
    result = _ssh_cmd(
        master_ip, user, key_path, "kubectl version -o json", check=False
    )
    git_version = ""
    if result.returncode == 0 and result.stdout:
        try:
            payload = json.loads(result.stdout)
            git_version = str(
                (payload.get("serverVersion") or {}).get("gitVersion") or ""
            )
        except json.JSONDecodeError:
            git_version = ""

    if not git_version:
        fallback = _ssh_cmd(
            master_ip, user, key_path, "kubeadm version -o short", check=False
        )
        if fallback.returncode == 0 and fallback.stdout:
            git_version = fallback.stdout.strip()

    match = re.search(r"v?(\d+)\.(\d+)", git_version)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return None


def _extract_node_labels(master_ip: str, user: str, key_path: str) -> list[str]:
    """Recover Datalayer-managed node labels from the live cluster."""
    result = _ssh_cmd(
        master_ip, user, key_path, "kubectl get nodes -o json", check=False
    )
    if result.returncode != 0 or not result.stdout:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    labels: list[str] = []
    for item in payload.get("items", []):
        node_labels = (item.get("metadata") or {}).get("labels") or {}
        for key, value in node_labels.items():
            if any(key.startswith(prefix) for prefix in _DATALAYER_LABEL_PREFIXES):
                entry = f"{key}={value}"
                if entry not in labels:
                    labels.append(entry)
    return labels


def register(kubeadm_app: typer.Typer):
    """Register the repair-config command on the given Typer app."""

    @kubeadm_app.command("repair-config")
    def kubeadm_repair_config(
        name: str | None = typer.Argument(
            None, help="Cluster name. If omitted, uses default kubeadm cluster."
        ),
        cloud: str | None = typer.Option(
            None,
            "--cloud",
            help="Target cloud provider (azure or aws). Defaults to current context cloud.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            "-f",
            help="Rebuild metadata even if the existing file is valid.",
        ),
        skip_ssh: bool = typer.Option(
            False,
            "--skip-ssh",
            help="Do not connect to the cluster over SSH; only use cloud inventory and prompts.",
        ),
    ):
        """Rebuild a damaged or missing cluster metadata file.

        Recovers cluster details from the cloud provider and the live cluster,
        prompting for any information (such as the SSH key) that cannot be
        discovered automatically, then writes a fresh
        ``~/.clouder/kubeadm/<name>/kubeadm.json``.
        """
        name = resolve_kubeadm_cluster_name(name)

        damaged, reason = _is_metadata_damaged(name)
        path = _cluster_metadata_path(name)
        if damaged:
            print(f"[yellow]Cluster '{name}' metadata needs repair:[/yellow] {reason}.")
        else:
            print(f"[green]Cluster '{name}' metadata is currently valid[/green] ({path}).")
            if not force and not Confirm.ask(
                "Rebuild it anyway?", default=False
            ):
                raise typer.Exit(0)

        # Preserve anything still readable from the old file as a starting point.
        existing = _load_cluster_metadata(name) or {}

        # --- 1) Recover VM inventory from the cloud provider ---
        cloud, context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=name)
        print(f"[dim]Resolving '{name}' VMs on {cloud} (context {context_id})…[/dim]")
        cluster = _resolve_cluster_vms(name, cloud=cloud, context_id=context_id)
        master = cluster["master"]
        workers = cluster["workers"]

        region = (
            master.get("region")
            or next((w.get("region") for w in workers if w.get("region")), None)
            or existing.get("region")
        )

        metadata: dict = {
            "name": name,
            "cluster_type": "kubeadm",
            "cloud": cloud,
            "context": {"cloud": cloud},
            "region": region,
            "requested_workers": len(workers),
            "master": master,
            "workers": workers,
        }
        if cloud == "azure":
            metadata["subscription_id"] = context_id
            metadata["context"]["subscription_id"] = context_id
            if master.get("resource_group"):
                metadata["resource_group"] = master["resource_group"]
        elif cloud == "aws":
            metadata["account_id"] = context_id
            metadata["context"]["account_id"] = context_id

        # --- 2) Recover live-cluster facts over SSH (best effort) ---
        admin_username: str | None = existing.get("admin_username")
        k8s_version: str | None = existing.get("k8s_version")
        node_labels: list[str] = []
        setup_complete = bool(existing.get("setup_complete"))

        ssh_key_name = existing.get("ssh_key_name")
        key_path = ""
        if not skip_ssh:
            ssh_key_name, key_path = _prompt_ssh_key(cloud, context_id, region)

            master_ip = master.get("ip")
            if master_ip:
                print(f"[dim]Connecting to master {master_ip} to read cluster state…[/dim]")
                detected_user = _detect_ssh_user(master_ip, key_path, cloud)
                if detected_user:
                    admin_username = detected_user
                    discovered_version = _extract_k8s_version(
                        master_ip, detected_user, key_path
                    )
                    if discovered_version:
                        k8s_version = discovered_version
                    node_labels = _extract_node_labels(
                        master_ip, detected_user, key_path
                    )
                    setup_complete = True
                    print("[green]Recovered cluster state from the live master.[/green]")
                else:
                    print(
                        "[yellow]Could not SSH into the master with the selected key. "
                        "Falling back to prompts.[/yellow]"
                    )
            else:
                print("[yellow]Master has no public IP; skipping SSH discovery.[/yellow]")
        else:
            print("[dim]Skipping SSH discovery (--skip-ssh).[/dim]")

        # --- 3) Prompt for anything still missing ---
        if not ssh_key_name:
            ssh_key_name, _ = _prompt_ssh_key(cloud, context_id, region)
        metadata["ssh_key_name"] = ssh_key_name

        if not admin_username:
            admin_username = Prompt.ask(
                "Admin / SSH username for the cluster nodes",
                default=_candidate_ssh_users(cloud)[0],
            )
        metadata["admin_username"] = admin_username

        if not k8s_version:
            k8s_version = Prompt.ask(
                "Kubernetes version (major.minor)", default="1.32"
            )
        metadata["k8s_version"] = k8s_version

        if not node_labels:
            stored = existing.get("node_labels")
            if isinstance(stored, list) and stored:
                node_labels = [str(v) for v in stored]
            else:
                node_labels = list(DEFAULT_NODE_LABELS)
        metadata["node_labels"] = node_labels

        metadata["setup_complete"] = setup_complete

        # --- 4) Persist ---
        _save_cluster_metadata(name, metadata)

        summary = [
            f"[green]Metadata rebuilt for cluster '{name}'.[/green]",
            "",
            f"  Cloud:     {cloud}",
            f"  Region:    {region or '-'}",
            f"  Master:    {master.get('name')} ({master.get('ip') or '-'})",
            f"  Workers:   {len(workers)}",
            f"  K8s:       v{k8s_version}",
            f"  Admin:     {admin_username}",
            f"  SSH key:   {ssh_key_name}",
            f"  Setup:     {'complete' if setup_complete else 'incomplete'}",
            "",
            f"  File: {path}",
            "",
            f"  Verify: [bold cyan]clouder kubeadm info {name}[/bold cyan]",
        ]
        print(Panel("\n".join(summary), title="Repair Complete", border_style="green"))
