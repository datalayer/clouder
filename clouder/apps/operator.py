from .base import ClouderApp

from ..operator.operations import (
  start_operator, stop_operator, test_operator
)


class ClouderOperatorApp(ClouderApp):
    """An application to get you started with Clouder."""

    description = """
      Get you started with Clouder.
    """

    def start(self):
        super().start()
        self.log.info("Testing operator...")
        start_operator()
        test_operator()
        import time
        time.sleep(3)
        stop_operator()
