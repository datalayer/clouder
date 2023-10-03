"""Clouder Me Application."""

from rich.console import Console
from rich.table import Table

from ..cloud.ovh.api import get_ovh_bill, get_ovh_bill_numbers, get_ovh_me
from .base import ClouderBaseApp


class ClouderProfileApp(ClouderBaseApp):
    """An application to get you started with Clouder."""

    description = """
      Get you started with Clouder.
    """

    def start(self):
        super().start()
        #
        me = get_ovh_me()
        self.log.debug(me)
        table = Table(title="OVHcloud Me")
        table.add_column("Legal Form", justify="right", style="cyan", no_wrap=True)
        table.add_column("Organisation", style="magenta")
        table.add_column("Name", justify="right", style="green")
        table.add_row(me["legalform"], me["organisation"], me["name"])
        console = Console()
        console.print(table)
        #
        bill_numbers = get_ovh_bill_numbers()
        for bill_number in bill_numbers:
          print(bill_number)
          bill_details = get_ovh_bill(bill_number)
          print("%12s (%s): %10s %s" % (
              bill_number,
              bill_details['date'],
              bill_details['priceWithTax']['text'],
              bill_details['pdfUrl'],
          ))
