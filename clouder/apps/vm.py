"""Clouder virtual machine application."""

import warnings

from rich import print
from rich.table import Table
from datalayer.application import NoStart

from .ctx import get_default_context
from ._base import ClouderBaseApp
from ..util.utils import DEFAULT_REGION
from ..cloud.ovh.api import (create_ovh_vm,                             
                             get_ovh_vm,
                             get_ovh_project,
                            )


class ClouderVirtualMachineCreateApp(ClouderBaseApp):
    """An application to create a virtual machine."""

    description = """
      An application to create a virtual machine.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 1:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (cloud, context_id) = get_default_context()
        vm_name = self.extra_args[0]
        res = create_ovh_vm(context_id, vm_name, DEFAULT_REGION)
        print(res)


class ClouderVirtualMachineListApp(ClouderBaseApp):
    """An application to list the virtual machines."""

    description = """
      An application to list the virtual machines.
    """

    cloud = ""
    project_name = ""
    vms = []

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (cloud, context_id) = get_default_context()
        project = get_ovh_project(context_id)
        self.cloud = cloud
        self.project_name = project["description"]
        self.vms = get_ovh_vm(context_id)
        if not self.no_print:
            table = Table(title=f"Virtual Machine {self.cloud}:{self.project_name}")
            table.add_column("ID", justify="left", style="cyan", no_wrap=True)
            table.add_column("Name", justify="left", style="green")
            table.add_column("Flavor ID", justify="left", style="green")
            table.add_column("Region", justify="left", style="green")
            table.add_column("Status", justify="left", style="green")
            for vm in self.vms:
                """
                {
                    'id': '',
                    'name': '',
                    'ipAddresses': [
                        {'ip': '', 'type': 'public', 'version': 4, 'networkId': '', 'gatewayIp': '},
                        {'ip': '', 'type': 'public', 'version': 6, 'networkId': '', 'gatewayIp': ''}
                    ],
                    'flavorId': '',
                    'imageId': '',
                    'sshKeyId': '',
                    'created': '2024-02-14T09:58:06Z',
                    'region': 'BHS5',
                    'monthlyBilling': None,
                    'status': 'ACTIVE',
                    'planCode': '',
                    'operationIds': [],
                    'currentMonthOutgoingTraffic': None
                }
                """
                table.add_row(
                    vm["id"],
                    vm["name"],
                    vm["flavorId"],
                    vm["region"],
                    vm["status"],
                    )
            print(table)


class ClouderVirtualMachineApp(ClouderBaseApp):
    """An application for the virtual machines."""

    description = """
      Manage the virtual machines.
    """

    subcommands = {
        "create": (ClouderVirtualMachineCreateApp, ClouderVirtualMachineCreateApp.description.splitlines()[0]),
        "ls": (ClouderVirtualMachineListApp, ClouderVirtualMachineListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            app = ClouderVirtualMachineListApp()
            app.start()
        except NoStart:
            pass
        self.exit(0)
