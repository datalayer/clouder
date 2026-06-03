"""Clouder CLI - helm wrapper using persisted kubeconfig."""

import os
import sys

import click

from ..util.utils import kubeadm_kubeconfig_path


@click.command(
    "helm",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True},
)
@click.argument("name")
@click.pass_context
def helm_command(ctx, name):
    """Run helm commands using the kubeconfig for a Clouder cluster.

    Example: clouder helm my-cluster list -A
    """
    kubeconfig_path = kubeadm_kubeconfig_path(name)
    if not kubeconfig_path.exists():
        click.echo(
            f"Kubeconfig not found: {kubeconfig_path}\n"
            f"Run 'clouder kubeadm get-config {name}' first.",
            err=True,
        )
        ctx.exit(1)

    # Build helm command with extra args
    helm_args = ["helm", f"--kubeconfig={kubeconfig_path}"] + ctx.args

    if not ctx.args:
        click.echo(f"Usage: clouder helm {name} <helm-args>")
        click.echo(f"  e.g. clouder helm {name} list -A")
        click.echo(f"  e.g. clouder helm {name} install my-release my-chart")
        click.echo(f"\nKubeconfig: {kubeconfig_path}")
        ctx.exit(0)

    os.execvp("helm", helm_args)
