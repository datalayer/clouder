"""Clouder kubernetes application."""

import os
import warnings
import yaml

from rich import print
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from datalayer.application import NoStart

from ._base import ClouderBaseApp
from .ctx import get_default_context
from ..util.utils import OVH_K8S_FOLDER
from ..cloud.ovh.api import (create_ovh_kubernetes,                             
                             create_ovh_kubernetes_nodepool,
                             get_ovh_project,
                             get_ovh_kubernetess,
                             get_ovh_kubernetes,
                             get_ovh_kubernetes_kubeconfig,
                             get_ovh_kubernetes_nodepools,
                             get_ovh_kubernetes_nodepool_nodes,
                            )


class ClouderKubernetesCreateApp(ClouderBaseApp):
    """An application to create a kubernetes clusters."""

    description = """
      An application to create a kubernetes clusters.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 1:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (_, box_id) = get_default_context()
        kubernetes_name = self.extra_args[0]
        create_ovh_kubernetes(box_id, kubernetes_name)


class ClouderKubernetesUseApp(ClouderBaseApp):
    """An application to use a kubernetes kubeconfig."""

    description = """
      An application to use a kubernetes kubeconfig.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 1:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        kubernetes_name = self.extra_args[0]
        (_, box_id) = get_default_context()
        kubernetess = get_ovh_kubernetess(box_id)
        for kubernetes_id in kubernetess:
            kubernetes = get_ovh_kubernetes(box_id, kubernetes_id)
            if kubernetes["name"] == kubernetes_name:
                kubeconfig_path = str((OVH_K8S_FOLDER / f"{kubernetes_name}.yaml").absolute())
                print(f"export KUBECONFIG={kubeconfig_path}")


class ClouderKubernetesKubeconfigApp(ClouderBaseApp):
    """An application to get a kubernetes kubeconfig."""

    description = """
      An application to get a kubernetes kubeconfig.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 1:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        kubernetes_name = self.extra_args[0]
        (_, box_id) = get_default_context()
        kubernetess = get_ovh_kubernetess(box_id)
        for kubernetes_id in kubernetess:
            kubernetes = get_ovh_kubernetes(box_id, kubernetes_id)
            if kubernetes["name"] == kubernetes_name:
                config = get_ovh_kubernetes_kubeconfig(box_id, kubernetes_id)
                kubeconfig = config["content"]
                OVH_K8S_FOLDER.mkdir(parents=True, exist_ok=True)
                kubeconfig_file = OVH_K8S_FOLDER / f"{kubernetes_name}.yaml"
                with open(kubeconfig_file, "w") as out:
                    k = yaml.safe_load(kubeconfig)
                    yaml.dump(k, out, default_flow_style=False, sort_keys=False)
                    kubeconfig_file.chmod(0o600)


class ClouderKubernetesNodepoolCreateApp(ClouderBaseApp):
    """An application to create a kubernetes nodepool."""

    description = """
      An application to create a kubernetes nodepool.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 8:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (_, box_id) = get_default_context()
        kubernetes_name = self.extra_args[0]
        nodepool_name = self.extra_args[1]
        flavor_name = self.extra_args[2]
        desired_nodes = int(self.extra_args[3])
        min_nodes = int(self.extra_args[4])
        max_nodes = int(self.extra_args[5])
        datalayer_role = self.extra_args[6]
        xpu = self.extra_args[7]
        (_, box_id) = get_default_context()
        kubernetess = get_ovh_kubernetess(box_id)
        for k in kubernetess:
            kubernetes = get_ovh_kubernetes(box_id, k)
            # ("{'id': '', 'region': 'BHS5', 'name': 'dev1-io--datalayer--run', 'url': '', 'nodesUrl': '', 'version': '1.28.3-0', 'nextUpgradeVersions': [], 'kubeProxyMode': 'iptables', 'customization': {'apiServer': {'admissionPlugins': {'enabled': ['AlwaysPullImages', 'NodeRestriction'], 'disabled': []}}}, 'status': 'READY', 'updatePolicy': 'ALWAYS_UPDATE', 'isUpToDate': False, 'controlPlaneIsUpToDate': False, 'privateNetworkId': None, 'nodesSubnetId': None, 'createdAt': '2024-01-04T04:59:32Z', 'updatedAt': '2024-01-19T13:18:04Z', 'auditLogsSubscribed': False}",
            if kubernetes["name"] == kubernetes_name:
                kubernetes_id = kubernetes["id"]
                template = {
                    "metadata": {
                        "annotations": {},
                        "finalizers": [],
                        "labels": {
                            "datalayer.io/role": datalayer_role,
                            "datalayer.io/xpu": xpu,
                        }
                    },
                    "spec": {
                        "taints": [],
                        "unschedulable": False,
                    }
                }
                create_ovh_kubernetes_nodepool(box_id, kubernetes_id, nodepool_name,
                                              flavor_name, desired_nodes, min_nodes, max_nodes,
                                              template)


class ClouderKubernetesListApp(ClouderBaseApp):
    """An application to list the kubernetes clusters."""

    description = """
      An application to list the kubernetes clusters.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        (_, box_id) = get_default_context()
        project = get_ovh_project(box_id)
        project_id = project["project_id"]
        project_name = project["description"]
        print(Markdown(f"# OVHcloud Kubernetes in project {project_name} ({project_id})"))
        kubernetess = get_ovh_kubernetess(project_id)
        for kubernetes_id in kubernetess:
            # ("{'id': '', 'region': 'BHS5', 'name': 'dev1-io--datalayer--run', 'url': '', 'nodesUrl': '', 'version': '1.28.3-0', 'nextUpgradeVersions': [], 'kubeProxyMode': 'iptables', 'customization': {'apiServer': {'admissionPlugins': {'enabled': ['AlwaysPullImages', 'NodeRestriction'], 'disabled': []}}}, 'status': 'READY', 'updatePolicy': 'ALWAYS_UPDATE', 'isUpToDate': False, 'controlPlaneIsUpToDate': False, 'privateNetworkId': None, 'nodesSubnetId': None, 'createdAt': '2024-01-04T04:59:32Z', 'updatedAt': '2024-01-19T13:18:04Z', 'auditLogsSubscribed': False}",
            kubernetes = get_ovh_kubernetes(project_id, kubernetes_id)
            self.log.debug("Kubernetes", kubernetes)
            table = Table(title=f"Kubernetes {project_name}:{kubernetes['name']}")
            table.add_column("ID", justify="left", style="cyan", no_wrap=True)
            table.add_column("Name", justify="left", style="cyan")
            table.add_column("Region", justify="left", style="green")
            table.add_column("Version", justify="left", style="green")
            table.add_column("Status", justify="left", style="green")
            table.add_row(
                kubernetes["id"],
                kubernetes["name"],
                kubernetes["region"],
                kubernetes["version"],
                kubernetes["status"],
                )
            print(table)
            nodepools = get_ovh_kubernetes_nodepools(project_id, kubernetes_id)
            for nodepool in nodepools:
                # ("{'id': '', 'projectId': '', 'name': 'datalayer-plane', 'flavor': 'b2-15', 'status': 'READY', 'sizeStatus': 'CAPACITY_OK', 'autoscale': False, 'monthlyBilled': False, 'antiAffinity': False, 'desiredNodes': 3, 'minNodes': 0, 'maxNodes': 100, 'currentNodes': 3, 'availableNodes': 3, 'upToDateNodes': 0, 'createdAt': '2024-01-04T05:11:15Z', 'updatedAt': '2024-01-04T05:16:43Z', 'autoscaling': {'scaleDownUtilizationThreshold': 0.5, 'scaleDownUnneededTimeSeconds': 600, 'scaleDownUnreadyTimeSeconds': 1200}, 'template': {'metadata': {'labels': {}, 'annotations': {}, 'finalizers': []}, 'spec': {'unschedulable': False, 'taints': []}}}",)
                self.log.debug("Nodepool", nodepool)
                title = f"Nodepool {project_name}:{kubernetes['name']}:{nodepool['name']}"
                print(Markdown("## " + title))
                table = Table(title=title)
                table.add_column("ID", justify="left", style="cyan", no_wrap=True)
                table.add_column("Name", justify="left", style="cyan")
                table.add_column("Flavor", justify="left", style="green")
                table.add_column("Current Nodes", justify="left", style="green")
                table.add_column("Available Nodes", justify="left", style="green")
                table.add_column("Min Nodes", justify="left", style="green")
                table.add_column("Desired Nodes", justify="left", style="green")
                table.add_column("Max Nodes", justify="left", style="green")
                table.add_column("Status", justify="left", style="green")
                table.add_row(
                    nodepool["id"],
                    nodepool["name"],
                    nodepool["flavor"],
                    str(nodepool["currentNodes"]),
                    str(nodepool["availableNodes"]),
                    str(nodepool["minNodes"]),
                    str(nodepool["desiredNodes"]),
                    str(nodepool["maxNodes"]),
                    nodepool["status"],
                    )
                print(table)
                nodes = get_ovh_kubernetes_nodepool_nodes(project_id, kubernetes_id, nodepool["id"])
                table = Table(title=f"Nodes {project_name}:{kubernetes['name']}:{nodepool['name']}")
                table.add_column("ID", justify="left", style="cyan", no_wrap=True)
                table.add_column("Instance ID", justify="left", style="cyan")
                table.add_column("Name", justify="left", style="green")
                table.add_column("Status", justify="left", style="green")
                for node in nodes:
                    # { 'id': '', 'projectId': '', 'instanceId': '','nodePoolId': '33267e25-9d40-423c-a6f4-8f5d56e44cc9', 'name': 'cpu-plane-node-111917', 'flavor': 'b2-15', 'status': 'READY', 'isUpToDate': False, 'version': '1.28.3-1283', 'createdAt': '2024-01-04T05:12:06Z', 'updatedAt': '2024-01-04T05:16:23Z', 'deployedAt': '2024-01-04T05:16:23Z' }
                    self.log.debug("Node", node)
                    table.add_row(
                        node["id"],
                        node["instanceId"],
                        node["name"],
                        node["status"],
                        )
                print(table)
                print()


class ClouderKubernetesApp(ClouderBaseApp):
    """An application for the kubernetes clusters."""

    description = """
      Manage the kubernetes clusters.
    """

    subcommands = {
        "create": (ClouderKubernetesCreateApp, ClouderKubernetesCreateApp.description.splitlines()[0]),
        "create-nodepool": (ClouderKubernetesNodepoolCreateApp, ClouderKubernetesNodepoolCreateApp.description.splitlines()[0]),
        "ls": (ClouderKubernetesListApp, ClouderKubernetesListApp.description.splitlines()[0]),
        "kubeconfig": (ClouderKubernetesKubeconfigApp, ClouderKubernetesKubeconfigApp.description.splitlines()[0]),
        "use": (ClouderKubernetesUseApp, ClouderKubernetesUseApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'`, `'.join(ClouderKubernetesApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
