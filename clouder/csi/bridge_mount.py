"""The bridge filesystem process, one per bridge on a node.

Started by :class:`clouder.csi.mounter.ProcessMounter` as::

    DATALAYER_BRIDGE_MOUNT_TOKEN=... python -m clouder.csi.bridge_mount \\
        --relay-url wss://relay/bridges/<bridge-uid> --mount-path /csi/mounts/<bridge-uid>/mnt --mode ro

It hands over to ``code_sandboxes.bridge_mount.run_bridge_mount``, the FUSE
filesystem over the Contents local bridge. That function reports by return
value and by one JSON line on stdout (``{"status": "connected", ...}`` or
``{"status": "failed", "error": ..., "detail": ...}``), which lands in the
bridge's ``mounter.log`` where the driver reads the last lines as the exit
reason. Its return value is this process's exit status, with the same
meaning on both sides:

- 0: the filesystem was unmounted normally;
- 1: the filesystem raised (this wrapper's own catch-all);
- 2: bad usage (mode, missing token);
- 3: FUSE is unavailable, the mount path cannot be made, or
  ``code_sandboxes.bridge_mount`` is not installed in this image;
- 4: the relay refused the mount token (revoked, expired, wrong bridge) or
  could not be reached;
- 5: the session was ended by the relay after the mount was up (revocation,
  heartbeat expiry): the filesystem unmounted itself before exiting.

The only network destination this process ever dials is ``--relay-url``.
"""

from __future__ import annotations

import argparse
import os
import sys

from .mounter import MOUNT_TOKEN_ENV, SESSION_KEY_ENV

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_PACKAGE = 3
EXIT_REFUSED = 4
EXIT_ENDED = 5

_REFUSAL_MARKERS = ("refus", "revok", "unauthor", "forbidden", "expired", "permission", "auth", "403", "401")


def _looks_refused(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _REFUSAL_MARKERS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clouder.csi.bridge_mount", description=__doc__.split("\n\n")[0])
    parser.add_argument("--relay-url", required=True, help="wss://.../bridges/<bridge-uid>")
    parser.add_argument("--mount-path", required=True, help="Where to mount the bridge filesystem.")
    parser.add_argument("--mode", required=True, choices=("ro", "rw"))
    parser.add_argument("--bridge-uid", default=None, help="The bridge this mount is one end of.")
    parser.add_argument(
        "--allow-other",
        action="store_true",
        help="Let the sandbox's user reach the mount this agent makes as root.",
    )
    parser.add_argument("--uid", type=int, default=None, help="Report files as owned by this uid.")
    parser.add_argument("--gid", type=int, default=None, help="Report files as owned by this gid.")
    args = parser.parse_args(argv)

    mount_token = os.environ.get(MOUNT_TOKEN_ENV, "")
    if not mount_token:
        print(f"{MOUNT_TOKEN_ENV} is not set", file=sys.stderr, flush=True)
        return EXIT_USAGE

    try:
        from code_sandboxes.bridge_mount import run_bridge_mount
    except ImportError as exc:
        print(f"code_sandboxes.bridge_mount is not installed: {exc}", file=sys.stderr, flush=True)
        return EXIT_NO_PACKAGE

    try:
        status = run_bridge_mount(
            args.relay_url,
            mount_token,
            args.mount_path,
            args.mode,
            bridge_uid=args.bridge_uid,
            # Sealed frames need both ends to hold the key. Empty means the
            # session has none, which is what a client with none also sends.
            session_key=os.environ.get(SESSION_KEY_ENV, "") or None,
            allow_other=args.allow_other,
            uid=args.uid,
            gid=args.gid,
        )
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - the exit status is the report
        if _looks_refused(exc):
            print(f"relay refused the mount token: {exc}", file=sys.stderr, flush=True)
            return EXIT_REFUSED
        print(f"bridge filesystem failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return EXIT_FAILED
    # run_bridge_mount reports by return value, not by raising: pass it on.
    if status is None:
        return EXIT_OK
    try:
        return int(status)
    except (TypeError, ValueError):
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
