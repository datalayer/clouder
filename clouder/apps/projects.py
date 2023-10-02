import warnings

from datalayer.application import NoStart

from .base import ClouderBaseApp
from ..cloud.ovh.api import get_ovh_projects


class ProjectsListApp(ClouderBaseApp):
    """An application to list the projects."""

    description = """
      An application to list the projects
    """

    def start(self):
        """Start the app."""
        if len(self.extra_args) > 1:
            warnings.warn("Too many arguments were provided.")
            self.exit(1)
        ovh_projects = get_ovh_projects()
        for ovh_project in ovh_projects:
            print('OVHcloud project', ovh_project)


class ClouderProjectsApp(ClouderBaseApp):
    """An application for the projects."""

    description = """
      Manage the projects.
    """

    subcommands = {
        "list": (ProjectsListApp, ProjectsListApp.description.splitlines()[0]),
    }

    def start(self):
        try:
            super().start()
            self.log.error(f"One of `{'` `'.join(ClouderProjectsApp.subcommands.keys())}` must be specified.")
            self.exit(1)
        except NoStart:
            pass
        self.exit(0)
