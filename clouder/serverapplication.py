"""The Clouder Server application."""

import os

from jupyter_server.extension.application import (ExtensionApp,
                                                  ExtensionAppJinjaMixin)
from jupyter_server.utils import url_path_join

from .handlers import (ConfigHandler, IndexHandler, OVHKeysHandler,
                       OVHProjectsHandler)

DEFAULT_STATIC_FILES_PATH = os.path.join(os.path.dirname(__file__), "./static")

DEFAULT_TEMPLATE_FILES_PATH = os.path.join(os.path.dirname(__file__), "./templates")


class ClouderApp(ExtensionAppJinjaMixin, ExtensionApp):
    """The Jupyter Server extension."""

    name = "clouder"

    extension_url = "/clouder"

    load_other_extensions = True

    static_paths = [DEFAULT_STATIC_FILES_PATH]
    template_paths = [DEFAULT_TEMPLATE_FILES_PATH]

    def initialize_settings(self):
        pass

    def initialize_handlers(self):
        handlers = [
            ("clouder", IndexHandler),
            (url_path_join("clouder", "config"), ConfigHandler),
            (url_path_join("clouder", "ovh", "projects"), OVHProjectsHandler),
            (url_path_join("clouder", "ovh", "keys"), OVHKeysHandler),
        ]
        self.handlers.extend(handlers)


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

main = launch_new_instance = ClouderApp.launch_instance
