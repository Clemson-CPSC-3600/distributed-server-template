"""Content-free report of the complete preserved snapshot graph."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from tests._capture import git_ops


def snapshot_ref(repo: Path) -> str | None:
    for ref in (git_ops.AUTO_TRACK_REF, "refs/heads/auto-track", "refs/remotes/origin/auto-track"):
        if git_ops.run_git(["rev-parse", "--verify", ref], repo).returncode == 0:
            return ref
    return None


def _snapshot_message(message: str) -> bool:
    """Recognize the capture message convention, including legacy v1 runs.

    First-parent ancestry also contains ordinary baseline commits. Neither
    ancestry nor the presence of copied trace files identifies a snapshot.
    Capture has always written a test-run/snapshot subject plus session_id.
    This classifies harness records; it is not an authenticity check.
    """
    lines = message.strip().splitlines()
    return bool(lines and lines[0].startswith(("test-run: ", "snapshot: "))
                and any(line.startswith("session_id: ") and
                        line.removeprefix("session_id: ").strip()
                        for line in lines[1:]))


def _valid_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    # These fields are used for classification and set membership. Validate
    # before access so an unsupported row cannot stop the rest of the report.
    for key in ("adapter_name", "event_type"):
        value = event.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    # Legacy records may omit IDs; their event identity falls back to a
    # content hash, while an absent session contributes no session count.
    for key in ("session_id", "event_id"):
        value = event.get(key)
        if value is not None and not isinstance(value, str):
            return False
    return True


def build_report(repo: Path, ref: str | None = None) -> dict:
    """Count records, never emit student text, paths, session IDs or commands.

    Do not use `git log -- path`: history simplification can omit snapshot
    merges whose tree matches another parent. Include recovered offline forks.
    """
    ref = ref or snapshot_ref(repo)
    if ref and ref.startswith("-"):
        raise ValueError("invalid snapshot reference")
    report = {"schema_version": 1, "snapshot_commits": 0, "archive_commits_scanned": 0, "stream_versions": 0,
              "adapters": {}, "current_stream_sessions": 0,
              "historical_sessions_not_in_current_stream": 0,
              "malformed_records": 0, "health_record_present": False}
    if not ref:
        report["state"] = "no_snapshot_history"
        return report
    commits = git_ops.run_git(["log", "--topo-order", "--format=%H", ref], repo)
    if commits.returncode:
        raise ValueError("snapshot history is unavailable")
    hashes = commits.stdout.splitlines()
    primary = git_ops.run_git(["log", "--first-parent", "--format=%B%x00", ref], repo)
    if primary.returncode:
        raise ValueError("primary snapshot history is unavailable")
    report["snapshot_commits"] = sum(_snapshot_message(message)
                                     for message in primary.stdout.split("\0"))
    report["archive_commits_scanned"] = len(hashes)
    blobs, events, sessions, current = set(), set(), defaultdict(set), set()
    counts = defaultdict(lambda: {"events": 0, "user_prompts": 0, "assistant_messages": 0,
                                  "tool_events": 0})
    for index, commit in enumerate(hashes):
        result = git_ops.run_git(["rev-parse", f"{commit}:.ai-traces/interaction-stream.jsonl"], repo)
        if result.returncode:
            continue
        blob = result.stdout.strip()
        if blob in blobs:
            continue
        blobs.add(blob)
        result = git_ops.run_git(["cat-file", "blob", blob], repo, timeout=30)
        if result.returncode:
            raise ValueError("recorded stream cannot be read")
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
                if not _valid_event(event):
                    raise ValueError("not a valid event")
            except (ValueError, TypeError):
                report["malformed_records"] += 1
                continue
            adapter = event.get("adapter_name")
            if adapter not in {"codex", "claude", "cursor", "vscode_chat", "aider"}:
                continue
            session = str(event.get("session_id") or "unknown")
            if session != "unknown":
                sessions[adapter].add(session)
                if index == 0:
                    current.add((adapter, session))
            identity = str(event.get("event_id") or hashlib.sha256(line.encode()).hexdigest())
            identity = (adapter, identity)
            if identity in events:
                continue
            events.add(identity)
            counts[adapter]["events"] += 1
            kind = event.get("event_type")
            if kind == "user_prompt":
                counts[adapter]["user_prompts"] += 1
            if kind == "assistant_message":
                counts[adapter]["assistant_messages"] += 1
            if kind in {"tool_start", "tool_result"}:
                counts[adapter]["tool_events"] += 1
    report["stream_versions"] = len(blobs)
    report["current_stream_sessions"] = len(current)
    all_sessions = {(adapter, session) for adapter, values in sessions.items() for session in values}
    report["historical_sessions_not_in_current_stream"] = len(all_sessions - current)
    for adapter, values in counts.items():
        report["adapters"][adapter] = {**values, "sessions": len(sessions[adapter]),
            "prompt_coverage": "missing" if values["assistant_messages"] and not values["user_prompts"] else "observed" if values["user_prompts"] else "unknown"}
    report["health_record_present"] = git_ops.run_git(
        ["cat-file", "-e", f"{ref}:.ai-traces/capture-health.json"], repo).returncode == 0
    report["state"] = "records_found" if blobs else "no_interaction_stream"
    return report
