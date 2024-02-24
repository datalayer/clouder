"""Clouder context application."""

import sys
import warnings
import yaml

from rich import print
from rich.table import Table
from datalayer.application import NoStart

from ._base import ClouderBaseApp
from ..cloud.ovh.api import (
  get_ovh_projects,
  get_ovh_project,
)
from ..util.utils import (
  CLOUDER_CONTEXT_FILE,
  CLOUDER_CONFIG_FOLDER,
)


DEFAULT_BOX_SEPARATOR = ":::"

"""
clouder:
    version: 1.0.0
    default_context: ovh${DEFAULT_BOX_SEPARATOR}ovh-1
    contexts:
        aws:
          aws-id-1:
            name: aws-account-name-1
        ovh:
          ovh-id-1:
            name: ovh-project-name-1
"""
def load_context():
    if not CLOUDER_CONTEXT_FILE.is_file():
        warnings.warn("You should init a context - run `clouder ctx init`.")
        sys.exit(1)
    else:
        with open(CLOUDER_CONTEXT_FILE, 'r') as file:
            context = yaml.safe_load(file)
    return context
    

def get_default_context():
    context = load_context()
    (cloud, context_id) = context["clouder"]["default_context"].split(DEFAULT_BOX_SEPARATOR)
    return (cloud, context_id)


def save_context(context):
    CLOUDER_CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
    with open(CLOUDER_CONTEXT_FILE, "w") as out:
        yaml.dump(context, out, default_flow_style=False, sort_keys=False)


def print_context(context):
    table = Table(title="Clouder Contexts")
    table.add_column("Cloud", justify="left", style="cyan", no_wrap=True)
    table.add_column("Context ID", justify="left", style="green")
    table.add_column("Context Name", justify="left", style="green")
    table.add_column("Default", justify="center", style="green")
    default = context["clouder"]["default_context"]
    def is_default(cloud, context_id):
        val = cloud + DEFAULT_BOX_SEPARATOR + context_id
        if default == val:
            return "*"
        else: 
            ""
    contexts = context["clouder"]["contexts"]
    for cloud in list(contexts.keys()):
        for context_id in list(contexts[cloud].keys()):
            val = contexts[cloud][context_id]
            table.add_row(
                cloud,
                context_id,
                val["name"],
                is_default(cloud, context_id)
                )
    print(table)


class ClouderContextShowApp(ClouderBaseApp):
    """An application to list the contexts."""

    description = """
      An application to list the contexts.
    """

    def start(self):
        """Start the app."""
        context = load_context()
        print_context(context)



class ClouderContextListApp(ClouderBaseApp):
    """An application to list the contexts."""

    description = """
      An application to list the contexts.
    """

    _contexts = []

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("Please provide the expected arguments.")
            self.exit(1)
        contexts = get_ovh_projects()
        table = Table(title="OVHcloud Projects")
        table.add_column("Cloud", justify="left", style="cyan")
        table.add_column("Context ID", justify="left", style="cyan")
        table.add_column("Name", justify="left", style="green")
        table.add_column("IAM ID", justify="left", style="purple")
        table.add_column("IAM URN", justify="left", style="purple")
        for context_id in contexts:
            context = get_ovh_project(context_id)
            self._contexts.append(context)
            iam = context["iam"]
            table.add_row(
                "ovh",
                context["project_id"],
                context["description"],
                iam["id"],
                iam["urn"],
            )
        print(table)


class ClouderContextSetApp(ClouderBaseApp):
    """An application to set the current context."""

    description = """
      An application to set the current context.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 2:
            warnings.warn("You should provide a cloud and a context_id.")
            self.exit(1)
        cloud = self.extra_args[0]
        context_id = self.extra_args[1]
        context = get_ovh_project(context_id)
        clouder = load_context()
        clouder["clouder"]["default_context"] = cloud + DEFAULT_BOX_SEPARATOR + context_id
        clouder["clouder"]["contexts"][cloud][context_id] = {}
        clouder["clouder"]["contexts"][cloud][context_id]["name"] = context["description"]
        self.log.debug("Setting context%s", yaml.dump(context, sort_keys=False))
        save_context(clouder)
        print_context(clouder)


class ClouderContextRemoveApp(ClouderBaseApp):
    """An application to remove context."""

    description = """
      An application to remove a context.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 2:
            warnings.warn("You should provide a cloud and a context_id.")
            self.exit(1)
        cloud = self.extra_args[0]
        context_id = self.extra_args[1]
        context = load_context()
        default_context = context["clouder"]["default_context"]
        if default_context == cloud + ":" + context_id:
            warnings.warn("Can not remove the default context.")
            self.exit(1)
        del context["clouder"]["contexts"][cloud][context_id]
        save_context(context)
        print_context(context)


class ClouderContextInitApp(ClouderBaseApp):
    """An application to init the context."""

    description = """
      An application to init the context.
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) != 0:
            warnings.warn("You should provide a cloud and a context_id.")
            self.exit(1)
        clouder = yaml.safe_load(f"""
clouder:
    version: 1.0.0
    default_context:
    contexts:
        ovh:
            pid:
                name: pname
""")
        app = ClouderContextListApp()
        app.start()
        contexts = app._contexts
        for context in contexts:
            clouder["clouder"]["contexts"]["ovh"][context["project_id"]] = {
                "name" : context["description"]
            }
        save_context(clouder)
        app = ClouderContextRemoveApp(extra_args=["ovh", "pid"])
        app.start()


class ClouderContextApp(ClouderBaseApp):
    """An application for the context."""

    description = """
      Manage the Clouder contexts.
    """

    subcommands = {
        "init": (ClouderContextInitApp, ClouderContextInitApp.description.splitlines()[0]),
        "ls": (ClouderContextListApp, ClouderContextListApp.description.splitlines()[0]),
        "show": (ClouderContextShowApp, ClouderContextShowApp.description.splitlines()[0]),
        "rm": (ClouderContextRemoveApp, ClouderContextRemoveApp.description.splitlines()[0]),
        "set": (ClouderContextSetApp, ClouderContextSetApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            app = ClouderContextShowApp()
            app.start()
        except NoStart:
            pass
        self.exit(0)
