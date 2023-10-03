from datalayer.application import NoStart

from ..operator.operator import start_operator, stop_operator
from ..util.shell import run_command
from .base import ClouderBaseApp


class ClouderOperatorStartApp(ClouderBaseApp):
    """An application to start the operator."""

    description = """
      An application to start the operator
    """

    def start(self):
        """Start the app."""
        start_operator()
        import time 
        while True:
            time.sleep(100)


class ClouderOperatorStopApp(ClouderBaseApp):
    """An application to stop the operator."""

    description = """
      An application to stop the operator
    """

    def start(self):
        """Start the app."""
        stop_operator()


class ClouderOperatorCRDApp(ClouderBaseApp):
    """An application to apply the CRD."""

    description = """
      An application to apply the CRD
    """

    def start(self):
        """Start the app."""
        run_command(["", "", "crd-apply"])

class ClouderOperatorApp(ClouderBaseApp):
    """An application to get you started with Clouder."""

    description = """
      Get you started with Clouder.
    """

    subcommands = {
        "stop": (ClouderOperatorStopApp, ClouderOperatorStopApp.description.splitlines()[0]),
        "start": (ClouderOperatorStartApp, ClouderOperatorStartApp.description.splitlines()[0]),
        "crd": (ClouderOperatorCRDApp, ClouderOperatorCRDApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'` `'.join(ClouderOperatorApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
