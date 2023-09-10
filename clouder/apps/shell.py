import os
import sys

from datalayer.application import DatalayerApp

from .._version import __version__
from ..util.shell import run_command


class ClouderShellApp(DatalayerApp):
    """A shell application for Clouder."""

    version = __version__
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
        # TODO Move this to a shell.
        os.environ["OS"] = "MACOS"
        os.environ["DATALAYER_K8S_VERSION"] = "1.25.4"
        #
        if len(args) > 2:
            run_command(args)
        else:
            self.log.error("You must provide a shell script to run.")
