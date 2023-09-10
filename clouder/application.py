from pathlib import Path

from traitlets import Bool, Unicode

from datalayer.application import DatalayerApp, base_aliases, base_flags

from ._version import __version__

from .apps import (
    ClouderConfigApp, ClouderKeyPairApp, ClouderKubernetesApp, ClouderShellApp,
    ClouderUpApp, ClouderVirtualMachineApp,
)


HERE = Path(__file__).parent


clouder_aliases = dict(base_aliases)
clouder_aliases["cloud"] = "ClouderApp.cloud"

clouder_flags = dict(base_flags)
clouder_flags["dev-build"] = (
    {"ClouderApp": {"dev_build": True}},
    "Build in development mode.",
)


class ClouderApp(DatalayerApp):
    name = "clouder"
    description = """
    Import or export a JupyterLab workspace or list all the JupyterLab workspaces

    You can use the "config" sub-commands.
    """
    version = __version__

    aliases = clouder_aliases
    flags = clouder_flags

    cloud = Unicode("ovh", config=True, help="The cloud to use.")

    minimize = Bool(
        True,
        config=True,
        help="Whether to minimize a production build (defaults to True).",
    )

    subcommands = {
        "config": (ClouderConfigApp, ClouderConfigApp.description.splitlines()[0]),
        "k8s": (ClouderKubernetesApp, ClouderKubernetesApp.description.splitlines()[0]),
        "kp": (ClouderKeyPairApp, ClouderKeyPairApp.description.splitlines()[0]),
        "sh": (ClouderShellApp, ClouderShellApp.description.splitlines()[0]),
        "up": (ClouderUpApp, ClouderUpApp.description.splitlines()[0]),
        "vm": (ClouderVirtualMachineApp, ClouderVirtualMachineApp.description.splitlines()[0]),
    }

    def initialize(self, argv=None):
        """Subclass because the ExtensionApp.initialize() method does not take arguments"""
        super().initialize()

    def start(self):
        super(ClouderApp, self).start()
        self.log.info("Clouder - Version %s - Cloud %s ", self.version, self.cloud)
        self.log.error(f"One of `{'` `'.join(ClouderApp.subcommands.keys())}` must be specified.")


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

main = launch_new_instance = ClouderApp.launch_instance

if __name__ == "__main__":
    main()
