"""The Clouder applications."""

from ._base import ClouderBaseApp
from .context import ClouderContextApp
from .k8s import ClouderKubernetesApp
from .me import ClouderMeApp
from .operator import ClouderOperatorApp
from .box import ClouderBoxApp
from .run import ClouderRunSbinApp, ClouderRunShellApp
from .ssh_key import ClouderSSHKeyApp
from .virtual_machine import ClouderVirtualMachineApp

# pylint: disable=invalid-all-object
__all__ = [
  ClouderBaseApp,
  ClouderContextApp,
  ClouderKubernetesApp,
  ClouderMeApp,
  ClouderOperatorApp,
  ClouderBoxApp,
  ClouderSSHKeyApp,
  ClouderRunSbinApp,
  ClouderRunShellApp,
  ClouderVirtualMachineApp,
]
