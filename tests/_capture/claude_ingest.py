"""Copy Claude Code session transcripts for this repo into .ai-traces/.

Called from ClaudeAdapter.ingest. We pull matching sessions out of
``$CLAUDE_CONFIG_DIR/projects/`` (default: ``~/.claude/projects/``),
filter by cwd match against the repo, and copy them into
``.ai-traces/claude/raw/sessions/`` so the adapter can normalize them.

Selection criteria:
  * the first cwd-bearing event in the session JSONL resolves to the same
    path as ``repo``

Idempotency is mtime+size-aware (see ``copy_dedup`` and codex_ingest's
module docstring for the same rationale — size alone misses an equal-size
rewrite). Claude Code keeps a single session JSONL file open and appends
events as turns happen, so the same filename keeps growing — we re-copy
whenever the source has grown. The same dedup guard applies to spillover
files in the sibling ``<uuid>/`` directory that Claude Code uses for
oversized tool outputs. A course-start time floor (``course_floor``) also
applies, keyed on the top-level session file's mtime — see that module's
docstring for the privacy rationale and the known false-negative case.

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

from tests._capture import ai_traces, copy_dedup, course_floor, platform_compat

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
    """True if ``candidate`` is ``repo`` itself or nested inside it.

    Thin wrapper around the shared ``platform_compat.path_within_repo``
    helper — see its docstring for the containment/case/nested-foreign-repo
    semantics shared with codex_ingest and vscode_chat_ingest.
    """
    if not candidate:
        return False
    return platform_compat.path_within_repo(candidate, repo)


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
                src_stat = src.stat()
            except OSError:
                continue
            if dest.exists():
                try:
                    dest_stat = dest.stat()
                    if copy_dedup.dest_is_up_to_date(
                        dest_stat.st_size, dest_stat.st_mtime,
                        src_stat.st_size, src_stat.st_mtime,
                    ):
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


def ingest_transcripts(
    repo: Path, skipped: Optional[List[Path]] = None,
) -> List[Path]:
    """Copy matching sessions into repo/.ai-traces/claude/raw/sessions/.

    When ``skipped`` is provided, every session that MATCHED this repo and
    cleared the course floor but was already up to date is appended to it.
    That is the "matched, not copied" population the capture_counts commit
    trailer needs: without it ``matched == copied``, so a steady-state run
    (nothing new to copy) emits no trailer at all and reads exactly like
    ingest being broken. Sessions rejected by the cwd filter or the course
    floor are deliberately absent from BOTH lists — counting them would
    leak the student's AI use outside this course.

    Selection: any session JSONL whose first cwd-bearing event resolves to
    ``repo``, and whose file mtime is at or after the course-start floor
    when one is configured (see ``course_floor``). Mtime+size-aware
    re-copy for both the JSONL and any sibling spillover directory (see
    ``copy_dedup``). Never raises.
    """
    try:
        projects_dir = _claude_projects_dir()
        if projects_dir is None or not projects_dir.is_dir():
            return []

        floor = course_floor.get_course_start_floor(repo)

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
            try:
                src_stat = src.stat()
            except OSError:
                continue
            if course_floor.file_predates_course(src_stat.st_mtime, floor):
                continue
            dest_dir = repo / CLAUDE_SESSIONS_DIR
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return copied
            dest = dest_dir / src.name
            need_copy = True
            if dest.exists():
                try:
                    dest_stat = dest.stat()
                    if copy_dedup.dest_is_up_to_date(
                        dest_stat.st_size, dest_stat.st_mtime,
                        src_stat.st_size, src_stat.st_mtime,
                    ):
                        need_copy = False
                except OSError:
                    pass
            if need_copy:
                try:
                    shutil.copy2(src, dest)
                    copied.append(dest)
                except OSError:
                    continue
            elif skipped is not None:
                # Matched this repo and cleared the floor, already current.
                skipped.append(dest)

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
