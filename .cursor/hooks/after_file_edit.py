"""Cursor afterFileEdit hook -- per-edit provenance for agent-driven edits."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, record_edit


def _on_event(repo, payload):
    record_edit(repo, payload, "agent")


if __name__ == "__main__":
    raise SystemExit(safe_main("afterFileEdit", _on_event))
