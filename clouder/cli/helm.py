"""Clouder CLI - helm wrapper using persisted kubeconfig."""

import os

import click

from ..util.utils import kubeadm_kubeconfig_path
from .kubeadm._helpers import resolve_kubeadm_cluster_name


@click.command(
    "helm",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True},
)
@click.argument("name", required=False)
@click.pass_context
def helm_command(ctx, name):
    """Run helm commands using the kubeconfig for a Clouder cluster.

    Examples:
      clouder helm my-cluster list -A
      clouder helm list -A
    """
    passthrough_args = list(ctx.args)
    cluster_name = name

    # If first positional token is not a known cluster, treat it as helm args
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

    # Build helm command with extra args
    helm_args = ["helm", f"--kubeconfig={kubeconfig_path}"] + passthrough_args

    if not passthrough_args:
        click.echo("Usage: clouder helm [cluster-name] <helm-args>")
        click.echo(f"  e.g. clouder helm {cluster_name} list -A")
        click.echo("  e.g. clouder helm list -A")
        click.echo(f"  e.g. clouder helm {cluster_name} install my-release my-chart")
        click.echo(f"\nKubeconfig: {kubeconfig_path}")
        ctx.exit(0)

    os.execvp("helm", helm_args)
