"""Clouder ssh key application."""

import warnings
from rich import print
from rich.panel import Panel
from rich.table import Table
from datalayer.application import NoStart

from .ctx import get_default_context
from ..util.utils import SSH_PUBLIC_KEY
from ..cloud.local.api import get_local_ssh_keys
from ..cloud.ovh.api import (get_ovh_ssh_keys,
                             create_ovh_ssh_key,
                             get_ovh_project,
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
        if len(self.extra_args) != 1:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (cloud, context_id) = get_default_context()
        key_name = self.extra_args[0]
        with SSH_PUBLIC_KEY.open(mode="r", encoding="utf-8") as pubic_key_file:
            public_key = pubic_key_file.read()
        res = create_ovh_ssh_key(context_id, key_name, public_key)
        print(res)


class ClouderSSHKeyListApp(ClouderBaseApp):
    """An application to list the SSH keys."""

    description = """
      An application to list the SSH keys.
    """

    cloud = ""
    project_name = ""
    ssh_keys = []

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
        (cloud, context_id) = get_default_context()
        project = get_ovh_project(context_id)
        self.cloud = cloud
        self.project_id = project["project_id"]
        self.project_name = project["description"]
        self.ssh_keys = get_ovh_ssh_keys(context_id)
        if not self.no_print:
            table = Table(title=f"SSH Key {self.cloud}:{self.project_name}")
            table.add_column("ID", justify="left", style="cyan", no_wrap=True)
            table.add_column("Name", justify="left", style="cyan")
            table.add_column("Fingerpint", justify="left", style="green")
            table.add_column("Public Key", justify="left", style="green")
            for ssh_key in self.ssh_keys:
                table.add_row(
                    ssh_key["id"],
                    ssh_key["name"],
                    ssh_key.get("fingerprint", ""),
                    ssh_key.get("publicKey", ""),
                    )
            print(table)


class ClouderSSHKeyApp(ClouderBaseApp):
    """An application for the SSH keys."""

    description = """
      Manage the SSH keys.
    """

    subcommands = {
        "create": (ClouderSSHKeyCreateApp, ClouderSSHKeyCreateApp.description.splitlines()[0]),
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
