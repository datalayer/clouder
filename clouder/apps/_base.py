"""Clouder base app."""

import os

from datalayer.application import DatalayerApp, base_aliases, base_flags
from traitlets import Unicode, Bool

from .._version import __version__


clouder_aliases = dict(base_aliases)
clouder_aliases["cloud"] = "ClouderBaseApp.cloud"
clouder_aliases["flavor"] = "ClouderBaseApp.flavor"
clouder_aliases["min"] = "ClouderBaseApp.min"
clouder_aliases["desired"] = "ClouderBaseApp.desired"
clouder_aliases["max"] = "ClouderBaseApp.max"
clouder_aliases["datalayer-role"] = "ClouderBaseApp.datalayer_role"
clouder_aliases["xpu"] = "ClouderBaseApp.xpu"

clouder_flags = dict(base_flags)
clouder_flags["no-print"] = (
    {"ClouderBaseApp": {"no_print": True}},
    "Do not print.",
)


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

    no_print = Bool(
        False,
        config=True,
        help="Do not print.",
    )


    def start(self):
        super(ClouderBaseApp, self).start()
        os.environ["DATALAYER_K8S_VERSION"] = "1.25.4"
