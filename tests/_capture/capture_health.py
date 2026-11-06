"""Capture-health manifest: stable, in-tree evidence about the capture layer.

Absence of AI traces is ambiguous on its own -- it can mean "the student
never used a tool", "the tool ran but our ingest broke", or "we don't cover
that tool at all". The manifest at ``.ai-traces/capture-health.json``
removes that ambiguity by recording, for every registered adapter, whether
it was detected on this machine, whether its ingest reported an error, and
whether a push-based heartbeat exists -- plus the bootstrap state of the
trigger layer itself.

DETERMINISM IS LOAD-BEARING. Snapshots are deduped by tree SHA (see
``git_ops.preview_tree``): if this file's bytes vary between two otherwise
identical runs, every run creates a new snapshot and dedupe dies. So the
manifest holds ONLY stable state -- booleans and strings. No timestamps, no
durations, no counters, no session ids, no unordered collections.
Run-varying values (how many transcripts matched/were copied) belong in the
snapshot's commit-message trailers instead; see
``metadata.format_commit_message(capture_counts=...)``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tests._capture import ai_traces, redaction, runtime_triggers

HEALTH_FILE = f"{ai_traces.AI_TRACES_DIR}/capture-health.json"

# Hook-based tools drop stable-content heartbeat files here to prove the
# hook actually fired. Until such hooks ship, the boolean is simply False
# for every adapter, which is itself accurate information.
HEARTBEAT_SUBDIR = "heartbeat"

SCHEMA_VERSION = "1"


def registered_adapter_names() -> List[str]:
    """Names of every adapter the registry knows about, present or not."""
    from tests._capture.agent_adapters import registry
    names = []
    for cls in registry._REGISTERED:
        name = getattr(cls, "name", "")
        if name:
            names.append(name)
    return names


def traced_adapter_names(repo: Path) -> List[str]:
    """Adapter names that have an on-disk trace directory in this repo.

    Push-based hooks can produce traces for a tool the pull-side registry
    knows nothing about -- Cursor is the first such adapter: its scavenger
    route was excluded, so hooks are its only channel and there is no
    ``CursorAdapter`` to register. Without this the manifest would silently
    omit Cursor entirely, and the evidence-AND-no-heartbeat conjunction
    (the only way to see hooks that never fired) could never be evaluated
    for exactly the tool that depends on it most.

    Directory-derived and sorted, so the value is stable across runs.
    """
    root = repo / ai_traces.AI_TRACES_DIR
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def has_heartbeat(repo: Path, adapter_name: str) -> bool:
    """True iff the adapter left at least one heartbeat artifact in-repo."""
    root = ai_traces.adapter_dir(repo, adapter_name) / HEARTBEAT_SUBDIR
    try:
        if not root.is_dir():
            return False
        return any(p.is_file() for p in root.rglob("*"))
    except OSError:
        return False


def has_evidence(repo: Path, adapter_name: str) -> bool:
    """True iff the adapter captured any trace artifact in-repo.

    Deliberately EXCLUDES the heartbeat subdirectory, so ``evidence`` and
    ``heartbeat`` stay independent signals. Their conjunction is the point:

      evidence AND heartbeat   -> the tool ran and its hooks fired
      evidence AND NOT hb      -> the tool ran but hooks never fired, i.e.
                                  they are almost certainly UNAUTHORIZED
                                  (authorization is CLI-only, so an
                                  editor-only student is never prompted)
      NOT evidence AND NOT hb  -> ambiguous: never used here
      NOT evidence AND hb      -> hooks fired but nothing was captured;
                                  an ingest problem, not a trust problem

    Without ``evidence`` the manifest cannot tell the second case from the
    third, which is exactly the silent failure the hooks exist to expose.
    """
    root = ai_traces.adapter_dir(repo, adapter_name)
    heartbeat_root = root / HEARTBEAT_SUBDIR
    try:
        if not root.is_dir():
            return False
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                path.relative_to(heartbeat_root)
            except ValueError:
                return True  # a real artifact, outside the heartbeat dir
        return False
    except OSError:
        return False


def format_error(exc: BaseException) -> str:
    """Stable one-line rendering of an exception for the manifest."""
    return f"{type(exc).__name__}: {exc}"


def build_health(
    repo: Path,
    *,
    present_names: Iterable[str],
    errors_by_adapter: Optional[Dict[str, List[str]]] = None,
    workspace_root_mismatch: bool = False,
) -> Dict[str, Any]:
    """Assemble the manifest structure. Values are booleans/strings only.

    ``workspace_root_mismatch`` is True when at least one AI session this
    run was observed running from a directory ABOVE the repo (the student
    opened a parent folder rather than the assignment). It is a bare
    boolean on purpose: the mismatched directory names belong to the
    student's unrelated projects, and the run-varying count rides in the
    commit-message trailers instead (see ``format_counts``) so this file
    stays byte-stable for tree-SHA dedupe.
    """
    errors_by_adapter = errors_by_adapter or {}
    present = set(present_names)
    names = (
        set(registered_adapter_names())
        | present
        | set(errors_by_adapter)
        | set(traced_adapter_names(repo))
    )

    adapters: Dict[str, Any] = {}
    for name in sorted(names):
        raw_errors = errors_by_adapter.get(name) or []
        adapters[name] = {
            "present": name in present,
            "heartbeat": has_heartbeat(repo, name),
            "evidence": has_evidence(repo, name),
            # Redaction fails closed: anything it could not parse is
            # withheld rather than committed. Surfacing the boolean here
            # keeps that gap VISIBLE -- otherwise a withheld transcript
            # looks identical to a session that never happened. Derived
            # from on-disk markers, so it stays stable run-to-run.
            "withheld_unparseable": redaction.has_withheld_markers(
                repo, name, reason="unparseable"),
            # DEFAULT DENY: this adapter's transcript did not match a
            # verified call/output vocabulary, so its content was withheld
            # rather than committed. Distinct from `withheld_unparseable`:
            # the file parsed fine, we just cannot prove what its records
            # mean, and an unmeasured adapter must not leak tool output.
            "withheld_unrecognized": redaction.has_withheld_markers(
                repo, name, reason="unrecognized_record_vocabulary"),
            # Exception messages can contain personal paths or source text.
            # Full diagnostics remain local; committed health keeps counts
            # and fixed codes only, outside the transcript-redaction pass.
            "errors": ["adapter_ingest_failed" for _ in raw_errors],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "adapters": adapters,
        "bootstrap": runtime_triggers.installation_state(repo),
        "workspace_root_mismatch": bool(workspace_root_mismatch),
    }


def hooks_look_unauthorized(health: Dict[str, Any], adapter_name: str) -> bool:
    """True if this adapter's manifest entry shows evidence but no heartbeat.

    Reads an already-built manifest, so an instructor can evaluate the
    conjunction from a committed `capture-health.json` alone -- no access
    to the student's machine required. Bare heartbeat absence is NOT
    enough: with no evidence either, the student simply never used the
    tool here.
    """
    entry = (health.get("adapters") or {}).get(adapter_name) or {}
    return bool(entry.get("evidence")) and not bool(entry.get("heartbeat"))


def serialize(health: Dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, fixed separators, trailing newline."""
    return json.dumps(
        health, sort_keys=True, indent=2, separators=(",", ": "),
        ensure_ascii=True,
    ) + "\n"


def write_health(repo: Path, health: Dict[str, Any]) -> Optional[Path]:
    """Write the manifest. Rewrites only when content differs (keeps mtime
    stable for editors/file watchers). Never raises."""
    path = repo / HEALTH_FILE
    payload = serialize(health)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            return path
        path.write_text(payload, encoding="utf-8", newline="\n")
        return path
    except OSError:
        return None


def format_counts(counts_by_adapter: Dict[str, Dict[str, int]]) -> List[str]:
    """Render volatile per-adapter counts as commit-message trailer lines.

    Counts are MATCHED-ONLY: they describe artifacts this repo's ingest
    matched and copied. Store-wide scan totals are deliberately never
    emitted -- they would leak how much AI the student uses outside this
    course.

    ONE DOCUMENTED CARVE-OUT: ``ancestor_cwd`` counts sessions that ran
    from a STRICT ANCESTOR of the repo -- the workspace-root-mismatch case,
    where both the cwd filter and hook discovery miss real work on this
    course. It is emitted only when non-zero, names no directories, and is
    structurally incapable of counting siblings or unrelated projects (see
    ``codex_ingest._cwd_is_repo_ancestor``). That keeps the leak bounded to
    "you opened a folder above the assignment N times", which is the whole
    point of recording it.
    """
    if not counts_by_adapter:
        return []
    lines = ["capture_counts:"]
    for name in sorted(counts_by_adapter):
        c = counts_by_adapter[name]
        line = (
            f"  {name}: matched={int(c.get('matched', 0))} "
            f"copied={int(c.get('copied', 0))}"
        )
        ancestor = int(c.get("ancestor_cwd", 0))
        if ancestor:
            line += f" ancestor_cwd={ancestor}"
        lines.append(line)
    return lines
