import warnings

from datalayer.application import DatalayerApp, NoStart

from .._version import __version__


class ConfigExportApp(DatalayerApp):
    """An application to export the configuration."""

    version = __version__
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


class ClouderConfigApp(DatalayerApp):
    """An application for the configuration."""

    version = __version__
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
