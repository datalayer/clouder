import sys

from datalayer.application import DatalayerApp

from .._version import __version__
from ..util.shell import run_command


class ClouderUpApp(DatalayerApp):
    """An application to get you started with Clouder."""

    version = __version__
    description = """
      Get you started with Clouder.
    """

    def start(self):
        super().start()
        self.log.info("Get started...")
