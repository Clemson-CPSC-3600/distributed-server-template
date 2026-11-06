"""Shared helpers for the shipped Claude Code hooks.

Sibling of ``.codex/hooks/_common.py``; same shape, same rules, different
adapter. Read that file's docstring first -- the design rules are identical
and are not repeated here in full:

1. **Stdlib only, no project imports** (the tree must work in an assignment
   repo with no Python test harness; snapshots go through the capture CLI
   seam via a subprocess).
2. **Never block a turn.** Every entry point swallows exceptions, writes
   nothing to stdout, and returns 0. In Claude Code stdout is a *control*
   channel -- a JSON object there can deny a tool call or inject context --
   so silence is not merely tidy, it is what keeps these hooks
   observational.
3. **Never assume cwd is the repo root.** Paths resolve from this file.
4. **Heartbeat content is stable** so tree-SHA snapshot dedupe survives.

Claude-specific divergences from the Codex sibling:

- Transcripts land in ``.ai-traces/claude/raw/sessions/`` -- the directory
  ``claude_ingest``/``claude_normalize`` already read, so a pushed copy is
  normalized exactly like a scavenged one.
- ``tool_input`` shapes differ: ``Write`` carries ``content``, ``Edit``
  carries ``new_string``, ``MultiEdit`` carries an ``edits`` list whose
  entries each carry ``new_string``. Measuring only the top level would
  record every MultiEdit as extent 0.

PACKAGING: this tree lives at ``.claude-student/`` in the instructor repo
and is installed as ``.claude/`` in student repos. It must never be copied
into the instructor's own ``.claude/``, which is a live directory holding
instructor-only tooling.
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
from typing import Any, Callable, Dict, List, Optional

AI_TRACES_DIR = ".ai-traces"
ADAPTER_NAME = "claude"
SCHEMA_VERSION = "1"

# Tool names whose PostToolUse payload describes a file edit. The
# shape-based fallback in ``_looks_like_write`` covers renames and any new
# editing tool that lands mid-semester.
EDIT_TOOL_NAMES = {
    "edit", "write", "multiedit", "notebookedit", "update", "create",
    "str_replace_editor", "apply_patch",
}


def project_repo_root() -> Path:
    """The repo root: <root>/.claude/hooks/_common.py -> <root>."""
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
    for key in ("session_id", "sessionId", "conversation_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_component(value.strip())
    return "unknown"


def _safe_component(value: str) -> str:
    """Filesystem-safe filename component (session ids are UUID-ish)."""
    keep = [c if (c.isalnum() or c in "-_.") else "-" for c in value]
    out = "".join(keep).strip("-.") or "unknown"
    return out[:80]


def append_hook_event(repo: Path, event_name: str, payload: Dict[str, Any]) -> None:
    """Append the raw hook event, verbatim, to this session's JSONL.

    Deliberately unfiltered by cwd: Claude Code runs these hooks only
    because it loaded THIS repo's project settings, so the session belongs
    to this repo even when the hook's cwd is a subdirectory.
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

    ``capture_health`` reports presence of anything under
    ``.ai-traces/<adapter>/heartbeat/``. Its absence *together with* Claude
    evidence is the "hooks never fired" signal -- for Claude Code the usual
    cause is a declined workspace-trust / hook-approval prompt. Absence
    alone only means the tool was never used here.

    Every hook writes it, not just SessionStart: a student may end up with
    only some events enabled, and no single hook may own the signal.
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
    """Copy the session JSONL named by ``transcript_path`` into the repo.

    Push-based capture's whole advantage: the path arrives in the payload,
    so nothing has to glob ``~/.claude/projects/`` and open the first ~50
    lines of every session on the machine to test its cwd. The destination
    matches ``claude_ingest``'s so the normalizer picks it up.

    Re-copies when the source has grown or been rewritten -- Claude appends
    to one file for the whole session.
    """
    source = payload.get("transcript_path")
    if not isinstance(source, str) or not source:
        return None
    try:
        src = Path(source).expanduser()
        if not src.is_file():
            return None
        dest = adapter_dir(repo) / "raw" / "sessions" / src.name
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
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "notebook_path", "path", "filename", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _new_text_fragments(tool_input: Dict[str, Any]) -> List[str]:
    """Every chunk of newly written text in this tool call.

    MultiEdit nests them one level down; missing that records extent 0 for
    the tool that does the largest edits.
    """
    fragments: List[str] = []
    for key in ("new_string", "content", "new_source", "text"):
        value = tool_input.get(key)
        if isinstance(value, str):
            fragments.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                fragments.extend(_new_text_fragments(edit))
    return fragments


def record_edit(repo: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Record per-edit provenance from a PostToolUse payload.

    Proportionality: file path, tool, change extent and a content hash --
    never the edited text. The text is already in the transcript that
    ``copy_transcript`` captures; duplicating it here would widen the
    disclosure surface for no extra signal (see AI_POLICY.md).
    """
    tool_name = payload.get("tool_name")
    tool_name = tool_name if isinstance(tool_name, str) else ""
    path = _edited_file(payload)
    if path is None:
        return None
    normalized = tool_name.replace("-", "_").replace("_", "").lower()
    if normalized not in {n.replace("_", "") for n in EDIT_TOOL_NAMES} \
            and not _looks_like_write(payload):
        return None

    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    fragments = _new_text_fragments(tool_input)
    joined = "".join(fragments)

    record = {
        "adapter_name": ADAPTER_NAME,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type": "file_edit",
        "session_id": session_id_of(payload),
        "tool_name": tool_name,
        "file": path,
        "edit_count": len(fragments),
        "extent_chars": len(joined),
        "content_sha256": hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest(),
    }
    out = adapter_dir(repo) / "raw" / "hooks" / f"edits-{session_id_of(payload)}.jsonl"
    _append_jsonl(out, record)
    return out


def _looks_like_write(payload: Dict[str, Any]) -> bool:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    return any(k in tool_input for k in ("new_string", "content", "edits", "patch"))


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
) -> int:
    """Common hook body. Writes nothing to stdout. Always returns 0."""
    try:
        payload = read_payload()
        repo = project_repo_root()
        write_heartbeat(repo, payload)
        append_hook_event(repo, event_name, payload)
        if callback is not None:
            callback(repo, payload)
    except Exception:
        pass
    return 0
