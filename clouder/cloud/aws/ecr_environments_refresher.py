"""Refresh the `ecr-environments` pull secret (PLAN_ENV.md, D-17).

This file runs inside the CronJob that `clouder aws ecr-environments refresher` installs,
mounted from a ConfigMap into a stock Python image, so it must stay standard library only.
The job's init container has already written an ECR password, which lasts 12 hours. This
writes it into a `kubernetes.io/dockerconfigjson` Secret through the Kubernetes API with the
pod's service account: it replaces the Secret, or creates it the first time.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Callable, Mapping

SERVICE_ACCOUNT = "/var/run/secrets/kubernetes.io/serviceaccount"
LABELS = {"app.kubernetes.io/managed-by": "clouder", "app.kubernetes.io/part-of": "datalayer-environments"}

Send = Callable[[str, str, bytes], None]


def docker_config(registry: str, password: str) -> bytes:
    auth = base64.b64encode(f"AWS:{password}".encode()).decode()
    entry = {"username": "AWS", "password": password, "auth": auth}
    return json.dumps({"auths": {registry: entry}}, sort_keys=True).encode()


def pull_secret(name: str, namespace: str, registry: str, password: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"name": name, "namespace": namespace, "labels": dict(LABELS)},
        "data": {".dockerconfigjson": base64.b64encode(docker_config(registry, password)).decode()},
    }


def refresh(send: Send, api: str, namespace: str, name: str, registry: str, password: str) -> str:
    """Replace the Secret, or create it when there is none."""
    body = json.dumps(pull_secret(name, namespace, registry, password)).encode()
    collection = f"{api}/api/v1/namespaces/{namespace}/secrets"
    try:
        send("PUT", f"{collection}/{name}", body)
        return "replaced"
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    send("POST", collection, body)
    return "created"


def sender(token: str, context: ssl.SSLContext) -> Send:
    def send(method: str, url: str, body: bytes) -> None:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(request, context=context, timeout=30) as response:  # noqa: S310
            response.read()

    return send


def _read(path: str) -> str:
    with open(path) as handle:
        return handle.read().strip()


def main(environ: Mapping[str, str] = os.environ, service_account: str = SERVICE_ACCOUNT) -> int:
    registry = environ.get("REGISTRY", "")
    name = environ.get("SECRET_NAME", "ecr-environments")
    host = environ.get("KUBERNETES_SERVICE_HOST", "")
    port = environ.get("KUBERNETES_SERVICE_PORT", "443")
    try:
        password = _read(environ.get("PASSWORD_FILE", "/work/password"))
        token = _read(f"{service_account}/token")
        namespace = _read(f"{service_account}/namespace")
        context = ssl.create_default_context(cafile=f"{service_account}/ca.crt")
    except OSError as error:
        print(f"cannot read {error.filename}: {error.strerror}", file=sys.stderr)
        return 1
    if not registry or not password or not host:
        print("REGISTRY, KUBERNETES_SERVICE_HOST and an ECR password are all required", file=sys.stderr)
        return 1
    api = f"https://[{host}]:{port}" if ":" in host else f"https://{host}:{port}"
    try:
        outcome = refresh(sender(token, context), api, namespace, name, registry, password)
    except urllib.error.HTTPError as error:
        print(f"the Kubernetes API answered {error.code} {error.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"the Kubernetes API is unreachable: {error.reason}", file=sys.stderr)
        return 1
    print(f"{name} {outcome} in {namespace} for {registry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
