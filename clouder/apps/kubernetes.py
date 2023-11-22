import warnings

from pathlib import Path

from datalayer.application import NoStart

from .base import ClouderBaseApp


SSH_FOLDER = Path.home() / ".ssh"


class KubernetesListApp(ClouderBaseApp):
    """An application to list the kubernetes clusters."""

    description = """
      An application to list the kubernetes clusters
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) > 1:  # pragma: no cover
            warnings.warn("Too many arguments were provided.")
            self.exit(1)
        for file in SSH_FOLDER.iterdir():
            if file.name.endswith(".pub"):
                print(file.name.replace(".pub", ""))


class ClouderKubernetesApp(ClouderBaseApp):
    """An application for the kubernetes clusters."""

    description = """
      Manage the kubernetes clusters
    """

    subcommands = {
        "list": (KubernetesListApp, KubernetesListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'`, `'.join(ClouderKubernetesApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
