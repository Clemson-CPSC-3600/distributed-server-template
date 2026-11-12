"""Cursor afterTabFileEdit hook -- inline-completion provenance, metadata only.

Tab completions are otherwise invisible: no transcript records them (tab
payloads carry no transcript_path, conversation_id, model or user_email).
That also means this hook would be the ONLY place the completion text ever
landed on disk, so the raw payload is deliberately NOT logged
(``log_payload=False``) and only file + extent + hash are recorded.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import safe_main, record_edit


def _on_event(repo, payload):
    record_edit(repo, payload, "tab")


if __name__ == "__main__":
    raise SystemExit(safe_main("afterTabFileEdit", _on_event, log_payload=False))
