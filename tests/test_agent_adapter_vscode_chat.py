"""Tests for VSCodeChatAdapter — the raw-only adapter glue."""
import json
from pathlib import Path

import pytest

from tests._capture.agent_adapters.vscode_chat import VSCodeChatAdapter


@pytest.fixture
def fake_vscode_home(tmp_path, monkeypatch):
    user_dir = tmp_path / "vscode_user"
    (user_dir / "workspaceStorage").mkdir(parents=True)
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(user_dir))
    return user_dir


def _make_workspace_with_session(user_dir: Path, ws_id: str, repo: Path,
                                  sid: str = "s1") -> None:
    ws = user_dir / "workspaceStorage" / ws_id
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text(
        json.dumps({"folder": "file://" + str(repo)}), encoding="utf-8",
    )
    chat = ws / "chatSessions"
    chat.mkdir()
    (chat / f"{sid}.jsonl").write_text(
        '{"kind":0,"v":{"sessionId":"' + sid + '","requests":[]}}\n',
        encoding="utf-8",
    )


def test_adapter_name_and_transcripts_dir():
    adapter = VSCodeChatAdapter()
    assert adapter.name == "vscode_chat"
    assert adapter.transcripts_dir == ".ai-traces"


def test_adapter_is_present_when_user_dir_exists(fake_vscode_home):
    assert VSCodeChatAdapter().is_present() is True


def test_adapter_is_present_false_when_no_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(tmp_path / "missing"))
    assert VSCodeChatAdapter().is_present() is False


def test_adapter_is_present_for_repo_when_evidence_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(tmp_path / "missing"))
    repo = tmp_path / "repo"
    (repo / ".ai-traces" / "vscode_chat").mkdir(parents=True)
    assert VSCodeChatAdapter().is_present_for_repo(repo) is True


def test_adapter_ingest_returns_empty_when_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(tmp_path / "missing"))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = VSCodeChatAdapter().ingest(repo, since=None)
    assert result.adapter_name == "vscode_chat"
    assert result.rollouts_copied == []
    assert result.summaries_built == []


def test_adapter_ingest_copies_raw_only(fake_vscode_home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_workspace_with_session(fake_vscode_home, "ws", repo, sid="alpha")

    result = VSCodeChatAdapter().ingest(repo, since=None)

    assert result.adapter_name == "vscode_chat"
    assert len(result.rollouts_copied) == 1
    # Raw-only: no normalized output and no contribution to interaction-stream.
    assert result.summaries_built == []
    assert (repo / ".ai-traces" / "vscode_chat" / "raw" / "sessions" / "alpha.jsonl").exists()
    assert not (repo / ".ai-traces" / "vscode_chat" / "normalized").exists()


def test_adapter_does_not_create_interaction_stream_entries(fake_vscode_home, tmp_path):
    """Capture is raw-only: nothing flows into interaction-stream.jsonl."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_workspace_with_session(fake_vscode_home, "ws", repo)

    VSCodeChatAdapter().ingest(repo, since=None)

    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    # The adapter does not call rebuild_interaction_stream; either the file
    # doesn't exist or it's empty (depending on whether other adapters ran).
    if stream.exists():
        assert stream.read_text(encoding="utf-8") == ""


def test_adapter_ingest_swallows_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(tmp_path / "u"))
    (tmp_path / "u" / "workspaceStorage").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(
        "tests._capture.vscode_chat_ingest.ingest_transcripts", boom,
    )
    result = VSCodeChatAdapter().ingest(repo, since=None)
    assert result.adapter_name == "vscode_chat"
    assert result.errors and "synthetic failure" in result.errors[0]


def test_adapter_detect_active_session_returns_none():
    assert VSCodeChatAdapter().detect_active_session() is None


def test_adapter_stage_paths():
    adapter = VSCodeChatAdapter()
    assert adapter.stage_paths() == [".ai-traces"]
    assert adapter.force_paths() == [".ai-traces"]
    assert adapter.unstage_after() == []
