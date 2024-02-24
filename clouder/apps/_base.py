"""Clouder base app."""

from datalayer.application import DatalayerApp, base_aliases, base_flags
from traitlets import Unicode, Bool

from .._version import __version__


clouder_aliases = dict(base_aliases)
clouder_aliases["cloud"] = "ClouderBaseApp.cloud"
clouder_aliases["flavor"] = "ClouderKubernetesNodepoolCreateApp.flavor"
clouder_aliases["min"] = "ClouderKubernetesNodepoolCreateApp.min"
clouder_aliases["desired"] = "ClouderKubernetesNodepoolCreateApp.desired"
clouder_aliases["max"] = "ClouderKubernetesNodepoolCreateApp.max"
clouder_aliases["datalayer-role"] = "ClouderKubernetesNodepoolCreateApp.datalayer_role"
clouder_aliases["xpu"] = "ClouderKubernetesNodepoolCreateApp.xpu"


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
#        os.environ["DATALAYER_K8S_VERSION"] = "1.25.4"
