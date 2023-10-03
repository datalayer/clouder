"""Clouder application."""

from ._version import __version__
from .apps import (ClouderBaseApp, ClouderConfigApp, ClouderKeysApp,
                   ClouderKubernetesApp, ClouderProfileApp, ClouderOperatorApp,
                   ClouderSetupApp, ClouderShellApp, ClouderStatusApp,
                   ClouderVirtualMachineApp, ClouderProjectsApp)


class ClouderApp(ClouderBaseApp):
    """The main Clouder application."""

    name = "clouder"
    description = """
      The main Clouder application.
    """

    subcommands = {
        "config": (ClouderConfigApp, ClouderConfigApp.description.splitlines()[0]),
        "k8s": (ClouderKubernetesApp, ClouderKubernetesApp.description.splitlines()[0]),
        "keys": (ClouderKeysApp, ClouderKeysApp.description.splitlines()[0]),
        "operator": (ClouderOperatorApp, ClouderOperatorApp.description.splitlines()[0]),
        "profile": (ClouderProfileApp, ClouderProfileApp.description.splitlines()[0]),
        "setup": (ClouderSetupApp, ClouderSetupApp.description.splitlines()[0]),
        "sh": (ClouderShellApp, ClouderShellApp.description.splitlines()[0]),
        "status": (ClouderStatusApp, ClouderStatusApp.description.splitlines()[0]),
        "projects": (ClouderProjectsApp, ClouderProjectsApp.description.splitlines()[0]),
        "vm": (ClouderVirtualMachineApp, ClouderVirtualMachineApp.description.splitlines()[0]),
    }

    def initialize(self, argv=None):
        """Subclass because the ExtensionApp.initialize() method does not take arguments"""
        super().initialize()

    def start(self):
        super(ClouderApp, self).start()
        clouder_shell = ClouderShellApp(is_shell_command=False)
        clouder_shell.start()


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

main = launch_new_instance = ClouderApp.launch_instance

if __name__ == "__main__":
    main()
