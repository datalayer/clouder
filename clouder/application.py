"""Clouder application."""

from ._version import __version__
from .apps._base import ClouderBaseApp
from .apps import (ClouderBaseApp, ClouderContextApp,
                   ClouderSSHKeyApp, ClouderRunSbinApp,
                   ClouderKubernetesApp, ClouderOperatorApp,
                   ClouderMeApp, ClouderRunShellApp, ClouderS3App,
                   ClouderVirtualMachineApp, ClouderInfoApp)


class ClouderApp(ClouderBaseApp):
    """The main Clouder application."""

    name = "clouder"

    description = """
      The main Clouder application.
    """

    subcommands = {
        "ctx": (ClouderContextApp, ClouderContextApp.description.splitlines()[0]),
        "info": (ClouderInfoApp, ClouderInfoApp.description.splitlines()[0]),
        "k8s": (ClouderKubernetesApp, ClouderKubernetesApp.description.splitlines()[0]),
        "me": (ClouderMeApp, ClouderMeApp.description.splitlines()[0]),
        "operator": (ClouderOperatorApp, ClouderOperatorApp.description.splitlines()[0]),
        "s3": (ClouderS3App, ClouderS3App.description.splitlines()[0]),
        "sh": (ClouderRunShellApp, ClouderRunShellApp.description.splitlines()[0]),
        "ssh-key": (ClouderSSHKeyApp, ClouderSSHKeyApp.description.splitlines()[0]),
        "vm": (ClouderVirtualMachineApp, ClouderVirtualMachineApp.description.splitlines()[0]),
    }

    def initialize(self, argv=None):
        """Subclass because the ExtensionApp.initialize() method does not take arguments"""
        super(ClouderApp, self).initialize(argv)

    def start(self):
        super(ClouderApp, self).start()
        clouder_sbin = ClouderRunSbinApp()
        clouder_sbin.start()


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

main = launch_new_instance = ClouderApp.launch_instance

if __name__ == "__main__":
    main()
