"""Clouder context application."""

import sys
import warnings
import yaml

from rich import print
from rich.table import Table
from datalayer.application import NoStart

from ._base import ClouderBaseApp
from ..cloud.ovh.api import get_ovh_project
from ..util.utils import CLOUDER_CONTEXT_FILE


DEFAULT_BOX_SEPARATOR = ":::"

"""
clouder:
    version: 1.0.0
    default_box: ovh${DEFAULT_BOX_SEPARATOR}ovh-1
    boxes:
        aws:
          aws-id-1:
            name: aws-account-name-1
        ovh:
          ovh-id-1:
            name: ovh-project-name-1
"""
def load_context():
    if not CLOUDER_CONTEXT_FILE.is_file():
        warnings.warn("You should init a context - run `clouder context init`.")
        sys.exit(1)
    else:
        with open(CLOUDER_CONTEXT_FILE, 'r') as file:
            context = yaml.safe_load(file)
    return context
    

def get_default_context():
    context = load_context()
    (cloud, box_id) = context["clouder"]["default_box"].split(DEFAULT_BOX_SEPARATOR)
    return (cloud, box_id)


def save_context(context):
    with open(CLOUDER_CONTEXT_FILE, "w") as out:
        yaml.dump(context, out, default_flow_style=False, sort_keys=False)


def print_context(context):
    table = Table(title="Clouder Contexts")
    table.add_column("Cloud", justify="left", style="cyan", no_wrap=True)
    table.add_column("Box ID", justify="left", style="green")
    table.add_column("Box Name", justify="left", style="green")
    table.add_column("Default", justify="center", style="green")
    default = context["clouder"]["default_box"]
    def is_default(cloud, box_id):
        val = cloud + DEFAULT_BOX_SEPARATOR + box_id
        if default == val:
            return "*"
        else: 
            ""
    boxes = context["clouder"]["boxes"]
    for cloud in list(boxes.keys()):
        for box_id in list(boxes[cloud].keys()):
            val = boxes[cloud][box_id]
            table.add_row(
                cloud,
                box_id,
                val["name"],
                is_default(cloud, box_id)
                )
    print(table)


class ClouderContextListApp(ClouderBaseApp):
    """An application to list the contexts."""

    description = """
      An application to list the contexts.
    """

    def start(self):
        """Start the app."""
        context = load_context()
        print_context(context)


class ClouderContextSetApp(ClouderBaseApp):
    """An application to set the current context."""

    description = """
      An application to set the current context.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 2:
            warnings.warn("You should provide a cloud and a box_id.")
            self.exit(1)
        cloud = self.extra_args[0]
        box_id = self.extra_args[1]
        box = get_ovh_project(box_id)
        context = load_context()
        context["clouder"]["default_box"] = cloud + DEFAULT_BOX_SEPARATOR + box_id
        context["clouder"]["boxes"][cloud][box_id] = {}
        context["clouder"]["boxes"][cloud][box_id]["name"] = box["description"]
        self.log.debug("Setting context%s", yaml.dump(context, sort_keys=False))
        save_context(context)
        print_context(context)


class ClouderContextRemoveApp(ClouderBaseApp):
    """An application to remove context."""

    description = """
      An application to remove a context.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 2:
            warnings.warn("You should provide a cloud and a box_id.")
            self.exit(1)
        cloud = self.extra_args[0]
        box_id = self.extra_args[1]
        context = load_context()
        default_context = context["clouder"]["default_box"]
        if default_context == cloud + ":" + box_id:
            warnings.warn("Can not remove the default context.")
            self.exit(1)
        del context["clouder"]["boxes"][cloud][box_id]
        save_context(context)
        print_context(context)


class ClouderContextInitApp(ClouderBaseApp):
    """An application to init the context."""

    description = """
      An application to init the context.
    """

    def start(self):
        """Start the app."""
        from .box import ClouderBoxListApp
        if len(self.extra_args) != 0:
            warnings.warn("You should provide a cloud and a box_id.")
            self.exit(1)
        app = ClouderBoxListApp()
        app.start()
        context = yaml.safe_load(f"""
clouder:
    version: 1.0.0
    default_box:
    boxes:
        ovh:
            pid:
                name: pname
""")
        boxes = app._boxes
        for box in boxes:
            context["clouder"]["boxes"]["ovh"][box["project_id"]] = {
                "name" : box["description"]
            }
        save_context(context)


class ClouderContextApp(ClouderBaseApp):
    """An application for the context."""

    description = """
      Manage the Clouder contexts.
    """

    subcommands = {
        "init": (ClouderContextInitApp, ClouderContextInitApp.description.splitlines()[0]),
        "ls": (ClouderContextListApp, ClouderContextListApp.description.splitlines()[0]),
        "rm": (ClouderContextRemoveApp, ClouderContextRemoveApp.description.splitlines()[0]),
        "set": (ClouderContextSetApp, ClouderContextSetApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.info("Clouder - Version %s - Cloud %s ", super().version, super().cloud)
            self.log.error(f"One of `{'`, `'.join(ClouderContextApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
