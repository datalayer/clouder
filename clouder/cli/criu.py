"""Clouder CLI - CRIU visibility and configuration commands."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import typer
from rich import print
from rich.panel import Panel
from rich.table import Table

from ._completions import deployment_name_completion, ssh_key_name_completion
from .ctx import get_current_context
from .kubeadm._helpers import (
    _load_cluster_metadata,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    _update_cluster_metadata,
)
from ..util.utils import SSH_FOLDER


criu_app = typer.Typer(no_args_is_help=True)


def _resolve_runtimes_api_base(run_url: Optional[str]) -> str:
    """Resolve the runtimes API base URL from argument/env.

    Accepts either a host URL (e.g. https://prod1.datalayer.run) or a full
    runtimes API URL (.../api/runtimes/v1).
    """
    raw = (run_url or os.environ.get("DATALAYER_RUN_URL") or "").strip()
    if not raw:
        raise typer.BadParameter(
            "Missing run URL. Set --run-url or export DATALAYER_RUN_URL."
        )

    base = raw.rstrip("/")
    if base.endswith("/api/runtimes/v1"):
        return base
    return f"{base}/api/runtimes/v1"


def _resolve_api_key(api_key: Optional[str]) -> str:
    """Resolve API key from argument/env."""
    key = (api_key or os.environ.get("DATALAYER_API_KEY") or "").strip()
    if not key:
        raise typer.BadParameter(
            "Missing API key. Set --api-key or export DATALAYER_API_KEY."
        )
    return key


def _api_call(
    method: str,
    path: str,
    run_url: Optional[str],
    api_key: Optional[str],
    payload: Optional[dict] = None,
) -> dict:
    """Call runtimes API and return parsed JSON payload."""
    base = _resolve_runtimes_api_base(run_url)
    key = _resolve_api_key(api_key)
    url = f"{base}{path}"

    body = None
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urlerror.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise typer.BadParameter(f"API {method} {url} failed ({e.code}): {err_body}") from e
    except urlerror.URLError as e:
        raise typer.BadParameter(f"API {method} {url} failed: {e}") from e


def _infer_cluster_name(cluster: Optional[str]) -> str:
    """Resolve the cluster name from argument or current kube context."""
    if cluster:
        return cluster

    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    raise typer.BadParameter(
        "Cluster name is required (pass --cluster) when current kube context cannot be resolved."
    )


def _default_admin_user(user: Optional[str]) -> str:
    """Pick a default SSH user based on the active cloud context."""
    if user:
        return user

    (cloud, _) = get_current_context()
    if cloud == "azure":
        return "azureuser"
    return "ubuntu"


@criu_app.command("status")
def criu_status(
    cluster: Optional[str] = typer.Option(
        None,
        "--cluster",
        help="Kubeadm cluster name.",
        autocompletion=deployment_name_completion,
    ),
    user: Optional[str] = typer.Option(None, "--admin-user", "-u", help="SSH username on nodes."),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-i",
        help="SSH key name (from ~/.ssh/).",
        autocompletion=ssh_key_name_completion,
    ),
):
    """Show CRIU readiness and checkpoint prerequisites on each cluster node."""
    cluster_name = _infer_cluster_name(cluster)
    resolved_user = _default_admin_user(user)
    cluster_data = _resolve_cluster_vms(cluster_name)
    key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(cluster_name)

    nodes = [cluster_data["master"], *cluster_data["workers"]]

    table = Table(title=f"CRIU Status - {cluster_name}")
    table.add_column("Node", style="cyan")
    table.add_column("IP", style="dim")
    table.add_column("CRIU", style="green")
    table.add_column("criu check", style="green")
    table.add_column("containerd", style="magenta")
    table.add_column("ContainerCheckpoint", style="yellow")

    ok_count = 0
    for node in nodes:
        if not node.get("ip"):
            table.add_row(
                node["name"],
                "-",
                "no public ip",
                "no public ip",
                "no public ip",
                "no public ip",
            )
            continue

        cmd = (
            "CRIU_VER=$(criu --version 2>/dev/null | head -1 || true); "
            "if sudo criu check >/tmp/clouder-criu-check.log 2>&1; then CRIU_CHECK=ok; else CRIU_CHECK=warn; fi; "
            "CTR_VER=$(containerd --version 2>/dev/null | head -1 || true); "
            "FG=$(sudo awk '/ContainerCheckpoint:/ {print $2}' /var/lib/kubelet/config.yaml 2>/dev/null | tail -1); "
            "echo \"${CRIU_VER}|${CRIU_CHECK}|${CTR_VER}|${FG}\""
        )
        result = _ssh_cmd(node["ip"], resolved_user, key_path, cmd, check=False)

        if result.returncode != 0 or not result.stdout.strip():
            table.add_row(
                node["name"],
                node["ip"] or "-",
                "unreachable",
                "unreachable",
                "unreachable",
                "unreachable",
            )
            continue

        criu_ver, criu_check, ctr_ver, feature_gate = (result.stdout.strip() + "|||").split("|", 3)

        fg_value = feature_gate.strip() or "missing"
        if criu_check == "ok" and fg_value == "true":
            ok_count += 1

        table.add_row(
            node["name"],
            node["ip"] or "-",
            criu_ver or "missing",
            criu_check,
            ctr_ver or "missing",
            fg_value,
        )

    print(table)
    print(
        Panel(
            f"Nodes healthy for CRIU checkpoint prerequisites: [bold]{ok_count}/{len(nodes)}[/bold]\n"
            "Healthy = `criu check` passes and kubelet has `ContainerCheckpoint: true`.",
            title="Summary",
        )
    )


@criu_app.command("checkpoints")
def criu_checkpoints(
    cluster: Optional[str] = typer.Option(
        None,
        "--cluster",
        help="Kubeadm cluster name.",
        autocompletion=deployment_name_completion,
    ),
    user: Optional[str] = typer.Option(None, "--admin-user", "-u", help="SSH username on nodes."),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-i",
        help="SSH key name (from ~/.ssh/).",
        autocompletion=ssh_key_name_completion,
    ),
):
    """List checkpoint archives currently present on each node."""
    cluster_name = _infer_cluster_name(cluster)
    resolved_user = _default_admin_user(user)
    cluster_data = _resolve_cluster_vms(cluster_name)
    key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(cluster_name)

    nodes = [cluster_data["master"], *cluster_data["workers"]]

    for node in nodes:
        if not node.get("ip"):
            print(Panel("(node has no public IP; skipping remote inspection)", title=f"{node['name']}"))
            continue

        cmd = "sudo ls -lh /var/lib/kubelet/checkpoints 2>/dev/null | tail -n +2 || true"
        result = _ssh_cmd(node["ip"], resolved_user, key_path, cmd, check=False)
        output = result.stdout.strip() or "(no checkpoint archives found)"
        print(
            Panel(
                output,
                title=f"{node['name']} ({node['ip']})",
            )
        )


@criu_app.command("checkpoint")
def criu_checkpoint(
    runtime: str = typer.Argument(..., help="Runtime pod name to checkpoint."),
    checkpoint_mode: str = typer.Option(
        "criu",
        "--checkpoint-mode",
        help="Checkpoint mode: criu or light.",
    ),
    agent_spec_id: str = typer.Option("", "--agent-spec-id", help="Optional agent spec id."),
    name: str = typer.Option("", "--name", help="Optional checkpoint display name."),
    description: str = typer.Option("", "--description", help="Optional checkpoint description."),
    run_url: Optional[str] = typer.Option(None, "--run-url", help="Datalayer run URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Datalayer API key."),
):
    """Create a runtime checkpoint via the runtimes API."""
    mode = (checkpoint_mode or "criu").lower()
    if mode not in {"criu", "light"}:
        raise typer.BadParameter("checkpoint-mode must be one of: criu, light")

    payload = {
        "checkpoint_mode": mode,
        "agent_spec_id": agent_spec_id,
        "name": name,
        "description": description,
    }
    resp = _api_call("POST", f"/runtimes/{urlparse.quote(runtime)}/pause", run_url, api_key, payload)
    checkpoint_id = resp.get("checkpoint_id", "")
    message = resp.get("message", "Checkpoint request accepted")
    print(
        Panel(
            f"Runtime: [bold]{runtime}[/bold]\n"
            f"Mode: [bold]{mode}[/bold]\n"
            f"Checkpoint ID: [bold]{checkpoint_id or 'N/A'}[/bold]\n"
            f"Message: {message}",
            title="CRIU Checkpoint",
        )
    )


@criu_app.command("restore")
def criu_restore(
    runtime: str = typer.Argument(..., help="Runtime pod name to restore."),
    checkpoint_id: str = typer.Option("", "--checkpoint-id", help="Specific checkpoint ID to restore."),
    run_url: Optional[str] = typer.Option(None, "--run-url", help="Datalayer run URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Datalayer API key."),
):
    """Restore a runtime from a checkpoint via the runtimes API."""
    payload = {}
    if checkpoint_id:
        payload["checkpoint_id"] = checkpoint_id

    resp = _api_call("POST", f"/runtimes/{urlparse.quote(runtime)}/resume", run_url, api_key, payload)
    message = resp.get("message", "Restore request accepted")
    print(
        Panel(
            f"Runtime: [bold]{runtime}[/bold]\n"
            f"Checkpoint ID: [bold]{checkpoint_id or 'auto-select paused checkpoint'}[/bold]\n"
            f"Message: {message}",
            title="CRIU Restore",
        )
    )


@criu_app.command("ls")
def criu_ls(
    runtime: str = typer.Option("", "--runtime", help="Filter checkpoints by runtime pod name."),
    run_url: Optional[str] = typer.Option(None, "--run-url", help="Datalayer run URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Datalayer API key."),
):
    """List runtime checkpoints."""
    path = "/runtime-checkpoints"
    if runtime:
        path = f"/runtime-checkpoints/{urlparse.quote(runtime)}"

    resp = _api_call("GET", path, run_url, api_key)
    checkpoints = resp.get("checkpoints", [])

    table = Table(title="Runtime Checkpoints")
    table.add_column("UID", style="cyan")
    table.add_column("Runtime", style="magenta")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Updated")

    for ckpt in checkpoints:
        table.add_row(
            str(ckpt.get("uid", "")),
            str(ckpt.get("runtime_uid", "")),
            str(ckpt.get("checkpoint_mode", "")),
            str(ckpt.get("status", "")),
            str(ckpt.get("updated_at", "")),
        )

    if not checkpoints:
        print(Panel("No checkpoints found.", title="Runtime Checkpoints"))
        return

    print(table)


@criu_app.command("inspect")
def criu_inspect(
    runtime: str = typer.Argument(..., help="Runtime pod name."),
    checkpoint_id: str = typer.Argument(..., help="Checkpoint ID."),
    run_url: Optional[str] = typer.Option(None, "--run-url", help="Datalayer run URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Datalayer API key."),
):
    """Inspect a single runtime checkpoint."""
    resp = _api_call(
        "GET",
        f"/runtime-checkpoints/{urlparse.quote(runtime)}/{urlparse.quote(checkpoint_id)}",
        run_url,
        api_key,
    )
    checkpoint = resp.get("checkpoint", {})
    print(Panel(json.dumps(checkpoint, indent=2), title=f"Checkpoint {checkpoint_id}"))


@criu_app.command("delete")
def criu_delete(
    runtime: str = typer.Argument(..., help="Runtime pod name."),
    checkpoint_id: str = typer.Argument(..., help="Checkpoint ID."),
    run_url: Optional[str] = typer.Option(None, "--run-url", help="Datalayer run URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Datalayer API key."),
):
    """Delete a runtime checkpoint."""
    _api_call(
        "DELETE",
        f"/runtime-checkpoints/{urlparse.quote(runtime)}/{urlparse.quote(checkpoint_id)}",
        run_url,
        api_key,
    )
    print(Panel(f"Deleted checkpoint [bold]{checkpoint_id}[/bold] for runtime [bold]{runtime}[/bold].", title="CRIU Delete"))


@criu_app.command("storage")
def criu_storage(
    cluster: Optional[str] = typer.Option(
        None,
        "--cluster",
        help="Kubeadm cluster name.",
        autocompletion=deployment_name_completion,
    ),
):
    """Show configured checkpoint storage for a deployment."""
    cluster_name = _infer_cluster_name(cluster)
    metadata = _load_cluster_metadata(cluster_name) or {}
    storage = metadata.get("checkpoint_storage")

    if not storage:
        print(
            Panel(
                "No checkpoint storage configured in deployment metadata.\n"
                "Set one with: clouder criu storage-set --cluster "
                f"{cluster_name} --storage s3://<bucket>/checkpoints/",
                title=f"CRIU Storage - {cluster_name}",
            )
        )
        return

    print(
        Panel(
            f"Checkpoint storage: [bold]{storage}[/bold]",
            title=f"CRIU Storage - {cluster_name}",
        )
    )


@criu_app.command("storage-set")
def criu_storage_set(
    storage: str = typer.Option(..., "--storage", help="Checkpoint storage URI (e.g. s3://bucket/checkpoints/)."),
    cluster: Optional[str] = typer.Option(
        None,
        "--cluster",
        help="Kubeadm cluster name.",
        autocompletion=deployment_name_completion,
    ),
):
    """Persist default checkpoint storage in deployment metadata."""
    cluster_name = _infer_cluster_name(cluster)
    _update_cluster_metadata(cluster_name, {"checkpoint_storage": storage})

    print(
        Panel(
            f"Stored checkpoint storage for [bold]{cluster_name}[/bold]:\n{storage}",
            title="CRIU Storage Updated",
        )
    )
