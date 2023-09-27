from .base import ClouderApp


class ClouderUpApp(ClouderApp):
    """An application to get you started with Clouder."""

    description = """
      Get you started with Clouder.
    """

    def start(self):
        super().start()
        self.log.info("Starting up application...")
