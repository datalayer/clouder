"""Clouder s3 application."""

import warnings

from rich import print
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from datalayer.application import NoStart

from ._base import ClouderBaseApp
from .ctx import get_default_context
from ..util.utils import DEFAULT_REGION
from ..cloud.ovh.api import (create_ovh_s3,
                            get_ovh_s3,
                            get_ovh_project,
                            )


class ClouderS3CreateApp(ClouderBaseApp):
    """An application to create a S3 buckets."""

    description = """
      An application to create a S3 buckets.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 1:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (cloud, context_id) = get_default_context()
        s3_name = self.extra_args[0]
        res = create_ovh_s3(context_id, s3_name, DEFAULT_REGION)
        print(res)


class ClouderS3ListApp(ClouderBaseApp):
    """An application to list the S3 buckets."""

    description = """
      An application to list the S3 buckets.
    """

    cloud = ""
    project_name = ""
    s3s = []

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (cloud, context_id) = get_default_context()
        project = get_ovh_project(context_id)
        self.cloud = cloud
        self.project_name = project["description"]
        s3s = get_ovh_s3(context_id, DEFAULT_REGION)
        self.s3s = s3s
        if not self.no_print:
            for s3 in s3s:
                # {'name': '', 'virtualHost': '', 'ownerId': 0, 'objectsCount': 0, 'objectsSize': 0, 'objects': [], 'region': 'BHS', 'createdAt': '2024-02-19T17:13:13Z'}
                self.log.debug("S3", s3)
                table = Table(title=f"S3 {self.cloud}:{self.project_name}:{s3['name']}")
                table.add_column("ID", justify="left", style="cyan", no_wrap=True)
                table.add_column("Name", justify="left", style="cyan")
                table.add_column("Virtual Host", justify="left", style="green")
                table.add_column("Objects Count", justify="left", style="green")
                table.add_column("Objects Size", justify="left", style="green")
                table.add_column("Region", justify="left", style="green")
                table.add_column("Created At", justify="left", style="green")
                table.add_row(
                    s3["name"],
                    s3["virtualHost"],
                    str(s3["objectsCount"]),
                    str(s3["objectsSize"]),
                    s3["region"],
                    s3["createdAt"],
                    )
            print(table)


class ClouderS3App(ClouderBaseApp):
    """An application for the S3 buckets."""

    description = """
      Manage the S3 buckets.
    """

    subcommands = {
        "create": (ClouderS3CreateApp, ClouderS3CreateApp.description.splitlines()[0]),
        "ls": (ClouderS3ListApp, ClouderS3ListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            app = ClouderS3ListApp()
            app.start()
        except NoStart:
            pass
        self.exit(0)
