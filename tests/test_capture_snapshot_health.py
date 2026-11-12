"""A confirmed upload must attest health inside that exact snapshot."""
import json
import os
import subprocess

import pytest

from tests._capture import capture_health, doctor, git_ops, orchestrator, upload


HEALTH = {"schema_version": "1", "redaction_ok": True, "snapshot_ok": True,
          "capture_package": "a" * 64, "adapters": {}}


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
        "CODEX_HOME": str(home / "codex"), "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "VSCODE_CONFIG_DIR": str(home / "vscode"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic snapshot health", "GIT_COMMITTER_NAME": "Synthetic snapshot health",
        "GIT_AUTHOR_EMAIL": "health@example.invalid", "GIT_COMMITTER_EMAIL": "health@example.invalid",
        "CAPTURE_DISABLED": "0", "_CAPTURE_SUBPROCESS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src/main.py").write_text("pass\n")
    (repo / "project-template-config.json").write_text('{"capture_enabled":true}')
    (repo / ".ai-traces").mkdir()
    (repo / capture_health.HEALTH_FILE).write_text(json.dumps(HEALTH))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic healthy baseline")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", git_ops.AUTO_TRACK_REF, sha)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    return repo


def commit_health(repo, payload):
    path = repo / capture_health.HEALTH_FILE
    if payload is None:
        path.unlink()
    else:
        path.write_text(payload)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Synthetic snapshot health variant")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", git_ops.AUTO_TRACK_REF, sha)
    path.write_text(json.dumps(HEALTH))
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"


@pytest.mark.parametrize("payload", [None, "{malformed", "[]", '{"schema_version":"unsupported"}'])
def test_stale_local_health_cannot_replace_missing_or_invalid_snapshot_health(project, payload):
    commit_health(project, payload)
    result = doctor.build_report(project)
    assert result["upload"]["status"] == "confirmed"
    assert result["health_record_present"] is True
    assert not result["ok"]
    assert "snapshot_health_unknown" in result["errors"]


@pytest.mark.parametrize("field,value", [
    ("redaction_ok", False), ("redaction_ok", None), ("redaction_ok", "true"),
    ("snapshot_ok", False), ("snapshot_ok", None), ("snapshot_ok", 1),
    ("capture_package", "unknown"), ("capture_package", None),
    ("adapters", {"codex": {"errors": ["adapter_ingest_failed"]}}),
])
def test_snapshot_health_must_be_affirmative_despite_healthy_local_copy(project, field, value):
    health = {**HEALTH, field: value}
    commit_health(project, json.dumps(health))
    result = doctor.build_report(project)
    assert not result["ok"]
    assert "snapshot_health_not_affirmative" in result["errors"]


def test_healthy_committed_and_local_health_with_confirmed_upload_is_ok(project):
    assert doctor.build_report(project)["ok"]


@pytest.mark.parametrize("field", ["redaction_ok", "snapshot_ok"])
def test_fresh_local_failure_remains_visible_with_older_healthy_snapshot(project, field):
    path = project / capture_health.HEALTH_FILE
    path.write_text(json.dumps({**HEALTH, field: False}))
    result = doctor.build_report(project)
    assert not result["ok"]
    assert ("redaction_failed" if field == "redaction_ok" else "snapshot_failed") in result["errors"]


def test_failed_health_write_and_confirmed_new_snapshot_cannot_report_ok(project, monkeypatch):
    monkeypatch.setattr(orchestrator, "installed_adapters", lambda _: [])
    monkeypatch.setattr(capture_health, "write_health", lambda *_: None)
    monkeypatch.setattr(git_ops, "push_auto_track_background", lambda *_: None)
    sha = orchestrator.take_snapshot(project, trigger="manual", pytest_session_id=None, force=True)
    assert sha
    assert capture_health.HEALTH_FILE not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert upload.push_snapshot(project, sha)["status"] == "confirmed"
    result = doctor.build_report(project)
    assert not result["ok"]
    assert "snapshot_health_unknown" in result["errors"]
