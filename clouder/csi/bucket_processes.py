"""Mounting a bucket, with a credential that outlives no mount.

An STS session lasts an hour or a few; a runtime lives for days. Whatever
mounts a bucket therefore has to **refresh**, and that single requirement
decided the rest of this file.

`s3fs-fuse` reads its credentials once, at start. Its refreshing modes are the
node's IMDS role — which is the node's identity, not the user's, so every
sandbox on that node would reach every bucket the node can — and the ECS
provider, which reads a fixed link-local address we cannot serve. So a bucket
mounted by s3fs starts returning 403 when its session expires, and there is
nothing to do about it from here.

Mountpoint for Amazon S3 uses the AWS SDK, whose container-credentials
provider re-fetches from `AWS_CONTAINER_CREDENTIALS_FULL_URI` as a session
nears expiry. So the agent serves that URI itself, on loopback, from the
Secret the grant names — and refreshing a mount becomes refreshing a Secret,
which the Operator can do without touching the mount at all. No unmount, no
remount, no open file handle broken mid-read.

The endpoint is bound to `127.0.0.1` in the DaemonSet's own network namespace,
which the tenant Pod does not share, and every request must carry the token
the mount was started with.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .node_mount_gateway import ERROR_PROCESS_UNSUPPORTED, NodeMountGatewayError
from .mounter import MountError, Mounter

log = logging.getLogger("clouder.csi.node_mount_gateway.bucket")

#: The grant kind this runs.
CLOUD_STORAGE_KIND = "cloud-storage"

#: What the pod-owned Secret carries: an STS session, refreshed in place by
#: whoever minted it. `expiration` is what tells the SDK when to come back.
SECRET_ACCESS_KEY_ID = "access-key-id"
SECRET_SECRET_ACCESS_KEY = "secret-access-key"
SECRET_SESSION_TOKEN = "session-token"
SECRET_EXPIRATION = "expiration"
SECRET_REGION = "region"
SECRET_ENDPOINT = "endpoint-url"


class CredentialEndpoint:
    """Serves one bucket mount's current credentials, to that mount alone.

    Bound to loopback in the agent's own network namespace — which a tenant
    pod does not share — and every request must present the token the mount
    was started with. A credential server anything on the node could read
    would be a worse place for a session than the Secret it came from.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._by_token: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._port = port

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._server is not None:
            return
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                token = self.headers.get("Authorization", "")
                payload = endpoint.credentials_for(token)
                if payload is None:
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002 - http.server API
                log.debug(format, *args)

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._server.daemon_threads = True
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="gateway-credentials", daemon=True
        )
        self._thread.start()
        log.info("bucket credential endpoint on %s:%s", self._host, self._port)

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}/"

    # -- what it serves ----------------------------------------------------

    def issue(self, credential: dict[str, bytes]) -> str:
        """Register one mount's credentials and return the token that reads them."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._by_token[token] = _aws_payload(credential)
        return token

    def refresh(self, token: str, credential: dict[str, bytes]) -> None:
        """Replace what a token serves, without disturbing the mount reading it."""
        with self._lock:
            if token in self._by_token:
                self._by_token[token] = _aws_payload(credential)

    def forget(self, token: str) -> None:
        with self._lock:
            self._by_token.pop(token, None)

    def credentials_for(self, token: str) -> dict[str, str] | None:
        with self._lock:
            return self._by_token.get(token.strip())


def _aws_payload(credential: dict[str, bytes]) -> dict[str, str]:
    """The shape the AWS SDK's container-credentials provider expects."""
    payload = {
        "AccessKeyId": _text(credential.get(SECRET_ACCESS_KEY_ID)),
        "SecretAccessKey": _text(credential.get(SECRET_SECRET_ACCESS_KEY)),
    }
    token = _text(credential.get(SECRET_SESSION_TOKEN))
    if token:
        payload["Token"] = token
    expiration = _text(credential.get(SECRET_EXPIRATION))
    if expiration:
        # Without this the SDK has no reason to come back, and the mount keeps
        # a session it cannot know has expired.
        payload["Expiration"] = expiration
    return payload


def is_static_credential(credential: dict[str, bytes]) -> bool:
    """A key pair with no session token and no expiration: nothing to refresh."""
    return not _text(credential.get(SECRET_SESSION_TOKEN)) and not _text(credential.get(SECRET_EXPIRATION))


class BucketProcesses:
    """The gateway's runner for `cloud-storage` grants, over Mountpoint for S3."""

    def __init__(
        self,
        mounter: Mounter,
        *,
        endpoint: CredentialEndpoint | None = None,
        binary: str = "mount-s3",
    ) -> None:
        self._mounter = mounter
        self._endpoint = endpoint or CredentialEndpoint()
        self._binary = binary
        self._procs: dict[int, subprocess.Popen] = {}
        self._tokens: dict[int, str] = {}

    def start(
        self,
        *,
        kind: str,
        source: str,
        target: str,
        read_only: bool,
        credential: dict[str, bytes],
    ) -> int:
        if kind != CLOUD_STORAGE_KIND:
            raise NodeMountGatewayError(
                ERROR_PROCESS_UNSUPPORTED,
                f"this runner serves '{CLOUD_STORAGE_KIND}' grants, not '{kind}'",
            )
        if not _text(credential.get(SECRET_ACCESS_KEY_ID)):
            raise NodeMountGatewayError(
                ERROR_PROCESS_UNSUPPORTED,
                f"the Secret for '{source}' carries no {SECRET_ACCESS_KEY_ID}",
            )
        bucket, _, prefix = str(source).strip("/").partition("/")
        if not bucket:
            raise NodeMountGatewayError(ERROR_PROCESS_UNSUPPORTED, f"'{source}' names no bucket")

        os.makedirs(target, exist_ok=True)
        command = [self._binary, bucket, target, "--allow-other", "--foreground"]
        if prefix:
            command += ["--prefix", f"{prefix.rstrip('/')}/"]
        if read_only:
            command.append("--read-only")
        region = _text(credential.get(SECRET_REGION))
        if region:
            command += ["--region", region]
        endpoint_url = _text(credential.get(SECRET_ENDPOINT))
        if endpoint_url:
            command += ["--endpoint-url", endpoint_url]

        env = dict(os.environ)
        # The SDK inside Mountpoint asks the EC2 instance metadata service
        # for what it was not told — and on a node that is not an EC2
        # instance that is a probe that times out, 2.3 s per mount, three
        # mounts per launch. Everything it needs is in the environment or
        # the argv; there is nothing to ask.
        env["AWS_EC2_METADATA_DISABLED"] = "true"
        token = ""
        if is_static_credential(credential):
            # A static key is not a session: nothing expires, nothing is
            # refreshed — and the container-credentials provider Mountpoint
            # reads a session through wants a token and an expiration, so a
            # bare key handed to it mounts, then answers nothing. The key
            # travels in the process's own environment instead, which no
            # other process on the node reads, and never in the argv.
            env["AWS_ACCESS_KEY_ID"] = _text(credential.get(SECRET_ACCESS_KEY_ID))
            env["AWS_SECRET_ACCESS_KEY"] = _text(credential.get(SECRET_SECRET_ACCESS_KEY))
            env.pop("AWS_CONTAINER_CREDENTIALS_FULL_URI", None)
            env.pop("AWS_CONTAINER_AUTHORIZATION_TOKEN", None)
        else:
            # The SDK re-fetches from here as the session nears expiry, so
            # refreshing the mount is refreshing what this serves — no
            # unmount, no remount, no open file handle broken mid-read.
            self._endpoint.start()
            token = self._endpoint.issue(credential)
            # The SDK reads a key from the environment before it asks the
            # endpoint: a key the agent's own process happened to carry
            # would win over the session. None does.
            for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
                env.pop(name, None)
            env["AWS_CONTAINER_CREDENTIALS_FULL_URI"] = self._endpoint.url
            env["AWS_CONTAINER_AUTHORIZATION_TOKEN"] = token
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._procs[proc.pid] = proc
        if token:
            self._tokens[proc.pid] = token
        return proc.pid

    def alive(self, pid: int) -> bool:
        proc = self._procs.get(pid)
        if proc is not None:
            return proc.poll() is None
        # Adopted after an agent restart: still serving, still not ours.
        return _running(pid)

    def refresh(self, pid: int, credential: dict[str, bytes]) -> None:
        """Give a running mount a newer session, without touching the mount."""
        token = self._tokens.get(pid)
        if token:
            self._endpoint.refresh(token, credential)

    def stop(self, pid: int, target: str) -> None:
        token = self._tokens.pop(pid, None)
        if token:
            self._endpoint.forget(token)
        proc = self._procs.pop(pid, None)
        if proc is not None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        elif _running(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                log.warning("bucket filesystem pid %s would not stop: %s", pid, exc)
        try:
            self._mounter.unmount(target)
        except MountError as exc:
            log.warning("bucket mount %s would not unmount: %s", target, exc)


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value or "").strip()


def _running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
