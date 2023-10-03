"""The clouder applications."""

from .base import ClouderBaseApp
from .config import ClouderConfigApp
from .keys import ClouderKeysApp
from .kubernetes import ClouderKubernetesApp
from .operator import ClouderOperatorApp
from .profile import ClouderProfileApp
from .projects import ClouderProjectsApp
from .setup import ClouderSetupApp
from .shell import ClouderShellApp
from .status import ClouderStatusApp
from .virtual_machine import ClouderVirtualMachineApp

# pylint: disable=invalid-all-object
__all__ = [
  ClouderBaseApp,
  ClouderConfigApp,
  ClouderKeysApp,
  ClouderKubernetesApp,
  ClouderProfileApp,
  ClouderOperatorApp,
  ClouderProjectsApp,
  ClouderSetupApp,
  ClouderShellApp,
  ClouderStatusApp,
  ClouderVirtualMachineApp,
]
