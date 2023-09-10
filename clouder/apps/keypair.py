import warnings

from pathlib import Path

from datalayer.application import DatalayerApp, NoStart

from .._version import __version__


SSH_FOLDER = Path.home() / ".ssh"


class KeyPairListApp(DatalayerApp):
    """An application to list the key pairs."""

    version = __version__
    description = """
   An application to list the key pairs
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


class ClouderKeyPairApp(DatalayerApp):
    """An application for the key pairs."""

    version = __version__
    description = """
    Manage the key pairs
    """

    subcommands = {
        "list": (KeyPairListApp, KeyPairListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error("One of `list` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
