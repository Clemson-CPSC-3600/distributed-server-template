"""Only explicitly processed AI-trace namespaces may enter a snapshot."""
import json
from pathlib import Path

import pytest

from tests._capture import capture_health, orchestrator, redaction
from tests.test_capture_malformed_records import project, snapshot, git


CANARY = "SYNTHETIC_UNPROCESSED_PRIVATE_CONTEXT"


@pytest.mark.parametrize("relative", [
    "private.json", "codex/private.txt", "codex/raw", "codex/normalized",
    "codex/normalized/private.json", "codex/normalized/private.txt",
    "codex/normalized/nested/private.jsonl", "codex/metadata/private.json",
    "codex/heartbeat/private.txt", "codex/heartbeat/private.json",
])
def test_unprocessed_namespace_fails_closed_in_real_snapshot(project, relative):
    target = project / ".ai-traces" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CANARY)
    sha, health = snapshot(project, "unexpected-namespace")
    assert health["redaction_ok"] is False
    paths = git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert "src/main.py" in paths
    assert not any(p.startswith(".ai-traces/") and p != capture_health.HEALTH_FILE for p in paths)
    assert target.read_text() == CANARY
    target.unlink()
    # Remove empty nested directories too; normalized is an immediate-file tier.
    while target.parent != project / ".ai-traces/codex" and target.parent != project / ".ai-traces":
        parent = target.parent
        try:
            parent.rmdir()
        except OSError:
            break
        target = parent
    _, repaired = snapshot(project, "namespace-repaired")
    assert repaired["redaction_ok"] is True


@pytest.mark.parametrize("tier", ["raw", "normalized", "redacted", "heartbeat"])
def test_namespace_alias_is_rejected_without_reading_target(project, tier):
    outside = project.parent / "outside"
    outside.mkdir()
    (outside / "private.jsonl").write_text(CANARY)
    alias = project / ".ai-traces/codex" / tier
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    sha, health = snapshot(project, "aliased-tier")
    assert health["redaction_ok"] is False
    assert alias.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert (outside / "private.jsonl").read_text() == CANARY


def test_valid_hook_heartbeat_remains_captured(project):
    path = project / ".ai-traces/codex/heartbeat/synthetic-session.json"
    path.parent.mkdir(parents=True)
    data = {"adapter_name": "codex", "schema_version": "1", "session_id": "synthetic-session"}
    path.write_text(json.dumps(data))
    sha, health = snapshot(project, "valid-heartbeat")
    assert health["redaction_ok"] is True
    assert json.loads(git(project, "show", sha + ":" + path.relative_to(project).as_posix())) == data


def test_unknown_namespace_name_never_enters_health(project):
    (project / ".ai-traces" / CANARY).mkdir()
    sha, health = snapshot(project, "unknown-adapter")
    assert health["redaction_ok"] is False
    assert CANARY not in json.dumps(health)
    assert CANARY not in git(project, "ls-tree", "-r", "--name-only", sha)


def test_heartbeat_extra_payload_is_not_treated_as_metadata(project):
    path = project / ".ai-traces/codex/heartbeat/synthetic-session.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"adapter_name": "codex", "schema_version": "1",
                               "session_id": "synthetic-session", "private": CANARY}))
    _, health = snapshot(project, "heartbeat-payload")
    assert health["redaction_ok"] is False


def test_raw_leaf_is_always_an_exclusion(project):
    path = project / ".ai-traces/codex/raw"
    path.write_text(CANARY)
    assert ".ai-traces/codex/raw" in redaction.raw_unstage_paths(project)


def test_unwritable_health_never_commits_old_unverified_bytes(project, monkeypatch):
    path = project / capture_health.HEALTH_FILE
    path.write_text(CANARY)
    monkeypatch.setattr(capture_health, "write_health", lambda *_: None)
    sha = orchestrator.take_snapshot(project, trigger="manual", pytest_session_id=None, force=True)
    assert sha
    assert capture_health.HEALTH_FILE not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()


def test_health_writer_never_follows_alias(project):
    outside = project.parent / "outside-health"
    outside.write_text(CANARY)
    path = project / capture_health.HEALTH_FILE
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert capture_health.write_health(project, {"schema_version": "1"}) is None
    assert outside.read_text() == CANARY
