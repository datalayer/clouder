"""Install the Local CSI driver chart on a kubeadm cluster.

``clouder kubeadm setup --local-csi`` calls :func:`install_local_csi` in
Step 7, after the cloud storage provider. The chart lives in the Plane tree,
not on the master, so it travels inside the install script as a base64 tar
and is applied there with Helm — the way ``install_storage`` installs the
EFS CSI driver on AWS.
"""

from __future__ import annotations

import base64
import io
import os
import shlex
import tarfile
from pathlib import Path

from rich import print

CHART_NAME = "datalayer-local-csi"
RELEASE_NAME = "datalayer-local-csi"
NAMESPACE = "datalayer-runtimes"
DRIVER_NAME = "local.csi.datalayer.io"
DEFAULT_IMAGE = "datalayer/local-csi:0.1.0"


def resolve_chart_dir(explicit: str | None = None) -> Path | None:
    """Find the chart: an explicit path, PLANE_HOME, DATALAYER_HOME, or the source tree."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    plane_home = os.environ.get("PLANE_HOME")
    if plane_home:
        candidates.append(Path(plane_home) / "etc" / "helm" / "charts" / CHART_NAME)
    datalayer_home = os.environ.get("DATALAYER_HOME")
    if datalayer_home:
        candidates.append(
            Path(datalayer_home) / "src" / "k8s" / "services" / "plane" / "etc" / "helm" / "charts" / CHART_NAME
        )
    # clouder/cli/kubeadm/local_csi.py -> .../src/k8s
    candidates.append(Path(__file__).resolve().parents[4] / "services" / "plane" / "etc" / "helm" / "charts" / CHART_NAME)
    for candidate in candidates:
        if (candidate / "Chart.yaml").is_file():
            return candidate
    return None


def pack_chart(chart_dir: Path) -> str:
    """Return the chart directory as a base64 gzip tar, rooted at the chart name."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(str(chart_dir), arcname=CHART_NAME, recursive=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


#: The node check the mount gateway depends on. A bind made inside the agent's
#: tree reaches a pod only if the kubelet directory is in a shared peer group:
#: on a node whose mounts are `private`, every grant succeeds on the node and
#: is invisible in the sandbox, which is the worst way for this to fail.
PROPAGATION_CHECK = """
GATEWAY_PROPAGATION="$(findmnt -no PROPAGATION --target /var/lib/kubelet 2>/dev/null || true)"
case "$GATEWAY_PROPAGATION" in
    *shared*)
        echo "  mount propagation on /var/lib/kubelet: $GATEWAY_PROPAGATION"
        ;;
    *)
        echo "  WARNING: /var/lib/kubelet propagation is '${GATEWAY_PROPAGATION:-unknown}', not shared."
        echo "  The mount gateway needs a shared peer group to reach pods."
        echo "  Fix it with: mount --make-rshared / (and make it persistent)."
        ;;
esac
"""


def build_local_csi_install_script(
    chart_dir: Path,
    *,
    image: str = DEFAULT_IMAGE,
    relay_host: str = "",
    relay_port: int = 443,
    relay_cidr: str = "",
    gateway: bool = False,
    shared_filesystem_claim: str = "",
) -> str:
    """Return a bash script that installs the chart on the master."""
    values = [
        f"--set driver.image={shlex.quote(image)}",
        f"--set relay.host={shlex.quote(relay_host)}",
        f"--set relay.port={int(relay_port)}",
    ]
    if relay_cidr:
        values.append(f"--set relay.cidr={shlex.quote(relay_cidr)}")
    if gateway:
        if not shared_filesystem_claim:
            raise ValueError(
                "the mount gateway needs the shared filesystem claim: "
                "pass --local-csi-shared-claim <claim name>"
            )
        values.append("--set gateway.enabled=true")
        values.append(f"--set gateway.sharedFilesystemClaim={shlex.quote(shared_filesystem_claim)}")
    set_args = " \\\n    ".join(values)
    return f"""set -euo pipefail
{PROPAGATION_CHECK if gateway else ""}

if ! command -v helm >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

CHART_TMP="$(mktemp -d)"
trap 'rm -rf "$CHART_TMP"' EXIT
echo '{pack_chart(chart_dir)}' | base64 -d | tar -xzf - -C "$CHART_TMP"

kubectl create namespace {NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install {RELEASE_NAME} "$CHART_TMP/{CHART_NAME}" \\
    --namespace {NAMESPACE} \\
    {set_args} \\
    --timeout 5m

kubectl get csidriver {DRIVER_NAME}
kubectl -n {NAMESPACE} rollout status daemonset/{RELEASE_NAME} --timeout=180s || \\
    echo "DaemonSet not ready yet: kubectl -n {NAMESPACE} get pods -l app={RELEASE_NAME}"
"""


def install_local_csi(
    *,
    master: dict,
    resolved_user: str,
    key_path: str,
    image: str = DEFAULT_IMAGE,
    relay_host: str = "",
    relay_port: int = 443,
    relay_cidr: str = "",
    chart_path: str | None = None,
    gateway: bool = False,
    shared_filesystem_claim: str = "",
) -> bool:
    """Install the Local CSI driver on an initialized cluster; True on success."""
    from ._helpers import _ssh_cmd_stream

    chart_dir = resolve_chart_dir(chart_path)
    if chart_dir is None:
        print(f"[yellow]  Chart {CHART_NAME} not found - skipping Local CSI driver setup.[/yellow]")
        print("  Set PLANE_HOME or DATALAYER_HOME, or pass --local-csi-chart, then re-run setup.")
        return False

    if not relay_host:
        print("[yellow]  No relay host given: the driver will accept any wss:// relay host.[/yellow]")
        print("  Pass --local-csi-relay-host <contents host> to pin it.")

    print(f"  Installing Local CSI driver from [dim]{chart_dir}[/dim] (image {image})...")
    try:
        script = build_local_csi_install_script(
            chart_dir,
            image=image,
            relay_host=relay_host,
            relay_port=relay_port,
            relay_cidr=relay_cidr,
            gateway=gateway,
            shared_filesystem_claim=shared_filesystem_claim,
        )
    except ValueError as exc:
        print(f"[red]  {exc}[/red]")
        return False
    rc = _ssh_cmd_stream(master["ip"], resolved_user, key_path, script)
    if rc != 0:
        print("[red]  Local CSI driver installation failed.[/red]")
        return False
    print(f"  [green]Local CSI driver installed ({DRIVER_NAME}).[/green]")
    return True
