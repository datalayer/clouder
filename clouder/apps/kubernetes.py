import warnings

from pathlib import Path

from datalayer.application import NoStart

from .base import ClouderApp


SSH_FOLDER = Path.home() / ".ssh"


class KubernetesListApp(ClouderApp):
    """An application to list the virtual machines."""

    description = """
      An application to list the virtual machines
    """

    def initialize(self, *args, **kwargs):
        """Initialize the app."""
        super().initialize(*args, **kwargs)

    def start(self):
        """Start the app."""
        if len(self.extra_args) > 1:  # pragma: no cover
            warnings.warn("Too many arguments were provided for workspace export.")
            self.exit(1)
        for file in SSH_FOLDER.iterdir():
            if file.name.endswith(".pub"):
                print(file.name.replace(".pub", ""))


class ClouderKubernetesApp(ClouderApp):
    """An application for the key pairs."""

    description = """
      Manage the virtual machines
    """

    subcommands = {
        "list": (KubernetesListApp, KubernetesListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error("One of `list` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
