"""The Node Mount Gateway: what it binds, what it refuses, and what it leaves behind.

The FakeMounter keeps the mount table in memory; the directories are real, so
the path resolution these tests exercise is the resolution that runs on a node.
"""

from __future__ import annotations

import json
import os

import pytest

from ..csi.node_mount_gateway import (
    ERROR_INVALID_SOURCE,
    ERROR_UNSUPPORTED_KIND,
    ERROR_MOUNT_DEAD,
    ERROR_PROCESS_UNSUPPORTED,
    ERROR_SECRET_REFUSED,
    NodeMountGatewayError,
    ERROR_MOUNT_FAILED,
    ERROR_NOT_READY,
    ERROR_TOO_MANY_MOUNTS,
    NODE_MOUNT_GATEWAY_VOLUME_NAME,
    STATE_DEGRADED,
    STATE_FAILED,
    STATE_READY,
    Grant,
    NodeMountGateway,
    PodRef,
    grants_hash,
    parse_grants,
)
from ..csi.mounter import FakeMounter, MountError

POD = "3f2b1a0c-1111-2222-3333-444455556666"


def annotation(*mounts) -> str:
    return json.dumps({"mounts": list(mounts)})


def mount(source: str, target: str, mode: str = "rw", allow_exec: bool = True) -> dict:
    return {"source": source, "target": target, "mode": mode, "allow_exec": allow_exec}


@pytest.fixture
def shared(tmp_path):
    root = tmp_path / "shared-fs"
    for sub in ("home/users/01H-eric", "home/organizations/01J-datalayer", "home/teams/01K-research"):
        (root / sub).mkdir(parents=True)
    return root


@pytest.fixture
def kubelet(tmp_path):
    root = tmp_path / "kubelet"
    (root / "pods" / POD / "volumes" / "kubernetes.io~empty-dir" / NODE_MOUNT_GATEWAY_VOLUME_NAME).mkdir(parents=True)
    return root


@pytest.fixture
def mounter() -> FakeMounter:
    return FakeMounter()


@pytest.fixture
def as_root(monkeypatch):
    """The agent is root on a node; a test runner is not.

    Only `chown` needs it, so that is all this stands in for — the walk, the
    modes and the refusals are exercised for real.
    """
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: None)


@pytest.fixture
def gateway(tmp_path, shared, kubelet, mounter) -> NodeMountGateway:
    return NodeMountGateway(
        mounter,
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(kubelet),
    )


def pod(annotation_value: str = "", *, terminating: bool = False, ready: str = "") -> PodRef:
    return PodRef(
        uid=POD,
        name="jupyter-01hx",
        namespace="datalayer-runtimes",
        terminating=terminating,
        annotation=annotation_value,
        ready_annotation=ready,
    )


# ---------------------------------------------------------------------------
# The wire format
# ---------------------------------------------------------------------------


def test_a_target_that_is_not_one_segment_is_dropped():
    grants = parse_grants(
        annotation(
            mount("home/users/01H-eric", "eric"),
            mount("home/users/01H-eve", "../../etc"),
            mount("home/users/01H-eve", "a/b"),
            mount("home/users/01H-eve", ""),
        )
    )
    assert [grant.target for grant in grants] == ["eric"]


def test_a_source_that_walks_out_is_dropped():
    grants = parse_grants(
        annotation(
            mount("home/../../etc", "escape"),
            mount("home/users/01H-eric", "eric"),
        )
    )
    assert [grant.target for grant in grants] == ["eric"]


def test_an_unreadable_annotation_is_an_empty_mount_set():
    # Not "keep what you had": an agent that cannot read what a pod may reach
    # must not keep mounting what it last understood.
    assert parse_grants("{not json") == []
    assert parse_grants("") == []
    assert parse_grants(None) == []


def test_the_same_set_hashes_the_same_in_any_order():
    one = parse_grants(annotation(mount("home/users/01H-eric", "eric"), mount("home/teams/01K", "t")))
    two = parse_grants(annotation(mount("home/teams/01K", "t"), mount("home/users/01H-eric", "eric")))
    assert grants_hash(one) == grants_hash(two)


def test_the_mode_is_part_of_the_hash():
    rw = parse_grants(annotation(mount("home/users/01H-eric", "eric", "rw")))
    ro = parse_grants(annotation(mount("home/users/01H-eric", "eric", "ro")))
    assert grants_hash(rw) != grants_hash(ro)


# ---------------------------------------------------------------------------
# Granting
# ---------------------------------------------------------------------------


def test_a_grant_binds_the_folder_into_the_pods_tree(gateway, mounter):
    report = gateway.reconcile(
        pod(annotation(mount("home/users/01H-eric", "eric"), mount("home/organizations/01J-datalayer", "datalayer")))
    )

    assert report.state == STATE_READY
    assert report.mounted == ["datalayer", "eric"]
    tree = gateway.pod_tree(POD)
    assert mounter.is_mount_point(os.path.join(tree, "eric"))
    assert mounter.is_mount_point(os.path.join(tree, "datalayer"))


def test_the_tree_is_shared_and_bound_once_into_the_pod_volume(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    tree = gateway.pod_tree(POD)
    volume_dir = gateway.pod_volume_dir(POD)
    # Shared, or nothing mounted inside it afterwards reaches the pod.
    assert tree in mounter.shared
    assert mounter.binds[volume_dir] == (tree, False)
    # Recursively, so a re-bind after a restart carries what is already there.
    assert ("bind_dir", tree, volume_dir, True) in mounter.calls


def test_nothing_is_mounted_directly_under_the_pods_empty_dir(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    volume_dir = gateway.pod_volume_dir(POD)
    # Exactly one mount under the emptyDir, ever: the agent's own tree. What
    # kubelet can reach must never be a bind of the user's home folder.
    under_volume = [path for path in mounter.mounts if path.startswith(volume_dir + os.sep)]
    assert under_volume == []


def test_read_only_is_recursive_and_nosuid_is_not_optional(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric", "ro", allow_exec=False))))

    attrs = mounter.attrs[os.path.join(gateway.pod_tree(POD), "eric")]
    assert attrs == {"read_only": True, "noexec": True, "recursive": True}


def test_a_grant_is_attached_with_its_attributes_never_bound_and_then_set(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric", "ro"))))

    target = os.path.join(gateway.pod_tree(POD), "eric")
    # Mount attributes do not propagate to peers: a mount is copied to every
    # peer when it is attached, with the flags it has then. Binding first and
    # setting `ro` after leaves the sandbox's copy writable — measured, in
    # test_csi_kernel.py. So the grant must be one atomic attach.
    assert ("attach", str(gateway.shared_root) + "/home/users/01H-eric", target, True, False) in mounter.calls
    assert not any(call[0] == "bind_dir" and call[2] == target for call in mounter.calls)
    assert not any(call[0] == "set_attrs" and call[1] == target for call in mounter.calls)


def test_a_home_folder_stays_executable(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric", "rw", allow_exec=True))))

    attrs = mounter.attrs[os.path.join(gateway.pod_tree(POD), "eric")]
    assert attrs["noexec"] is False


def test_reconciling_the_same_set_again_mounts_nothing_new(gateway, mounter):
    request = pod(annotation(mount("home/users/01H-eric", "eric")))
    first = gateway.reconcile(request)
    binds = len([call for call in mounter.calls if call[0] == "bind_dir"])

    second = gateway.reconcile(request)

    assert second.applied_hash == first.applied_hash
    assert second.state == STATE_READY
    assert len([call for call in mounter.calls if call[0] == "bind_dir"]) == binds


def test_a_shrinking_annotation_revokes_what_it_dropped(gateway, mounter):
    gateway.reconcile(
        pod(annotation(mount("home/users/01H-eric", "eric"), mount("home/teams/01K-research", "research")))
    )
    report = gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    assert report.mounted == ["eric"]
    assert not mounter.is_mount_point(os.path.join(gateway.pod_tree(POD), "research"))


def test_changing_a_grants_mode_remounts_it(gateway, mounter):
    target = os.path.join(gateway.pod_tree(POD), "eric")
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric", "rw"))))
    assert mounter.attrs[target]["read_only"] is False

    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric", "ro"))))

    assert mounter.attrs[target]["read_only"] is True
    assert ("unmount", target) in mounter.calls


def test_a_source_that_is_a_symlink_out_of_the_claim_is_refused(gateway, shared, mounter):
    os.symlink("/etc", shared / "home" / "users" / "elsewhere")

    report = gateway.reconcile(pod(annotation(mount("home/users/elsewhere", "eric"))))

    assert report.state == STATE_FAILED
    assert report.failed == {"eric": ERROR_INVALID_SOURCE}
    assert not mounter.is_mount_point(os.path.join(gateway.pod_tree(POD), "eric"))


def test_a_source_that_does_not_exist_is_refused(gateway):
    # No kind: nothing is provisioned lazily, so a missing folder is a
    # mistake rather than a folder nobody has written to yet.
    report = gateway.reconcile(pod(annotation(mount("home/users/01H-nobody", "nobody"))))

    assert report.failed == {"nobody": ERROR_INVALID_SOURCE}


def test_a_home_folder_nobody_has_written_to_yet_is_created(gateway, shared, mounter, as_root):
    # A Home Folder exists the first time a sandbox mounts it or something is
    # uploaded into it, whichever comes first. Mounting used to be an init
    # container's mkdir; with the gateway it is the agent's. Without this a
    # brand-new user gets an error for a folder that is simply new.
    report = gateway.reconcile(
        pod(annotation({**mount("home/users/01H-new", "nina"), "kind": "files"}))
    )

    assert report.state == STATE_READY
    assert report.mounted == ["nina"]
    created = shared / "home" / "users" / "01H-new"
    assert created.is_dir()
    assert mounter.binds[os.path.join(gateway.pod_tree(POD), "nina")][0] == str(created)


def test_a_created_folder_the_sandbox_cannot_write_is_refused(gateway, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "chown", refuse)
    monkeypatch.setattr("clouder.csi.node_mount_gateway._writable_by_sandbox_user", lambda path: False)

    report = gateway.reconcile(
        pod(annotation({**mount("home/users/01H-new", "nina"), "kind": "files"}))
    )

    # A folder the sandbox cannot write arrives read-only with no explanation
    # anywhere. Refusing it says which folder and why.
    assert report.failed == {"nina": ERROR_INVALID_SOURCE}


def test_a_backend_that_sets_its_own_ownership_is_accepted(gateway, shared, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "chown", refuse)
    monkeypatch.setattr("clouder.csi.node_mount_gateway._writable_by_sandbox_user", lambda path: True)

    report = gateway.reconcile(
        pod(annotation({**mount("home/users/01H-new", "nina"), "kind": "files"}))
    )

    # An EFS access point or a squashing NFS export decides ownership itself,
    # and the folder is already right.
    assert report.state == STATE_READY


def test_creating_a_home_folder_cannot_be_talked_out_of_the_claim(gateway, shared, as_root):
    os.symlink("/tmp", shared / "home" / "elsewhere")

    report = gateway.reconcile(
        pod(annotation({**mount("home/elsewhere/01H-new", "nina"), "kind": "files"}))
    )

    # The creation walks component by component with O_NOFOLLOW, so a symlink
    # in the path is refused rather than followed to somewhere it may write.
    assert report.failed == {"nina": ERROR_INVALID_SOURCE}


def test_only_a_home_folder_is_created(gateway, shared, as_root):
    for kind in ("shared-folder", ""):
        report = gateway.reconcile(
            pod(annotation({**mount("volumes/absent", "vol"), "kind": kind}))
        )
        # Inventing a directory for a dataset somebody was supposed to put
        # there turns a clear failure into an empty folder debugged later. (A
        # bucket is refused earlier still, as a kind whose filesystem this
        # agent cannot run.)
        assert report.failed == {"vol": ERROR_INVALID_SOURCE}, kind
        assert not (shared / "volumes").exists()


def test_a_kind_nobody_serves_is_refused_with_its_reason(gateway, shared, as_root):
    for kind in ("volume", "dataset", "sftp"):
        report = gateway.reconcile(
            pod(annotation({**mount("volumes/absent", "vol"), "kind": kind}))
        )
        # Reported, not dropped: a grant that vanishes is a missing folder the
        # user is given no reason for, and "we do not mount that" is a reason
        # they can act on.
        assert report.failed == {"vol": ERROR_UNSUPPORTED_KIND}, kind
        assert not (shared / "volumes").exists()


def test_a_kind_whose_filesystem_the_agent_cannot_run_is_refused(gateway, shared):
    report = gateway.reconcile(
        pod(annotation({**mount("acme-bucket/data", "data"), "kind": "cloud-storage"}))
    )

    # Not "invalid source": there is nothing wrong with the source. The agent
    # has no way to run the filesystem a bucket needs, and reporting that is
    # what tells an operator to configure one.
    assert report.failed == {"data": ERROR_PROCESS_UNSUPPORTED}
    assert not (shared / "acme-bucket").exists()


def test_one_bad_grant_does_not_cost_the_good_ones(gateway):
    report = gateway.reconcile(
        pod(annotation(mount("home/users/01H-eric", "eric"), mount("home/users/01H-nobody", "nobody")))
    )

    assert report.state == STATE_DEGRADED
    assert report.mounted == ["eric"]
    assert report.failed == {"nobody": ERROR_INVALID_SOURCE}


def test_a_failed_bind_leaves_no_half_mount(gateway, mounter):
    mounter.fail_bind_dir = "no"
    report = gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    assert report.state == STATE_FAILED
    assert report.failed == {"*": ERROR_MOUNT_FAILED}


def test_more_mounts_than_the_cap_are_refused_as_a_set(tmp_path, shared, kubelet, mounter):
    gateway = NodeMountGateway(
        mounter,
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(kubelet),
        max_mounts_per_pod=2,
    )
    report = gateway.reconcile(
        pod(
            annotation(
                mount("home/users/01H-eric", "a"),
                mount("home/users/01H-eric", "b"),
                mount("home/users/01H-eric", "c"),
            )
        )
    )

    assert report.state == STATE_FAILED
    assert report.failed == {"*": ERROR_TOO_MANY_MOUNTS}
    assert mounter.mounts == set()


def test_a_pod_whose_volume_kubelet_has_not_made_yet_is_not_ready(tmp_path, shared, mounter):
    gateway = NodeMountGateway(
        mounter,
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(tmp_path / "empty-kubelet"),
    )
    report = gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    assert report.state == STATE_FAILED
    assert report.failed == {"*": ERROR_NOT_READY}


# ---------------------------------------------------------------------------
# Releasing
# ---------------------------------------------------------------------------


def test_release_takes_the_grants_down_before_the_pods_copy(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    mounter.calls.clear()

    gateway.release(POD)

    unmounts = [call[1] for call in mounter.calls if call[0] in ("unmount", "unmount_once")]
    volume_dir = gateway.pod_volume_dir(POD)
    tree = gateway.pod_tree(POD)
    # Forced by the kernel, not by taste: a grant propagates into the pod's
    # copy as a child of it, so unmounting the copy first fails with EBUSY.
    # Unmounting the grant inside the tree propagates the unmount outwards,
    # which is what leaves the copy childless and removable.
    assert unmounts[0] == os.path.join(tree, "eric")
    assert unmounts[1] == volume_dir
    assert unmounts[-1] == tree
    # And exactly one mount comes off the pod's volume: the tree is stacked on
    # kubelet's tmpfs, and unstacking the path would take that with it.
    assert ("unmount_once", volume_dir) in mounter.calls
    assert ("unmount", volume_dir) not in mounter.calls


def test_release_removes_the_tree_and_forgets_the_pod(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    gateway.release(POD)

    assert not os.path.isdir(gateway.pod_tree(POD))
    assert gateway.snapshot()["pods"] == {}


def test_releasing_twice_is_a_no_op(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    gateway.release(POD)
    mounter.calls.clear()

    gateway.release(POD)

    assert mounter.calls == []


def test_a_terminating_pod_is_released(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric")), terminating=True))

    assert not os.path.isdir(gateway.pod_tree(POD))


def test_an_empty_annotation_releases_the_pod(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    report = gateway.reconcile(pod(""))

    assert report.state == STATE_READY
    assert not os.path.isdir(gateway.pod_tree(POD))


def test_a_mount_that_will_not_come_down_is_counted_and_kept(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    def refuse(path):
        raise MountError("device or resource busy")

    mounter.unmount = refuse  # type: ignore[method-assign]
    gateway.release(POD)

    # The tree stays: a leak that is cleaned up quietly is a leak nobody
    # notices until kubelet deletes through it.
    assert gateway.counters["leaked"] == 1
    assert os.path.isdir(gateway.pod_tree(POD))


def test_the_tree_of_a_pod_that_is_gone_is_released(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    released = gateway.release_unknown(["another-pod-uid"])

    assert released == [POD]
    assert not os.path.isdir(gateway.pod_tree(POD))


def test_a_live_pod_is_left_alone(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    assert gateway.release_unknown([POD]) == []
    assert os.path.isdir(gateway.pod_tree(POD))


# ---------------------------------------------------------------------------
# What an operator reads
# ---------------------------------------------------------------------------


def test_the_snapshot_names_every_mount_and_whether_it_is_standing(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric", "ro"))))

    snapshot = gateway.snapshot()

    assert snapshot["pods"][POD]["published"] is True
    assert snapshot["pods"][POD]["mounts"]["eric"]["mode"] == "ro"
    assert snapshot["pods"][POD]["mounts"]["eric"]["mounted"] is True
    assert snapshot["counters"]["granted"] == 1


def test_state_is_kept_outside_the_tree_the_pod_can_see(gateway):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    tree = gateway.pod_tree(POD)
    assert sorted(os.listdir(tree)) == ["eric"]
    assert os.path.isfile(os.path.join(gateway.state_dir(), f"{POD}.json"))


def test_applied_state_survives_a_restart(tmp_path, shared, kubelet, mounter):
    def build():
        return NodeMountGateway(
            mounter,
            shared_root=str(shared),
            gateway_root=str(tmp_path / "gateway"),
            kubelet_dir=str(kubelet),
        )

    build().reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    binds = len([call for call in mounter.calls if call[0] == "bind_dir"])

    # A new agent, the same node: it reads what it applied from the state
    # directory and the mount table, and does not mount anything twice.
    report = build().reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    assert report.state == STATE_READY
    assert len([call for call in mounter.calls if call[0] == "bind_dir"]) == binds


def test_the_report_is_json_naming_the_hash_and_what_was_mounted(gateway):
    report = gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    decoded = json.loads(report.encode())
    assert decoded == {
        "hash": report.applied_hash,
        "state": STATE_READY,
        "mounted": ["eric"],
        "failed": {},
    }


# ---------------------------------------------------------------------------
# The mirror
# ---------------------------------------------------------------------------


def test_the_wire_format_is_imported_rather_than_copied():
    """There is one implementation of the format, and this uses it.

    It used to be copied here and held to the original by a test that compared
    the two. That worked, and was the wrong shape: a comparison can only fail
    *after* somebody has changed one side, and the failure it reports is a
    disagreement rather than the reason for it. Now the Operator writes the
    annotation with `datalayer_common`, this reads it with `clouder`, and both
    resolve to the same module in `datalayer_core`.
    """
    from datalayer_core import contents_node_mount_gateway as shared

    from ..csi import node_mount_gateway as agent

    # The same objects, not equal copies of them.
    assert agent.NODE_MOUNT_GATEWAY_MOUNTS_ANNOTATION is shared.NODE_MOUNT_GATEWAY_MOUNTS_ANNOTATION
    assert agent.NodeMountGatewayError is shared.NodeMountGatewayError
    assert agent.clean_target is shared.clean_target
    assert agent.clean_source is shared.clean_source

    common = pytest.importorskip(
        "datalayer_common.node_mount_gateway",
        reason="datalayer_common is not installed beside clouder",
    )
    assert common.NodeMountGatewayError is shared.NodeMountGatewayError


def test_a_grant_the_operator_wrote_is_read_with_the_same_hash():
    """The end-to-end statement the mirror test was really trying to make."""
    common = pytest.importorskip(
        "datalayer_common.node_mount_gateway",
        reason="datalayer_common is not installed beside clouder",
    )

    written = common.encode_grants(
        [
            common.grant(source="home/users/01H-eric", target="eric"),
            common.grant(source="home/organizations/01J", target="datalayer", mode="ro"),
        ]
    )
    read = parse_grants(written)

    assert [(item.target, item.source, item.mode) for item in read] == [
        ("datalayer", "home/organizations/01J", "ro"),
        ("eric", "home/users/01H-eric", "rw"),
    ]
    # The hash is the whole of how the Operator knows the agent answered for
    # the set it asked about, and it now comes from one implementation.
    assert grants_hash(read) == json.loads(written)["hash"]
    assert common.is_ready_for(
        common.encode_ready(applied_hash=grants_hash(read), state=STATE_READY, mounted=["eric"]),
        common.decode_grants(written),
    )


def test_a_grant_dataclass_reports_read_only():
    assert Grant(source="a", target="b", mode="ro").read_only is True
    assert Grant(source="a", target="b", mode="rw").read_only is False


# ---------------------------------------------------------------------------
# The credential a mount may need
# ---------------------------------------------------------------------------


class _Secrets:
    """A credential source that answers from a dict, and records the asking."""

    def __init__(self, data=None, refuse=None):
        self.data = data or {}
        self.refuse = refuse
        self.asked: list[tuple[str, str, str]] = []

    def read_secret(self, namespace, name, pod_uid):
        self.asked.append((namespace, name, pod_uid))
        if self.refuse:
            raise NodeMountGatewayError(ERROR_SECRET_REFUSED, self.refuse)
        return self.data


def _with_credentials(tmp_path, shared, kubelet, mounter, credentials):
    return NodeMountGateway(
        mounter,
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(kubelet),
        credentials=credentials,
    )


def test_a_grant_without_a_secret_asks_for_none(tmp_path, shared, kubelet, mounter):
    secrets = _Secrets()
    gateway = _with_credentials(tmp_path, shared, kubelet, mounter, secrets)

    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))

    # A Home Folder is a sub-path of a claim the agent already holds. Asking
    # for a credential it does not need would be a Secret read for nothing.
    assert secrets.asked == []


def test_a_grant_naming_a_secret_reads_it_against_the_pod(tmp_path, shared, kubelet, mounter):
    secrets = _Secrets({"key": b"scoped-session-token"})
    gateway = _with_credentials(tmp_path, shared, kubelet, mounter, secrets)

    gateway.reconcile(
        pod(annotation({**mount("home/users/01H-eric", "eric"), "secret": "mount-01h"}))
    )

    assert secrets.asked == [("datalayer-runtimes", "mount-01h", POD)]


def test_a_refused_secret_refuses_the_mount_and_makes_none(tmp_path, shared, kubelet, mounter):
    secrets = _Secrets(refuse="not owned by the pod")
    gateway = _with_credentials(tmp_path, shared, kubelet, mounter, secrets)

    report = gateway.reconcile(
        pod(annotation({**mount("home/users/01H-eric", "eric"), "secret": "someone-elses"}))
    )

    # Read before mounting, so a refusal is never a mount that briefly existed.
    assert report.failed == {"eric": ERROR_SECRET_REFUSED}
    assert not mounter.is_mount_point(os.path.join(gateway.pod_tree(POD), "eric"))


def test_an_agent_without_credentials_cannot_be_talked_into_reading_one(gateway):
    # The default. A deployment that has not turned credentials on must not
    # acquire the ability because a grant asked for it.
    report = gateway.reconcile(
        pod(annotation({**mount("home/users/01H-eric", "eric"), "secret": "mount-01h"}))
    )

    assert report.failed == {"eric": ERROR_SECRET_REFUSED}


def test_a_secret_name_that_is_not_a_name_is_dropped():
    grants = parse_grants(
        annotation(
            {**mount("home/users/01H-eric", "eric"), "secret": "../../etc/shadow"},
            {**mount("home/users/01H-nina", "nina"), "secret": "mount-01h"},
        )
    )
    assert [item.target for item in grants] == ["nina"]


def test_the_secret_is_part_of_the_name_of_a_mount_set():
    one = parse_grants(annotation({**mount("s/x", "a"), "secret": "mount-1"}))
    two = parse_grants(annotation({**mount("s/x", "a"), "secret": "mount-2"}))
    # Pointing a mount at a different credential is a different mount, and the
    # agent must be asked to make it again rather than reporting the previous
    # one as still applied.
    assert grants_hash(one) != grants_hash(two)


def test_a_credential_never_reaches_the_report_or_the_state(tmp_path, shared, kubelet, mounter):
    secrets = _Secrets({"key": b"scoped-session-token"})
    gateway = _with_credentials(tmp_path, shared, kubelet, mounter, secrets)

    report = gateway.reconcile(
        pod(annotation({**mount("home/users/01H-eric", "eric"), "secret": "mount-01h"}))
    )

    # The name travels; the value does not. A credential in an annotation is a
    # credential anyone who can read a pod can read.
    written = report.encode() + json.dumps(gateway.snapshot())
    assert "scoped-session-token" not in written
    assert "mount-01h" in json.dumps(gateway.snapshot())


# ---------------------------------------------------------------------------
# A mount that is a process
# ---------------------------------------------------------------------------


class _Processes:
    """A filesystem runner that mounts by recording, and can be killed."""

    def __init__(self, mounter, fail=None):
        self.mounter = mounter
        self.fail = fail
        self.started: list[dict] = []
        self.stopped: list[tuple[int, str]] = []
        self.dead: set[int] = set()
        self.mounts_nothing = False
        self._next_pid = 1000

    def start(self, *, kind, source, target, read_only, credential):
        if self.fail:
            raise NodeMountGatewayError(ERROR_PROCESS_UNSUPPORTED, self.fail)
        self._next_pid += 1
        self.started.append(
            {"kind": kind, "source": source, "target": target, "read_only": read_only,
             "credential": credential, "pid": self._next_pid}
        )
        if not self.mounts_nothing:
            self.mounter.mounts.add(target)
        return self._next_pid

    def alive(self, pid):
        return pid not in self.dead and pid != 0

    def stop(self, pid, target):
        self.stopped.append((pid, target))
        self.mounter.mounts.discard(target)


def _bucket(target="data", **fields):
    return {**mount("acme-bucket/prefix", target), "kind": "cloud-storage", **fields}


@pytest.fixture
def running(tmp_path, shared, kubelet, mounter):
    processes = _Processes(mounter)
    gateway = NodeMountGateway(
        mounter,
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(kubelet),
        credentials=_Secrets({"key": b"session"}),
        processes=processes,
    )
    return gateway, processes


def test_a_process_mount_is_started_rather_than_bound(running, mounter):
    gateway, processes = running

    report = gateway.reconcile(pod(annotation(_bucket(secret="mount-01h"))))

    assert report.state == STATE_READY
    started = processes.started[0]
    assert started["kind"] == "cloud-storage"
    # The source is passed through, not resolved beneath the shared claim: a
    # bucket is not a directory the agent reaches.
    assert started["source"] == "acme-bucket/prefix"
    assert started["credential"] == {"key": b"session"}
    # And nothing was bound: a process mounts at the target itself.
    assert not any(call[0] in ("attach", "bind_dir") and call[2] == started["target"] for call in mounter.calls)


def test_a_process_that_mounts_nothing_is_a_failure_not_a_mount(running):
    gateway, processes = running
    processes.mounts_nothing = True

    report = gateway.reconcile(pod(annotation(_bucket())))

    # A directory reported as a mount is how somebody reads an empty bucket
    # and believes it.
    assert report.failed == {"data": ERROR_MOUNT_FAILED}
    assert processes.stopped, "the process that mounted nothing should be stopped"


def test_a_filesystem_that_died_is_degraded_not_ready(running):
    gateway, processes = running
    gateway.reconcile(pod(annotation(_bucket())))
    processes.dead.add(processes.started[0]["pid"])

    report = gateway.reconcile(pod(annotation(_bucket())))

    # The mount stays and returns errors, which is what the sandbox should
    # see; saying `ready` is what would make somebody trust the bytes.
    assert report.state == STATE_FAILED
    assert report.failed == {"data": ERROR_MOUNT_DEAD}


def test_the_pid_is_not_part_of_the_grant(running):
    gateway, processes = running
    gateway.reconcile(pod(annotation(_bucket())))

    gateway.reconcile(pod(annotation(_bucket())))

    # A restarted filesystem is the same grant. If the pid were part of the
    # set's identity, every restart would look like a new mount set.
    assert len(processes.started) == 1


def test_revoking_stops_the_filesystem_before_taking_its_mount_away(running, mounter):
    gateway, processes = running
    gateway.reconcile(pod(annotation(_bucket())))
    pid = processes.started[0]["pid"]
    mounter.calls.clear()

    gateway.reconcile(pod(""))

    # Stopped first: a process left serving a path nothing can reach is a
    # process nothing will ever stop.
    assert processes.stopped[0][0] == pid
    assert processes.stopped[0][1] == gateway.target_path(POD, "data")


def test_releasing_a_pod_stops_its_filesystems(running):
    gateway, processes = running
    gateway.reconcile(pod(annotation(_bucket())))

    gateway.release(POD)

    assert [pid for pid, _ in processes.stopped] == [processes.started[0]["pid"]]


def test_a_bucket_whose_credential_is_refused_starts_nothing(tmp_path, shared, kubelet, mounter):
    processes = _Processes(mounter)
    gateway = NodeMountGateway(
        mounter,
        shared_root=str(shared),
        gateway_root=str(tmp_path / "gateway"),
        kubelet_dir=str(kubelet),
        credentials=_Secrets(refuse="not owned by the pod"),
        processes=processes,
    )

    report = gateway.reconcile(pod(annotation(_bucket(secret="someone-elses"))))

    assert report.failed == {"data": ERROR_SECRET_REFUSED}
    assert processes.started == []


# --- The kinds that are not a bind and not a process -------------------------
#
# The gateway replaces a creation-time path that serves three unrelated things:
# a Git checkout in an init container, an NFS sub-path on the shared claim, and
# an S3 bucket through Datashim. A gateway that only did buckets would replace
# one of the three, so each has a delivery here.


def test_an_nfs_export_is_mounted_by_the_kernel_with_the_flags_the_grant_asked_for(
    gateway, mounter
):
    report = gateway.reconcile(
        pod(
            annotation(
                {
                    **mount("nfs.datalayer.svc:/exports/ai-models", "models", mode="ro"),
                    "kind": "nfs",
                }
            )
        )
    )

    assert report.state == STATE_READY
    call = [entry for entry in mounter.calls if entry[0] == "mount_filesystem"]
    assert call, mounter.calls
    _, fs_type, source, target, options = call[0]
    assert fs_type == "nfs"
    assert source == "nfs.datalayer.svc:/exports/ai-models"
    assert target.endswith("/models")
    # `nosuid` and `nodev` on a tenant's data, always: an export is content to
    # read, not a place to bring device nodes into a sandbox from.
    assert "nosuid" in options and "nodev" in options and "ro" in options


def test_an_nfs_grant_that_is_not_an_export_never_reaches_the_node(gateway, mounter):
    for source in ("/exports/models", "nfs.datalayer.svc:exports", "home/users/01H-eric"):
        report = gateway.reconcile(
            pod(annotation({**mount(source, "models"), "kind": "nfs"}))
        )
        # Dropped at the annotation, so no `mount -t nfs` is ever run with it.
        assert report.mounted == [], source
        assert not [entry for entry in mounter.calls if entry[0] == "mount_filesystem"]


def test_a_repository_is_checked_out_once_and_bound_read_only(gateway, mounter, tmp_path):
    checkouts = []

    class Checkout:
        def materialize(self, *, kind, source, revision, credential=None):
            checkouts.append((kind, source, revision))
            path = tmp_path / "checkouts" / revision
            path.mkdir(parents=True, exist_ok=True)
            return str(path)

    gateway.materializer = Checkout()
    grant = {
        **mount("https://github.com/scikit-learn/scikit-learn", "sklearn", mode="rw"),
        "kind": "git",
        "revision": "1.5.2",
    }

    report = gateway.reconcile(pod(annotation(grant)))

    assert report.state == STATE_READY
    assert checkouts == [("git", "https://github.com/scikit-learn/scikit-learn", "1.5.2")]
    # `rw` was asked for and read-only is what it gets: the checkout is shared
    # with every other Pod on this node pinned to the same revision, and one
    # sandbox writing into it is every other sandbox's files changing.
    source, read_only = mounter.binds[gateway.target_path(POD, "sklearn")]
    assert read_only is True

    # A second Pod's pass does not clone it again.
    gateway.reconcile(pod(annotation(grant)))
    assert len(checkouts) == 1


def test_an_unpinned_repository_is_refused(gateway):
    report = gateway.reconcile(
        pod(
            annotation(
                {**mount("https://github.com/org/repo", "repo"), "kind": "git"}
            )
        )
    )

    # A checkout with no revision is a mount whose content depends on the day
    # it was made. There is no reading of that which is safe to guess.
    assert report.mounted == []


def test_an_agent_that_cannot_check_out_says_so_rather_than_reporting_a_mount(gateway):
    report = gateway.reconcile(
        pod(
            annotation(
                {
                    **mount("https://github.com/org/repo", "repo"),
                    "kind": "git",
                    "revision": "v1.0",
                }
            )
        )
    )

    assert report.failed == {"repo": ERROR_MOUNT_FAILED}
    assert report.mounted == []
