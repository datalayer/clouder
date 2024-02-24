"""Clouder info application."""

from rich import print
from rich.table import Table
from datalayer.application import NoStart

from ._base import ClouderBaseApp
from .k8s import ClouderKubernetesListApp
from .ssh_key import ClouderSSHKeyListApp
from .vm import ClouderVirtualMachineListApp
from .s3 import ClouderS3ListApp


class ClouderInfoCurrentApp(ClouderBaseApp):
    """An application for info about the current context."""

    description = """
      An application for info about the current context.
    """

    def start(self):
        """Start the app."""
        app = ClouderKubernetesListApp()
        app.no_print = True
        app.start()
        table = Table(title=f"Kubernetes {app.cloud}:{app.project_name}")
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Version", justify="left", style="green")
        table.add_column("Status", justify="left", style="green")
        table.add_column("Nodepool", justify="left", style="green")
        table.add_column("Nodes", justify="left", style="green")
        for kubernetes in app.kubernetess:
            table.add_row(
                kubernetes["name"],
                kubernetes["region"],
                kubernetes["version"],
                kubernetes["status"],
                )
            nodepools = kubernetes["nodepools"]
            for nodepool in nodepools:
                nodes = nodepool["nodes"]
                table.add_row(
                    "",
                    "",
                    "",
                    "",
                    nodepool["name"],
                    str(len(nodes)),
                    )
        print(table)
        app = ClouderVirtualMachineListApp()
        app.no_print = True
        app.start()
        table = Table(title=f"Virtual Machines {app.cloud}:{app.project_name}")
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="green")
        table.add_column("Flavor ID", justify="left", style="green")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Status", justify="left", style="green")
        for vm in app.vms:
            table.add_row(
                vm["id"],
                vm["name"],
                vm["flavorId"],
                vm["region"],
                vm["status"],
                )
        print(table)
        app = ClouderS3ListApp()
        app.no_print = True
        app.start()
        table = Table(title=f"S3 {app.cloud}:{app.project_name}")
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Virtual Host", justify="left", style="green")
        table.add_column("Objects Count", justify="left", style="green")
        table.add_column("Objects Size", justify="left", style="green")
        table.add_column("Region", justify="left", style="green")
        table.add_column("Created At", justify="left", style="green")
        for s3 in app.s3s:
            table.add_row(
                s3["name"],
                s3["virtualHost"],
                str(s3["objectsCount"]),
                str(s3["objectsSize"]),
                s3["region"],
                s3["createdAt"],
                )
        print(table)
        app = ClouderSSHKeyListApp()
        app.no_print = True
        app.start()
        table = Table(title=f"SSH Key {app.cloud}:{app.project_name}")
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Fingerpint", justify="left", style="green")
        table.add_column("Public Key", justify="left", style="green")
        for ssh_key in app.ssh_keys:
            table.add_row(
                ssh_key["id"],
                ssh_key["name"],
                ssh_key.get("fingerprint", ""),
                ssh_key.get("publicKey", ""),
                )
        print(table)


class ClouderInfoApp(ClouderBaseApp):
    """An application for the info."""

    description = """
      Manage the Clouder info.
    """

    subcommands = {
        "current": (ClouderInfoCurrentApp, ClouderInfoCurrentApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.info("Clouder - Version %s - Cloud %s ", super().version, super().cloud)
            self.log.error(f"One of `{'`, `'.join(ClouderInfoApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
