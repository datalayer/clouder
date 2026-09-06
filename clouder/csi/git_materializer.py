# Copyright (c) 2023-2026 Datalayer, Inc.
# Datalayer License

"""Checking out a Git repository on the node, so a sandbox can bind it.

A repository is the one kind of content the gateway cannot mount. There is no
filesystem behind a URL and nothing to bind, so the content has to exist on
the node before a mount of it can be made — which is what the creation-time
path does too, with an init container that clones into an `emptyDir`.

Doing it here rather than there buys two things. The checkout happens while
the Pod is already running, which is the whole point of the gateway; and it is
made once per node rather than once per Pod, so the tenth sandbox to ask for
the same tutorial repository binds the checkout the first one paid for.

The checkout is bound read-only, always. It is shared, and a writable mount of
a shared checkout is one sandbox's `git clean` showing up in another's files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any

from datalayer_core.contents_node_mount_gateway import (
    ERROR_MOUNT_FAILED,
    GIT_KIND,
    NodeMountGatewayError,
)

log = logging.getLogger("clouder.csi.git_materializer")


def _subcommand(args: list[str]) -> str:
    """The Git command being run, for an error a person has to read."""
    rest = list(args)
    while rest and rest[0].startswith("-"):
        # `-C <path>` and friends come first and say nothing about what failed.
        rest = rest[2:] if len(rest) > 1 else []
    return rest[0] if rest else "git"


#: How long a checkout may take before it is abandoned. A clone that hangs is
#: a Pod waiting on a mount that will never arrive, and the sandbox is already
#: running: it is better to fail it and say so.
DEFAULT_CLONE_TIMEOUT_SECONDS = 300

#: A full commit sha, the only revision that means the same thing everywhere.
_SHA_LENGTH = 40

#: What a checkout may speak. Not `file`, not `ext`, not the transports that
#: read this node's own disk or run a command to fetch.
DEFAULT_PROTOCOLS = "https:ssh"


class GitMaterializer:
    """Produces a checkout on the node and hands back the path to bind."""

    def __init__(
        self,
        root: str,
        *,
        git: str = "git",
        timeout: int = DEFAULT_CLONE_TIMEOUT_SECONDS,
        protocols: str = DEFAULT_PROTOCOLS,
        runner=subprocess.run,
    ) -> None:
        self.root = os.path.normpath(root)
        self.git = git
        self.timeout = timeout
        self.protocols = protocols
        self._run = runner

    def kinds(self) -> tuple[str, ...]:
        return (GIT_KIND,)

    def path_for(self, source: str, revision: str) -> str:
        """Where a checkout of this repository at this revision lives.

        Content-addressed on both, so re-pinning a grant to a new tag is a new
        directory rather than a checkout mutated under a Pod that is reading
        it. The old one is left for the sweeper.
        """
        digest = hashlib.sha256(f"{source}\n{revision}".encode("utf-8")).hexdigest()
        return os.path.join(self.root, GIT_KIND, digest[:32])

    def materialize(
        self,
        *,
        kind: str,
        source: str,
        revision: str,
        credential: dict[str, Any] | None = None,
    ) -> str:
        if kind != GIT_KIND:
            raise NodeMountGatewayError(
                ERROR_MOUNT_FAILED, f"this materializer does not produce '{kind}' content"
            )
        path = self.path_for(source, revision)
        if os.path.isdir(os.path.join(path, ".git")):
            # Already here: either this node checked it out for another Pod,
            # or for this one before it was restarted. Either way it is the
            # same commit, because the revision is part of the path.
            return path
        return self._checkout(path, source, revision, credential or {})

    # -- making one --------------------------------------------------------

    def _checkout(
        self, path: str, source: str, revision: str, credential: dict[str, Any]
    ) -> str:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        # Built beside the destination and renamed into place, so a clone that
        # dies half way leaves a directory nobody will ever bind rather than
        # half a repository at the path a Pod is about to mount.
        staging = tempfile.mkdtemp(prefix=".staging-", dir=os.path.dirname(path))
        try:
            self._clone(staging, source, revision, credential)
            resolved = self._resolve_head(staging)
            self._write_state(staging, source, revision, resolved)
            try:
                os.rename(staging, path)
            except OSError:
                # Another pass won the race and published the same commit.
                if not os.path.isdir(os.path.join(path, ".git")):
                    raise
                shutil.rmtree(staging, ignore_errors=True)
            return path
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _clone(
        self, path: str, source: str, revision: str, credential: dict[str, Any]
    ) -> None:
        # Written once, for the whole checkout: it is the same credential for
        # every command, and creating it per command is a file that already
        # exists by the second one.
        askpass = self._askpass(path, credential)
        username = str(credential.get("username") or "x-access-token")
        # `init` + `fetch` rather than `clone --branch`, because it is the one
        # form that takes a branch, a tag *and* a commit sha. `clone --branch`
        # does not take a sha, and a sha is the revision worth pinning to.
        self._git(path, ["init", "--quiet", "--initial-branch", "main", path])
        self._git(path, ["-C", path, "remote", "add", "origin", source])
        self._git(
            path,
            ["-C", path, "fetch", "--quiet", "--depth", "1", "origin", revision],
            askpass=askpass,
            username=username,
        )
        self._git(path, ["-C", path, "checkout", "--quiet", "--detach", "FETCH_HEAD"])
        if askpass:
            # The token has done its work. Leaving it in the checkout would
            # publish it to every sandbox that binds this repository.
            for leftover in (askpass, f"{askpass}.token"):
                try:
                    os.unlink(leftover)
                except OSError as exc:  # noqa: BLE001
                    log.warning("could not remove %s: %s", leftover, exc)

    def _git(
        self, path: str, args: list[str], *, askpass: str = "", username: str = ""
    ) -> str:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": path,
            # None of this node's Git configuration applies to a tenant's
            # checkout: no `insteadOf` rewriting the URL somewhere else, no
            # helper handing it a credential it was not granted.
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            # Two protocols and no others. It repeats the check the grant
            # already passed, because this is the one that also applies to a
            # redirect the remote sends *after* the URL was approved — a
            # `file://` reply to an `https://` request being the interesting
            # one, since the node has a filesystem worth reading.
            "GIT_ALLOW_PROTOCOL": self.protocols,
        }
        if askpass:
            env["GIT_ASKPASS"] = askpass
            env["GIT_USERNAME"] = username
        try:
            done = self._run(
                [self.git, *args],
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NodeMountGatewayError(
                ERROR_MOUNT_FAILED, f"the checkout did not finish in {self.timeout}s"
            ) from exc
        except OSError as exc:
            raise NodeMountGatewayError(
                ERROR_MOUNT_FAILED, f"'{self.git}' would not run: {exc}"
            ) from exc
        if done.returncode != 0:
            # The token is passed through askpass and never appears in a
            # command line, so stderr is safe to report — but it is trimmed,
            # because a Pod annotation is not a log.
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            raise NodeMountGatewayError(
                ERROR_MOUNT_FAILED,
                f"git {_subcommand(args)} failed: "
                f"{detail[-1][:200] if detail else done.returncode}",
            )
        return (done.stdout or "").strip()

    def _askpass(self, path: str, credential: dict[str, Any]) -> str:
        """A helper that prints the token, so it never reaches a command line.

        A credential in a URL is a credential in `ps`, in the reflog, and in
        every error message Git prints about the remote.
        """
        token = str(credential.get("token") or credential.get("password") or "")
        if not token:
            return ""
        helper = os.path.join(path, ".git-askpass")
        with open(
            os.open(helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700), "w"
        ) as handle:
            handle.write(
                '#!/bin/sh\ncase "$1" in Username*) echo "$GIT_USERNAME";; *) cat "$0.token";; esac\n'
            )
        with open(
            os.open(f"{helper}.token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as handle:
            handle.write(token)
        os.chmod(helper, stat.S_IRWXU)
        return helper

    def _resolve_head(self, path: str) -> str:
        try:
            return self._git(path, ["-C", path, "rev-parse", "HEAD"])
        except NodeMountGatewayError:
            return ""

    def _write_state(self, path: str, source: str, revision: str, resolved: str) -> None:
        """What this checkout is, beside it, for whoever debugs it later.

        The commit matters most. A grant pinned to a branch is resolved once
        per node and then kept, so two nodes can serve two commits for the
        same grant — which is why the Operator resolves a revision to a sha
        before it writes one, and why this file exists to prove it did not.
        """
        if revision != resolved and len(revision) != _SHA_LENGTH:
            log.info(
                "checkout of %s at '%s' resolved to %s on this node",
                source,
                revision,
                resolved or "an unknown commit",
            )
        try:
            with open(os.path.join(path, ".datalayer-checkout.json"), "w") as handle:
                json.dump(
                    {
                        "source": source,
                        "revision": revision,
                        "commit": resolved,
                        "checked_out_at": int(time.time()),
                    },
                    handle,
                )
        except OSError as exc:  # noqa: BLE001 - a note, not the mount
            log.debug("could not record what was checked out: %s", exc)


class NoMaterialize:
    """The default: the agent mounts what exists and produces nothing.

    An agent without a materializer refuses a grant that needs one rather than
    reporting a mount it never made.
    """

    def kinds(self) -> tuple[str, ...]:
        return ()

    def materialize(self, *, kind: str, source: str, revision: str, credential=None) -> str:
        raise NodeMountGatewayError(
            ERROR_MOUNT_FAILED,
            f"this node agent cannot produce the content a '{kind}' mount needs",
        )
