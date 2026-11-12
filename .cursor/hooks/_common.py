"""Shared helpers for the repo-local Cursor hooks.

Third sibling of ``.codex/hooks/_common.py`` and
``.claude-student/hooks/_common.py``; same shape, same rules, different
adapter. Read the Codex file's docstring for the full statement of the four
design rules -- stdlib only / never block a turn / never assume cwd is the
repo root / heartbeat content is stable -- which hold here unchanged.

Cursor-specific divergences, all from the Aug 2026 docs review and the
corrections in ``docs/superpowers/plans/2026-08-11-plan-review-amendments.md``:

- **No scavenger fallback exists.** The SQLite ``state.vscdb`` route was
  excluded (1b7fb42), so these hooks are the ONLY channel for Cursor.
  Anything they drop is gone; hence the transcript copy hangs off both
  ``stop`` and ``sessionEnd``.
- **Cloud agents** pick up project hooks but never fire
  ``sessionStart``/``sessionEnd`` and never fire tab hooks. ``stop`` is the
  only reliable copy point there.
- **Edit payload shape.** ``afterFileEdit`` (and ``afterTabFileEdit``)
  carry ``file_path`` plus ``edits: [{old_string, new_string}, ...]``, so
  ``record_edit`` can compute both extents and a content hash without
  copying the text into its own record. The agent path still STORES that
  text: ``append_hook_event`` logs the whole payload into ``raw/``, and the
  capture layer's redaction pass withholds it when ``file_path`` is outside
  the project.
- **Tab payloads are impoverished AND privileged.** They carry no
  ``conversation_id`` / ``transcript_path`` / ``model`` / ``user_email``,
  which means no transcript will ever hold the completion text either.
  Logging the raw tab payload would make this hook the sole place that text
  lands on disk, so tab events are recorded as file + extent + hash only
  (``log_payload=False``) -- deliberately stricter than the agent path.
- **Session key** is ``conversation_id``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

AI_TRACES_DIR = ".ai-traces"
ADAPTER_NAME = "cursor"
SCHEMA_VERSION = "1"


def project_repo_root() -> Path:
    """The repo root: <root>/.cursor/hooks/_common.py -> <root>."""
    return Path(__file__).resolve().parents[2]


def adapter_dir(repo: Path) -> Path:
    return repo / AI_TRACES_DIR / ADAPTER_NAME


def read_payload() -> Dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def session_id_of(payload: Dict[str, Any]) -> str:
    for key in ("conversation_id", "session_id", "generation_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_component(value.strip())
    return "unknown"  # tab-hook payloads carry no conversation id at all


def _safe_component(value: str) -> str:
    """Filesystem-safe filename component (conversation ids are UUID-ish)."""
    keep = [c if (c.isalnum() or c in "-_.") else "-" for c in value]
    out = "".join(keep).strip("-.") or "unknown"
    return out[:80]


def append_hook_event(repo: Path, event_name: str, payload: Dict[str, Any]) -> None:
    """Append the raw hook event, verbatim, to this session's JSONL.

    Deliberately unfiltered by cwd: Cursor ran this hook only because it
    loaded THIS repo's project-level hooks.json, so the session belongs to
    this repo even when the hook's cwd is a subdirectory.

    NOT called for tab events -- see the module docstring.
    """
    out = adapter_dir(repo) / "raw" / "hooks" / f"hooks-{session_id_of(payload)}.jsonl"
    event = {
        "adapter_name": ADAPTER_NAME,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hook_event_name": event_name,
        "payload": payload,
    }
    _append_jsonl(out, event)


def write_heartbeat(repo: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Prove a hook actually ran on this machine.

    With no scavenger fallback for Cursor, the conjunction "evidence present
    AND heartbeat absent" is the only way an instructor can tell "hooks were
    never authorized/never fired" from "this student never opened Cursor".
    Every hook writes it, so no single event owns the signal.

    Content is stable (session-id keyed, no timestamps) because snapshots
    dedupe by tree SHA.
    """
    path = adapter_dir(repo) / "heartbeat" / f"{session_id_of(payload)}.json"
    body = json.dumps(
        {
            "adapter_name": ADAPTER_NAME,
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id_of(payload),
        },
        sort_keys=True, indent=2,
    ) + "\n"
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == body:
            return path  # identical bytes; do not disturb mtime
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    except OSError:
        return None
    return path


def copy_transcript(repo: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Copy the conversation transcript named by ``transcript_path`` in-repo.

    Re-copies when the source has grown or been rewritten -- Cursor appends
    to one file across a conversation.
    """
    source = payload.get("transcript_path")
    if not isinstance(source, str) or not source:
        return None
    try:
        src = Path(source).expanduser()
        if not src.is_file():
            return None
        dest = adapter_dir(repo) / "raw" / "transcripts" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            s, d = src.stat(), dest.stat()
            if d.st_size == s.st_size and int(d.st_mtime) >= int(s.st_mtime):
                return dest
        shutil.copy2(src, dest)
        return dest
    except OSError:
        return None


def _edited_file(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("file_path", "path", "filename", "target_file"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _hunks(payload: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(old_string, new_string) pairs from an edit payload."""
    edits = payload.get("edits")
    if not isinstance(edits, list):
        return []
    out: List[Tuple[str, str]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        old = edit.get("old_string")
        new = edit.get("new_string")
        if not isinstance(old, str) and not isinstance(new, str):
            continue
        out.append((old if isinstance(old, str) else "", new if isinstance(new, str) else ""))
    return out


def record_edit(repo: Path, payload: Dict[str, Any], modality: str) -> Optional[Path]:
    """Record per-edit provenance from an afterFileEdit/afterTabFileEdit payload.

    Proportionality: file path, hunk count, the extent of what was replaced
    AND of what replaced it, and a hash of the new text -- never the text.
    Both extents matter: with only the new extent a large deletion is
    indistinguishable from a no-op, and deletions are exactly what the
    attribution pass has to explain.
    """
    path = _edited_file(payload)
    hunks = _hunks(payload)
    if path is None or not hunks:
        return None

    old_joined = "".join(old for old, _ in hunks)
    new_joined = "".join(new for _, new in hunks)
    record = {
        "adapter_name": ADAPTER_NAME,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type": "file_edit",
        "session_id": session_id_of(payload),
        "modality": modality,  # "agent" | "tab"
        "file": path,
        "edit_count": len(hunks),
        "old_extent_chars": len(old_joined),
        "new_extent_chars": len(new_joined),
        "extent_chars": len(new_joined),  # cross-adapter alias
        "content_sha256": hashlib.sha256(new_joined.encode("utf-8", "replace")).hexdigest(),
    }
    out = adapter_dir(repo) / "raw" / "hooks" / f"edits-{session_id_of(payload)}.jsonl"
    _append_jsonl(out, record)
    return out


def request_snapshot(repo: Path, reason: str) -> bool:
    """Ask the capture layer for a snapshot through its CLI seam.

    Shelling out (rather than importing) is the epic's standing constraint:
    this tree ships unchanged into assignment repos that may have no Python
    capture layer at all, where this simply no-ops.
    """
    seam = repo / "tests" / "_capture" / "__main__.py"
    if not seam.is_file():
        return False
    env = dict(os.environ)
    # The child IS the snapshot; stop the venv sitecustomize from taking a
    # second one when the child exits.
    env["_CAPTURE_SUBPROCESS"] = "1"
    try:
        subprocess.run(
            [sys.executable, "-m", "tests._capture", "snapshot", "--reason", reason],
            cwd=str(repo), env=env, timeout=25,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    except OSError:
        return


def safe_main(
    event_name: str,
    callback: Optional[Callable[[Path, Dict[str, Any]], None]] = None,
    log_payload: bool = True,
) -> int:
    """Common hook body. Writes nothing to stdout. Always returns 0.

    Stdout silence is load-bearing: Cursor reads a JSON object on stdout as
    a verdict (``continue: false`` / permission decisions), so a stray print
    would turn an observational hook into a policing one.

    ``log_payload=False`` is the tab-hook path -- see the module docstring.
    """
    try:
        payload = read_payload()
        repo = project_repo_root()
        write_heartbeat(repo, payload)
        if log_payload:
            append_hook_event(repo, event_name, payload)
        if callback is not None:
            callback(repo, payload)
    except Exception:
        pass
    return 0
