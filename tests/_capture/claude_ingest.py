"""Copy Claude Code session transcripts for this repo into .ai-traces/.

Called from ClaudeAdapter.ingest. We pull matching sessions out of
``$CLAUDE_CONFIG_DIR/projects/`` (default: ``~/.claude/projects/``),
filter by cwd match against the repo, and copy them into
``.ai-traces/claude/raw/sessions/`` so the adapter can normalize them.

Selection criteria:
  * the first cwd-bearing event in the session JSONL resolves to the same
    path as ``repo``

Idempotency is size-aware (see codex_ingest module docstring for the same
rationale). Claude Code keeps a single session JSONL file open and appends
events as turns happen, so the same filename keeps growing — we re-copy
whenever the source has grown. The same size guard applies to spillover
files in the sibling ``<uuid>/`` directory that Claude Code uses for
oversized tool outputs.

Contract: never raises. Returns the list of copied destination paths.

Session shape (Claude Code 2.x):
    Path:    $CLAUDE_CONFIG_DIR/projects/<slug>/<uuid>.jsonl
    Sibling: $CLAUDE_CONFIG_DIR/projects/<slug>/<uuid>/  (spillover dir)
    Slug:    repo absolute path with '/' replaced by '-' (informational
             only — we use the per-event ``cwd`` field as the authoritative
             filter, since the slug encoding can change between Claude Code
             releases and varies on Windows for drive letters).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import List, Optional

from tests._capture import ai_traces

CLAUDE_SESSIONS_DIR = Path(ai_traces.AI_TRACES_DIR) / "claude" / "raw" / "sessions"
SESSION_GLOB = "*.jsonl"

# Maximum lines to scan per session looking for a cwd field. Real sessions
# put cwd on the first user event (line 3 in our samples); 50 is generous.
_CWD_SCAN_LIMIT = 50


def _claude_projects_dir() -> Optional[Path]:
    """Return the projects directory, or None if not usable."""
    home = os.environ.get("CLAUDE_CONFIG_DIR")
    if home:
        return Path(home) / "projects"
    try:
        return Path.home() / ".claude" / "projects"
    except (OSError, RuntimeError):
        return None


def _session_cwd(path: Path) -> str:
    """Return the cwd from the first cwd-bearing event, or "" on error.

    Scans up to ``_CWD_SCAN_LIMIT`` lines because the leading lines of a
    session are housekeeping events (``permission-mode``, ``ai-title``,
    ``file-history-snapshot``) that lack cwd; the first user/assistant
    event always carries it.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= _CWD_SCAN_LIMIT:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                cwd = obj.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _same_path(candidate: str, repo: Path) -> bool:
    if not candidate:
        return False
    try:
        return Path(candidate).resolve() == repo.resolve()
    except (OSError, ValueError):
        return False


def _sync_spillover_dir(src_dir: Path, dest_dir: Path) -> int:
    """Copy/refresh files inside the spillover directory. Size-aware.
    Returns the number of files freshly written. Never raises."""
    written = 0
    try:
        for src in src_dir.rglob("*"):
            try:
                if not src.is_file():
                    continue
            except OSError:
                continue
            try:
                rel = src.relative_to(src_dir)
            except ValueError:
                continue
            dest = dest_dir / rel
            try:
                src_size = src.stat().st_size
            except OSError:
                continue
            if dest.exists():
                try:
                    if dest.stat().st_size >= src_size:
                        continue
                except OSError:
                    continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                written += 1
            except OSError:
                continue
    except OSError:
        return written
    return written


def ingest_transcripts(repo: Path, session_started_at: float) -> List[Path]:
    """Copy matching sessions into repo/.ai-traces/claude/raw/sessions/.

    Selection: any session JSONL whose first cwd-bearing event resolves to
    ``repo``. Size-aware re-copy for both the JSONL and any sibling
    spillover directory. Never raises.

    ``session_started_at`` is accepted for API stability but unused — see
    codex_ingest for the rationale (same workflow shape).
    """
    del session_started_at
    try:
        projects_dir = _claude_projects_dir()
        if projects_dir is None or not projects_dir.is_dir():
            return []

        copied: List[Path] = []
        try:
            candidates = list(projects_dir.glob(f"*/{SESSION_GLOB}"))
        except OSError:
            return []

        for src in candidates:
            try:
                if not src.is_file():
                    continue
            except OSError:
                continue
            if not _same_path(_session_cwd(src), repo):
                continue
            dest_dir = repo / CLAUDE_SESSIONS_DIR
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return copied
            dest = dest_dir / src.name
            try:
                src_size = src.stat().st_size
            except OSError:
                continue
            need_copy = True
            if dest.exists():
                try:
                    if dest.stat().st_size >= src_size:
                        need_copy = False
                except OSError:
                    pass
            if need_copy:
                try:
                    shutil.copy2(src, dest)
                    copied.append(dest)
                except OSError:
                    continue

            # Sibling spillover directory: <uuid>/ alongside <uuid>.jsonl,
            # holding oversized tool outputs that exceed inline limits.
            spillover_src = src.with_suffix("")
            try:
                if spillover_src.is_dir():
                    spillover_dest = dest_dir / spillover_src.name
                    _sync_spillover_dir(spillover_src, spillover_dest)
            except OSError:
                continue
        return copied
    except Exception:
        # Belt-and-suspenders: capture must never propagate to pytest.
        return []
