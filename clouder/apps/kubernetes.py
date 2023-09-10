import warnings

from pathlib import Path

from datalayer.application import DatalayerApp, NoStart

from .._version import __version__


SSH_FOLDER = Path.home() / ".ssh"


class KubernetesListApp(DatalayerApp):
    """An application to list the virtual machines."""

    version = __version__
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


class ClouderKubernetesApp(DatalayerApp):
    """An application for the key pairs."""

    version = __version__
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
