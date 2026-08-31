"""The mount gateway: what it binds, what it refuses, and what it leaves behind.

The FakeMounter keeps the mount table in memory; the directories are real, so
the path resolution these tests exercise is the resolution that runs on a node.
"""

from __future__ import annotations

import json
import os

import pytest

from ..csi.gateway import (
    ERROR_INVALID_SOURCE,
    ERROR_MOUNT_FAILED,
    ERROR_NOT_READY,
    ERROR_TOO_MANY_MOUNTS,
    GATEWAY_VOLUME_NAME,
    STATE_DEGRADED,
    STATE_FAILED,
    STATE_READY,
    Grant,
    MountGateway,
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
    (root / "pods" / POD / "volumes" / "kubernetes.io~empty-dir" / GATEWAY_VOLUME_NAME).mkdir(parents=True)
    return root


@pytest.fixture
def mounter() -> FakeMounter:
    return FakeMounter()


@pytest.fixture
def gateway(tmp_path, shared, kubelet, mounter) -> MountGateway:
    return MountGateway(
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
    report = gateway.reconcile(pod(annotation(mount("home/users/01H-nobody", "nobody"))))

    assert report.failed == {"nobody": ERROR_INVALID_SOURCE}


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
    gateway = MountGateway(
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
    gateway = MountGateway(
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


def test_release_takes_the_pods_copy_down_before_the_grants(gateway, mounter):
    gateway.reconcile(pod(annotation(mount("home/users/01H-eric", "eric"))))
    mounter.calls.clear()

    gateway.release(POD)

    unmounts = [call[1] for call in mounter.calls if call[0] == "unmount"]
    volume_dir = gateway.pod_volume_dir(POD)
    tree = gateway.pod_tree(POD)
    # kubelet cannot unmount the gateway tmpfs while the pod's copy stands, so
    # that one comes down first; the tree itself comes down last.
    assert unmounts[0] == volume_dir
    assert unmounts[-1] == tree
    assert os.path.join(tree, "eric") in unmounts


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
        return MountGateway(
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


def test_the_wire_format_matches_datalayer_common():
    """`clouder` cannot import `datalayer_common`, so it copies the format.

    This is the test that keeps the copy honest: the Operator writes the
    annotation with one module and the agent reads it with another, and a
    difference between them is a mount that silently never happens.
    """
    common = pytest.importorskip(
        "datalayer_common.mount_gateway",
        reason="datalayer_common is not installed beside clouder",
    )
    from ..csi import gateway as mirror

    assert mirror.GATEWAY_MOUNTS_ANNOTATION == common.GATEWAY_MOUNTS_ANNOTATION
    assert mirror.GATEWAY_READY_ANNOTATION == common.GATEWAY_READY_ANNOTATION
    assert mirror.GATEWAY_VOLUME_NAME == common.GATEWAY_VOLUME_NAME
    assert mirror.STATE_READY == common.STATE_READY
    assert mirror.STATE_DEGRADED == common.STATE_DEGRADED
    assert mirror.ERROR_NOT_READY == common.ERROR_NOT_READY

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
    # And the two sides agree on the name of the set, which is the whole of
    # how the Operator knows the agent answered for what it asked.
    assert grants_hash(read) == json.loads(written)["hash"]
    assert common.is_ready_for(
        common.encode_ready(applied_hash=grants_hash(read), state=STATE_READY, mounted=["eric", "datalayer"]),
        common.decode_grants(written),
    )


def test_a_grant_dataclass_reports_read_only():
    assert Grant(source="a", target="b", mode="ro").read_only is True
    assert Grant(source="a", target="b", mode="rw").read_only is False
