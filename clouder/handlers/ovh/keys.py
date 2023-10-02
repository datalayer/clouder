"""OVHcloud Keys handler."""

import json

import tornado
from jupyter_server.base.handlers import APIHandler
from jupyter_server.extension.handler import ExtensionHandlerMixin

from ..._version import __version__
from ...cloud.ovh.api import get_ovh_projects, get_ovh_ssh_keys
from ...operator.handlers.sshkey import get_sshkeys


class OVHKeysHandler(ExtensionHandlerMixin, APIHandler):
    """The handler for OVHcloud."""

    @tornado.web.authenticated
    def get(self):
        """Returns the OVHcloud projects."""
        projects = get_ovh_projects()
        keys = []
        for project in projects:
            ssh_keys = get_ovh_ssh_keys(project)
            keys.extend(ssh_keys)
        res = json.dumps({
            "success": True,
            "message": "List of OVHcloud keys.",
            "keys": keys,
            "managed_keys": get_sshkeys(),
        }, default=str)
        self.finish(res)
