"""Aider must preserve sanitized evidence without another installed adapter."""
import json
import os
import subprocess

import pytest

from tests._capture import ai_traces, git_ops, orchestrator, redaction
from tests._capture.agent_adapters.registry import installed_adapters


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    for key, value in {
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "APPDATA": str(home / "appdata"), "LOCALAPPDATA": str(home / "localappdata"),
        "CODEX_HOME": str(home / "codex"), "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "VSCODE_CONFIG_DIR": str(home / "vscode"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic Aider test", "GIT_COMMITTER_NAME": "Synthetic Aider test",
        "GIT_AUTHOR_EMAIL": "aider@example.invalid", "GIT_COMMITTER_EMAIL": "aider@example.invalid",
        "CAPTURE_DISABLED": "0", "_CAPTURE_SUBPROCESS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src/main.py").write_text("pass\n")
    (repo / ".gitignore").write_text(".aider*\n")
    (repo / "project-template-config.json").write_text(json.dumps({
        "capture_enabled": True,
        "agent_adapters": {name: {"enabled": name == "aider"}
                           for name in ("codex", "claude", "vscode_chat", "aider")},
    }))
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic baseline")
    (repo / ".aider.chat.history.md").write_text("# synthetic chat\nUnverified chat content\n")
    (repo / ".aider.input.history").write_text("Explain a timeout\n")
    assert [adapter.name for adapter in installed_adapters(repo)] == ["aider"]
    monkeypatch.setattr(git_ops, "push_auto_track_background", lambda *_: None)
    return repo


@pytest.mark.parametrize("ignored_traces", [False, True])
def test_aider_only_snapshot_preserves_derivatives_and_attestation(project, ignored_traces):
    if ignored_traces:
        (project / ".gitignore").write_text(".aider*\n.ai-traces/\n")
    sha = orchestrator.take_snapshot(project, trigger="pytest",
                                     pytest_session_id="synthetic-aider", force=True)
    assert sha
    paths = git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    derivative = ".ai-traces/aider/redacted/.aider.input.history"
    marker = ".ai-traces/aider/redacted/.aider.chat.history.md.withheld.json"
    assert derivative in paths
    assert marker in paths
    assert ai_traces.ATTESTATION_FILE in paths
    assert "Explain a timeout" in git(project, "show", sha + ":" + derivative)
    health = json.loads(git(project, "show", sha + ":" + ai_traces.AI_TRACES_DIR + "/capture-health.json"))
    assert health["redaction_ok"] is True
    assert health["adapters"]["aider"]["present"] is True
    assert ".aider.chat.history.md" not in paths
    assert ".aider.input.history" not in paths
    assert not any("/raw/" in path for path in paths)


def test_aider_only_failed_sanitizer_withholds_conversation(project, monkeypatch):
    def fail_sanitizer(_repo):
        raise RuntimeError("synthetic sanitizer failure")
    monkeypatch.setattr(redaction, "redact_traces", fail_sanitizer)
    sha = orchestrator.take_snapshot(project, trigger="pytest",
                                     pytest_session_id="synthetic-aider-failure", force=True)
    assert sha
    paths = git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    health = json.loads(git(project, "show", sha + ":.ai-traces/capture-health.json"))
    assert health["redaction_ok"] is False
    assert [path for path in paths if path.startswith(".ai-traces/")] == [".ai-traces/capture-health.json"]
    assert ".aider.chat.history.md" not in paths
    assert ".aider.input.history" not in paths
