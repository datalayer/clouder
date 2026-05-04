"""Clouder CLI - Main entry point using Typer."""

import typer

from .._version import __version__
from .ctx import ctx_app
from .vm import vm_app
from .k8s import k8s_app
from .ssh_key import ssh_key_app
from .s3 import s3_app
from .info import info_app
from .operator import operator_app
from .sh import sh_app
from .ssh import ssh_app
from .kubeadm import kubeadm_app
from .kubectl import kubectl_command
from .helm import helm_command
from .azure_cmd import azure_app
from .aws_cmd import aws_app

app = typer.Typer(
    name="clouder",
    help="Clouder - Cloud-agnostic Kubernetes cluster management with CRIU support.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(ctx_app, name="ctx", help="Manage Clouder contexts.")
app.add_typer(vm_app, name="vm", help="Manage virtual machines.")
app.add_typer(k8s_app, name="k8s", help="Manage Kubernetes clusters.")
app.add_typer(ssh_key_app, name="ssh-key", help="Manage SSH keys.")
app.add_typer(s3_app, name="s3", help="Manage S3 buckets.")
app.add_typer(info_app, name="info", help="Show info about the current context.")
app.add_typer(operator_app, name="operator", help="Manage the Clouder operator.")
app.add_typer(sh_app, name="sh", help="Run shell/sbin scripts.")
app.add_typer(ssh_app, name="ssh", help="SSH into a virtual machine.")
app.add_typer(kubeadm_app, name="kubeadm", help="Provision and setup kubeadm Kubernetes clusters.")
app.add_typer(azure_app, name="azure", help="Azure cloud operations.")
app.add_typer(aws_app, name="aws", help="AWS cloud operations.")


def version_callback(value: bool):
    if value:
        typer.echo(f"Clouder {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
):
    """Clouder - Cloud-agnostic Kubernetes cluster management with CRIU support."""
    pass


@app.command("server")
def server():
    """Start the Clouder Jupyter server extension."""
    from ..serverapplication import main as server_main
    server_main()


# Register the kubectl Click command directly on Typer's Click group.
# Typer uses `add_typer` for sub-Typer apps, but kubectl needs raw Click
# passthrough (ignore_unknown_options) which Typer can't handle natively.
# We monkey-patch app's __call__ to inject kubectl into every Click group Typer creates.
import functools as _functools

_original_typer_call = type(app).__call__

@_functools.wraps(_original_typer_call)
def _patched_call(self, *args, **kwargs):
    click_app = typer.main.get_command(self)
    click_app.add_command(kubectl_command)
    click_app.add_command(helm_command)
    return click_app(*args, **kwargs)

type(app).__call__ = _patched_call


if __name__ == "__main__":
    app()
