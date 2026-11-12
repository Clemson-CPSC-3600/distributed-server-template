"""Cursor stop hook -- copies the transcript in-repo and snapshots the tree.

Also the ONLY reliable copy point for cloud agents, which never fire
sessionStart/sessionEnd.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, copy_transcript, request_snapshot


def _on_event(repo, payload):
    # Order matters: get the evidence into the tree BEFORE asking for the
    # snapshot, so the snapshot contains this turn's transcript.
    copy_transcript(repo, payload)
    request_snapshot(repo, "cursor_stop")


if __name__ == "__main__":
    raise SystemExit(safe_main("stop", _on_event))
