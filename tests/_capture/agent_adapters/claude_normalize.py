"""Normalize Claude Code session JSONL files into the common AI interaction schema.

Claude Code persists each conversation as a single JSONL file at
``~/.claude/projects/<slug>/<session-uuid>.jsonl``. Each line is one event;
event ``type`` discriminates the union. We translate the rich Claude Code
schema into the same vendor-neutral event shape Codex uses, so the merged
``interaction-stream.jsonl`` can carry both transparently.

Filter list (events dropped from the normalized stream — pure housekeeping):
    - ai-title                  (auto-generated thread name)
    - permission-mode           (state change)
    - last-prompt               (trailing summary record)
    - file-history-snapshot     (internal undo state)
    - system w/ subtype turn_duration

Kept events (mapped to common schema):
    - user (string content)         -> user_prompt OR slash_command
    - user (array w/ tool_result)   -> tool_result
    - assistant (text/thinking)     -> assistant_message + reasoning
    - assistant (tool_use)          -> tool_start
    - attachment                    -> attachment
    - system (substantive subtype)  -> system_message
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests._capture import ai_traces


ADAPTER = "claude"
SESSION_GLOB = "*.jsonl"

_DROP_TYPES = {"ai-title", "permission-mode", "last-prompt", "file-history-snapshot"}
_DROP_SYSTEM_SUBTYPES = {"turn_duration"}

_SLASH_CMD_RE = re.compile(r"<command-name>([^<]+)</command-name>", re.DOTALL)


def normalize_all(repo: Path) -> List[Path]:
    built: List[Path] = []
    sessions_root = ai_traces.raw_dir(repo, ADAPTER, "sessions")
    if not sessions_root.exists():
        return built
    for session in sorted(sessions_root.glob(SESSION_GLOB)):
        events = normalize_session(repo, session)
        if not events:
            continue
        session_id = str(events[0].get("session_id") or session.stem)
        out = ai_traces.normalized_dir(repo, ADAPTER) / f"{session_id}.jsonl"
        ai_traces.write_jsonl(out, events)
        built.append(out)
    if built:
        ai_traces.rebuild_interaction_stream(repo)
    return built


def normalize_session(repo: Path, session: Path) -> List[dict]:
    rows = ai_traces.load_jsonl(session)
    if not rows:
        return []

    rel = ai_traces.repo_rel(repo, session)
    session_id = session.stem
    cwd = ""
    git_branch = ""
    turn_num = 0
    turn_id = "turn-0"
    events: List[dict] = []
    started = False

    for idx, row in enumerate(rows):
        typ = str(row.get("type", ""))
        if typ in _DROP_TYPES:
            continue

        sid = row.get("sessionId")
        if isinstance(sid, str) and sid:
            session_id = sid
        ev_cwd = row.get("cwd")
        if isinstance(ev_cwd, str) and ev_cwd:
            cwd = ev_cwd
        ev_gb = row.get("gitBranch")
        if isinstance(ev_gb, str) and ev_gb:
            git_branch = ev_gb
        ts = str(row.get("timestamp") or "")
        evidence_ref = f"{rel}:{idx + 1}"

        if not started and cwd:
            extras: Dict[str, Any] = {"raw_path": rel}
            if git_branch:
                extras["git_branch"] = git_branch
            events.append(_event(
                session_id=session_id,
                turn_id="session",
                event_type="session_start",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                extras=extras,
            ))
            started = True

        if typ == "user":
            new_turn, user_events = _normalize_user_events(
                row, session_id, turn_id, cwd, evidence_ref=evidence_ref, ts=ts,
            )
            if new_turn:
                turn_num += 1
                turn_id = f"turn-{turn_num}"
                for ev in user_events:
                    ev["turn_id"] = turn_id
            events.extend(user_events)
            continue

        if typ == "assistant":
            events.extend(_normalize_assistant_events(
                row, session_id, turn_id, cwd, evidence_ref=evidence_ref, ts=ts,
            ))
            continue

        if typ == "system":
            subtype = str(row.get("subtype") or "")
            if subtype in _DROP_SYSTEM_SUBTYPES:
                continue
            content = row.get("content")
            text = content if isinstance(content, str) else None
            events.append(_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="system_message",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                text=text,
                extras={"raw_type": typ, "subtype": subtype, "raw_payload": _safe_payload(row)},
            ))
            continue

        if typ == "attachment":
            events.append(_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="attachment",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                extras={"raw_type": typ, "attachment": row.get("attachment")},
            ))
            continue

        # Unknown type — keep so the audit trail is lossless.
        events.append(_event(
            session_id=session_id,
            turn_id=turn_id,
            event_type=f"raw_{typ}" if typ else "raw_event",
            cwd=cwd,
            evidence_refs=[evidence_ref],
            ts=ts,
            extras={"raw_type": typ, "raw_payload": _safe_payload(row)},
        ))

    if events:
        events.append(_event(
            session_id=session_id,
            turn_id=turn_id,
            event_type="session_end",
            cwd=cwd,
            evidence_refs=[rel],
            ts=events[-1].get("ts"),
            extras={"raw_path": rel},
        ))
    return events


def _normalize_user_events(
    row: Dict[str, Any], session_id: str, turn_id: str, cwd: str,
    *, evidence_ref: str, ts: str,
) -> tuple[bool, List[dict]]:
    """Return (is_new_turn, events). String content starts a new turn;
    array content (tool results) does not.
    """
    msg = row.get("message")
    if not isinstance(msg, dict):
        return False, []
    content = msg.get("content")
    extras_base: Dict[str, Any] = {"raw_type": "user"}
    if "promptId" in row:
        extras_base["promptId"] = row.get("promptId")

    if isinstance(content, str):
        slash = _SLASH_CMD_RE.search(content)
        extras = dict(extras_base)
        event_type = "user_prompt"
        if slash:
            event_type = "slash_command"
            extras["slash_command"] = slash.group(1).strip()
        return True, [_event(
            session_id=session_id,
            turn_id=turn_id,
            event_type=event_type,
            cwd=cwd,
            evidence_refs=[evidence_ref],
            ts=ts,
            text=content,
            extras=extras,
        )]

    if isinstance(content, list):
        out: List[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            extras = dict(extras_base)
            if "is_error" in block:
                extras["is_error"] = block.get("is_error")
            block_content = block.get("content")
            text = block_content if isinstance(block_content, str) else _flatten_blocks(block_content)
            tool_use_id = block.get("tool_use_id")
            out.append(_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="tool_result",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
                text=text,
                extras=extras,
            ))
        return False, out
    return False, []


def _normalize_assistant_events(
    row: Dict[str, Any], session_id: str, turn_id: str, cwd: str,
    *, evidence_ref: str, ts: str,
) -> List[dict]:
    msg = row.get("message")
    if not isinstance(msg, dict):
        return []
    blocks = msg.get("content")
    if not isinstance(blocks, list):
        return []
    out: List[dict] = []
    text_parts: List[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text")
            if isinstance(t, str):
                text_parts.append(t)
        elif btype == "thinking":
            t = block.get("thinking")
            if isinstance(t, str):
                out.append(_event(
                    session_id=session_id,
                    turn_id=turn_id,
                    event_type="reasoning",
                    cwd=cwd,
                    evidence_refs=[evidence_ref],
                    ts=ts,
                    text=t,
                    extras={"raw_type": "assistant_thinking"},
                ))
        elif btype == "tool_use":
            tool_name = block.get("name")
            tool_input = block.get("input")
            tool_id = block.get("id")
            command: Optional[str] = None
            if isinstance(tool_input, dict):
                cmd = tool_input.get("command") or tool_input.get("cmd")
                if isinstance(cmd, str):
                    command = cmd
            extras: Dict[str, Any] = {"raw_type": "assistant_tool_use"}
            if isinstance(tool_input, (dict, list, str, int, float, bool)) or tool_input is None:
                extras["tool_input"] = tool_input
            if tool_name == "Skill" and isinstance(tool_input, dict):
                skill = tool_input.get("skill")
                if isinstance(skill, str):
                    extras["skill_name"] = skill
            out.append(_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="tool_start",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                tool_name=tool_name if isinstance(tool_name, str) else None,
                tool_use_id=tool_id if isinstance(tool_id, str) else None,
                command=command,
                extras=extras,
            ))
    if text_parts:
        out.insert(0, _event(
            session_id=session_id,
            turn_id=turn_id,
            event_type="assistant_message",
            cwd=cwd,
            evidence_refs=[evidence_ref],
            ts=ts,
            text="\n".join(text_parts),
            extras={"raw_type": "assistant_text"},
        ))
    return out


def _flatten_blocks(content: Any) -> Optional[str]:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text") or item.get("content")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    return None


def _safe_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the heavyweight ``message`` field from raw payload extras to
    keep the normalized event size bounded; the original line is still
    referenced via evidence_refs."""
    return {k: v for k, v in row.items() if k != "message"}


def _event(**kwargs: Any) -> dict:
    return ai_traces.make_event(adapter_name=ADAPTER, **kwargs)
