"""HTTP health endpoint of the Local CSI node plugin.

- ``GET /healthz`` and ``GET /readyz``: 200 once the gRPC server is serving;
- ``GET /mounts``: the driver's bridges and volumes as JSON, what
  ``clouder local-csi status`` reads;
- ``GET /gateway``: the Node Mount Gateway's per-pod trees and counters, when the
  gateway runs in this process;
- ``GET /metrics``: the same counters in Prometheus text format. A leaked
  mount is what makes a Pod stick in ``Terminating``, and an operator should
  not have to run a CLI to find out one happened.

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


def _escape(value: str) -> str:
    """A Prometheus label value: backslash, quote and newline are special."""
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class HealthServer:
    def __init__(self, driver, *, port: int = 9808, host: str = "0.0.0.0", gateway=None):
        self.driver = driver
        self.gateway = gateway
        self.port = port
        self.host = host
        self.serving = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def metrics(self) -> str:
        """The node's driver and gateway state, in Prometheus text format.

        Written by hand rather than with a client library: this process is a
        privileged node component, and a metrics endpoint is not worth a
        dependency in it. The names carry the ``datalayer_`` prefix and the
        node label the observer groups by.
        """
        node = _escape(self.driver.node_id)
        lines = [
            "# HELP datalayer_local_csi_bridges The bridge filesystems this node is running.",
            "# TYPE datalayer_local_csi_bridges gauge",
            "# HELP datalayer_local_csi_bridges_disconnected Bridges whose filesystem is not connected.",
            "# TYPE datalayer_local_csi_bridges_disconnected gauge",
        ]
        try:
            bridges = (self.driver.snapshot().get("bridges") or {}).values()
        except Exception:  # noqa: BLE001 - metrics must not be able to break the probe
            bridges = []
        disconnected = sum(1 for bridge in bridges if not bridge.get("connected"))
        lines.append(f'datalayer_local_csi_bridges{{node="{node}"}} {len(list(bridges))}')
        lines.append(f'datalayer_local_csi_bridges_disconnected{{node="{node}"}} {disconnected}')

        if self.gateway is not None:
            try:
                snapshot = self.gateway.snapshot()
            except Exception:  # noqa: BLE001
                snapshot = {"counters": {}, "pods": {}}
            counters = snapshot.get("counters") or {}
            pods = snapshot.get("pods") or {}
            mounted = sum(
                1
                for detail in pods.values()
                for spec in (detail.get("mounts") or {}).values()
                if spec.get("mounted")
            )
            lines += [
                "# HELP datalayer_mount_gateway_pods Runtime pods this node holds a gateway tree for.",
                "# TYPE datalayer_mount_gateway_pods gauge",
                f'datalayer_mount_gateway_pods{{node="{node}"}} {len(pods)}',
                "# HELP datalayer_mount_gateway_mounts Folders currently bound into running pods.",
                "# TYPE datalayer_mount_gateway_mounts gauge",
                f'datalayer_mount_gateway_mounts{{node="{node}"}} {mounted}',
            ]
            for name, help_text, kind in (
                ("granted", "Folders bound since this agent started.", "counter"),
                ("revoked", "Folders unmounted since this agent started.", "counter"),
                ("failed", "Grants that could not be applied.", "counter"),
                ("released", "Pods whose gateway tree was taken down cleanly.", "counter"),
                (
                    "leaked",
                    "Mounts that would not unmount. Each one is a Pod that will stick in Terminating.",
                    "counter",
                ),
            ):
                metric = f"datalayer_mount_gateway_{name}_total"
                lines += [
                    f"# HELP {metric} {help_text}",
                    f"# TYPE {metric} {kind}",
                    f'{metric}{{node="{node}"}} {int(counters.get(name, 0) or 0)}',
                ]
        return "\n".join(lines) + "\n"

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
                elif self.path == "/gateway":
                    if health.gateway is None:
                        self._json(404, {"error": "the Node Mount Gateway is not enabled on this node"})
                    else:
                        self._json(200, health.gateway.snapshot())
                elif self.path == "/metrics":
                    self._text(200, health.metrics())
                else:
                    self._json(404, {"error": "not found"})

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _text(self, status: int, body: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

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
