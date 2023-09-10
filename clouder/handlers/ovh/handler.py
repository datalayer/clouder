"""Docker handler."""

import json

import tornado

import ovh

from jupyter_server.base.handlers import APIHandler
from jupyter_server.extension.handler import ExtensionHandlerMixin

from ...commands.ovh.project import get_projects

from ..._version import __version__


client = ovh.Client()


# pylint: disable=W0223
class OVHHandler(ExtensionHandlerMixin, APIHandler):
    """The handler for OVHcloud."""

    @tornado.web.authenticated
    @tornado.gen.coroutine
    def get(self):
        """Returns the OVHcloud projects."""
        projects = get_projects(client)
        res = json.dumps({
            "success": True,
            "message": "List of OVH projects.",
            "projects": projects,
        }, default=str)
        self.finish(res)
