"""Tests for claude_normalize — schema translation only, no IO besides reading
the staged JSONL we set up.
"""
import json
from pathlib import Path

import pytest

from tests._capture import ai_traces
from tests._capture.agent_adapters import claude_normalize


def _write_session(repo: Path, sid: str, rows: list[dict]) -> Path:
    sessions_dir = ai_traces.raw_dir(repo, "claude", "sessions")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_normalize_user_prompt_and_assistant_text(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s1", [
        {"type": "user", "message": {"role": "user", "content": "Hello"},
         "cwd": "/repo", "sessionId": "s1", "timestamp": "2026-05-09T01:00:00Z",
         "gitBranch": "main"},
        {"type": "assistant",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "Hi back"}]},
         "cwd": "/repo", "sessionId": "s1", "timestamp": "2026-05-09T01:00:01Z"},
    ])

    built = claude_normalize.normalize_all(repo)
    assert len(built) == 1

    events = ai_traces.load_jsonl(built[0])
    types = [e["event_type"] for e in events]
    assert types == ["session_start", "user_prompt", "assistant_message", "session_end"]
    assert events[1]["text"] == "Hello"
    assert events[2]["text"] == "Hi back"
    # Turn numbering: session_start is "session", first user_prompt is turn-1.
    assert events[0]["turn_id"] == "session"
    assert events[1]["turn_id"] == "turn-1"
    assert events[2]["turn_id"] == "turn-1"


def test_normalize_drops_housekeeping_events(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s1", [
        {"type": "permission-mode", "permissionMode": "auto", "sessionId": "s1"},
        {"type": "ai-title", "aiTitle": "thing", "sessionId": "s1"},
        {"type": "file-history-snapshot", "messageId": "x"},
        {"type": "user", "message": {"role": "user", "content": "Hello"},
         "cwd": "/repo", "sessionId": "s1", "timestamp": "2026-05-09T01:00:00Z"},
        {"type": "system", "subtype": "turn_duration", "content": "",
         "cwd": "/repo", "sessionId": "s1", "timestamp": "2026-05-09T01:00:01Z"},
        {"type": "last-prompt", "lastPrompt": "Hello"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    types = [e["event_type"] for e in events]
    assert "permission-mode" not in types
    assert "ai-title" not in types
    assert "file-history-snapshot" not in types
    assert "last-prompt" not in types
    # turn_duration system events should be dropped too
    assert not any(e.get("extras", {}).get("subtype") == "turn_duration"
                   for e in events)


def test_normalize_tool_use_and_tool_result_pair(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s2", [
        {"type": "user", "message": {"role": "user", "content": "List files"},
         "cwd": "/repo", "sessionId": "s2", "timestamp": "2026-05-09T01:00:00Z"},
        {"type": "assistant",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_1", "name": "Bash",
              "input": {"command": "ls", "description": "list"}}
         ]},
         "cwd": "/repo", "sessionId": "s2", "timestamp": "2026-05-09T01:00:01Z"},
        {"type": "user",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_1",
              "content": "AGENTS.md\nREADME.md\n"}
         ]},
         "cwd": "/repo", "sessionId": "s2", "timestamp": "2026-05-09T01:00:02Z"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    types = [e["event_type"] for e in events]
    assert types == ["session_start", "user_prompt", "tool_start", "tool_result", "session_end"]
    tool_start = events[2]
    assert tool_start["tool_name"] == "Bash"
    assert tool_start["tool_use_id"] == "toolu_1"
    assert tool_start["command"] == "ls"
    tool_result = events[3]
    assert tool_result["tool_use_id"] == "toolu_1"
    assert tool_result["text"].startswith("AGENTS.md")
    # Tool-result events do NOT start a new turn.
    assert tool_result["turn_id"] == events[1]["turn_id"]


def test_normalize_slash_command_detected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s3", [
        {"type": "user",
         "message": {"role": "user",
                     "content": "<command-name>review</command-name>\n<args>PR123</args>"},
         "cwd": "/repo", "sessionId": "s3", "timestamp": "2026-05-09T01:00:00Z"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    cmd = next(e for e in events if e["event_type"] == "slash_command")
    assert cmd["extras"]["slash_command"] == "review"


def test_normalize_skill_invocation_tagged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s4", [
        {"type": "user", "message": {"role": "user", "content": "do init"},
         "cwd": "/repo", "sessionId": "s4", "timestamp": "2026-05-09T01:00:00Z"},
        {"type": "assistant",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "skl_1", "name": "Skill",
              "input": {"skill": "init"}}
         ]},
         "cwd": "/repo", "sessionId": "s4", "timestamp": "2026-05-09T01:00:01Z"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    skill_event = next(e for e in events if e.get("tool_name") == "Skill")
    assert skill_event["event_type"] == "tool_start"
    assert skill_event["extras"]["skill_name"] == "init"


def test_normalize_thinking_blocks_emitted_as_reasoning(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s5", [
        {"type": "user", "message": {"role": "user", "content": "Hi"},
         "cwd": "/repo", "sessionId": "s5", "timestamp": "2026-05-09T01:00:00Z"},
        {"type": "assistant",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "Let me consider...", "signature": "x"},
             {"type": "text", "text": "Hello"},
         ]},
         "cwd": "/repo", "sessionId": "s5", "timestamp": "2026-05-09T01:00:01Z"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    types = [e["event_type"] for e in events]
    assert "reasoning" in types
    reasoning = next(e for e in events if e["event_type"] == "reasoning")
    assert reasoning["text"] == "Let me consider..."


def test_normalize_session_start_carries_git_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s6", [
        {"type": "user", "message": {"role": "user", "content": "Hi"},
         "cwd": "/repo", "sessionId": "s6", "timestamp": "2026-05-09T01:00:00Z",
         "gitBranch": "feature/x"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    assert events[0]["event_type"] == "session_start"
    assert events[0]["extras"]["git_branch"] == "feature/x"


def test_normalize_unknown_type_kept_as_raw(tmp_path):
    """Lossless audit: unrecognized types are passed through."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(repo, "s7", [
        {"type": "user", "message": {"role": "user", "content": "Hi"},
         "cwd": "/repo", "sessionId": "s7", "timestamp": "2026-05-09T01:00:00Z"},
        {"type": "future-feature", "data": "novel",
         "cwd": "/repo", "sessionId": "s7", "timestamp": "2026-05-09T01:00:01Z"},
    ])

    events = ai_traces.load_jsonl(claude_normalize.normalize_all(repo)[0])
    types = [e["event_type"] for e in events]
    assert "raw_future-feature" in types
