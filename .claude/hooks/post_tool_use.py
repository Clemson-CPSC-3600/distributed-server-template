"""Claude Code PostToolUse hook -- records per-edit provenance.

The provenance row itself holds no edit text, but safe_main also logs the
full hook payload (tool_input included) to raw/hooks/; redaction then
withholds that text when the edited file is outside the project.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, record_edit


def _on_event(repo, payload):
    record_edit(repo, payload)


if __name__ == "__main__":
    raise SystemExit(safe_main("PostToolUse", _on_event))
