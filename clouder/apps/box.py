"""Clouder box application."""

import warnings

from rich import print
from rich.table import Table

from datalayer.application import NoStart

from ._base import ClouderBaseApp
from .k8s import ClouderKubernetesListApp
from ..cloud.ovh.api import (
  get_ovh_projects, get_ovh_project
)


class ClouderBoxInfoApp(ClouderBaseApp):
    """An application to get info on a box."""

    description = """
      An application to get information on a box.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 2:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
#        box_id = self.extra_args[0]
        app = ClouderKubernetesListApp()
        app.start()


class ClouderBoxListApp(ClouderBaseApp):
    """An application to list the boxes."""

    description = """
      An application to list the boxes.
    """

    _boxes = []

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        boxes = get_ovh_projects()
        table = Table(title="OVHcloud Projects")
        table.add_column("Cloud", justify="left", style="cyan")
        table.add_column("Box ID", justify="left", style="cyan")
        table.add_column("Name", justify="left", style="green")
        table.add_column("IAM ID", justify="left", style="purple")
        table.add_column("IAM URN", justify="left", style="purple")
        for box_id in boxes:
            box = get_ovh_project(box_id)
            self._boxes.append(box)
            iam = box["iam"]
            table.add_row(
                "ovh",
                box["project_id"],
                box["description"],
                iam["id"],
                iam["urn"],
            )
        print(table)


class ClouderBoxApp(ClouderBaseApp):
    """An application for the boxes."""

    description = """
      Manage the boxes.
    """

    subcommands = {
        "info": (ClouderBoxInfoApp, ClouderBoxInfoApp.description.splitlines()[0]),
        "ls": (ClouderBoxListApp, ClouderBoxListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'`, `'.join(ClouderBoxApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
