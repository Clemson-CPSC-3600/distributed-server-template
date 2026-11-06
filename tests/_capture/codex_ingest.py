"""Copy Codex rollout transcripts for this repo into .ai-traces/.

Called from CodexAdapter.ingest (orchestrator-driven). We pull matching
rollouts out of $CODEX_HOME/sessions/, filter by cwd == repo, and copy them
into .ai-traces/codex/raw/rollouts so the adapter can normalize them.

We intentionally do NOT filter by the pytest session start. The realistic
workflow is: student runs Codex (rollout mtime fixed), then LATER runs
pytest. An mtime >= session_start filter would exclude every such rollout
because the rollout was finalized before pytest started. The right
semantic — "capture all Codex rollouts for this repo that aren't already
fully captured, and that aren't from before this course started" — is
achieved by the cwd match, the course-start time floor (see
``course_floor`` module — keys on file mtime, NOT session start), and the
mtime+size idempotency check below.

Why mtime+size and not size-only or filename-only: Codex CLI 0.119+ (and
the desktop Codex.app) reuse a single rollout file across multiple turns
within one session. A pure ``dest.exists() → skip`` check freezes the
captured copy at whatever size it had on first ingest, so every turn
after the first is silently lost. Comparing dest size to source size lets
us re-copy when the rollout has grown. Size alone is not enough, though:
a source rewritten in place at the SAME byte length (edited, or
truncated-and-rewritten) would be silently skipped by a size-only check,
freezing stale evidence. Adding an mtime comparison (``shutil.copy2``
preserves mtime, so an unchanged copy's mtime never falls behind its
source) catches that case too — see ``copy_dedup.dest_is_up_to_date``. No
content hashing is used; this runs in the synchronous trigger path where
hashing every candidate file on every trigger is an avoidable I/O cost.

Contract: never raises. Returns the list of copied destination paths (possibly
empty). Any error is swallowed and results in an empty list (or a shorter
list of successfully-copied paths), matching capture.py's best-effort error
policy.

Rollout shape (as of Codex CLI 0.119.x, see Task 1 discovery notes):
    Path:    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
    Line 1:  {"type": "session_meta",
              "payload": {"id": ..., "cwd": "C:\\...", ...}, ...}
Drive-letter casing in payload.cwd varies by producer (``codex exec`` emits
``C:\\`` while the VS Code extension emits ``c:\\``), so both candidate cwd
and target repo are normalized via ``Path.resolve()`` before comparison.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import List, Optional

from tests._capture import ai_traces, copy_dedup, course_floor, platform_compat

TRANSCRIPTS_DIR = ai_traces.AI_TRACES_DIR
CODEX_ROLLOUTS_DIR = Path(ai_traces.AI_TRACES_DIR) / "codex" / "raw" / "rollouts"
ROLLOUT_GLOB = "rollout-*.jsonl"


def _codex_sessions_dir() -> Optional[Path]:
    """Return the sessions directory, or None if $CODEX_HOME is unusable."""
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home) / "sessions"
    # Fall back to the default Codex CLI location when the env var is unset.
    try:
        return Path.home() / ".codex" / "sessions"
    except (OSError, RuntimeError):
        return None


def _rollout_cwd(path: Path) -> str:
    """Return payload.cwd from the first JSONL line, or "" on any error.

    Guards on ``type == "session_meta"`` so that a future schema change
    (different first-record type) fails cleanly rather than matching garbage.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            first = f.readline()
        if not first:
            return ""
        obj = json.loads(first)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    if obj.get("type") != "session_meta":
        return ""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return ""
    cwd = payload.get("cwd", "")
    return cwd if isinstance(cwd, str) else ""


def _same_path(candidate: str, repo: Path) -> bool:
    """True if ``candidate`` is ``repo`` itself or nested inside it.

    Thin wrapper around the shared ``platform_compat.path_within_repo``
    helper (handles Windows drive-letter/darwin case insensitivity and
    excludes nested foreign git checkouts/worktrees — see that function's
    docstring). Kept as a module-level name for readability at call sites.
    """
    if not candidate:
        return False
    return platform_compat.path_within_repo(candidate, repo)


def _cwd_is_repo_ancestor(candidate: str, repo: Path) -> bool:
    """True if ``candidate`` is a STRICT ancestor of ``repo``.

    This is the workspace-root-mismatch condition: the student opened a
    folder that merely *contains* the assignment (``~/cpsc-3600/projects/``
    while the repo is ``projects/assignment-1/``). Codex then reports that
    parent as its cwd, which fails the ``_same_path`` filter, so the
    rollout is dropped -- and, because hook discovery is an upward walk
    from cwd, no hook fires either. Real AI use becomes indistinguishable
    from no AI use.

    Uses the documented reversed form of ``path_within_repo`` ("is the repo
    within this folder?"), which requires ``exclude_nested_git=False``
    because the repo's own ``.git`` is expected in that direction and is
    not a foreign boundary.

    STRICT: the equality case is excluded, so a cwd of the repo itself is
    not a mismatch. Siblings are excluded structurally -- ``projects/
    other-thing`` is not an ancestor of ``projects/assignment-1`` -- which
    is what keeps this from becoming a store-wide scan total.
    """
    if not candidate:
        return False
    try:
        if platform_compat.path_within_repo(candidate, repo):
            return False  # inside the repo (or the repo itself) -- not an ancestor
        return platform_compat.path_within_repo(
            str(repo), Path(candidate), exclude_nested_git=False,
        )
    except (OSError, ValueError):
        return False


def ingest_transcripts(
    repo: Path, skipped: Optional[List[Path]] = None,
    ancestor_cwd: Optional[List[Path]] = None,
) -> List[Path]:
    """Copy matching rollouts into repo/.ai-traces/codex/raw/rollouts/. Never raises.

    When ``skipped`` is provided, every rollout that MATCHED this repo and
    cleared the course floor but was already up to date is appended to it
    — the "matched, not copied" population the capture_counts trailer
    needs so a steady-state run stays distinguishable from broken ingest
    (see ``claude_ingest.ingest_transcripts`` for the full rationale).
    Rollouts rejected by the cwd filter or the floor appear in NEITHER
    list: counting them would leak out-of-course AI use.

    ONE NARROW EXCEPTION: when ``ancestor_cwd`` is provided, rejected
    rollouts whose cwd is a STRICT ANCESTOR of ``repo`` are appended to it
    (never copied). That is the workspace-root-mismatch case -- the student
    opened a folder above the assignment, so both this filter and hook
    discovery miss real work on this course. Siblings and unrelated
    directories are structurally excluded (see ``_cwd_is_repo_ancestor``),
    so this stays a targeted diagnostic rather than a store-wide total.

    Selection criteria:
      * rollout payload.cwd resolves to ``repo`` itself or a subdirectory
        of it (see ``platform_compat.path_within_repo``)
      * rollout file mtime is at or after the course-start floor, when one
        is configured (see ``course_floor`` module)

    Idempotent on unchanged input: if a destination file already exists
    AND is at least as large and at least as new as the source (see
    ``copy_dedup.dest_is_up_to_date``), the copy is skipped. If the source
    has grown, or was rewritten in place (same size, newer mtime), it is
    re-copied so the destination reflects the latest state. Returned list
    contains only paths that were freshly written this call.
    """
    try:
        sessions_dir = _codex_sessions_dir()
        if sessions_dir is None or not sessions_dir.is_dir():
            return []

        floor = course_floor.get_course_start_floor(repo)

        copied: List[Path] = []
        try:
            candidates = list(sessions_dir.rglob(ROLLOUT_GLOB))
        except OSError:
            return []

        for src in candidates:
            try:
                if not src.is_file():
                    continue
            except OSError:
                continue
            rollout_cwd = _rollout_cwd(src)
            if not _same_path(rollout_cwd, repo):
                # Not ours. One sub-case is still diagnostic: the cwd is a
                # strict ancestor of the repo, i.e. the student opened a
                # folder above the assignment. Record it (never copy it)
                # so the mismatch is visible instead of silent.
                if ancestor_cwd is not None and _cwd_is_repo_ancestor(
                    rollout_cwd, repo,
                ):
                    try:
                        if not course_floor.file_predates_course(
                            src.stat().st_mtime, floor,
                        ):
                            ancestor_cwd.append(src)
                    except OSError:
                        pass
                continue
            try:
                src_stat = src.stat()
            except OSError:
                continue
            if course_floor.file_predates_course(src_stat.st_mtime, floor):
                continue
            dest_dir = repo / CODEX_ROLLOUTS_DIR
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return copied
            dest = dest_dir / src.name
            if dest.exists():
                try:
                    dest_stat = dest.stat()
                    if copy_dedup.dest_is_up_to_date(
                        dest_stat.st_size, dest_stat.st_mtime,
                        src_stat.st_size, src_stat.st_mtime,
                    ):
                        # Matched and current — counted, not re-copied.
                        if skipped is not None:
                            skipped.append(dest)
                        continue
                except OSError:
                    continue
            try:
                shutil.copy2(src, dest)
                copied.append(dest)
            except OSError:
                continue
        return copied
    except Exception:
        # Absolute belt-and-suspenders: the capture layer must never propagate
        # an exception into pytest's session_finish. If anything above slips
        # past the narrower except clauses, swallow it here.
        return []
