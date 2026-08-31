"""The mount gateway against a real kernel, in a throwaway mount namespace.

Everything else about the gateway is tested with a `FakeMounter`, which is
right for the logic and blind to the thing the design actually rests on: what
the kernel does with a tmpfs, a bind and mount propagation. These tests make
real mounts inside `unshare -Urm`, so nothing touches the machine, and they
exist because the fake could not have caught what they caught — the gateway
mistook kubelet's own `emptyDir` tmpfs for its published tree, bound nothing,
and reported `ready` to a sandbox with an empty directory.

They skip where unprivileged user namespaces are unavailable. The pieces they
cannot reach are kubelet and RBAC, which is what `clouder local-csi verify`
and the cluster run are for.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parents[2])

pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["unshare", "-Urm", "true"], capture_output=True, check=False
    ).returncode
    != 0,
    reason="unprivileged user and mount namespaces are not available here",
)

PREAMBLE = """
import json, os, subprocess, sys
sys.path.insert(0, {repo!r})
from clouder.csi.gateway import GATEWAY_VOLUME_NAME, MountGateway, PodRef
from clouder.csi.mounter import ProcessMounter

S = {root!r}
POD = "aaaa1111-2222-3333-4444-555566667777"
SHARED = S + "/shared-fs"
KUBELET = S + "/kubelet"
POD_DIR = KUBELET + "/pods/" + POD + "/volumes/kubernetes.io~empty-dir/" + GATEWAY_VOLUME_NAME
os.makedirs(SHARED + "/home/users/01H-eric", exist_ok=True)
open(SHARED + "/home/users/01H-eric/notes.txt", "w").write("the user's file")
os.makedirs(POD_DIR, exist_ok=True)

def kubelet_makes_the_volume():
    # Memory-backed, because that is what the pod template asks for.
    subprocess.run(["mount", "-t", "tmpfs", "-o", "size=1M", "tmpfs", POD_DIR], check=True)

def gateway():
    return MountGateway(
        ProcessMounter(), shared_root=SHARED, gateway_root=S + "/gateway", kubelet_dir=KUBELET
    )

def grant_of(target="eric", mode="rw"):
    return json.dumps({{"mounts": [{{
        "source": "home/users/01H-eric", "target": target, "mode": mode,
        "allow_exec": True, "kind": "files",
    }}]}})

def a_pod(annotation):
    return PodRef(uid=POD, name="jupyter-1", namespace="datalayer-runtimes", annotation=annotation)

def umount_rc(path):
    return subprocess.run(["umount", path], capture_output=True).returncode

def answer(**values):
    print("<<<" + json.dumps(values) + ">>>")
"""


def run_in_namespace(tmp_path: Path, body: str) -> dict:
    """Run ``body`` as root inside a fresh user+mount namespace."""
    root = tmp_path / "ns"
    root.mkdir()
    script = root / "case.py"
    script.write_text(
        PREAMBLE.format(repo=REPO, root=str(root)) + textwrap.dedent(body)
    )
    result = subprocess.run(
        ["unshare", "-Urm", sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    marker = result.stdout.split("<<<")[-1].split(">>>")[0]
    return json.loads(marker)


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


def test_a_granted_folder_reaches_the_pods_copy_of_the_volume(tmp_path):
    """The whole delivery mechanism, end to end, against the real kernel."""
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        report = gateway().reconcile(a_pod(grant_of()))
        answer(
            state=report.state,
            mounted=report.mounted,
            visible=os.path.exists(POD_DIR + "/eric/notes.txt"),
            content=open(POD_DIR + "/eric/notes.txt").read() if os.path.exists(POD_DIR + "/eric/notes.txt") else "",
        )
    """)

    assert out["state"] == "ready"
    assert out["mounted"] == ["eric"]
    assert out["visible"] is True
    assert out["content"] == "the user's file"


def test_kubelets_tmpfs_is_not_mistaken_for_a_published_tree(tmp_path):
    """The regression test for the bug the fake could not see.

    kubelet's memory-backed `emptyDir` is a mount point. Reading that as "my
    tree is already published" is a gateway that binds nothing, answers
    `ready`, and hands the sandbox an empty directory — the exact silent
    failure everything else here is arranged to prevent.
    """
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        gw = gateway()
        before = gw._is_published(POD)
        gw.reconcile(a_pod(grant_of()))
        answer(
            published_before_the_gateway_ran=before,
            published_after=gw._is_published(POD),
            visible=os.path.exists(POD_DIR + "/eric/notes.txt"),
        )
    """)

    assert out["published_before_the_gateway_ran"] is False
    assert out["published_after"] is True
    assert out["visible"] is True


def test_a_grant_reaches_a_container_view_established_before_it(tmp_path):
    """The ordering hot attach depends on, and the one that could break it.

    kubelet mounts the gateway volume into the sandbox when the **pod is
    created**, while it is empty. The agent publishes its tree and every grant
    afterwards. If a mount stacked onto the volume did not reach a container
    view that already existed, hot attach would deliver nothing and the whole
    milestone would be a slower way to do what a cold pod already did.
    """
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        # kubelet's side: shared on the host, rslave into the container.
        subprocess.run(["mount", "--make-rshared", POD_DIR], check=True)
        container = S + "/container"
        os.makedirs(container, exist_ok=True)
        subprocess.run(["mount", "--rbind", POD_DIR, container], check=True)
        subprocess.run(["mount", "--make-rslave", container], check=True)

        # Only now does the agent publish and grant.
        gw = gateway()
        gw.reconcile(a_pod(grant_of()))
        seen = os.path.exists(container + "/eric/notes.txt")

        # And a grant added later still arrives, which is the whole point.
        gw.reconcile(a_pod(json.dumps({"mounts": [
            {"source": "home/users/01H-eric", "target": "eric", "mode": "rw", "kind": "files"},
            {"source": "home/users/01H-eric", "target": "second", "mode": "rw", "kind": "files"},
        ]})))
        answer(
            first_grant_visible=seen,
            later_grant_visible=os.path.exists(container + "/second/notes.txt"),
        )
    """)

    assert out["first_grant_visible"] is True
    assert out["later_grant_visible"] is True


def test_a_restarted_agent_adopts_what_it_already_mounted(tmp_path):
    """A new agent on the same node must converge, not remount or double-mount."""
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        gateway().reconcile(a_pod(grant_of()))

        # A second agent, the same node, the same annotation.
        fresh = gateway()
        published = fresh._is_published(POD)
        report = fresh.reconcile(a_pod(grant_of()))
        mounts = [l.split()[4] for l in open("/proc/self/mountinfo") if l.split()[4].endswith("/eric")]
        answer(
            published_on_arrival=published,
            state=report.state,
            mounted=report.mounted,
            eric_mounted_once=len(mounts),
            still_visible=os.path.exists(POD_DIR + "/eric/notes.txt"),
        )
    """)

    assert out["published_on_arrival"] is True, "a restarted agent should recognise its own tree"
    assert out["state"] == "ready"
    assert out["still_visible"] is True
    # Two mounts on one target is a folder that cannot be revoked in one go.
    assert out["eric_mounted_once"] == 2, "the tree's copy and the pod's copy, and no more"


# ---------------------------------------------------------------------------
# The safety mechanism
# ---------------------------------------------------------------------------


def test_the_volume_cannot_be_unmounted_while_a_grant_stands(tmp_path):
    """Why the gateway volume is memory-backed.

    kubelet must unmount a tmpfs before it removes the directory, and a mount
    with children refuses to unmount. So a leaked bind leaves a Pod stuck in
    `Terminating` — visible and recoverable — instead of letting a recursive
    delete walk into a user's home folder on the shared claim.
    """
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        gw = gateway()
        gw.reconcile(a_pod(grant_of()))
        while_mounted = umount_rc(POD_DIR)
        gw.release(POD)
        answer(
            while_mounted=while_mounted,
            after_release=umount_rc(POD_DIR),
            counters=gw.counters,
        )
    """)

    assert out["while_mounted"] != 0, "the tmpfs came away with a grant standing in it"
    # And once the gateway has let go, kubelet's own teardown works.
    assert out["after_release"] == 0
    assert out["counters"]["leaked"] == 0
    assert out["counters"]["released"] == 1


def test_release_leaves_kubelets_own_volume_where_it_was(tmp_path):
    """Releasing takes one mount off the pod's volume, not everything on it."""
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        gw = gateway()
        gw.reconcile(a_pod(grant_of()))
        gw.release(POD)
        # The gateway's bind is gone; kubelet's tmpfs is still mounted, which
        # is kubelet's to remove and not the agent's.
        still_a_mount = ProcessMounter().is_mount_point(POD_DIR)
        answer(
            gone_from_pod=not os.path.exists(POD_DIR + "/eric/notes.txt"),
            kubelet_volume_survived=still_a_mount,
            published=gw._is_published(POD),
        )
    """)

    assert out["gone_from_pod"] is True
    assert out["kubelet_volume_survived"] is True
    assert out["published"] is False


# ---------------------------------------------------------------------------
# The attributes a grant is mounted with
# ---------------------------------------------------------------------------


def test_read_only_is_recursive_where_a_remount_is_not(tmp_path):
    """Why `mount_setattr` is in this codebase at all.

    `mount -o remount,bind,ro` sets the flag on one mount and says nothing
    about what is nested under it, so a "read-only" folder with a nested mount
    beneath it is writable where it matters.
    """
    out = run_in_namespace(tmp_path, """
        from clouder.csi.linux import mount_setattr
        os.makedirs(S + "/src/nested_src", exist_ok=True)
        os.makedirs(S + "/parent", exist_ok=True)
        subprocess.run(["mount", "--bind", S + "/src", S + "/parent"], check=True)
        os.makedirs(S + "/parent/nested", exist_ok=True)
        subprocess.run(["mount", "--bind", S + "/src/nested_src", S + "/parent/nested"], check=True)

        def writable(path):
            try:
                open(path + "/probe", "w").write("x")
                os.remove(path + "/probe")
                return True
            except OSError:
                return False

        subprocess.run(["mount", "-o", "remount,bind,ro", S + "/parent"], check=True)
        after_remount = writable(S + "/parent/nested")
        mount_setattr(S + "/parent", read_only=True, recursive=True)
        answer(
            nested_writable_after_remount=after_remount,
            nested_writable_after_setattr=writable(S + "/parent/nested"),
        )
    """)

    assert out["nested_writable_after_remount"] is True, "a remount was recursive after all"
    assert out["nested_writable_after_setattr"] is False


def test_a_grant_is_mounted_nosuid_and_nodev(tmp_path):
    """A file on a shared filesystem must not hand a sandbox a device or an identity."""
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        gw = gateway()
        gw.reconcile(a_pod(grant_of()))
        options = ""
        for line in open("/proc/self/mountinfo"):
            fields = line.split()
            if fields[4] == gw.target_path(POD, "eric"):
                options = fields[5]
        answer(options=options)
    """)

    assert "nosuid" in out["options"]
    assert "nodev" in out["options"]


def test_a_read_only_grant_is_mounted_read_only(tmp_path):
    out = run_in_namespace(tmp_path, """
        kubelet_makes_the_volume()
        gw = gateway()
        gw.reconcile(a_pod(grant_of(mode="ro")))
        try:
            open(POD_DIR + "/eric/probe", "w").write("x")
            wrote = True
        except OSError:
            wrote = False
        answer(wrote=wrote, exists=os.path.exists(POD_DIR + "/eric/notes.txt"))
    """)

    assert out["exists"] is True, "a read-only grant must still be readable"
    assert out["wrote"] is False


# ---------------------------------------------------------------------------
# A mount that is a process, against a real kernel
# ---------------------------------------------------------------------------


def test_a_process_mount_propagates_and_stops_like_any_other(tmp_path):
    """The lifecycle with a real process making a real mount.

    A bucket and a local bridge are each a userspace filesystem, which needs
    `/dev/fuse` and is not available here. What *is* testable is everything
    around it — that a mount a process made propagates to the pod's copy, that
    a dead process is reported rather than believed, and that stopping takes
    the mount with it — so the stand-in makes a real bind and sleeps.
    """
    out = run_in_namespace(tmp_path, """
        import signal, time

        class Runner:
            def __init__(self):
                self.pids = {}

            def start(self, *, kind, source, target, read_only, credential):
                # A real mount, made by something that keeps running, which is
                # what a bucket or bridge filesystem is from here.
                subprocess.run(["mount", "--bind", SHARED + "/home/users/01H-eric", target], check=True)
                proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
                self.pids[proc.pid] = proc
                return proc.pid

            def alive(self, pid):
                proc = self.pids.get(pid)
                return proc is not None and proc.poll() is None

            def stop(self, pid, target):
                proc = self.pids.pop(pid, None)
                if proc is not None:
                    proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=5)
                subprocess.run(["umount", target], check=False)

        kubelet_makes_the_volume()
        runner = Runner()
        gw = MountGateway(
            ProcessMounter(), shared_root=SHARED, gateway_root=S + "/gateway",
            kubelet_dir=KUBELET, processes=runner,
        )
        bucket = json.dumps({"mounts": [{
            "source": "acme-bucket/prefix", "target": "data", "mode": "rw", "kind": "cloud-storage",
        }]})
        report = gw.reconcile(a_pod(bucket))
        visible = os.path.exists(POD_DIR + "/data/notes.txt")

        pid = list(runner.pids)[0]
        runner.pids[pid].send_signal(signal.SIGKILL)
        runner.pids[pid].wait(timeout=5)
        after_death = gw.reconcile(a_pod(bucket))

        gw.release(POD)
        answer(
            state=report.state,
            visible_in_pod=visible,
            state_after_death=after_death.state,
            failed_after_death=after_death.failed,
            gone=not os.path.exists(POD_DIR + "/data/notes.txt"),
            volume_unmounts=umount_rc(POD_DIR),
        )
    """)

    assert out["state"] == "ready"
    assert out["visible_in_pod"] is True, "a mount a process made must reach the pod's copy"
    # A dead filesystem leaves a mount that answers with errors. Reporting it
    # ready is what would make somebody trust what they read through it.
    assert out["state_after_death"] == "failed"
    assert out["failed_after_death"] == {"data": "GATEWAY_MOUNT_DEAD"}
    assert out["gone"] is True
    assert out["volume_unmounts"] == 0
