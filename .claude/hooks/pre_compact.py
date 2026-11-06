"""Claude Code PreCompact hook -- the transcript is about to be summarized away."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, copy_transcript


def _on_event(repo, payload):
    # Compaction rewrites the live transcript. Copy BEFORE it happens or the
    # detail in the compacted-away turns is gone from every later copy.
    copy_transcript(repo, payload)


if __name__ == "__main__":
    raise SystemExit(safe_main("PreCompact", _on_event))
