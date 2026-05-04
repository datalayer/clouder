"""OVHcloud Project handler."""

import json

import tornado
from jupyter_server.base.handlers import APIHandler
from jupyter_server.extension.handler import ExtensionHandlerMixin

from ..._version import __version__
from ...cloud.ovh.api import get_ovh_projects


# pylint: disable=abstract-method
class OVHProjectsHandler(ExtensionHandlerMixin, APIHandler):
    """The handler for OVHcloud."""

    @tornado.web.authenticated
    def get(self):
        """Returns the OVHcloud projects."""
        projects = get_ovh_projects()
        res = json.dumps({
            "success": True,
            "message": "List of OVHcloud projects.",
            "projects": projects,
        }, default=str)
        self.finish(res)
