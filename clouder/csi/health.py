"""HTTP health endpoint of the Local CSI node plugin.

- ``GET /healthz`` and ``GET /readyz``: 200 once the gRPC server is serving;
- ``GET /mounts``: the driver's bridges and volumes as JSON, what
  ``clouder local-csi status`` reads.

The liveness probe in the chart hits ``/healthz``. Bound on all interfaces of
the pod: the port is not a Service and nothing routes to it from outside the
node.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("clouder.csi.health")


class HealthServer:
    def __init__(self, driver, *, port: int = 9808, host: str = "0.0.0.0"):
        self.driver = driver
        self.port = port
        self.host = host
        self.serving = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        health = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                if self.path in ("/healthz", "/readyz", "/"):
                    if health.serving:
                        self._json(200, {"status": "ok", "driver": health.driver.snapshot()["driver"], "node_id": health.driver.node_id})
                    else:
                        self._json(503, {"status": "starting"})
                elif self.path == "/mounts":
                    self._json(200, health.driver.snapshot())
                else:
                    self._json(404, {"error": "not found"})

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002 - http.server API
                log.debug(format, *args)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="local-csi-health", daemon=True)
        self._thread.start()
        log.info("health endpoint on http://%s:%s/healthz", self.host, self.port)

    @property
    def bound_port(self) -> int:
        return self._server.server_address[1] if self._server is not None else self.port

    def stop(self) -> None:
        self.serving = False
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
