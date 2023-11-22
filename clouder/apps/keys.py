import warnings

from datalayer.application import NoStart

from ..cloud.local.api import get_local_ssh_keys
from ..cloud.ovh.api import get_ovh_projects, get_ovh_ssh_keys
from ..operator.commands.sshkey import (create_clouder_sshkey,
                                        delete_clouder_sshkey,
                                        get_clouder_sshkeys)
from .base import ClouderBaseApp


class KeysCreateApp(ClouderBaseApp):
    """An application to create."""

    description = """
      An application to create
    """
    def start(self):
        """Start the app."""
        create_clouder_sshkey("ssh-clouder-key-1")


class KeysDeleteApp(ClouderBaseApp):
    """An application to delete."""

    description = """
      An application to delete
    """
    """
    def initialize(self, *args, **kwargs):
        super().initialize(*args, **kwargs)
    """
    def start(self):
        """Start the app."""
        delete_clouder_sshkey("ssh-clouder-key-1")


class KeysListApp(ClouderBaseApp):
    """An application to list the keys."""

    description = """
      An application to list the keys
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) > 1:
            warnings.warn("Too many arguments were provided.")
            self.exit(1)
        print('Clouder SSH Keys', get_clouder_sshkeys())
        print()
        print('Local SSH Keys', get_local_ssh_keys())
        print()
        ovh_projects = get_ovh_projects()
        for ovh_project in ovh_projects:
            print('All SSH Keys in project', ovh_project, get_ovh_ssh_keys(ovh_project))
            print()


class ClouderKeysApp(ClouderBaseApp):
    """An application for the keys."""

    description = """
      Manage the keys.
    """

    subcommands = {
        "create": (KeysCreateApp, KeysCreateApp.description.splitlines()[0]),
        "delete": (KeysDeleteApp, KeysDeleteApp.description.splitlines()[0]),
        "list": (KeysListApp, KeysListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'`, `'.join(ClouderKeysApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
