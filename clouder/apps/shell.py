"""Clouder shell."""

import sys

from .base import ClouderBaseApp

from ..util.shell import run_command


class ClouderShellApp(ClouderBaseApp):
    """A shell application for Clouder."""

    description = """
      Run predefined shell scripts.
    """

    is_shell_command = True

    def __init__(self, is_shell_command):
        super().__init__()
        self.is_shell_command = is_shell_command


    def start(self):
        super().start()
        if not self.is_shell_command:
            args = ["shell"]
            args = args + sys.argv
        else:
            args = sys.argv
        if len(args) > 2:
            cmd = "-".join(args[2:])
            cmd_args = args[0:2] + [cmd]
            run_command(cmd_args)
        else:
            self.log.error("You must provide a shell script to run.")
