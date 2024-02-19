"""Clouder virtual machine application."""

import warnings

from datalayer.application import NoStart

from ._base import ClouderBaseApp
from ..util.utils import SSH_FOLDER


class VirtualMachineListApp(ClouderBaseApp):
    """An application to list the virtual machines."""

    description = """
      An application to list the virtual machines.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        for file in SSH_FOLDER.iterdir():
            if file.name.endswith(".pub"):
                print(file.name.replace(".pub", ""))


class ClouderVirtualMachineApp(ClouderBaseApp):
    """An application for the virtual machines."""

    description = """
      Manage the virtual machines.
    """

    subcommands = {
        "ls": (VirtualMachineListApp, VirtualMachineListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'`, `'.join(ClouderVirtualMachineApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
