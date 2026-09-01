"""Clouder CLI - Local CSI driver (local.csi.datalayer.io) checks."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich import print
from rich.panel import Panel
from rich.table import Table

from ._completions import deployment_name_completion, ssh_key_name_completion
from .criu import _default_admin_user, _infer_cluster_name
from .kubeadm._helpers import (
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
    resolve_kubeadm_cloud_context,
)
from .kubeadm.node_mounts import DRIVER_NAME, NAMESPACE, RELEASE_NAME
from ..util.utils import SSH_FOLDER


node_mounts_app = typer.Typer(no_args_is_help=True)


@node_mounts_app.callback()
def node_mounts_callback():
    """Local CSI driver (local.csi.datalayer.io): the node plugin serving Local Mounts."""


def _collect_status(master_ip: str, user: str, key_path: str, namespace: str, release: str, health_port: int) -> dict:
    """Ask the master about the CSIDriver, the DaemonSet and each node plugin's mounts."""
    status: dict = {"csidriver": False, "daemonset": None, "pods": []}

    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl get csidriver {DRIVER_NAME} -o jsonpath='{{.metadata.name}}' 2>/dev/null || true",
        check=False,
    )
    status["csidriver"] = result.stdout.strip() == DRIVER_NAME

    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl -n {namespace} get daemonset {release} -o json 2>/dev/null || true",
        check=False,
    )
    if result.stdout.strip():
        try:
            ds_status = json.loads(result.stdout).get("status", {})
            status["daemonset"] = {
                "desired": ds_status.get("desiredNumberScheduled", 0),
                "ready": ds_status.get("numberReady", 0),
            }
        except json.JSONDecodeError:
            status["daemonset"] = None

    result = _ssh_cmd(
        master_ip,
        user,
        key_path,
        f"kubectl -n {namespace} get pods -l app={release} -o json 2>/dev/null || true",
        check=False,
    )
    pods = []
    if result.stdout.strip():
        try:
            pods = json.loads(result.stdout).get("items", [])
        except json.JSONDecodeError:
            pods = []

    for pod in pods:
        name = pod["metadata"]["name"]
        node = pod.get("spec", {}).get("nodeName", "-")
        phase = pod.get("status", {}).get("phase", "-")
        ready = all(
            c.get("ready") for c in pod.get("status", {}).get("containerStatuses", [])
        ) and bool(pod.get("status", {}).get("containerStatuses"))
        entry = {
            "pod": name,
            "node": node,
            "phase": phase,
            "ready": ready,
            "mounts": None,
            "gateway": None,
            "error": "",
        }
        if ready:
            result = _ssh_cmd(
                master_ip,
                user,
                key_path,
                f"kubectl -n {namespace} exec {name} -c driver -- curl -fsS http://127.0.0.1:{health_port}/mounts 2>/dev/null || true",
                check=False,
            )
            try:
                entry["mounts"] = json.loads(result.stdout) if result.stdout.strip() else None
            except json.JSONDecodeError:
                entry["error"] = "unreadable /mounts"
            result = _ssh_cmd(
                master_ip,
                user,
                key_path,
                f"kubectl -n {namespace} exec {name} -c driver -- curl -fsS http://127.0.0.1:{health_port}/gateway 2>/dev/null || true",
                check=False,
            )
            try:
                gateway = json.loads(result.stdout) if result.stdout.strip() else None
            except json.JSONDecodeError:
                gateway = None
            # 404 means the gateway is not enabled on this node, which is a
            # deployment choice rather than a fault: report it as absent.
            entry["gateway"] = gateway if isinstance(gateway, dict) and "pods" in gateway else None
        status["pods"].append(entry)
    return status


@node_mounts_app.command("status")
def node_mounts_status(
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
    cloud: Optional[str] = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws)."),
    namespace: str = typer.Option(NAMESPACE, "--namespace", "-n", help="Namespace of the DaemonSet."),
    release: str = typer.Option(RELEASE_NAME, "--release", help="Helm release name."),
    health_port: int = typer.Option(9808, "--health-port", help="Driver health port (driver.healthPort)."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw status as JSON."),
):
    """Show the Local CSI driver: CSIDriver, DaemonSet, and each node's bridge mounts."""
    cluster_name = _infer_cluster_name(cluster)
    resolved_cloud, resolved_context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=cluster_name)
    resolved_user = _default_admin_user(user, cloud=resolved_cloud)
    cluster_data = _resolve_cluster_vms(cluster_name, cloud=resolved_cloud, context_id=resolved_context_id)
    key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(cluster_name)
    master = cluster_data["master"]

    status = _collect_status(master["ip"], resolved_user, key_path, namespace, release, health_port)

    if as_json:
        typer.echo(json.dumps(status, indent=2, sort_keys=True))
        return

    daemonset = status["daemonset"]
    print(
        Panel(
            f"CSIDriver {DRIVER_NAME}: [bold]{'present' if status['csidriver'] else 'missing'}[/bold]\n"
            + (
                f"DaemonSet {namespace}/{release}: [bold]{daemonset['ready']}/{daemonset['desired']}[/bold] ready"
                if daemonset
                else f"DaemonSet {namespace}/{release}: [bold]missing[/bold]"
            ),
            title=f"Local CSI - {cluster_name}",
        )
    )

    table = Table(title="Node plugins")
    table.add_column("Node", style="cyan")
    table.add_column("Pod", style="dim")
    table.add_column("Ready", style="green")
    table.add_column("Bridges", style="magenta")
    table.add_column("Volumes", style="magenta")
    table.add_column("Disconnected", style="yellow")

    for entry in status["pods"]:
        mounts = entry["mounts"] or {}
        bridges = mounts.get("bridges", {}) or {}
        connected = sum(1 for b in bridges.values() if b.get("connected"))
        disconnected = [
            f"{uid}: {b.get('reason') or 'disconnected'}" for uid, b in bridges.items() if not b.get("connected")
        ]
        table.add_row(
            entry["node"],
            entry["pod"],
            "yes" if entry["ready"] else entry["phase"],
            f"{connected}/{len(bridges)}" if entry["mounts"] is not None else (entry["error"] or "-"),
            str(len(mounts.get("volumes", {}) or {})) if entry["mounts"] is not None else "-",
            "\n".join(disconnected) or "-",
        )
    print(table)

    _print_gateway(status)


def _print_gateway(status: dict) -> None:
    """The Node Mount Gateway, per node and per pod: what is bound and what leaked.

    A node without the gateway prints nothing rather than an empty table: it
    is off there, which is a deployment choice, not a fault.
    """
    nodes = [entry for entry in status["pods"] if entry.get("gateway")]
    if not nodes:
        return

    table = Table(title="Node Mount Gateway")
    table.add_column("Node", style="cyan")
    table.add_column("Runtime pod", style="dim")
    table.add_column("Published", style="green")
    table.add_column("Mounts", style="magenta")
    table.add_column("Leaked", style="red")

    for entry in nodes:
        gateway = entry["gateway"]
        counters = gateway.get("counters", {}) or {}
        pods = gateway.get("pods", {}) or {}
        leaked = str(counters.get("leaked", 0) or 0)
        if not pods:
            table.add_row(entry["node"], "-", "-", "0", leaked)
            continue
        for pod_uid, detail in sorted(pods.items()):
            mounts = detail.get("mounts", {}) or {}
            names = ", ".join(
                f"{target}{'' if spec.get('mounted') else ' (gone)'}"
                f"{' ro' if spec.get('mode') == 'ro' else ''}"
                for target, spec in sorted(mounts.items())
            )
            table.add_row(
                entry["node"],
                pod_uid,
                "yes" if detail.get("published") else "no",
                names or "-",
                leaked,
            )
    print(table)

    total_leaked = sum((entry["gateway"].get("counters", {}) or {}).get("leaked", 0) for entry in nodes)
    if total_leaked:
        # A mount that would not come down is what makes a Pod stick in
        # Terminating; kubelet is about to try the same unmount and fail too.
        print(
            f"[red]{total_leaked} gateway mount(s) could not be unmounted. "
            "Pods holding them will stay in Terminating; see the node-mounts runbook.[/red]"
        )


# ---------------------------------------------------------------------------
# Verifying the Node Mount Gateway on a cluster
# ---------------------------------------------------------------------------

#: What `verify` looks at, and why each one is worth a command.
#:
#: The gateway's failure mode is silence: a grant is written, the agent does
#: nothing an operator can see, and a runtime starts without the folders it
#: asked for. Every check here catches one way that happens, and each says
#: what to do rather than only that something is wrong.
NODE_MOUNT_GATEWAY_NAMESPACE = "datalayer-runtimes"
#: Where the Operator runs. `datalayer-runtimes`, not `datalayer-api`: it is
#: deployed beside the runtimes it manages. Looking in the wrong namespace
#: made `verify` report the Operator's half off on a cluster where it was on —
#: an empty answer and a `false` are the same string to a jsonpath.
OPERATOR_NAMESPACE = "datalayer-runtimes"
RUNTIME_SERVICE_ACCOUNT = "datalayer-runtimes-sa"

#: The `app` label every runtime Pod carries, used to find the containers a
#: tenant runs code in.
RUNTIME_POD_LABEL_VALUE = "runtime-pools"


def _check(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def _kubectl(master_ip: str, user: str, key_path: str, command: str) -> str:
    result = _ssh_cmd(master_ip, user, key_path, f"{command} 2>/dev/null || true", check=False)
    return result.stdout.strip()


def collect_gateway_checks(
    master_ip: str,
    user: str,
    key_path: str,
    *,
    namespace: str = NAMESPACE,
    release: str = RELEASE_NAME,
    health_port: int = 9808,
    gateway_namespace: str = NODE_MOUNT_GATEWAY_NAMESPACE,
    operator_namespace: str = OPERATOR_NAMESPACE,
) -> list[dict]:
    """Run the preflight the cluster run depends on, and say what each answer means."""
    checks: list[dict] = []

    # 1. Is the node component there at all?
    daemonset = _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {namespace} get daemonset {release} "
        "-o jsonpath='{.status.numberReady}/{.status.desiredNumberScheduled}'",
    )
    ready, _, desired = daemonset.partition("/")
    all_ready = bool(desired) and ready == desired and desired != "0"
    checks.append(_check(
        "Node driver DaemonSet",
        all_ready,
        f"{daemonset or 'not found'} ready",
        "plane up datalayer-node-mounts, or check the pods for pull and scheduling errors",
    ))

    # 2. Mount propagation. kubelet refuses to start a container asking for
    #    Bidirectional propagation on a host path that is not a shared mount,
    #    so a DaemonSet that is Running has already proved this — but say it
    #    explicitly, because "the pod is up" is not an obvious proof of it.
    propagation = _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {namespace} exec daemonset/{release} -c driver -- "
        "findmnt -no PROPAGATION --target /var/lib/kubelet",
    )
    checks.append(_check(
        "Mount propagation (rshared)",
        "shared" in propagation if propagation else None,
        propagation or "could not be read",
        "mount --make-rshared / on each runtimes node, and make it persistent. "
        "Without it every grant succeeds on the node and is invisible in the sandbox",
    ))

    # 3. The kernel. `mount_setattr` (5.12) is what makes a `ro` grant
    #    read-only in the sandbox rather than only on the node, and there is
    #    no safe fallback for it — the agent refuses the mount instead.
    kernel = _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {namespace} exec daemonset/{release} -c driver -- uname -r",
    )
    release_numbers = kernel.split("-")[0].split(".")
    try:
        new_enough = (int(release_numbers[0]), int(release_numbers[1])) >= (5, 12)
    except (IndexError, ValueError):
        new_enough = None
    checks.append(_check(
        "Kernel supports mount_setattr (5.12+)",
        new_enough,
        kernel or "could not be read",
        "Without it a read-only grant cannot be made read-only in the sandbox, and the "
        "agent refuses the mount rather than delivering a writable one",
    ))

    # 4. Is the gateway half switched on, and on how many nodes?
    gateway = _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {namespace} exec daemonset/{release} -c driver -- "
        f"curl -fsS http://127.0.0.1:{health_port}/gateway",
    )
    # An empty answer is ambiguous and was, for one release, read as "off":
    # the image has no `wget`, so the command printed nothing and a gateway
    # that was running perfectly reported as not enabled. `curl -fsS` fails
    # loudly, and a body that is neither valid nor empty is now its own state
    # rather than being folded into "off".
    gateway_on = bool(gateway) and '"pods"' in gateway
    gateway_unreadable = bool(gateway) and not gateway_on and "not enabled" not in gateway
    # Informational on its own: off is a deployment choice, not a fault. What
    # is a fault is the two halves disagreeing, which the next check catches —
    # reporting "off" as broken would teach an operator to ignore this command.
    checks.append(_check(
        "Node Mount Gateway enabled on the node",
        True if gateway_on else (False if gateway_unreadable else None),
        "serving /gateway"
        if gateway_on
        else (
            f"the endpoint answered something unreadable: {gateway[:80]}"
            if gateway_unreadable
            else "not enabled (the driver answers 404)"
        ),
        "helm upgrade with nodeMountGateway.enabled=true and "
        "nodeMountGateway.sharedFilesystemClaim set, or set "
        "DATALAYER_NODE_MOUNT_GATEWAY_ENABLED=true and re-run `plane up datalayer-node-mounts`",
    ))

    # 5. The Operator's half. On without an agent means pods carry the volume
    #    and wait for mounts nobody makes; off with an agent means the agent
    #    idles. Either way nothing works and nothing says so.
    operator_env = _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {operator_namespace} get deploy datalayer-operator -o jsonpath="
        "'{.spec.template.spec.containers[*].env[?(@.name==\"DATALAYER_NODE_MOUNT_GATEWAY_ENABLED\")].value}'",
    )
    # A deployment that is not there and one deployed with the switch off
    # answer the same empty string, and reading both as "off" is what let a
    # wrong namespace look like a deployment choice for as long as it did.
    operator_found = bool(
        _kubectl(
            master_ip, user, key_path,
            f"kubectl -n {operator_namespace} get deploy datalayer-operator "
            "-o jsonpath='{.metadata.name}' 2>/dev/null",
        ).strip()
    )
    operator_on = operator_env.strip().lower() == "true"
    if not operator_found:
        detail, ok = (
            f"no datalayer-operator deployment in {operator_namespace} — "
            "pass --operator-namespace if it runs elsewhere",
            False,
        )
    elif operator_on and gateway_on:
        detail, ok = "both halves on", True
    elif not operator_on and not gateway_on:
        detail, ok = "both halves off (the gateway is not in use)", None
    elif operator_on:
        detail, ok = "the Operator grants mounts but no node agent applies them", False
    else:
        detail, ok = (
            "the node agent is running but the Operator has "
            "DATALAYER_NODE_MOUNT_GATEWAY_ENABLED unset or false",
            None,
        )
    checks.append(_check(
        "Operator and agent agree",
        ok,
        detail,
        "Deploy the node agent FIRST, then set DATALAYER_NODE_MOUNT_GATEWAY_ENABLED on the Operator",
    ))

    # 6. The claim the agent binds from.
    claim = _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {namespace} get daemonset {release} -o jsonpath="
        "'{.spec.template.spec.volumes[?(@.name==\"shared-fs\")].persistentVolumeClaim.claimName}'",
    )
    checks.append(_check(
        "Shared filesystem claim",
        bool(claim) if gateway_on else None,
        claim or "not mounted into the DaemonSet",
        "Set nodeMountGateway.sharedFilesystemClaim to the RWX claim the Operator and Contents use",
    ))

    # 7. RBAC, from both directions. This is the exit gate's security claim
    #    made as a command rather than an argument.
    agent_patch = _kubectl(
        master_ip, user, key_path,
        f"kubectl auth can-i patch pods -n {gateway_namespace} "
        f"--as=system:serviceaccount:{namespace}:{release}",
    )
    checks.append(_check(
        "Agent may patch a pod",
        agent_patch == "yes" if gateway_on else None,
        agent_patch or "unknown",
        "The agent writes its answer on the pod; without this every mount stays unreported",
    ))

    runtime_patch = _kubectl(
        master_ip, user, key_path,
        f"kubectl auth can-i patch pods -n {gateway_namespace} "
        f"--as=system:serviceaccount:{gateway_namespace}:{RUNTIME_SERVICE_ACCOUNT}",
    )
    # Whether the SA can patch is half the question. The half that decides
    # whether a *tenant* can is where its token goes: a runtime Pod holds it in
    # the companion, which is our code, and not in the containers that run the
    # user's. Reporting only the first turns a real but narrow finding into a
    # red line an operator learns to scroll past.
    tenant_containers = _kubectl(
        master_ip, user, key_path,
        f"kubectl get pods -n {gateway_namespace} "
        f"-l app={RUNTIME_POD_LABEL_VALUE} -o json 2>/dev/null "
        "| python3 -c \"import json,sys;"
        "pods=json.load(sys.stdin).get('items') or [];"
        "print(','.join(sorted({c['name'] for p in pods for c in p['spec']['containers'] "
        "if c['name'] not in ('companion',) and any('serviceaccount' in m['mountPath'] "
        "for m in (c.get('volumeMounts') or []))})) or 'none')\"",
    )
    reachable = tenant_containers not in ("none", "", None)
    checks.append(_check(
        "A runtime may NOT grant itself a mount",
        # Only a token inside a container the tenant runs code in is a way for
        # the tenant to use the permission.
        None if runtime_patch != "no" and not reachable else runtime_patch == "no",
        (
            f"runtime service account can patch pods: {runtime_patch or 'unknown'}"
            + (
                f"; its token is in tenant container(s): {tenant_containers}"
                if reachable
                else "; its token is not in any container that runs user code"
            )
        ),
        "A sandbox that can patch its own pod can mount any folder on the claim. "
        "Narrow the runtime service account to what the companion actually needs, "
        "and keep its token out of the containers a tenant runs code in",
    ))

    agent_secrets = _kubectl(
        master_ip, user, key_path,
        f"kubectl auth can-i get secrets -n {gateway_namespace} "
        f"--as=system:serviceaccount:{namespace}:{release}",
    )
    credentials_on = "--node-mount-gateway-credentials" in _kubectl(
        master_ip, user, key_path,
        f"kubectl -n {namespace} get daemonset {release} -o jsonpath="
        "'{.spec.template.spec.containers[0].args}'",
    )
    checks.append(_check(
        "Agent Secret access matches its configuration",
        (agent_secrets == "yes") == credentials_on,
        f"can-i get secrets: {agent_secrets or 'unknown'}; "
        f"credentials {'on' if credentials_on else 'off'}",
        "The agent reads Secrets only with nodeMountGateway.credentials, and only in the runtimes "
        "namespace. Access without the switch is a permission nothing uses",
    ))

    # 8. Anything left behind. A leaked mount is a pod that will not terminate.
    leaked = 0
    try:
        leaked = int((json.loads(gateway) if gateway_on else {}).get("counters", {}).get("leaked", 0) or 0)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        leaked = 0
    checks.append(_check(
        "No leaked mounts",
        leaked == 0 if gateway_on else None,
        f"{leaked} mount(s) would not unmount",
        "Each one is a Pod that will stick in Terminating; unmount by hand on the node "
        "and never rm -rf the pod's gateway directory",
    ))
    return checks


@node_mounts_app.command("verify")
def node_mounts_verify(
    cluster: Optional[str] = typer.Option(
        None, "--cluster", help="Kubeadm cluster name.", autocompletion=deployment_name_completion,
    ),
    user: Optional[str] = typer.Option(None, "--admin-user", "-u", help="SSH username on nodes."),
    key: Optional[str] = typer.Option(
        None, "--key", "-i", help="SSH key name (from ~/.ssh/).", autocompletion=ssh_key_name_completion,
    ),
    cloud: Optional[str] = typer.Option(None, "--cloud", help="Target cloud provider (azure or aws)."),
    namespace: str = typer.Option(NAMESPACE, "--namespace", "-n", help="Namespace of the DaemonSet."),
    release: str = typer.Option(RELEASE_NAME, "--release", help="Helm release name."),
    health_port: int = typer.Option(9808, "--health-port", help="Driver health port."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw checks as JSON."),
):
    """Check that the Node Mount Gateway can actually mount: propagation, both halves, RBAC.

    The gateway's failure mode is silence — a grant is written, nothing
    happens, and a runtime starts without the folders it asked for. This is
    the preflight that turns each of those into a line that says what to do.
    """
    cluster_name = _infer_cluster_name(cluster)
    resolved_cloud, resolved_context_id = resolve_kubeadm_cloud_context(cloud=cloud, cluster_name=cluster_name)
    resolved_user = _default_admin_user(user, cloud=resolved_cloud)
    cluster_data = _resolve_cluster_vms(cluster_name, cloud=resolved_cloud, context_id=resolved_context_id)
    key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(cluster_name)
    master = cluster_data["master"]

    checks = collect_gateway_checks(
        master["ip"], resolved_user, key_path,
        namespace=namespace, release=release, health_port=health_port,
    )

    if as_json:
        typer.echo(json.dumps(checks, indent=2, sort_keys=True))
        raise typer.Exit(0 if all(c["ok"] is not False for c in checks) else 1)

    table = Table(title=f"Node Mount Gateway - {cluster_name}")
    table.add_column("Check", style="cyan")
    table.add_column("", style="bold")
    table.add_column("Detail", style="dim")
    for check in checks:
        mark = "[green]ok[/green]" if check["ok"] else ("[red]FAIL[/red]" if check["ok"] is False else "[yellow]-[/yellow]")
        table.add_row(check["name"], mark, check["detail"])
    print(table)

    failures = [check for check in checks if check["ok"] is False]
    for check in failures:
        print(f"[red]{check['name']}:[/red] {check['fix']}.")
    if failures:
        return

    # "No failures" is not "the gateway works". With the gateway off every
    # gateway check is a warning rather than a failure — correctly, since
    # nothing is broken — and reporting that as success tells an operator the
    # thing they just tried to turn on is running when it is not.
    enabled = next(
        (c for c in checks if c["name"] == "Node Mount Gateway enabled on the node"),
        None,
    )
    if enabled is not None and enabled["ok"] is not True:
        print(
            "[yellow]The node driver is healthy and the Node Mount Gateway is NOT enabled.[/yellow]\n"
            "Nothing is broken, and nothing will be mounted through the gateway either: "
            "the agent is running as the CSI driver alone.\n"
            f"To enable it: [bold]{enabled['fix']}[/bold]."
        )
        return
    # The node half being ready is not the whole gateway either: with the
    # Operator's half off, no grant is ever written, so "launch a runtime to
    # prove it" would send an operator to watch a launch that cannot succeed.
    agreement = next((c for c in checks if c["name"] == "Operator and agent agree"), None)
    if agreement is not None and agreement["ok"] is not True:
        print(
            "[yellow]The node agent is ready and the Operator is not granting.[/yellow]\n"
            f"{agreement['detail'].capitalize()}, so no launch will use the gateway yet.\n"
            f"To finish: [bold]{agreement['fix']}[/bold]."
        )
        return
    print("[green]The gateway can mount. Launch a runtime with the home folders to prove it does.[/green]")
    raise typer.Exit(1 if failures else 0)
