"""Shared mtime+size dedup check for the codex/claude/vscode_chat ingesters.

Size-only dedup (``dest.size >= src.size`` -> skip) has a real bug: Codex
CLI 0.119+ and Claude Code both reuse/append to a single session file, but
if a source file is REWRITTEN at the same byte length (edited-in-place, or
truncated-and-rewritten by the producing tool), the destination copy is
never refreshed and silently goes stale — a wrong-content, not just a
missing-content, failure. That matters here specifically because this
capture layer is graded evidence: a frozen stale copy misrepresents what
actually happened in the session.

Fix: also compare mtime. ``shutil.copy2`` (used by every call site) copies
the source's mtime onto the destination, so an unchanged source's copy has
dest.mtime >= src.mtime forever. A rewritten source gets a new (newer)
mtime, so ``dest.mtime < src.mtime`` reliably flags "the destination is
stale even though the size matches." No content hashing is done here —
this check runs synchronously in the pytest/atexit/post-commit trigger
path, where reading and hashing every candidate file's full contents on
every trigger would be a real, avoidable I/O cost multiplied by however
many rollouts/sessions have accumulated over the term.
"""
from __future__ import annotations


def dest_is_up_to_date(
    dest_size: int, dest_mtime: float, src_size: int, src_mtime: float
) -> bool:
    """True when the destination already reflects the source's current
    state and a re-copy can be skipped.

    Both conditions must hold: the destination must be at least as large
    as the source (catches growth — appended turns) AND at least as new
    (catches an equal-size rewrite — the case size-only dedup missed).
    """
    return dest_size >= src_size and dest_mtime >= src_mtime
