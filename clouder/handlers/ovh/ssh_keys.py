"""OVHcloud Keys handler."""

import json

import tornado
from jupyter_server.base.handlers import APIHandler
from jupyter_server.extension.handler import ExtensionHandlerMixin

from ..._version import __version__
from ...cloud.ovh.api import get_ovh_projects, get_ovh_ssh_keys
from ...operator.commands.ssh_key import get_clouder_ssh_keys


# pylint: disable=abstract-method
class OVHSSHKeysHandler(ExtensionHandlerMixin, APIHandler):
    """The handler for OVHcloud."""

    @tornado.web.authenticated
    def get(self):
        """Returns the OVHcloud keys."""
        projects = get_ovh_projects()
        ssh_keys = []
        for project in projects:
            ssh_keys = get_ovh_ssh_keys(project)
            ssh_keys.extend(ssh_keys)
        res = json.dumps({
            "success": True,
            "message": "List of OVHcloud keys.",
            "ssh_keys": ssh_keys,
            "clouder_ssh_keys": get_clouder_ssh_keys(),
        }, default=str)
        self.finish(res)
