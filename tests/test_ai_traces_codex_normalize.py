"""Tests for the shared AI trace helpers and Codex normalizer."""
import json
import shutil
from pathlib import Path

from tests._capture import ai_traces
from tests._capture.agent_adapters import codex_normalize

FIXTURES = Path(__file__).parent / "fixtures" / "codex"
REAL_FIXTURE = FIXTURES / "rollout-real-schema.jsonl"
LEGACY_FIXTURE = FIXTURES / "rollout-legacy-schema.jsonl"

REAL_PROMPT = "Please add a docstring to the add() helper in src/calculator.py."
REAL_REPLY = "I added a docstring to add() explaining the parameters and return value."


def _normalize_fixture(tmp_path, fixture):
    """Copy a checked-in rollout fixture into a repo and normalize it."""
    repo = tmp_path / "repo"
    dest = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / ("rollout-%s.jsonl" % fixture.stem)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(fixture), str(dest))
    codex_normalize.normalize_all(repo)
    return ai_traces.load_jsonl(repo / ".ai-traces" / "interaction-stream.jsonl")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_codex_rollout_normalizes_prompts_tools_and_file_edits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rollout = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / "rollout-one.jsonl"
    _write_jsonl(rollout, [
        {
            "timestamp": "2026-05-02T10:00:00Z",
            "type": "session_meta",
            "payload": {"id": "sess-one", "cwd": str(repo)},
        },
        {
            "timestamp": "2026-05-02T10:00:01Z",
            "type": "turn_context",
            "payload": {"turn_id": "turn-1", "cwd": str(repo)},
        },
        {
            "timestamp": "2026-05-02T10:00:02Z",
            "type": "event_msg",
            "payload": {"role": "user", "text": "write helper"},
        },
        {
            "timestamp": "2026-05-02T10:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call-1",
                "name": "apply_patch",
                "patch": "*** Update File: src/helper.py\n",
            },
        },
        {
            "timestamp": "2026-05-02T10:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "ok",
            },
        },
    ])

    built = codex_normalize.normalize_all(repo)
    assert built == [repo / ".ai-traces" / "codex" / "normalized" / "sess-one.jsonl"]
    events = ai_traces.load_jsonl(repo / ".ai-traces" / "interaction-stream.jsonl")
    event_types = [e["event_type"] for e in events]
    assert "user_prompt" in event_types
    assert "tool_result" in event_types
    tool = next(e for e in events if e["event_type"] == "tool_result")
    assert tool["files_touched"] == ["src/helper.py"]
    assert tool["adapter_name"] == "codex"
    assert tool["session_id"] == "sess-one"
    assert tool["evidence_refs"]


def test_codex_hook_logs_normalize_approvals_and_inferred_approval(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks = repo / ".ai-traces" / "codex" / "raw" / "hooks" / "hooks-sess.jsonl"
    _write_jsonl(hooks, [
        {
            "hook_event_name": "PermissionRequest",
            "payload": {
                "session_id": "sess",
                "request_id": "tool-1",
                "cwd": str(repo),
                "tool_name": "shell_command",
            },
        },
        {
            "hook_event_name": "PreToolUse",
            "captured_at": "2026-05-02T10:00:01Z",
            "payload": {
                "session_id": "sess",
                "tool_use_id": "tool-1",
                "cwd": str(repo),
                "tool_name": "shell_command",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Update File: src/app.py\n+print('x')\n*** End Patch\n",
                },
            },
        },
        {
            "hook_event_name": "PostToolUse",
            "captured_at": "2026-05-02T10:00:02Z",
            "payload": {
                "session_id": "sess",
                "tool_use_id": "tool-1",
                "cwd": str(repo),
                "tool_name": "shell_command",
                "tool_response": json.dumps({
                    "output": "Success. Updated the following files:\nM src/app.py\n",
                    "metadata": {"exit_code": 0},
                }),
            },
        },
    ])

    codex_normalize.normalize_all(repo)
    events = ai_traces.load_jsonl(repo / ".ai-traces" / "interaction-stream.jsonl")
    approval = next(e for e in events if e["event_type"] == "approval_request")
    assert approval["approval_outcome"] == "inferred_approved"
    result = next(e for e in events if e["event_type"] == "tool_result")
    assert result["exit_code"] == 0
    assert result["files_touched"] == ["src/app.py"]


def test_legacy_codex_transcripts_remain_readable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy = repo / ".codex-transcripts" / "rollout-old.jsonl"
    _write_jsonl(legacy, [
        {
            "type": "session_meta",
            "payload": {"id": "legacy-session", "cwd": str(repo)},
        },
        {
            "type": "event_msg",
            "payload": {"role": "user", "text": "old prompt"},
        },
    ])

    events = codex_normalize.normalize_legacy_codex_transcripts(repo)
    assert [e["event_type"] for e in events][:2] == ["session_start", "user_prompt"]
    assert events[0]["session_id"] == "legacy-session"


def test_real_schema_user_message_event_normalizes_to_user_prompt(tmp_path):
    events = _normalize_fixture(tmp_path, REAL_FIXTURE)
    prompts = [e for e in events if e["event_type"] == "user_prompt"]
    assert [e.get("text") for e in prompts] == [REAL_PROMPT]


def test_real_schema_prompt_in_both_encodings_is_not_double_counted(tmp_path):
    events = _normalize_fixture(tmp_path, REAL_FIXTURE)
    assert sum(1 for e in events if e["event_type"] == "user_prompt") == 1
    assistant = [e for e in events if e["event_type"] == "assistant_message"]
    assert [e.get("text") for e in assistant] == [REAL_REPLY]


def test_user_message_item_is_the_prompt_and_is_not_double_counted(tmp_path):
    """Current Codex CLIs record a typed prompt as an `item_completed`
    UserMessage (sometimes beside the older `user_message` event). It is
    the student's prompt; the response_item copy is a duplicate."""
    repo = tmp_path / "repo"
    rollout = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / "rollout-items.jsonl"

    def item(text):
        return {"type": "event_msg", "payload": {
            "type": "item_completed", "turn_id": "t1", "item": {
                "type": "UserMessage", "id": "u-" + text,
                "content": [{"type": "text", "text": text, "text_elements": []}]}}}

    _write_jsonl(rollout, [
        {"type": "session_meta", "payload": {"id": "sess-items", "cwd": str(repo)}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "first prompt"}]}},
        item("first prompt"),
        {"type": "event_msg", "payload": {"type": "user_message", "message": "second prompt"}},
        item("second prompt"),
        # A user-role message nobody typed is the tool's, not a prompt.
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "<subagent_notification>done"}]}},
    ])
    codex_normalize.normalize_all(repo)
    events = ai_traces.load_jsonl(repo / ".ai-traces" / "interaction-stream.jsonl")
    # Each prompt once, from its typed record (the redaction pass keeps a
    # prompt because it joins such a record).
    prompts = sorted((e["text"], e["extras"]["raw_type"])
                     for e in events if e["event_type"] == "user_prompt")
    assert prompts == [("first prompt", "event_msg"), ("second prompt", "event_msg")]
    system = [e["text"] for e in events if e["event_type"] == "system_message"]
    assert system == ["<subagent_notification>done"]


def test_real_schema_injected_context_is_not_a_student_prompt(tmp_path):
    events = _normalize_fixture(tmp_path, REAL_FIXTURE)
    for event in events:
        if event["event_type"] == "user_prompt":
            assert not str(event.get("text", "")).startswith("# AGENTS.md instructions")
    system_texts = [str(e.get("text", "")) for e in events if e["event_type"] == "system_message"]
    assert any(t.startswith("# AGENTS.md instructions") for t in system_texts)
    assert any(t.startswith("<permissions instructions>") for t in system_texts)


def test_real_schema_tool_calls_and_patches_are_captured(tmp_path):
    events = _normalize_fixture(tmp_path, REAL_FIXTURE)
    starts = [e for e in events if e["event_type"] == "tool_start"]
    assert {e.get("tool_name") for e in starts} >= {"exec", "apply_patch"}
    touched = set()
    for event in events:
        for path in event.get("files_touched") or []:
            touched.add(path)
    assert "src/calculator.py" in touched
    assert any(e["event_type"] == "reasoning" for e in events)


def test_real_schema_emits_no_unclassified_or_dropped_text(tmp_path):
    events = _normalize_fixture(tmp_path, REAL_FIXTURE)
    assert not [e for e in events if e["event_type"] == "unclassified_text"]


def test_legacy_schema_fixture_still_normalizes(tmp_path):
    events = _normalize_fixture(tmp_path, LEGACY_FIXTURE)
    types = [e["event_type"] for e in events]
    assert types.count("user_prompt") == 1
    assert types.count("assistant_message") == 1
    result = next(e for e in events if e["event_type"] == "tool_result")
    assert result["files_touched"] == ["src/calculator.py"]


def test_unknown_text_row_becomes_explicit_unclassified_text(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rollout = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / "rollout-odd.jsonl"
    _write_jsonl(rollout, [
        {"type": "session_meta", "payload": {"id": "sess-odd", "cwd": str(repo)}},
        {"type": "event_msg", "payload": {"type": "brand_new_event", "message": "mystery text"}},
    ])

    codex_normalize.normalize_all(repo)
    events = ai_traces.load_jsonl(repo / ".ai-traces" / "interaction-stream.jsonl")
    odd = next(e for e in events if e["event_type"] == "unclassified_text")
    assert odd["text"] == "mystery text"
    assert odd["extras"]["raw_payload"]["type"] == "brand_new_event"


def test_collect_hashes_includes_raw_normalized_and_stream(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / ".ai-traces" / "codex" / "normalized" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    (repo / ".ai-traces" / "interaction-stream.jsonl").write_text("{}\n", encoding="utf-8")

    hashes = ai_traces.collect_hashes(repo)
    assert {h["path"] for h in hashes} == {
        ".ai-traces/codex/normalized/s.jsonl",
        ".ai-traces/interaction-stream.jsonl",
    }
