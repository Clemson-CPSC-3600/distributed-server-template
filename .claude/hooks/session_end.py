"""Claude Code SessionEnd hook -- final transcript copy for the session."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, copy_transcript


def _on_event(repo, payload):
    # Backstop for a session that ends without a Stop (/clear, /quit, a
    # closed terminal). No snapshot: Stop already took one every turn, and a
    # full snapshot at shutdown risks the hook timeout.
    copy_transcript(repo, payload)


if __name__ == "__main__":
    raise SystemExit(safe_main("SessionEnd", _on_event))
