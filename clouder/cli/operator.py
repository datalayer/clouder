"""Clouder CLI - Operator commands."""

import time

import typer

from ..operator.operator import start_operator, stop_operator
from ..util.utils import run_sbin_direct

operator_app = typer.Typer(no_args_is_help=True)


@operator_app.command("start")
def operator_start():
    """Start the Clouder operator."""
    typer.echo("Starting Clouder operator...")
    start_operator()
    try:
        while True:
            time.sleep(100)
    except KeyboardInterrupt:
        typer.echo("\nStopping operator.")


@operator_app.command("stop")
def operator_stop():
    """Stop the Clouder operator."""
    stop_operator()
    typer.echo("Operator stopped.")


@operator_app.command("crd")
def operator_crd():
    """Apply the CRD definitions."""
    run_sbin_direct(["", "", "crd-apply"])
