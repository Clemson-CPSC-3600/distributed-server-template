"""Tests for ClaudeAdapter — the adapter glue. Mirrors test_agent_adapter_codex
in style and coverage."""
import json
import os
import time
from pathlib import Path

import pytest

from tests._capture.agent_adapters.claude import ClaudeAdapter


def _write_session(home: Path, sid: str, cwd: str) -> Path:
    slug = cwd.replace("/", "-")
    project_dir = home / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{sid}.jsonl"
    rows = [
        {"type": "permission-mode", "permissionMode": "auto", "sessionId": sid},
        {"type": "user", "message": {"role": "user", "content": "Hi"},
         "cwd": cwd, "sessionId": sid,
         "timestamp": "2026-05-09T01:00:00Z"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def fake_claude_home(tmp_path, monkeypatch):
    home = tmp_path / "claude_home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def test_claude_adapter_name_and_transcripts_dir():
    adapter = ClaudeAdapter()
    assert adapter.name == "claude"
    assert adapter.transcripts_dir == ".ai-traces"


def test_claude_adapter_is_present_when_projects_dir_exists(fake_claude_home):
    assert ClaudeAdapter().is_present() is True


def test_claude_adapter_is_present_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
    assert ClaudeAdapter().is_present() is False


def test_claude_adapter_is_present_for_repo_when_evidence_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
    repo = tmp_path / "repo"
    (repo / ".ai-traces" / "claude").mkdir(parents=True)
    assert ClaudeAdapter().is_present_for_repo(repo) is True


def test_claude_adapter_ingest_returns_empty_when_nothing_to_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "missing"))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = ClaudeAdapter().ingest(repo, since=None)
    assert result.adapter_name == "claude"
    assert result.rollouts_copied == []
    assert result.summaries_built == []


def test_claude_adapter_ingest_copies_and_normalizes(fake_claude_home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(fake_claude_home, sid="alpha", cwd=str(repo))

    result = ClaudeAdapter().ingest(repo, since=None)

    assert result.adapter_name == "claude"
    assert len(result.rollouts_copied) == 1
    assert len(result.summaries_built) == 1
    assert (repo / ".ai-traces" / "claude" / "raw" / "sessions" / "alpha.jsonl").exists()
    assert (repo / ".ai-traces" / "claude" / "normalized" / "alpha.jsonl").exists()
    assert (repo / ".ai-traces" / "interaction-stream.jsonl").exists()
    assert (repo / ".ai-traces" / "external-attestation.txt").exists()


def test_claude_adapter_ingest_swallows_exceptions(tmp_path, monkeypatch):
    """Capture must never propagate to the orchestrator."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude_home"))
    (tmp_path / "claude_home" / "projects").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(
        "tests._capture.claude_ingest.ingest_transcripts", boom,
    )
    result = ClaudeAdapter().ingest(repo, since=None)
    assert result.adapter_name == "claude"
    assert result.errors and "synthetic failure" in result.errors[0]


def test_claude_adapter_detect_active_session_returns_none():
    assert ClaudeAdapter().detect_active_session() is None


def test_claude_adapter_stage_paths():
    adapter = ClaudeAdapter()
    assert adapter.stage_paths() == [".ai-traces"]
    assert adapter.force_paths() == [".ai-traces"]
    assert adapter.unstage_after() == []
