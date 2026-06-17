"""Clouder CLI - kubeadm sub-commands package.

This package replaces the monolithic ``kubeadm.py`` module.  Each sub-command
lives in its own file and registers itself via a ``register(kubeadm_app)``
function that is called below.
"""

import typer

from ..ctx import get_current_context  # noqa: F401 — re-exported for convenience


kubeadm_app = typer.Typer(no_args_is_help=True)


@kubeadm_app.callback()
def kubeadm_callback():
    """Manage kubeadm-based Kubernetes clusters on cloud VMs."""


# -- Register every sub-command module --
from . import create, setup, get_config, scale, terminate, prune  # noqa: E402
from . import list_clusters                                     # noqa: E402
from . import set_default                                       # noqa: E402
from . import info, ingress_nginx, ingress_traefik, smoke_test    # noqa: E402
from . import upgrade_kubelet                                     # noqa: E402
from . import use                                                 # noqa: E402
from . import repair                                              # noqa: E402
from . import remove_node                                         # noqa: E402

create.register(kubeadm_app)
list_clusters.register(kubeadm_app)
set_default.register(kubeadm_app)
setup.register(kubeadm_app)
get_config.register(kubeadm_app)
use.register(kubeadm_app)
info.register(kubeadm_app)
scale.register(kubeadm_app)
terminate.register(kubeadm_app)
prune.register(kubeadm_app)
ingress_nginx.register(kubeadm_app)
ingress_traefik.register(kubeadm_app)
smoke_test.register(kubeadm_app)
upgrade_kubelet.register(kubeadm_app)
repair.register(kubeadm_app)
remove_node.register(kubeadm_app)
