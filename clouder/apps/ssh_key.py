"""Clouder ssh key application."""

import warnings
from rich import print
from rich.panel import Panel

from datalayer.application import NoStart

from ..cloud.local.api import get_local_ssh_keys
from ..cloud.ovh.api import (get_ovh_projects,
                             get_ovh_ssh_keys,
                             create_ovh_ssh_key,
                             delete_ovh_ssh_key
                             )
# from ..operator.commands.ssh_key import (create_clouder_ssh_key,
#                                         delete_clouder_ssh_key,
#                                         get_clouder_ssh_keys)
from ._base import ClouderBaseApp


class ClouderSSHKeyCreateApp(ClouderBaseApp):
    """An application to create a SSH key."""

    description = """
      An application to create a SSH key.
    """
    def start(self):
        """Start the app."""
        create_ovh_ssh_key("ssh-clouder-key-1")


class SSHKeyDeleteApp(ClouderBaseApp):
    """An application to delete a SSH key."""

    description = """
      An application to delete a SSH key.
    """

    def start(self):
        """Start the app."""
        delete_ovh_ssh_key("ssh-clouder-key-1")


class ClouderSSHKeyListApp(ClouderBaseApp):
    """An application to list the SSH keys."""

    description = """
      An application to list the SSH keys.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        print(Panel.fit('Local SSH Keys'))
        print(str(get_local_ssh_keys()))
        print()
#        print('Clouder SSH Keys', get_clouder_ssh_keys())
#        print()
        ovh_projects = get_ovh_projects()
        for ovh_project in ovh_projects:
            print(Panel.fit("SSH Keys in OVHcloud project " + ovh_project))
            print()
            ssh_keys = get_ovh_ssh_keys(ovh_project)
            print(str(ssh_keys))
            print()


class ClouderSSHKeyApp(ClouderBaseApp):
    """An application for the SSH keys."""

    description = """
      Manage the SSH keys.
    """

    subcommands = {
        "create": (ClouderSSHKeyCreateApp, ClouderSSHKeyCreateApp.description.splitlines()[0]),
        "delete": (SSHKeyDeleteApp, SSHKeyDeleteApp.description.splitlines()[0]),
        "ls": (ClouderSSHKeyListApp, ClouderSSHKeyListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'`, `'.join(ClouderSSHKeyApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
