"""Clouder me application."""

from rich import print
from rich.table import Table

from ._base import ClouderBaseApp
from ..cloud.ovh.api import get_ovh_me


class ClouderMeApp(ClouderBaseApp):
    """An application to know about the current user."""

    description = """
      Get information on the current user.
    """

    def start(self):
        super().start()
        me = get_ovh_me()
        self.log.debug(me)
        table = Table(title="OVHcloud Me")
        table.add_column("Legal Form", justify="left", style="cyan", no_wrap=True)
        table.add_column("Organisation", justify="left", style="magenta")
        table.add_column("First Name", justify="left", style="green")
        table.add_column("Name", justify="left", style="green")
        table.add_column("Country", justify="left", style="green")
        table.add_row(
            me["legalform"],
            me["organisation"],
            me["firstname"],
            me["name"],
            me["country"],
            )
        print(table)
