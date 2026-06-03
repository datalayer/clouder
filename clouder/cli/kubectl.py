"""Clouder CLI - kubectl wrapper using persisted kubeconfig."""

import os
import sys

import click

from ..util.utils import kubeadm_kubeconfig_path


@click.command(
    "kubectl",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True},
)
@click.argument("name")
@click.pass_context
def kubectl_command(ctx, name):
    """Run kubectl commands using the kubeconfig for a Clouder cluster.

    Example: clouder kubectl my-cluster get nodes
    """
    kubeconfig_path = kubeadm_kubeconfig_path(name)
    if not kubeconfig_path.exists():
        click.echo(
            f"Kubeconfig not found: {kubeconfig_path}\n"
            f"Run 'clouder kubeadm get-config {name}' first.",
            err=True,
        )
        ctx.exit(1)

    # Build kubectl command with extra args
    kubectl_args = ["kubectl", f"--kubeconfig={kubeconfig_path}"] + ctx.args

    if not ctx.args:
        click.echo(f"Usage: clouder kubectl {name} <kubectl-args>")
        click.echo(f"  e.g. clouder kubectl {name} get nodes")
        click.echo(f"\nKubeconfig: {kubeconfig_path}")
        ctx.exit(0)

    os.execvp("kubectl", kubectl_args)
