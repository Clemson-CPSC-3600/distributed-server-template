"""Codex PostToolUse hook -- records the completed tool call and per-edit provenance."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, record_edit


def _on_event(repo, payload):
    record_edit(repo, payload)


if __name__ == "__main__":
    raise SystemExit(safe_main("PostToolUse", _on_event))
