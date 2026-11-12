"""Cursor sessionEnd hook -- final transcript copy for the session.

No snapshot here: stop already snapshotted every turn, and an end-of-session
hook is the worst place to spend seconds. Cursor has no scavenger fallback,
so this backstop copy is the difference between a partial transcript and a
complete one when the last turn's stop was cut short.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, copy_transcript


def _on_event(repo, payload):
    copy_transcript(repo, payload)


if __name__ == "__main__":
    raise SystemExit(safe_main("sessionEnd", _on_event))
