# Copyright (c) 2023-2026 Datalayer, Inc.
# Datalayer License

"""A repository is checked out on the node before anything can bind it.

These run against real `git` against a real local repository, because the
things worth checking here — that a revision is what came out, that a failed
clone leaves nothing at the path a Pod would mount — are exactly what a fake
`git` would be written to satisfy.
"""

import json
import os
import shutil
import subprocess

import pytest

from ..csi.git_materializer import GitMaterializer, NoMaterialize
from ..csi.node_mount_gateway import NodeMountGatewayError


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def git(path, *args):
    subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@datalayer.io",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@datalayer.io",
        },
    )


@pytest.fixture
def repository(tmp_path):
    """A repository with two commits, the first one tagged."""
    path = tmp_path / "origin"
    path.mkdir()
    git(path, "init", "--quiet", "--initial-branch", "main")
    (path / "README.md").write_text("first\n")
    git(path, "add", "README.md")
    git(path, "commit", "--quiet", "-m", "first")
    git(path, "tag", "v1.0")
    (path / "README.md").write_text("second\n")
    git(path, "commit", "--quiet", "-am", "second")
    return path


@pytest.fixture
def materializer(tmp_path):
    # `file` is added here and nowhere else. The default is `https:ssh`, and a
    # local path is not a repository the gateway will clone in production —
    # the grant format refuses one long before this does.
    return GitMaterializer(
        str(tmp_path / "materialized"), protocols="https:ssh:file"
    )


def test_a_local_path_is_not_a_repository_this_node_will_clone(tmp_path, repository):
    plain = GitMaterializer(str(tmp_path / "m"))

    with pytest.raises(NodeMountGatewayError) as failure:
        plain.materialize(kind="git", source=str(repository), revision="v1.0")

    # A node has a filesystem worth reading, and a checkout is one of the few
    # things on it that fetches a URL a tenant chose.
    assert "not allowed" in str(failure.value) or "protocol" in str(failure.value)


def test_a_tag_is_checked_out_at_the_commit_it_names(materializer, repository):
    path = materializer.materialize(
        kind="git", source=str(repository), revision="v1.0"
    )

    # The tag, not the branch tip: a pinned revision that quietly followed
    # `main` would be a mount whose content changes when somebody pushes.
    assert (open(os.path.join(path, "README.md")).read()) == "first\n"


def test_a_commit_sha_is_checked_out_too(materializer, repository, tmp_path):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    path = materializer.materialize(kind="git", source=str(repository), revision=head)

    # `clone --branch` cannot do this, which is why the checkout is an init
    # and a fetch — and a sha is the revision worth pinning to.
    assert (open(os.path.join(path, "README.md")).read()) == "second\n"
    assert json.load(open(os.path.join(path, ".datalayer-checkout.json")))["commit"] == head


def test_the_same_revision_is_checked_out_once_for_the_whole_node(
    materializer, repository
):
    first = materializer.materialize(kind="git", source=str(repository), revision="v1.0")
    marker = os.path.join(first, ".was-here")
    open(marker, "w").close()

    second = materializer.materialize(kind="git", source=str(repository), revision="v1.0")

    # The tenth sandbox to ask for the same tutorial repository binds the
    # checkout the first one paid for. That is most of why this is on the node.
    assert second == first
    assert os.path.exists(marker)


def test_two_revisions_of_one_repository_are_two_checkouts(materializer, repository):
    first = materializer.materialize(kind="git", source=str(repository), revision="v1.0")
    second = materializer.materialize(kind="git", source=str(repository), revision="main")

    # Re-pinning must not mutate a checkout a running Pod has mounted.
    assert first != second
    assert open(os.path.join(first, "README.md")).read() == "first\n"
    assert open(os.path.join(second, "README.md")).read() == "second\n"


def test_a_revision_that_is_not_there_leaves_nothing_behind(materializer, repository):
    with pytest.raises(NodeMountGatewayError) as failure:
        materializer.materialize(kind="git", source=str(repository), revision="v9.9")

    assert failure.value.code == "NODE_MOUNT_GATEWAY_MOUNT_FAILED"
    # Nothing half-cloned at the path a Pod would have bound: the checkout is
    # built beside it and renamed in, so a failure is a directory that never
    # appeared rather than an empty repository somebody mounts.
    assert not os.path.exists(materializer.path_for(str(repository), "v9.9"))
    assert os.listdir(os.path.join(materializer.root, "git")) == []


def test_a_clone_that_hangs_is_given_up_on(tmp_path, repository):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    materializer = GitMaterializer(str(tmp_path / "m"), timeout=1, runner=hang)

    with pytest.raises(NodeMountGatewayError) as failure:
        materializer.materialize(kind="git", source=str(repository), revision="v1.0")

    # The sandbox is already running and waiting on this mount. Failing it
    # with a reason beats holding it open on a clone that will never finish.
    assert "did not finish" in str(failure.value)


def test_the_token_never_reaches_a_command_line(tmp_path, repository):
    seen = []

    def record(args, **kwargs):
        seen.append((args, kwargs.get("env") or {}))
        return subprocess.CompletedProcess(args, 0, "", "")

    materializer = GitMaterializer(str(tmp_path / "m"), runner=record)
    try:
        materializer.materialize(
            kind="git",
            source="https://github.com/org/private",
            revision="v1.0",
            credential={"token": "ghp-secret-token"},
        )
    except NodeMountGatewayError:
        pass  # nothing was really cloned; the argument vectors are the point

    # A credential in a URL is a credential in `ps`, in the reflog and in
    # every error Git prints about the remote. It goes through askpass.
    assert not any("ghp-secret-token" in " ".join(args) for args, _ in seen), seen
    assert any(env.get("GIT_ASKPASS") for _, env in seen)


def test_the_token_is_not_left_in_the_checkout(materializer, repository):
    path = materializer.materialize(
        kind="git",
        source=str(repository),
        revision="v1.0",
        credential={"token": "ghp-secret-token"},
    )

    # Every sandbox on this node that asks for this repository binds this
    # directory. A credential left in it is a credential handed to all of them.
    leftovers = [name for name in os.listdir(path) if name.startswith(".git-askpass")]
    assert leftovers == []
    assert "ghp-secret-token" not in subprocess.run(
        ["grep", "-r", "ghp-secret-token", path], capture_output=True, text=True
    ).stdout


def test_an_agent_with_no_materializer_refuses_rather_than_reporting_a_mount():
    with pytest.raises(NodeMountGatewayError) as failure:
        NoMaterialize().materialize(kind="git", source="https://h/o/r", revision="v1")

    assert "cannot produce" in str(failure.value)
