"""Normalize Codex raw artifacts into the common AI interaction schema."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tests._capture import ai_traces


ADAPTER = "codex"
LEGACY_TRANSCRIPTS_DIR = ".codex-transcripts"
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$")

# ``event_msg`` payload kinds that carry no student/model authored text. They
# are session bookkeeping only; ``task_complete`` repeats the final
# ``agent_message`` verbatim, so emitting it would double-count the reply.
_LIFECYCLE_EVENT_KINDS = frozenset({
    "task_started",
    "task_complete",
    "token_count",
    "thread_settings_applied",
    "turn_aborted",
    "thread_rolled_back",
})
_TOOL_START_EVENT_KINDS = frozenset({
    "exec_command_begin",
    "patch_apply_begin",
    "mcp_tool_call_begin",
    "web_search_begin",
})
_TOOL_RESULT_EVENT_KINDS = frozenset({
    "exec_command_end",
    "patch_apply_end",
    "mcp_tool_call_end",
    "web_search_end",
    "tool_result",
})
# ``response_item`` messages with ``role == "user"`` are also used by the CLI
# to inject machine-generated context (AGENTS.md bodies, environment blocks,
# plugin listings). Those must never be counted as student prompts.
_INJECTED_PREFIXES = (
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
    "<app-context>",
    "<environment_context>",
    "<permissions instructions>",
    "<plugins>",
    "<recommended_plugins>",
    "<user_instructions>",
)


def normalize_all(repo: Path) -> List[Path]:
    built: List[Path] = []
    rollout_root = ai_traces.raw_dir(repo, ADAPTER, "rollouts")
    for rollout in sorted(rollout_root.glob("rollout-*.jsonl")):
        events = normalize_rollout(repo, rollout)
        if not events:
            continue
        session_id = str(events[0].get("session_id", rollout.stem))
        out = ai_traces.normalized_dir(repo, ADAPTER) / f"{session_id}.jsonl"
        ai_traces.write_jsonl(out, events)
        built.append(out)

    hook_events = normalize_hook_logs(repo)
    if hook_events:
        out = ai_traces.normalized_dir(repo, ADAPTER) / "hooks.jsonl"
        ai_traces.write_jsonl(out, hook_events)
        built.append(out)

    if built:
        ai_traces.rebuild_interaction_stream(repo)
    return built


def normalize_legacy_codex_transcripts(repo: Path) -> List[dict]:
    events: List[dict] = []
    for rollout in sorted((repo / LEGACY_TRANSCRIPTS_DIR).glob("rollout-*.jsonl")):
        events.extend(normalize_rollout(repo, rollout))
    return events


def normalize_rollout(repo: Path, rollout: Path) -> List[dict]:
    rows = _load_jsonl(rollout)
    if not rows:
        return []

    rel = ai_traces.repo_rel(repo, rollout)
    session_id = rollout.stem
    cwd = ""
    events: List[dict] = []
    turn_id = "turn-0"
    turn_num = 0
    pending_tool_files: Dict[str, List[str]] = {}
    prompt_texts, assistant_texts, message_event_texts = _event_msg_texts(rows)

    for idx, row in enumerate(rows):
        typ = str(row.get("type", ""))
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        ts = _timestamp(row, payload)

        if typ == "session_meta":
            # A fork child copies its parent's session_meta after its own;
            # every row of the file is still the file's own session.
            if not events:
                session_id = str(payload.get("id") or session_id)
            cwd = str(payload.get("cwd") or cwd)
            events.append(_event(
                session_id=session_id,
                turn_id="session",
                event_type="session_start",
                cwd=cwd,
                evidence_refs=[f"{rel}:{idx + 1}"],
                ts=ts,
                extras={"raw_type": typ, "raw_payload": payload},
            ))
            continue

        if typ == "turn_context":
            turn_num += 1
            turn_id = str(payload.get("turn_id") or f"turn-{turn_num}")
            cwd = str(payload.get("cwd") or cwd)
            events.append(_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="turn_start",
                cwd=cwd,
                evidence_refs=[f"{rel}:{idx + 1}"],
                ts=ts,
                extras={"raw_type": typ, "raw_payload": payload},
            ))
            continue

        event = _normalize_known_row(
            typ=typ,
            payload=payload,
            row=row,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            evidence_ref=f"{rel}:{idx + 1}",
            ts=ts,
            prompt_texts=prompt_texts,
            assistant_texts=assistant_texts,
            message_event_texts=message_event_texts,
        )
        if event:
            tool_use_id = str(event.get("tool_use_id") or "")
            if event.get("event_type") == "tool_start" and tool_use_id:
                files = event.get("files_touched") or []
                if isinstance(files, list) and files:
                    pending_tool_files[tool_use_id] = [str(f) for f in files]
            elif event.get("event_type") == "tool_result" and tool_use_id:
                if not event.get("files_touched") and tool_use_id in pending_tool_files:
                    event["files_touched"] = pending_tool_files[tool_use_id]
            events.append(event)

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


def normalize_hook_logs(repo: Path) -> List[dict]:
    events: List[dict] = []
    root = ai_traces.raw_dir(repo, ADAPTER, "hooks")
    seen_pre_tool: set[str] = set()
    pending_tool_files: Dict[str, List[str]] = {}
    pending_permissions: List[dict] = []
    for log in sorted(root.glob("hooks-*.jsonl")):
        rel = ai_traces.repo_rel(repo, log)
        for idx, row in enumerate(_load_jsonl(log)):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            hook_name = str(row.get("hook_event_name") or row.get("event_name") or "")
            session_id = str(payload.get("session_id") or row.get("session_id") or log.stem)
            turn_id = str(payload.get("turn_id") or payload.get("request_id") or "hook")
            tool_name = _first_str(payload, "tool_name", "tool")
            tool_use_id = _first_str(payload, "tool_use_id", "call_id", "request_id")
            cwd = str(payload.get("cwd") or payload.get("working_dir") or "")
            command = _extract_command(payload)
            files = _extract_files(payload)
            event_type = _hook_event_type(hook_name)

            if event_type == "tool_start" and tool_use_id:
                seen_pre_tool.add(tool_use_id)
                if files:
                    pending_tool_files[tool_use_id] = files
            approval = _approval_outcome(payload, event_type)
            event = _event(
                session_id=session_id,
                turn_id=turn_id,
                event_type=event_type,
                cwd=cwd,
                evidence_refs=[f"{rel}:{idx + 1}"],
                ts=str(row.get("ts") or row.get("captured_at") or payload.get("timestamp") or ""),
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                command=command,
                exit_code=_extract_exit_code(payload),
                text=_first_str(payload, "prompt", "user_prompt", "message", "content"),
                files_touched=files or None,
                approval_outcome=approval,
                extras={"hook_event_name": hook_name, "raw_payload": payload},
            )
            if event_type == "approval_request":
                pending_permissions.append(event)
            elif event_type == "tool_result" and tool_use_id:
                if not event.get("files_touched") and tool_use_id in pending_tool_files:
                    event["files_touched"] = pending_tool_files[tool_use_id]
            events.append(event)

    for event in pending_permissions:
        if event.get("approval_outcome"):
            continue
        tool_use_id = str(event.get("tool_use_id") or "")
        event["approval_outcome"] = (
            "inferred_approved" if tool_use_id and tool_use_id in seen_pre_tool else "unknown"
        )
    return events


def _event_msg_texts(rows: Iterable[Dict[str, Any]]) -> tuple:
    """Collect the authoritative prompt/reply texts from ``event_msg`` rows.

    A real Codex rollout encodes every human turn more than once: as an
    ``event_msg`` with ``payload.type == "user_message"`` and/or (Codex
    0.140 and later) an ``item_completed`` event holding a ``UserMessage``
    item, and again as a ``response_item`` message with ``payload.role ==
    "user"``. The ``event_msg`` copies are authoritative (they only ever
    exist for genuine turns), so they are collected up-front and the
    duplicate ``response_item`` copy is dropped later. The redaction pass
    relies on the same fact: a message item is kept only when it joins one
    of these typed records.

    Returns (every typed prompt, every reply, the ``user_message`` prompts
    alone -- a UserMessage item repeating one of those is a duplicate).
    """
    prompts: set = set()
    replies: set = set()
    message_events: set = set()
    for row in rows:
        if str(row.get("type", "")) != "event_msg":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("type") or "")
        item_text = _user_message_item_text(payload)
        if item_text:
            prompts.add(item_text.strip())
            continue
        text = _extract_text(payload)
        if not text:
            continue
        if kind == "user_message":
            prompts.add(text.strip())
            message_events.add(text.strip())
        elif kind == "agent_message":
            replies.add(text.strip())
    return prompts, replies, message_events


def _user_message_item_text(payload: Dict[str, Any]) -> Optional[str]:
    """The text of an ``item_completed`` UserMessage (a typed prompt)."""
    item = payload.get("item")
    if payload.get("type") != "item_completed" or not isinstance(item, dict):
        return None
    if item.get("type") != "UserMessage":
        return None
    return _extract_text(item)


def _is_injected_context(text: Optional[str]) -> bool:
    stripped = (text or "").lstrip()
    return any(stripped.startswith(prefix) for prefix in _INJECTED_PREFIXES)


def _normalize_known_row(
    *,
    typ: str,
    payload: Dict[str, Any],
    row: Dict[str, Any],
    session_id: str,
    turn_id: str,
    cwd: str,
    evidence_ref: str,
    ts: str,
    prompt_texts: Optional[set] = None,
    assistant_texts: Optional[set] = None,
    message_event_texts: Optional[set] = None,
) -> Optional[dict]:
    prompt_texts = prompt_texts or set()
    assistant_texts = assistant_texts or set()
    message_event_texts = message_event_texts or set()
    role = str(payload.get("role") or row.get("role") or "")
    kind = str(payload.get("type") or row.get("kind") or "")
    text = _extract_text(payload)
    extras = {"raw_type": typ, "raw_payload": payload}

    def simple(event_type: str, **fields: Any) -> dict:
        return _event(session_id=session_id, turn_id=turn_id, event_type=event_type,
                      cwd=cwd, evidence_refs=[evidence_ref], ts=ts, text=text,
                      extras=extras, **fields)

    if typ == "event_msg":
        # Real rollouts key event_msg on payload.type and carry NO role field;
        # pre-0.140 template fixtures used a role field instead. Support both.
        if kind == "user_message" or (not kind and role == "user"):
            return simple("user_prompt")
        item_text = _user_message_item_text(payload)
        if item_text is not None:
            if item_text.strip() in message_event_texts:
                return None  # the same prompt, already a user_message
            return _event(session_id=session_id, turn_id=turn_id,
                          event_type="user_prompt", cwd=cwd,
                          evidence_refs=[evidence_ref], ts=ts,
                          text=item_text, extras=extras)
        if kind == "agent_message" or (not kind and role in {"assistant", "agent"}):
            return simple("assistant_message")
        if kind == "agent_reasoning":
            return simple("reasoning")
        if kind in _LIFECYCLE_EVENT_KINDS:
            return None
        if kind in _TOOL_START_EVENT_KINDS or kind in _TOOL_RESULT_EVENT_KINDS:
            event_type = "tool_start" if kind in _TOOL_START_EVENT_KINDS else "tool_result"
            return simple(
                event_type,
                tool_name=_first_str(payload, "tool_name", "tool") or kind,
                tool_use_id=_first_str(payload, "call_id", "tool_use_id"),
                command=_extract_command(payload),
                exit_code=_extract_exit_code(payload),
                files_touched=_extract_files(payload) or None,
            )

    if typ == "response_item":
        item_type = str(payload.get("type") or payload.get("item_type") or "")
        if item_type in {"message", "assistant_message"}:
            if role == "assistant" or item_type == "assistant_message":
                if text and text.strip() in assistant_texts:
                    return None  # duplicate of the event_msg agent_message
                return simple("assistant_message")
            if role == "developer" or _is_injected_context(text):
                return simple("system_message")
            if role == "user":
                if text and text.strip() in prompt_texts:
                    return None  # duplicate of the event_msg user_message
                if prompt_texts:
                    # The rollout records what the student typed, and this
                    # is not it: the tool wrote it (a sub-agent notice, an
                    # approval reviewer's transcript copy, ...).
                    return simple("system_message")
                return simple("user_prompt")
            return simple("unclassified_text") if text else None
        if item_type == "reasoning":
            return simple("reasoning")
        if item_type == "custom_tool_call":
            return simple(
                "tool_start",
                tool_name=_first_str(payload, "name", "tool_name", "tool"),
                tool_use_id=_first_str(payload, "call_id", "id", "tool_use_id"),
                command=_extract_command(payload),
                files_touched=_extract_files(payload) or None,
            )
        if item_type in {"custom_tool_call_output", "local_shell_call_output"}:
            return simple(
                "tool_result",
                tool_name=_first_str(payload, "name", "tool_name", "tool"),
                tool_use_id=_first_str(payload, "call_id", "tool_use_id"),
                exit_code=_extract_exit_code(payload),
                files_touched=_extract_files(payload) or None,
            )
        if item_type in {"function_call", "tool_call", "local_shell_call"}:
            return _event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="tool_start",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                tool_name=_first_str(payload, "name", "tool_name", "tool"),
                tool_use_id=_first_str(payload, "call_id", "id", "tool_use_id"),
                command=_extract_command(payload),
                files_touched=_extract_files(payload) or None,
                extras=extras,
            )
        if item_type in {"function_call_output", "tool_result"}:
            return _event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="tool_result",
                cwd=cwd,
                evidence_refs=[evidence_ref],
                ts=ts,
                tool_name=_first_str(payload, "name", "tool_name", "tool"),
                tool_use_id=_first_str(payload, "call_id", "id", "tool_use_id"),
                exit_code=_extract_exit_code(payload),
                text=text,
                files_touched=_extract_files(payload) or None,
                extras=extras,
            )

    # Anything that carries text but does not match a known shape is emitted
    # explicitly rather than guessed at or dropped: an unrecognized schema must
    # stay visible in the trace instead of silently vanishing.
    if text:
        return _event(session_id=session_id, turn_id=turn_id, event_type="unclassified_text",
                      cwd=cwd, evidence_refs=[evidence_ref], ts=ts, text=text, extras=extras)
    return None


def _event(**kwargs: Any) -> dict:
    return ai_traces.make_event(adapter_name=ADAPTER, **kwargs)


def _load_jsonl(path: Path) -> List[dict]:
    return ai_traces.load_jsonl(path)


def _timestamp(row: Dict[str, Any], payload: Dict[str, Any]) -> str:
    return str(row.get("ts") or row.get("timestamp") or payload.get("timestamp") or "")


def _extract_text(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("text", "message", "content", "output"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                if isinstance(item, dict):
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts)
    return None


def _extract_command(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("command", "cmd"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
    args = _as_mapping(payload.get("arguments"))
    if isinstance(args, dict):
        value = args.get("command") or args.get("cmd")
        if isinstance(value, str):
            return value
    # custom_tool_call carries the invocation as a script string in `input`.
    value = payload.get("input")
    if isinstance(value, str):
        return value
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        value = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("patch")
        if isinstance(value, str):
            return value
    return None


def _extract_files(payload: Dict[str, Any]) -> List[str]:
    files: List[str] = []
    for key in ("files_touched", "files", "paths"):
        value = payload.get(key)
        if isinstance(value, list):
            files.extend(str(v) for v in value if isinstance(v, (str, Path)))
        elif isinstance(value, str):
            files.append(value)
    for text_key in ("patch", "input", "content", "text"):
        value = payload.get(text_key)
        if not isinstance(value, str):
            continue
        for line in value.splitlines():
            m = _PATCH_FILE_RE.match(line.strip())
            if m:
                files.append(m.group(1).strip())
    # patch_apply_begin/end report edits as a {path: {...}} changes mapping.
    changes = payload.get("changes")
    if isinstance(changes, dict):
        files.extend(str(k) for k in changes)
    for nested_key in ("arguments", "tool_input"):
        nested = _as_mapping(payload.get(nested_key))
        if not isinstance(nested, dict):
            continue
        for text_key in ("patch", "command", "cmd", "input", "content", "text"):
            value = nested.get(text_key)
            if not isinstance(value, str):
                continue
            for line in value.splitlines():
                m = _PATCH_FILE_RE.match(line.strip())
                if m:
                    files.append(m.group(1).strip())
    return sorted(set(files))


def _as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """Return a dict for a mapping or for a JSON object encoded as a string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_exit_code(payload: Dict[str, Any]) -> Any:
    direct = _first_value(payload, "exit_code", "status_code")
    if direct is not None:
        return direct
    success = payload.get("success")
    if isinstance(success, bool):
        return 0 if success else 1
    response = payload.get("tool_response")
    if isinstance(response, dict):
        metadata = response.get("metadata")
        if isinstance(metadata, dict) and "exit_code" in metadata:
            return metadata["exit_code"]
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if isinstance(parsed, dict):
            metadata = parsed.get("metadata")
            if isinstance(metadata, dict) and "exit_code" in metadata:
                return metadata["exit_code"]
    return None


def _hook_event_type(hook_name: str) -> str:
    return {
        "SessionStart": "session_start",
        "UserPromptSubmit": "user_prompt",
        "PreToolUse": "tool_start",
        "PermissionRequest": "approval_request",
        "PostToolUse": "tool_result",
        "Stop": "turn_end",
    }.get(hook_name, f"hook_{hook_name}" if hook_name else "hook_event")


def _approval_outcome(payload: Dict[str, Any], event_type: str) -> Optional[str]:
    if event_type != "approval_request":
        return None
    value = _first_str(payload, "approval_outcome", "outcome", "decision")
    return value


def _first_value(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _first_str(payload: Dict[str, Any], *keys: str) -> Optional[str]:
    value = _first_value(payload, *keys)
    return value if isinstance(value, str) else None
