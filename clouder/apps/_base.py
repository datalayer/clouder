"""Clouder base app."""

from datalayer.application import DatalayerApp, base_aliases, base_flags
from traitlets import Unicode, Bool, Int

from .._version import __version__


clouder_aliases = dict(base_aliases)
clouder_aliases["cloud"] = "ClouderBaseApp.cloud"
clouder_aliases["flavor"] = "ClouderBaseApp.flavor"
clouder_aliases["min"] = "ClouderBaseApp.min"
clouder_aliases["desired"] = "ClouderBaseApp.desired"
clouder_aliases["max"] = "ClouderBaseApp.max"
clouder_aliases["role"] = "ClouderBaseApp.role"
clouder_aliases["variant"] = "ClouderBaseApp.variant"
clouder_aliases["xpu"] = "ClouderBaseApp.xpu"
clouder_aliases["min"] = "ClouderBaseApp.min"
clouder_aliases["desired"] = "ClouderBaseApp.desired"
clouder_aliases["max"] = "ClouderBaseApp.max"


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

    flavor = Unicode(
        "b2-15",
        config=True,
        help="The node flavor.",
    )

    min = Int(
        3,
        config=True,
        help="Minimun number of nodes.",
    )

    desired = Int(
        3,
        config=True,
        help="Desired number of nodes.",
    )

    max = Int(
        10,
        config=True,
        help="Maximum number of nodes.",
    )

    role = Unicode(
        "datalayer",
        config=True,
        help="The role for the pool.",
    )

    variant = Unicode(
        "default",
        config=True,
        help="The variant for the pool.",
    )

    xpu = Unicode(
        "cpu",
        config=True,
        help="cpu, gpu or qpu.",
    )


    def start(self):
        super(ClouderBaseApp, self).start()
#        os.environ["DATALAYER_K8S_VERSION"] = "1.25.4"
