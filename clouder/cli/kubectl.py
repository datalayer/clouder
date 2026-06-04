"""Clouder CLI - kubectl wrapper using persisted kubeconfig."""

import os

import click

from ..util.utils import kubeadm_kubeconfig_path
from .kubeadm._helpers import resolve_kubeadm_cluster_name


@click.command(
    "kubectl",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True},
)
@click.argument("name", required=False)
@click.pass_context
def kubectl_command(ctx, name):
    """Run kubectl commands using the kubeconfig for a Clouder cluster.

    Examples:
      clouder kubectl my-cluster get nodes
      clouder kubectl get nodes
    """
    passthrough_args = list(ctx.args)
    cluster_name = name

    # If the first positional token is not a known cluster, treat it as a kubectl argument
    # and resolve the cluster from configured default kubeadm cluster.
    if cluster_name:
        explicit_kubeconfig = kubeadm_kubeconfig_path(cluster_name)
        explicit_metadata = explicit_kubeconfig.parent / "kubeadm.json"
        if not explicit_kubeconfig.exists() and not explicit_metadata.exists():
            passthrough_args = [cluster_name] + passthrough_args
            cluster_name = None

    cluster_name = resolve_kubeadm_cluster_name(cluster_name)

    kubeconfig_path = kubeadm_kubeconfig_path(cluster_name)
    if not kubeconfig_path.exists():
        click.echo(
            f"Kubeconfig not found: {kubeconfig_path}\n"
            f"Run 'clouder kubeadm get-config {cluster_name}' first.",
            err=True,
        )
        ctx.exit(1)

    # Build kubectl command with extra args
    kubectl_args = ["kubectl", f"--kubeconfig={kubeconfig_path}"] + passthrough_args

    if not passthrough_args:
        click.echo("Usage: clouder kubectl [cluster-name] <kubectl-args>")
        click.echo(f"  e.g. clouder kubectl {cluster_name} get nodes")
        click.echo("  e.g. clouder kubectl get nodes")
        click.echo(f"\nKubeconfig: {kubeconfig_path}")
        ctx.exit(0)

    os.execvp("kubectl", kubectl_args)
