"""Clouder base app."""

import os

from datalayer.application import DatalayerApp, base_aliases, base_flags
from traitlets import Unicode

from .._version import __version__


clouder_aliases = dict(base_aliases)
clouder_aliases["cloud"] = "ClouderBaseApp.cloud"

clouder_flags = dict(base_flags)



class ClouderBaseApp(DatalayerApp):
    """An base application for Clouder."""

    version = __version__

    aliases = clouder_aliases
    flags = clouder_flags

    cloud = Unicode(
        "ovh",
        config=True,
        help="The cloud to use.",
    )


    def start(self):
        super(ClouderBaseApp, self).start()
        # TODO Move this to a shell.
        os.environ["OS"] = "MACOS"
        os.environ["DATALAYER_K8S_VERSION"] = "1.25.4"
