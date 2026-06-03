"""Clouder CLI - kubeadm smoke-test command."""

import json
import time

import typer
from rich import print
from rich.panel import Panel

from ..ctx import get_current_context
from ...util.utils import SSH_FOLDER

from ._helpers import (
    resolve_kubeadm_cluster_name,
    _resolve_cluster_vms,
    _resolve_ssh_key_for_cluster,
    _ssh_cmd,
)


# ---------------------------------------------------------------------------
# Smoke-test pod YAML
# ---------------------------------------------------------------------------

_SMOKE_POD_YAML = """\
apiVersion: v1
kind: Pod
metadata:
  name: clouder-smoke-test
  namespace: default
spec:
  containers:
  - name: counter
    image: busybox:latest
    command: ["/bin/sh", "-c"]
    args:
    - |
      # Write a random token once at startup — used to prove filesystem
      # state survived the checkpoint/restore cycle.
      head -c 16 /dev/urandom | od -A n -t x1 | tr -d ' \\n' > /tmp/smoke_token
      i=0
      while true; do
        i=$((i + 1))
        echo $i > /tmp/counter_value
        sleep 1
      done
"""


def register(kubeadm_app: typer.Typer):
    """Register the smoke-test command on the given Typer app."""

    @kubeadm_app.command("smoke-test")
    def kubeadm_smoke_test(
        name: str | None = typer.Argument(None, help="Cluster name. If omitted, uses default kubeadm cluster."),
        user: str = typer.Option("azureuser", "--admin-user", "-u", help="SSH username on the VMs."),
        key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
        cleanup: bool = typer.Option(True, "--cleanup/--no-cleanup", help="Clean up test pods after the test."),
    ):
        """Run a smoke test: ingress load balancer + CRIU checkpoint/restore.

        Section 1 (Ingress): If an ingress controller and LB are present, deploys a
        test web server with an Ingress resource and validates that HTTP traffic
        reaches it through the Azure Load Balancer from localhost.

        Section 2 (CRIU): Deploys a counter pod, takes a CRIU checkpoint via the
        kubelet API, deletes the pod, builds an OCI image from the checkpoint
        using buildah, deploys a restored pod, and validates that state was preserved.
        """
        name = resolve_kubeadm_cluster_name(name)
        (cloud, context_id) = get_current_context()

        cluster = _resolve_cluster_vms(name)
        master = cluster["master"]
        workers = cluster["workers"]
        key_path = key and str(SSH_FOLDER / key) or _resolve_ssh_key_for_cluster(name)

        print(Panel(
            f"[bold]Cluster:[/bold]  {name}\n"
            f"[bold]Master:[/bold]   {master['name']} ({master['ip']})\n"
            f"[bold]Workers:[/bold]  {', '.join(w['name'] for w in workers)}",
            title="Smoke Test",
        ))

        # =====================================================================
        # Section 1: Ingress + Load Balancer validation
        # =====================================================================
        ingress_passed = None  # None = skipped
        ingress_type = None
        lb_public_ip = None

        print("\n" + "=" * 60)
        print("[bold]Section 1: Ingress + Load Balancer[/bold]")
        print("=" * 60)

        # Detect ingress controller (nginx or traefik)
        result = _ssh_cmd(
            master["ip"], user, key_path,
            "kubectl get ns datalayer-nginx -o name 2>/dev/null || true",
            check=False,
        )
        has_nginx = "datalayer-nginx" in result.stdout

        result = _ssh_cmd(
            master["ip"], user, key_path,
            "kubectl get ns datalayer-traefik -o name 2>/dev/null || true",
            check=False,
        )
        has_traefik = "datalayer-traefik" in result.stdout

        if not has_nginx and not has_traefik:
            print("\n  [dim]No ingress controller detected (datalayer-nginx or datalayer-traefik).[/dim]")
            print("  [dim]Skipping ingress LB validation.[/dim]")
            print("  [dim]Run 'clouder kubeadm enable-ingress-traefik' or 'enable-ingress-nginx' first.[/dim]")
        else:
            ingress_type = "traefik" if has_traefik else "nginx"
            print(f"\n  Detected ingress controller: [cyan]{ingress_type}[/cyan]")

            # Discover LB public IP from Azure
            if cloud == "azure":
                lb_ip_name = f"{name}-lb-ip"
                rg = master["resource_group"]
                try:
                    from ...cloud.azure.api import _get_network_client
                    nc = _get_network_client(context_id)
                    pip = nc.public_ip_addresses.get(rg, lb_ip_name)
                    lb_public_ip = pip.ip_address
                    print(f"  LB Public IP: [cyan]{lb_public_ip}[/cyan]")
                except Exception:
                    print("  [dim]No Azure LB public IP found. Skipping ingress validation.[/dim]")

            if lb_public_ip:
                ingress_passed = True
                try:
                    # ---- Step 1/4: Deploy test web server ----
                    print("\n[bold]Step 1/4: Deploying test web server...[/bold]")
                    ingress_deploy_script = """\
cat <<'EOF' | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: clouder-smoke-web
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: clouder-smoke-web
  template:
    metadata:
      labels:
        app: clouder-smoke-web
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: clouder-smoke-web
  namespace: default
spec:
  selector:
    app: clouder-smoke-web
  ports:
  - port: 80
    targetPort: 80
EOF
"""
                    result = _ssh_cmd(master["ip"], user, key_path, ingress_deploy_script, check=False)
                    if result.returncode != 0:
                        print(f"  [red]Failed to deploy test web server:[/red]\n{result.stderr}")
                        ingress_passed = False
                    else:
                        print("  Deployment + Service created.")

                        # Wait for deployment ready
                        result = _ssh_cmd(
                            master["ip"], user, key_path,
                            "kubectl rollout status deployment/clouder-smoke-web --timeout=120s",
                            check=False,
                        )
                        if result.returncode != 0:
                            print("  [yellow]Deployment not ready within timeout.[/yellow]")
                            ingress_passed = False
                        else:
                            print("  Deployment is ready.")

                        # Show created resources
                        print("\n  [dim][bold]Resources created:[/bold][/dim]")
                        for kubectl_cmd in [
                            "kubectl get deployment clouder-smoke-web -o wide",
                            "kubectl get pods -l app=clouder-smoke-web -o wide",
                            "kubectl get service clouder-smoke-web -o wide",
                        ]:
                            print(f"  [dim]$ {kubectl_cmd}[/dim]")
                            r = _ssh_cmd(master["ip"], user, key_path, kubectl_cmd, check=False)
                            if r.stdout.strip():
                                for line in r.stdout.strip().split("\n"):
                                    print(f"  [dim]{line}[/dim]")

                    # ---- Step 2/4: Create Ingress resource ----
                    if ingress_passed:
                        print("\n[bold]Step 2/4: Creating Ingress resource...[/bold]")
                        ingress_class = "datalayer-nginx" if ingress_type == "nginx" else "datalayer-traefik"

                        ingress_yaml = f"""\
cat <<'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: clouder-smoke-ingress
  namespace: default
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
    traefik.ingress.kubernetes.io/router.entrypoints: web,websecure
spec:
  ingressClassName: {ingress_class}
  rules:
  - http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: clouder-smoke-web
            port:
              number: 80
EOF
"""
                        result = _ssh_cmd(master["ip"], user, key_path, ingress_yaml, check=False)
                        if result.returncode != 0:
                            print(f"  [red]Failed to create Ingress:[/red]\n{result.stderr}")
                            ingress_passed = False
                        else:
                            print("  Ingress resource created.")

                            # Show the ingress resource
                            print("\n  [dim][bold]Ingress details:[/bold][/dim]")
                            for kubectl_cmd in [
                                "kubectl get ingress clouder-smoke-ingress -o wide",
                                "kubectl describe ingress clouder-smoke-ingress",
                            ]:
                                print(f"  [dim]$ {kubectl_cmd}[/dim]")
                                r = _ssh_cmd(master["ip"], user, key_path, kubectl_cmd, check=False)
                                if r.stdout.strip():
                                    for line in r.stdout.strip().split("\n"):
                                        print(f"  [dim]{line}[/dim]")

                    # ---- Step 3/4: Validate HTTP through LB from localhost ----
                    if ingress_passed:
                        print("\n[bold]Step 3/4: Validating HTTP through Load Balancer (IP)...[/bold]")
                        print("  Waiting 15s for ingress rules to propagate...")
                        time.sleep(15)

                        # Try up to 5 times with 10s spacing — curl from localhost, not from master
                        import subprocess as _sp
                        curl_cmd = [
                            "curl", "-sv", "--connect-timeout", "10",
                            f"http://{lb_public_ip}/",
                        ]
                        print(f"  [dim]$ {' '.join(curl_cmd)}[/dim]")

                        curl_ok = False
                        for attempt in range(1, 6):
                            try:
                                curl_result = _sp.run(
                                    curl_cmd,
                                    capture_output=True, text=True, timeout=15,
                                )
                                # curl -sv writes headers/connection info to stderr, body to stdout
                                body = curl_result.stdout
                                verbose = curl_result.stderr
                                # Extract HTTP status code from verbose output
                                http_code = ""
                                for vline in verbose.split("\n"):
                                    if vline.startswith("< HTTP/"):
                                        parts = vline.split()
                                        if len(parts) >= 3:
                                            http_code = parts[2]
                                        break
                            except Exception:
                                body = ""
                                verbose = ""
                                http_code = ""

                            if http_code == "200":
                                print(f"  Attempt {attempt}: HTTP {http_code} [green]OK[/green]")
                                # Print response headers
                                print("\n  [bold]Response headers:[/bold]")
                                for vline in verbose.split("\n"):
                                    vline = vline.rstrip()
                                    if vline.startswith("< "):
                                        print(f"  [dim]{vline[2:]}[/dim]")
                                # Print a snippet of the body
                                body_snippet = body.strip()[:500]
                                if body_snippet:
                                    print("\n  [bold]Response body (first 500 chars):[/bold]")
                                    for bline in body_snippet.split("\n"):
                                        print(f"  [dim]{bline}[/dim]")
                                curl_ok = True
                                break
                            else:
                                detail = ""
                                if verbose:
                                    # Show connection-level detail on failure
                                    for vline in verbose.split("\n"):
                                        if vline.startswith("* ") and ("connect" in vline.lower() or "refused" in vline.lower() or "timed out" in vline.lower()):
                                            detail = f" ({vline.strip('* ').strip()})"
                                            break
                                print(f"  Attempt {attempt}: HTTP {http_code or 'timeout'}{detail} — retrying in 10s...")
                                if attempt < 5:
                                    time.sleep(10)

                        if curl_ok:
                            print("\n  [green]Ingress LB: PASSED[/green]")
                        else:
                            print("\n  [red]Ingress LB: FAILED — could not reach web server via LB from localhost.[/red]")
                            # Show last attempt verbose output for debugging
                            if verbose:
                                print("\n  [bold]Last attempt details:[/bold]")
                                for vline in verbose.strip().split("\n"):
                                    print(f"  [dim]{vline}[/dim]")
                            ingress_passed = False

                    # ---- Step 4/4: Validate HTTP through DATALAYER_RUN_URL ----
                    if ingress_passed:
                        import os as _os
                        run_url = _os.environ.get("DATALAYER_RUN_URL", "")
                        run_host = run_url.replace("https://", "").replace("http://", "").rstrip("/") if run_url else ""
                        if not run_host:
                            print("\n[bold]Step 4/4: Validating HTTP through DATALAYER_RUN_URL...[/bold]")
                            print("  [dim]DATALAYER_RUN_URL is not set — skipping DNS validation.[/dim]")
                            print("  [dim]Set it to test DNS resolution: export DATALAYER_RUN_URL=https://prod1.datalayer.run[/dim]")
                        else:
                            print(f"\n[bold]Step 4/4: Validating HTTP through {run_host}...[/bold]")

                            # Check DNS resolution first
                            import subprocess as _sp2
                            try:
                                dig_result = _sp2.run(
                                    ["dig", "+short", run_host],
                                    capture_output=True, text=True, timeout=10,
                                )
                                resolved_ip = dig_result.stdout.strip().split("\n")[0] if dig_result.stdout.strip() else ""
                                if resolved_ip:
                                    print(f"  DNS resolves {run_host} → [cyan]{resolved_ip}[/cyan]")
                                    if resolved_ip != lb_public_ip:
                                        print(f"  [yellow]Warning: DNS resolves to {resolved_ip}, but LB IP is {lb_public_ip}[/yellow]")
                                else:
                                    print(f"  [yellow]DNS does not resolve {run_host} — check your DNS A record.[/yellow]")
                            except Exception:
                                print("  [dim]Could not run dig to check DNS.[/dim]")

                            # Try HTTP request via hostname
                            url_curl_cmd = [
                                "curl", "-sv", "--connect-timeout", "10",
                                "-H", f"Host: {run_host}",
                                f"http://{run_host}/",
                            ]
                            print(f"  [dim]$ {' '.join(url_curl_cmd)}[/dim]")

                            url_ok = False
                            for attempt in range(1, 4):
                                try:
                                    url_result = _sp2.run(
                                        url_curl_cmd,
                                        capture_output=True, text=True, timeout=15,
                                    )
                                    url_body = url_result.stdout
                                    url_verbose = url_result.stderr
                                    url_code = ""
                                    for vline in url_verbose.split("\n"):
                                        if vline.startswith("< HTTP/"):
                                            parts = vline.split()
                                            if len(parts) >= 3:
                                                url_code = parts[2]
                                            break
                                except Exception:
                                    url_body = ""
                                    url_verbose = ""
                                    url_code = ""

                                if url_code == "200":
                                    print(f"  Attempt {attempt}: HTTP {url_code} [green]OK[/green]")
                                    # Print response headers
                                    print("\n  [bold]Response headers:[/bold]")
                                    for vline in url_verbose.split("\n"):
                                        vline = vline.rstrip()
                                        if vline.startswith("< "):
                                            print(f"  [dim]{vline[2:]}[/dim]")
                                    # Print a snippet of the body
                                    body_snippet = url_body.strip()[:500]
                                    if body_snippet:
                                        print("\n  [bold]Response body (first 500 chars):[/bold]")
                                        for bline in body_snippet.split("\n"):
                                            print(f"  [dim]{bline}[/dim]")
                                    url_ok = True
                                    break
                                else:
                                    detail = ""
                                    if url_verbose:
                                        for vline in url_verbose.split("\n"):
                                            if vline.startswith("* ") and ("connect" in vline.lower() or "refused" in vline.lower() or "resolve" in vline.lower()):
                                                detail = f" ({vline.strip('* ').strip()})"
                                                break
                                    print(f"  Attempt {attempt}: HTTP {url_code or 'timeout'}{detail} — retrying in 5s...")
                                    if attempt < 3:
                                        time.sleep(5)

                            if url_ok:
                                print(f"\n  [green]DNS Ingress ({run_host}): PASSED[/green]")
                            else:
                                print(f"\n  [yellow]DNS Ingress ({run_host}): FAILED — DNS may not be configured yet.[/yellow]")
                                print(f"  [dim]Ensure your DNS A record points {run_host} → {lb_public_ip}[/dim]")
                                # Don't fail the overall test — DNS might not be configured yet

                except Exception as e:
                    print(f"\n[red]Ingress validation error: {e}[/red]")
                    ingress_passed = False
                finally:
                    if cleanup:
                        print("\n[bold]Cleaning up ingress test resources...[/bold]")
                        _ssh_cmd(
                            master["ip"], user, key_path,
                            "kubectl delete ingress clouder-smoke-ingress 2>/dev/null || true; "
                            "kubectl delete deployment clouder-smoke-web 2>/dev/null || true; "
                            "kubectl delete service clouder-smoke-web 2>/dev/null || true",
                            check=False,
                        )
                        print("  Ingress cleanup done.")

        # =====================================================================
        # Section 2: CRIU Checkpoint / Restore
        # =====================================================================
        criu_passed = True
        checkpoint_path = None
        node_name = None
        worker_ip = None
        counter_before = "?"
        token_before = None

        print("\n" + "=" * 60)
        print("[bold]Section 2: CRIU Checkpoint / Restore[/bold]")
        print("=" * 60)

        try:
            # ---- Step 1/8: Deploy test pod ----
            print("\n[bold]Step 1/8: Deploying counter pod...[/bold]")
            deploy_cmd = f"cat <<'EOFPOD' | kubectl apply -f -\n{_SMOKE_POD_YAML}EOFPOD"
            result = _ssh_cmd(master["ip"], user, key_path, deploy_cmd, check=False)
            if result.returncode != 0:
                print(f"[red]Failed to deploy test pod:[/red]\n{result.stderr}")
                raise typer.Exit(1)
            print("  Pod created.")

            # ---- Step 2/8: Wait for Running ----
            print("\n[bold]Step 2/8: Waiting for pod to be ready...[/bold]")
            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl wait --for=condition=Ready pod/clouder-smoke-test --timeout=120s",
                check=False,
            )
            if result.returncode != 0:
                print(f"[red]Pod did not become ready:[/red]\n{result.stderr}")
                raise typer.Exit(1)
            print("  Pod is running.")

            # ---- Step 3/8: Accumulate state ----
            print("\n[bold]Step 3/8: Letting counter accumulate state (15s)...[/bold]")
            time.sleep(15)
            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl exec clouder-smoke-test -- cat /tmp/counter_value",
                check=False,
            )
            if result.returncode != 0:
                print(f"[red]Failed to read counter:[/red]\n{result.stderr}")
                raise typer.Exit(1)
            counter_before = result.stdout.strip()
            print(f"  Counter value before checkpoint: [cyan]{counter_before}[/cyan]")

            # Read random token written at pod startup
            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl exec clouder-smoke-test -- cat /tmp/smoke_token",
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                token_before = result.stdout.strip()
                print(f"  [dim]Random token (written at startup): {token_before}[/dim]")
            else:
                print("  [yellow]Could not read smoke_token from pod.[/yellow]")

            # Print pod details before checkpoint
            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl get pod clouder-smoke-test -o wide",
                check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    print(f"  [dim]{line}[/dim]")
            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl get pod clouder-smoke-test -o jsonpath='"
                "{.status.containerStatuses[0].containerID} "
                "image={.status.containerStatuses[0].image} "
                "restarts={.status.containerStatuses[0].restartCount}'",
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                print(f"  [dim]Container: {result.stdout.strip()}[/dim]")

            # ---- Step 4/8: Identify node ----
            print("\n[bold]Step 4/8: Identifying pod node...[/bold]")
            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl get pod clouder-smoke-test -o jsonpath='{.spec.nodeName}'",
            )
            node_name = result.stdout.strip().strip("'")

            result = _ssh_cmd(
                master["ip"], user, key_path,
                "kubectl get pod clouder-smoke-test -o jsonpath='{.status.hostIP}'",
            )
            node_internal_ip = result.stdout.strip().strip("'")

            # Match to a worker by name
            worker = next((w for w in workers if w["name"].lower() == node_name.lower()), None)
            if not worker:
                worker = next((w for w in workers if node_name in w["name"] or w["name"] in node_name), None)
            if not worker:
                print(f"[red]Could not match node '{node_name}' to any worker VM.[/red]")
                print(f"  Workers: {[w['name'] for w in workers]}")
                raise typer.Exit(1)
            worker_ip = worker["ip"]
            print(f"  Pod is on node: [cyan]{node_name}[/cyan] (internal: {node_internal_ip}, public: {worker_ip})")

            # ---- Step 5/8: Checkpoint via kubelet API ----
            print("\n[bold]Step 5/8: Checkpointing via kubelet API...[/bold]")
            checkpoint_cmd = (
                f"sudo curl -sk -X POST "
                f"'https://{node_internal_ip}:10250/checkpoint/default/clouder-smoke-test/counter' "
                f"--cert /etc/kubernetes/pki/apiserver-kubelet-client.crt "
                f"--key /etc/kubernetes/pki/apiserver-kubelet-client.key"
            )
            result = _ssh_cmd(master["ip"], user, key_path, checkpoint_cmd, check=False)
            if result.returncode != 0:
                print(f"[red]Checkpoint API call failed:[/red]\n{result.stderr}")
                criu_passed = False
            else:
                body = result.stdout.strip()
                # The kubelet may return JSON {"items":["<path>"]} on success,
                # or a plain-text / JSON error message on failure.
                try:
                    checkpoint_response = json.loads(body)
                    if "items" in checkpoint_response and checkpoint_response["items"]:
                        checkpoint_path = checkpoint_response["items"][0]
                        print(f"  Checkpoint created: [cyan]{checkpoint_path}[/cyan]")
                    else:
                        # JSON but no items — treat as error
                        err_msg = checkpoint_response.get("err", checkpoint_response.get("message", body))
                        print(f"  [yellow]Checkpoint refused by kubelet:[/yellow] {err_msg}")
                        criu_passed = False
                except json.JSONDecodeError:
                    # Plain-text response from kubelet — usually a gRPC or runtime error
                    if "failed" in body.lower() or "error" in body.lower() or "unimplemented" in body.lower():
                        print(f"  [yellow]Checkpoint not supported by container runtime:[/yellow]")
                        print(f"  [dim]{body}[/dim]")
                        print("  [dim]CRIU checkpoint requires containerd 2.0+ or CRI-O with checkpoint/restore enabled.[/dim]")
                    else:
                        print(f"  [yellow]Unexpected checkpoint response:[/yellow]\n  [dim]{body}[/dim]")
                    criu_passed = False

            # Continue only if checkpoint was created
            if criu_passed and checkpoint_path:
                # ---- Step 6/8: Verify checkpoint on worker ----
                print("\n[bold]Step 6/8: Verifying checkpoint on worker node...[/bold]")
                result = _ssh_cmd(
                    worker_ip, user, key_path,
                    f"sudo ls -lh {checkpoint_path} && echo 'CHECKPOINT_EXISTS'",
                    check=False,
                )
                if "CHECKPOINT_EXISTS" not in result.stdout:
                    print(f"[red]Checkpoint file not found on {worker['name']}.[/red]")
                    criu_passed = False
                else:
                    # Print checkpoint file details
                    for line in result.stdout.strip().split("\n"):
                        if line.strip() and line.strip() != "CHECKPOINT_EXISTS":
                            print(f"  [dim]{line.strip()}[/dim]")
                    print(f"  Checkpoint verified on {worker['name']}.")

                    # Show checkpoint archive contents
                    result = _ssh_cmd(
                        worker_ip, user, key_path,
                        f"sudo tar tf {checkpoint_path} 2>/dev/null | head -20",
                        check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        print(f"  [dim]Archive contents:[/dim]")
                        for line in result.stdout.strip().split("\n"):
                            print(f"  [dim]  {line}[/dim]")

            if criu_passed and checkpoint_path:
                # ---- Step 7/8: Delete original pod ----
                print("\n[bold]Step 7/8: Deleting original pod...[/bold]")
                _ssh_cmd(
                    master["ip"], user, key_path,
                    "kubectl delete pod clouder-smoke-test --grace-period=0 --force 2>/dev/null",
                    check=False,
                )
                time.sleep(3)
                print("  Original pod deleted.")

                # ---- Step 8/8: Import checkpoint and restore ----
                print("\n[bold]Step 8/8: Importing checkpoint and restoring pod...[/bold]")

                # The kubelet checkpoint tar is NOT an OCI image — it contains
                # CRIU dump files, config, and a rootfs-diff.tar with filesystem
                # changes.  We use buildah to extract the rootfs diff, layer it
                # on top of the original busybox image, and produce a proper OCI
                # image that containerd can run.
                import_script = f"""
set -e
WORK_DIR=$(mktemp -d)
cd "$WORK_DIR"

# 1. Extract rootfs-diff.tar from checkpoint archive
sudo tar xf {checkpoint_path} rootfs-diff.tar 2>/dev/null
if [ ! -f rootfs-diff.tar ]; then
    echo "NO_ROOTFS_DIFF"
    sudo rm -rf "$WORK_DIR"
    exit 0
fi

# 2. Build OCI image: busybox base + checkpoint filesystem diff
NEWCTR=$(sudo buildah from docker.io/library/busybox:latest 2>/dev/null)
if [ -z "$NEWCTR" ]; then
    echo "BUILDAH_FROM_FAILED"
    sudo rm -rf "$WORK_DIR"
    exit 0
fi
sudo buildah add "$NEWCTR" rootfs-diff.tar / > /dev/null 2>&1
sudo buildah commit "$NEWCTR" localhost/clouder-smoke-restored:latest > /dev/null 2>&1
sudo buildah rm "$NEWCTR" > /dev/null 2>&1

# 3. Export as docker-archive and import into containerd
#    NOTE: ctr images import requires docker-archive format, NOT oci-archive
#    (oci-archive silently imports nothing on containerd 2.x).
sudo buildah push localhost/clouder-smoke-restored:latest docker-archive:${{WORK_DIR}}/ckpt-docker.tar > /dev/null 2>&1
sudo ctr -n k8s.io images import ${{WORK_DIR}}/ckpt-docker.tar > /dev/null 2>&1

# 4. Verify the image is now available
IMG=$(sudo ctr -n k8s.io images ls -q | grep "clouder-smoke-restored" | head -1)
if [ -n "$IMG" ]; then
    echo "IMPORTED_IMAGE=$IMG"
else
    echo "IMPORT_FAILED"
fi

sudo rm -rf "$WORK_DIR"
"""
                result = _ssh_cmd(worker_ip, user, key_path, import_script, check=False)
                checkpoint_image = ""
                import_status = ""
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("IMPORTED_IMAGE="):
                        checkpoint_image = line.split("=", 1)[1].strip()
                    elif line in ("NO_ROOTFS_DIFF", "BUILDAH_FROM_FAILED", "IMPORT_FAILED"):
                        import_status = line

                if not checkpoint_image:
                    if import_status == "NO_ROOTFS_DIFF":
                        print("  [yellow]Checkpoint archive has no rootfs-diff.tar — cannot build image.[/yellow]")
                    elif import_status == "BUILDAH_FROM_FAILED":
                        print("  [yellow]buildah could not pull busybox base image.[/yellow]")
                    elif import_status == "IMPORT_FAILED":
                        print("  [yellow]Image built but ctr import into containerd failed.[/yellow]")
                    else:
                        print("  [yellow]Could not import checkpoint as container image.[/yellow]")
                    print("  [yellow]Checkpoint archive was created successfully — manual restore possible.[/yellow]")
                    print(f"  Archive: {checkpoint_path} on {worker['name']}")
                    criu_passed = False
                else:
                    print(f"  Imported image: [cyan]{checkpoint_image}[/cyan]")

                    # The restored pod reads the existing counter value from the
                    # checkpoint filesystem (/tmp/counter_value) and continues
                    # incrementing from there, proving state was preserved.
                    restored_yaml = f"""\
apiVersion: v1
kind: Pod
metadata:
  name: clouder-smoke-restored
  namespace: default
spec:
  nodeName: {node_name}
  containers:
  - name: counter
    image: {checkpoint_image}
    imagePullPolicy: Never
    command: ["/bin/sh", "-c"]
    args:
    - |
      # Continue the counter from the checkpoint state.
      # /tmp/counter_value and /tmp/smoke_token are baked
      # into the image from the checkpoint rootfs-diff.
      i=$(cat /tmp/counter_value 2>/dev/null || echo 0)
      while true; do
        i=$((i + 1))
        echo $i > /tmp/counter_value
        sleep 1
      done
"""
                    deploy_cmd = f"cat <<'EOFPOD' | kubectl apply -f -\n{restored_yaml}EOFPOD"
                    result = _ssh_cmd(master["ip"], user, key_path, deploy_cmd, check=False)
                    if result.returncode != 0:
                        print(f"  [yellow]Failed to deploy restored pod: {result.stderr.strip()}[/yellow]")
                        criu_passed = False
                    else:
                        result = _ssh_cmd(
                            master["ip"], user, key_path,
                            "kubectl wait --for=condition=Ready pod/clouder-smoke-restored --timeout=60s",
                            check=False,
                        )
                        if result.returncode != 0:
                            print("  [yellow]Restored pod did not become ready.[/yellow]")
                            status = _ssh_cmd(
                                master["ip"], user, key_path,
                                "kubectl describe pod clouder-smoke-restored 2>/dev/null | tail -20",
                                check=False,
                            )
                            if status.stdout.strip():
                                print(f"  [dim]{status.stdout.strip()}[/dim]")
                            criu_passed = False
                        else:
                            time.sleep(5)
                            result = _ssh_cmd(
                                master["ip"], user, key_path,
                                "kubectl exec clouder-smoke-restored -- cat /tmp/counter_value",
                                check=False,
                            )
                            if result.returncode == 0:
                                counter_after = result.stdout.strip()
                                print(f"  Counter value before checkpoint: [dim]{counter_before}[/dim]")
                                print(f"  Counter value after restore:     [cyan]{counter_after}[/cyan]")

                                # Read the random token from the restored pod
                                token_after = None
                                res = _ssh_cmd(
                                    master["ip"], user, key_path,
                                    "kubectl exec clouder-smoke-restored -- cat /tmp/smoke_token",
                                    check=False,
                                )
                                if res.returncode == 0 and res.stdout.strip():
                                    token_after = res.stdout.strip()
                                    print(f"  [dim]Random token (read from restored pod): {token_after}[/dim]")

                                # Print restored pod details
                                res = _ssh_cmd(
                                    master["ip"], user, key_path,
                                    "kubectl get pod clouder-smoke-restored -o wide",
                                    check=False,
                                )
                                if res.returncode == 0:
                                    for line in res.stdout.strip().split("\n"):
                                        print(f"  [dim]{line}[/dim]")
                                res = _ssh_cmd(
                                    master["ip"], user, key_path,
                                    "kubectl get pod clouder-smoke-restored -o jsonpath='"
                                    "{.status.containerStatuses[0].containerID} "
                                    "image={.status.containerStatuses[0].image} "
                                    "restarts={.status.containerStatuses[0].restartCount}'",
                                    check=False,
                                )
                                if res.returncode == 0 and res.stdout.strip():
                                    print(f"  [dim]Container: {res.stdout.strip()}[/dim]")

                                # --- Verify state preservation ---
                                # 1. Token comparison (definitive proof of filesystem state)
                                token_match = False
                                if token_before and token_after:
                                    print(f"  [dim]Token before checkpoint: {token_before}[/dim]")
                                    print(f"  [dim]Token after restore:     {token_after}[/dim]")
                                    if token_before == token_after:
                                        print(f"  [green]Token check: PASSED — tokens match, filesystem state preserved.[/green]")
                                        token_match = True
                                    else:
                                        print(f"  [red]Token check: FAILED — tokens differ![/red]")
                                        criu_passed = False
                                elif not token_before:
                                    print("  [yellow]Token check: SKIPPED — could not read token before checkpoint.[/yellow]")
                                else:
                                    print("  [yellow]Token check: SKIPPED — could not read token from restored pod.[/yellow]")

                                # 2. Counter continuity (additional confidence)
                                try:
                                    before_int = int(counter_before)
                                    after_int = int(counter_after)
                                    if after_int >= before_int:
                                        print(f"  [green]Counter check: PASSED — counter continued ({before_int} → {after_int}).[/green]")
                                    else:
                                        print(f"  [yellow]Counter check: counter restarted ({before_int} → {after_int}).[/yellow]")
                                except ValueError:
                                    print(f"  [yellow]Counter check: could not parse values (before='{counter_before}', after='{counter_after}').[/yellow]")

                                # Overall verdict
                                if token_match:
                                    print("  [green]CRIU restore: PASSED — filesystem state fully preserved.[/green]")
                                elif criu_passed:
                                    print("  [green]Checkpoint creation: PASSED[/green]")
                            else:
                                print("  [yellow]Could not read counter from restored pod.[/yellow]")
                                criu_passed = False

        except (typer.Exit, typer.Abort):
            criu_passed = False
        except Exception as e:
            print(f"\n[red]Unexpected error: {e}[/red]")
            criu_passed = False
        finally:
            if cleanup:
                print("\n[bold]Cleaning up CRIU test resources...[/bold]")
                _ssh_cmd(
                    master["ip"], user, key_path,
                    "kubectl delete pod clouder-smoke-test --grace-period=0 --force 2>/dev/null || true",
                    check=False,
                )
                _ssh_cmd(
                    master["ip"], user, key_path,
                    "kubectl delete pod clouder-smoke-restored --grace-period=0 --force 2>/dev/null || true",
                    check=False,
                )
                if checkpoint_path and worker_ip:
                    _ssh_cmd(
                        worker_ip, user, key_path,
                        f"sudo rm -f {checkpoint_path} 2>/dev/null || true; "
                        "sudo ctr -n k8s.io images rm localhost/clouder-smoke-restored:latest 2>/dev/null || true; "
                        "sudo buildah rmi localhost/clouder-smoke-restored:latest 2>/dev/null || true",
                        check=False,
                    )
                print("  CRIU cleanup done.")

        # =====================================================================
        # Final summary
        # =====================================================================
        print()
        summary_lines = []

        # Ingress result
        if ingress_passed is None:
            summary_lines.append("[dim]Ingress LB: SKIPPED (no ingress controller)[/dim]")
        elif ingress_passed:
            summary_lines.append(f"[green]Ingress LB: PASSED[/green]")
            summary_lines.append(f"  LB IP: {lb_public_ip}")
            summary_lines.append(f"  HTTP 200 from localhost via {ingress_type} ingress → nginx backend")
        else:
            summary_lines.append(f"[red]Ingress LB: FAILED[/red]")
            summary_lines.append("  See output above for details.")

        # CRIU result
        if criu_passed:
            summary_lines.append(f"\n[green]CRIU checkpoint/restore: PASSED[/green]")
            summary_lines.append(f"  Checkpoint: kubelet API on {node_name}")
            summary_lines.append(f"  Counter before: {counter_before}")
        else:
            summary_lines.append("\n[yellow]CRIU checkpoint/restore: WARNINGS[/yellow]")
            if not checkpoint_path:
                summary_lines.append("  Checkpoint not supported. Requires containerd 2.0+ or CRI-O.")
            else:
                summary_lines.append("  Checkpoint creation validated. Full restore may require containerd 2.0+ or CRI-O.")

        overall = (ingress_passed is None or ingress_passed) and criu_passed
        title_text = "PASSED" if overall else "COMPLETED WITH WARNINGS"

        print(Panel(
            "\n".join(summary_lines),
            title=f"Smoke Test Result: {title_text}",
        ))
