"""Index handler."""

import tornado

from ..._version import __version__
from ..base import BaseTemplateHandler


class IndexHandler(BaseTemplateHandler):
    """The handler for the index."""

    @tornado.web.authenticated
    def get(self):
        """The index page."""
        self.write(self.render_template("index.html"))
