import warnings

from datalayer.application import NoStart

from .base import ClouderApp


class ConfigExportApp(ClouderApp):
    """An application to export the configuration."""

    description = """
   An application to export the configuration
    """

    def initialize(self, *args, **kwargs):
        """Initialize the app."""
        super().initialize(*args, **kwargs)

    def start(self):
        """Start the app."""
        if len(self.extra_args) > 1:  # pragma: no cover
            warnings.warn("Too many arguments were provided for workspace export.")
            self.exit(1)
        self.log.info("ClouderConfigApp %s", self.version)


class ClouderConfigApp(ClouderApp):
    """An application for the configuration."""

    description = """
    Manage the configuration
    """

    subcommands = {
        "export": (ConfigExportApp, ConfigExportApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error("One of `export` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
