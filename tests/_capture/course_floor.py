"""Course-start time floor shared by the codex/claude/vscode_chat ingesters.

The floor exists to solve a privacy problem, not just a correctness one: a
size-only dedup check has no time bound at all, so re-running the capture
layer in a repo whose local AI-tool config directories still hold sessions
from a prior semester (or a shared lab account) silently imports someone
else's transcripts into the CURRENT student's graded repo.

Source of the floor: ``<repo>/assignment-config.json`` — a NEW file,
written by ``create-assignment.sh``'s heredoc, deliberately left OUT of
``tools/INTEGRITY_HASHES.txt``. ``project-template-config.json`` was
considered and rejected: that file already has its own heredoc rewrite in
create-assignment.sh (a second, later `cat > project-template-config.json`)
and is a hash-pin candidate elsewhere, so adding a per-cohort value there
would collide with both. ``assignment-config.json`` is intentionally
soft-tracked (presence-only, not content-pinned) since ``course_start`` is
legitimately different for every cohort/section.

The floor keys on FILE MTIME, not on any in-file "session started at"
timestamp. ``shutil.copy2`` (used by every ingester) preserves the
source's mtime on the copy, so comparing the ON-DISK file's mtime to
course_start is the only signal available without opening and parsing
every candidate file up front. Known, accepted consequence: a session that
began before the course but continued into it (e.g. a shared-account
Codex session started the night before enrollment, still open on day one)
has an mtime at or after the floor and IS captured. That's a false
negative in the "keep old sessions out" direction, not a bug — the
alternative (parsing an in-file start timestamp per candidate before
deciding whether to even look at the file) is exactly the per-turn content
parsing the synchronous trigger path is required to avoid.

Default when the floor can't be determined (file absent, unreadable,
malformed JSON, missing/malformed ``course_start``): NO FLOOR — capture
everything, exactly like today. This is deliberately the permissive
default, not the restrictive one, for two reasons: (1) every repo
generated going forward by ``create-assignment.sh`` gets a real
``course_start`` value, so the privacy gap this task exists to close is
closed for the case that actually matters (student clones a fresh
assignment repo); (2) a large population of existing/instructor-only
repos (this template repo included) will never gain the file, and making
capture silently go quiet there — destroying evidence with no visible
error — is a worse failure mode than the privacy risk in a case the file
was never meant to guard against. If a stronger default is ever wanted for
already-shipped repos, it should be an explicit backfill of
assignment-config.json, not a change to this fallback.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ASSIGNMENT_CONFIG_FILENAME = "assignment-config.json"


def get_course_start_floor(repo: Path) -> Optional[float]:
    """Return ``course_start`` from ``<repo>/assignment-config.json`` as a
    POSIX timestamp, or ``None`` when no reliable floor is available.

    ``None`` means "no floor" (see module docstring for why that's the safe
    default). Never raises — any I/O, JSON, or parse error is treated the
    same as "absent".

    Accepts ISO 8601 date (``"2026-01-12"``) or datetime strings, including
    a trailing ``"Z"`` (interpreted as UTC, since ``datetime.fromisoformat``
    does not accept bare ``Z`` before Python 3.11 and this project's floor
    is >=3.8). A naive (timezone-less) datetime is assumed to be UTC.
    """
    config_path = repo / ASSIGNMENT_CONFIG_FILENAME
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    course_start = data.get("course_start")
    if not isinstance(course_start, str) or not course_start:
        return None
    value = course_start
    if value.endswith("Z") or value.endswith("z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def file_predates_course(mtime: float, floor: Optional[float]) -> bool:
    """True if ``mtime`` is strictly before ``floor``.

    ``floor is None`` always returns False (no floor means nothing is
    excluded on time grounds).
    """
    if floor is None:
        return False
    return mtime < floor
