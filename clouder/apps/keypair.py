import warnings

from pathlib import Path

from datalayer.application import NoStart

from .base import ClouderApp


SSH_FOLDER = Path.home() / ".ssh"


class KeyPairListApp(ClouderApp):
    """An application to list the key pairs."""

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


class ClouderKeyPairApp(ClouderApp):
    """An application for the key pairs."""

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
