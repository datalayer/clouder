"""The Clouder applications."""

from ._base import ClouderBaseApp
from .ctx import ClouderContextApp
from .k8s import ClouderKubernetesApp
from .me import ClouderMeApp
from .operator import ClouderOperatorApp
from .box import ClouderBoxApp
from .run import ClouderRunSbinApp, ClouderRunShellApp
from .s3 import ClouderS3App
from .ssh_key import ClouderSSHKeyApp
from .vm import ClouderVirtualMachineApp

# pylint: disable=invalid-all-object
__all__ = [
  ClouderBaseApp,
  ClouderContextApp,
  ClouderKubernetesApp,
  ClouderMeApp,
  ClouderOperatorApp,
  ClouderBoxApp,
  ClouderS3App,
  ClouderSSHKeyApp,
  ClouderRunSbinApp,
  ClouderRunShellApp,
  ClouderVirtualMachineApp,
]
