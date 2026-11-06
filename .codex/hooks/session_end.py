"""Codex SessionEnd hook -- final transcript copy for the session."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, copy_transcript


def _on_event(repo, payload):
    # No snapshot here. SessionEnd hooks default to a ONE-SECOND timeout
    # (verified empirically, cyq.1): hooks.json raises it explicitly so the
    # copy survives, but a full snapshot is still too slow to risk. Stop
    # already snapshotted every turn.
    copy_transcript(repo, payload)


if __name__ == "__main__":
    raise SystemExit(safe_main("SessionEnd", _on_event))
