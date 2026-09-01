"""Mounting a bucket, and the credential that has to outlive nothing.

An STS session lasts an hour; a runtime lives for days. Everything here
follows from that: the agent serves the credentials itself so that refreshing
a mount is refreshing what the endpoint answers, rather than unmounting and
remounting a filesystem somebody is reading from.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from ..csi.bucket_processes import (
    CLOUD_STORAGE_KIND,
    SECRET_ACCESS_KEY_ID,
    SECRET_ENDPOINT,
    SECRET_EXPIRATION,
    SECRET_REGION,
    SECRET_SECRET_ACCESS_KEY,
    SECRET_SESSION_TOKEN,
    BucketProcesses,
    CredentialEndpoint,
)
from ..csi.node_mount_gateway import ERROR_PROCESS_UNSUPPORTED, NodeMountGatewayError
from ..csi.mounter import FakeMounter

SESSION = {
    SECRET_ACCESS_KEY_ID: b"ASIAEXAMPLE",
    SECRET_SECRET_ACCESS_KEY: b"secret",
    SECRET_SESSION_TOKEN: b"token",
    SECRET_EXPIRATION: b"2026-08-31T12:00:00Z",
    SECRET_REGION: b"eu-west-1",
}


# ---------------------------------------------------------------------------
# The credential endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def endpoint():
    served = CredentialEndpoint()
    served.start()
    yield served
    served.close()


def fetch(url: str, token: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"Authorization": token})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - loopback
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, {}


def test_it_serves_the_session_in_the_shape_the_sdk_expects(endpoint):
    token = endpoint.issue(SESSION)

    status, payload = fetch(endpoint.url, token)

    assert status == 200
    assert payload["AccessKeyId"] == "ASIAEXAMPLE"
    assert payload["SecretAccessKey"] == "secret"
    assert payload["Token"] == "token"
    # Without an expiry the SDK has no reason to come back, and the mount
    # keeps a session it cannot know has expired.
    assert payload["Expiration"] == "2026-08-31T12:00:00Z"


def test_a_request_without_the_mounts_token_gets_nothing(endpoint):
    endpoint.issue(SESSION)

    assert fetch(endpoint.url, "")[0] == 403
    assert fetch(endpoint.url, "some-other-token")[0] == 403


def test_one_mount_cannot_read_anothers_credentials(endpoint):
    mine = endpoint.issue(SESSION)
    theirs = endpoint.issue({**SESSION, SECRET_ACCESS_KEY_ID: b"ASIAOTHER"})

    assert fetch(endpoint.url, mine)[1]["AccessKeyId"] == "ASIAEXAMPLE"
    assert fetch(endpoint.url, theirs)[1]["AccessKeyId"] == "ASIAOTHER"


def test_refreshing_changes_what_a_token_serves(endpoint):
    token = endpoint.issue(SESSION)

    endpoint.refresh(token, {**SESSION, SECRET_SESSION_TOKEN: b"a-newer-session"})

    # The whole point: the mount keeps running and reads the new session on
    # its own schedule. No unmount, no remount, no broken file handle.
    assert fetch(endpoint.url, token)[1]["Token"] == "a-newer-session"


def test_refreshing_a_token_nobody_issued_does_nothing(endpoint):
    endpoint.refresh("invented", SESSION)

    assert fetch(endpoint.url, "invented")[0] == 403


def test_forgetting_a_mount_stops_serving_it(endpoint):
    token = endpoint.issue(SESSION)
    endpoint.forget(token)

    assert fetch(endpoint.url, token)[0] == 403


def test_it_listens_on_loopback_only(endpoint):
    # The tenant pod does not share the agent's network namespace, and this
    # is the second reason nothing outside it can read a session.
    assert endpoint.url.startswith("http://127.0.0.1:")


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for `mount-s3`, recording how it was invoked."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.pid = 5000

    def __call__(self, command, env=None, **kwargs):
        self.calls.append((command, env or {}))
        self.pid += 1
        return _Proc(self.pid)


class _Proc:
    def __init__(self, pid):
        self.pid = pid
        self._returncode = None

    def poll(self):
        return self._returncode

    def send_signal(self, sig):
        self._returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._returncode = -9


@pytest.fixture
def target(tmp_path):
    return str(tmp_path / "pods" / "x" / "data")


@pytest.fixture
def runner(monkeypatch, endpoint):
    mounter = FakeMounter()
    recorder = _Recorder()
    monkeypatch.setattr("clouder.csi.bucket_processes.subprocess.Popen", recorder)
    return BucketProcesses(mounter, endpoint=endpoint), recorder, mounter


def start(processes, target="/tmp/unused", **overrides):
    kwargs = {
        "kind": CLOUD_STORAGE_KIND,
        "source": "acme-bucket/research",
        "target": target,
        "read_only": False,
        "credential": SESSION,
    }
    kwargs.update(overrides)
    return processes.start(**kwargs)


def test_the_bucket_and_prefix_are_split_from_the_source(runner, target):
    processes, recorder, _mounter = runner

    start(processes, target)

    command, _env = recorder.calls[0]
    assert command[1] == "acme-bucket"
    assert "--prefix" in command and command[command.index("--prefix") + 1] == "research/"


def test_a_bucket_with_no_prefix_asks_for_none(runner, target):
    processes, recorder, _mounter = runner

    start(processes, target, source="acme-bucket")

    command, _env = recorder.calls[0]
    assert "--prefix" not in command


def test_the_credential_reaches_the_mount_through_the_endpoint_never_the_argv(runner, endpoint, target):
    processes, recorder, _mounter = runner

    start(processes, target)

    command, env = recorder.calls[0]
    # A credential on a command line is a credential in `ps`.
    assert "ASIAEXAMPLE" not in " ".join(command)
    assert "secret" not in " ".join(command)
    assert env["AWS_CONTAINER_CREDENTIALS_FULL_URI"] == endpoint.url
    status, payload = fetch(endpoint.url, env["AWS_CONTAINER_AUTHORIZATION_TOKEN"])
    assert status == 200 and payload["AccessKeyId"] == "ASIAEXAMPLE"


def test_a_read_only_grant_mounts_read_only(runner, target):
    processes, recorder, _mounter = runner

    start(processes, target, read_only=True)

    assert "--read-only" in recorder.calls[0][0]


def test_the_region_and_endpoint_travel_with_the_session(runner, target):
    processes, recorder, _mounter = runner

    start(processes, target, credential={**SESSION, SECRET_ENDPOINT: b"https://s3.example"})

    command, _env = recorder.calls[0]
    assert command[command.index("--region") + 1] == "eu-west-1"
    assert command[command.index("--endpoint-url") + 1] == "https://s3.example"


def test_a_secret_without_a_key_starts_nothing(runner, target):
    processes, recorder, _mounter = runner

    with pytest.raises(NodeMountGatewayError) as raised:
        start(processes, target, credential={SECRET_REGION: b"eu-west-1"})

    assert raised.value.code == ERROR_PROCESS_UNSUPPORTED
    assert recorder.calls == []


def test_a_source_naming_no_bucket_starts_nothing(runner, target):
    processes, recorder, _mounter = runner

    with pytest.raises(NodeMountGatewayError):
        start(processes, target, source="/")

    assert recorder.calls == []


def test_a_kind_this_runner_does_not_serve_is_refused(runner, target):
    processes, _recorder, _mounter = runner

    with pytest.raises(NodeMountGatewayError):
        start(processes, target, kind="local-bridge")


def test_refreshing_a_running_mount_changes_only_what_it_will_read(runner, endpoint, target):
    processes, recorder, _mounter = runner
    pid = start(processes, target)
    token = recorder.calls[0][1]["AWS_CONTAINER_AUTHORIZATION_TOKEN"]

    processes.refresh(pid, {**SESSION, SECRET_SESSION_TOKEN: b"newer"})

    # One process, still running, now reading a newer session.
    assert len(recorder.calls) == 1
    assert processes.alive(pid) is True
    assert fetch(endpoint.url, token)[1]["Token"] == "newer"


def test_stopping_forgets_the_credential_and_unmounts(runner, endpoint, target):
    processes, recorder, mounter = runner
    pid = start(processes, target)
    token = recorder.calls[0][1]["AWS_CONTAINER_AUTHORIZATION_TOKEN"]
    mounter.mounts.add(target)

    processes.stop(pid, target)

    # A session still being served for a mount that is gone is a session
    # nobody is watching.
    assert fetch(endpoint.url, token)[0] == 403
    assert target not in mounter.mounts


def test_a_process_it_did_not_start_is_still_alive(runner):
    processes, _recorder, _mounter = runner

    assert processes.alive(os.getpid()) is True
    assert processes.alive(2**22) is False
