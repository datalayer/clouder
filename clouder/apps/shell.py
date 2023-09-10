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

    def start(self):
        super().start()
        args = sys.argv
        if len(args) > 2:
            run_command(args)
        else:
            self.log.error("You must provide a shell script to run.")
