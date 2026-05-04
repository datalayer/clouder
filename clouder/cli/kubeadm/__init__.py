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
from . import vm_create, setup, get_config, scale, vm_terminate  # noqa: E402
from . import info, ingress_nginx, ingress_traefik, smoke_test    # noqa: E402
from . import upgrade_kubelet                                     # noqa: E402

vm_create.register(kubeadm_app)
setup.register(kubeadm_app)
get_config.register(kubeadm_app)
info.register(kubeadm_app)
scale.register(kubeadm_app)
vm_terminate.register(kubeadm_app)
ingress_nginx.register(kubeadm_app)
ingress_traefik.register(kubeadm_app)
smoke_test.register(kubeadm_app)
upgrade_kubelet.register(kubeadm_app)
