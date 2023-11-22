import warnings

from datalayer.application import NoStart

from .base import ClouderBaseApp


class ConfigExportApp(ClouderBaseApp):
    """An application to export the configuration."""

    description = """
   An application to export the configuration
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) > 1:  # pragma: no cover
            warnings.warn("Too many arguments were provided.")
            self.exit(1)
        self.log.info("ClouderConfigApp %s", self.version)


class ClouderConfigApp(ClouderBaseApp):
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
            self.log.info("Clouder - Version %s - Cloud %s ", super().version, super().cloud)
            self.log.error(f"One of `{'`, `'.join(ClouderConfigApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
