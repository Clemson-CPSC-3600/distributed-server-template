"""Malformed generated conversation tiers must fail closed before real staging."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests._capture import git_ops, orchestrator, redaction
from tests._capture.agent_adapters.codex import CodexAdapter
from tests._capture.agent_adapters.registry import installed_adapters


CANARY = "SYNTHETIC_PRIVATE_CANARY"
VALID = json.dumps({"adapter_name": "codex", "event_type": "user_prompt",
                    "session_id": "synthetic", "text": "synthetic request"}).encode() + b"\n"
BAD = ('{"event_type":"tool_result","text":"' + CANARY).encode()


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    codex = tmp_path / "codex"
    (codex / "sessions").mkdir(parents=True)
    for key, value in {
        "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(codex),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "VSCODE_CONFIG_DIR": str(tmp_path / "vscode"),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "CAPTURE_DISABLED": "0",
        "_CAPTURE_SUBPROCESS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic malformed test")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    (repo / "src").mkdir()
    (repo / "src/main.py").write_text("pass\n")
    (repo / ".ai-traces/codex").mkdir(parents=True)
    (repo / "project-template-config.json").write_text(json.dumps({
        "capture_enabled": True,
        "agent_adapters": {name: {"enabled": False}
                           for name in ("claude", "vscode_chat", "aider")},
    }))
    assert [type(adapter) for adapter in installed_adapters(repo)] == [CodexAdapter]
    monkeypatch.setattr(git_ops, "push_auto_track_background", lambda *_: None)
    return repo


def snapshot(repo, session):
    sha = orchestrator.take_snapshot(repo, trigger="pytest",
                                     pytest_session_id=session, force=True)
    assert sha
    health = json.loads(git(repo, "show", sha + ":.ai-traces/capture-health.json"))
    return sha, health


@pytest.mark.parametrize("tier", ["codex/normalized/interrupted.jsonl", "interaction-stream.jsonl"])
@pytest.mark.parametrize("payload", [BAD, VALID + BAD, b"[]\n", VALID + b"null\n",
                                    b'"' + CANARY.encode() + b'"\n',
                                    VALID + b"\xff" + CANARY.encode()])
def test_malformed_tiers_are_withheld_from_real_snapshot(project, tier, payload):
    path = project / ".ai-traces" / tier
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    # A failure must remain visible on the next pass, not become cache-fresh.
    for session in ("first", "second"):
        sha, health = snapshot(project, session)
        assert health["redaction_ok"] is False
        tree = git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
        assert ".ai-traces/" + tier not in tree
        assert "src/main.py" in tree
        assert not any("/normalized/" in name or name.endswith("interaction-stream.jsonl")
                       for name in tree)
        assert CANARY not in json.dumps(health)
        assert str(project) not in json.dumps(health)
        assert path.name not in json.dumps(health)
    assert path.read_bytes() == payload  # local evidence is retained for diagnosis


@pytest.mark.parametrize("tier", ["codex/normalized/empty.jsonl", "interaction-stream.jsonl"])
def test_empty_generated_tier_is_safe(project, tier):
    path = project / ".ai-traces" / tier
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    sha, health = snapshot(project, "empty")
    assert health["redaction_ok"] is True
    assert git(project, "show", sha + ":.ai-traces/" + tier) == ""


def test_repaired_file_is_redacted_after_failure_and_then_cached(project):
    path = project / ".ai-traces/codex/normalized/repaired.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(VALID)
    assert not redaction.redact_traces(project).errors
    assert redaction.redact_traces(project).skipped_unchanged
    path.write_bytes(BAD)
    for _ in range(2):
        failed = redaction.redact_traces(project)
        assert failed.errors == ["repaired.jsonl: ValueError: normalized_record_invalid"]
        assert not failed.skipped_unchanged
        assert CANARY not in str(failed.errors)
    path.write_bytes(VALID)
    report = redaction.redact_traces(project)
    assert not report.errors
    assert "synthetic request" in path.read_text()
    assert redaction.redact_traces(project).skipped_unchanged
    sha, health = snapshot(project, "repaired")
    assert health["redaction_ok"] is True
    assert "synthetic request" in git(project, "show", sha + ":.ai-traces/codex/normalized/repaired.jsonl")


@pytest.mark.parametrize("tier", ["codex/normalized/cached.jsonl", "interaction-stream.jsonl"])
def test_cached_generated_tier_verifies_bytes_despite_preserved_metadata(project, tier):
    path = project / ".ai-traces" / tier
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(VALID)
    assert not redaction.redact_traces(project).errors
    old = time.time_ns() - 10_000_000_000
    os.utime(path, ns=(old, old))
    assert not redaction.redact_traces(project).errors
    # A copied file can preserve old metadata despite different bytes. This
    # must not inherit the previous content's successful redaction decision.
    stat = path.stat()
    corrupt = BAD.ljust(stat.st_size, b" ")
    assert len(corrupt) == stat.st_size
    path.write_bytes(corrupt)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    for session in ("cached-corrupt-first", "cached-corrupt-second"):
        sha, health = snapshot(project, session)
        assert health["redaction_ok"] is False
        assert ".ai-traces/" + tier not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
        assert CANARY not in json.dumps(health)
    path.write_bytes(VALID)
    sha, health = snapshot(project, "cached-repaired")
    assert health["redaction_ok"] is True
    assert "synthetic request" in git(project, "show", sha + ":.ai-traces/" + tier)


def test_cached_generated_tier_read_failure_is_withheld(project, monkeypatch):
    path = project / ".ai-traces/codex/normalized/unreadable.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(VALID)
    assert not redaction.redact_traces(project).errors
    old = time.time_ns() - 10_000_000_000
    os.utime(path, ns=(old, old))
    assert not redaction.redact_traces(project).errors
    original = Path.open

    def unavailable(self, *args, **kwargs):
        if self == path:
            raise OSError(CANARY)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unavailable)
    sha, health = snapshot(project, "cached-unreadable")
    assert health["redaction_ok"] is False
    assert ".ai-traces/codex/normalized/unreadable.jsonl" not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert CANARY not in json.dumps(health)
