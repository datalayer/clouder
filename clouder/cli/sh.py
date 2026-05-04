"""Clouder CLI - Shell/sbin runner commands."""

import sys
from typing import List, Optional

import typer

from ..util.utils import run_sbin, run_shell

sh_app = typer.Typer(no_args_is_help=True)


@sh_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def sh_run(ctx: typer.Context):
    """Run a predefined shell script.

    Pass the script name and any arguments after --.
    Example: clouder sh run -- my-script arg1 arg2
    """
    args = ctx.args
    if not args:
        typer.echo("You must provide a shell script to run.", err=True)
        raise typer.Exit(1)
    cmd = "-".join(args)
    cmd_args = ["clouder", "sh", cmd]
    run_shell(cmd_args)


@sh_app.command("sbin", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def sh_sbin(ctx: typer.Context):
    """Run a predefined sbin script.

    Example: clouder sh sbin about
    """
    args = ctx.args
    if not args:
        args = ["about"]
    shell_args = ["shell"] + ["clouder", "sh"] + args
    cmd = "-".join(shell_args[2:])
    cmd_args = shell_args[0:2] + [cmd]
    run_sbin(cmd_args)
