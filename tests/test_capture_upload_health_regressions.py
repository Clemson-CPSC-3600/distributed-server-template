"""Synthetic destination-bound upload and affirmative health regressions."""
import json
import subprocess

import pytest

from tests._capture import doctor, git_ops, upload


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic upload test")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    (repo / "project-template-config.json").write_text('{"capture_enabled":true}')
    (repo / ".ai-traces").mkdir()
    health = {"schema_version": "1", "redaction_ok": True,
              "snapshot_ok": True, "capture_package": "a" * 64, "adapters": {}}
    (repo / ".ai-traces/capture-health.json").write_text(json.dumps(health))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic baseline")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", git_ops.AUTO_TRACK_REF, sha)
    remotes = []
    for name in ("first.git", "second.git"):
        remote = tmp_path / name
        git(tmp_path, "init", "--bare", "-q", str(remote))
        remotes.append(remote)
    git(repo, "remote", "add", "origin", str(remotes[0]))
    return repo, sha, remotes


def test_acknowledgment_does_not_follow_origin_to_empty_destination(project, monkeypatch):
    repo, sha, remotes = project
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    git(repo, "remote", "set-url", "origin", str(remotes[1]))
    monkeypatch.delenv("CAPTURE_DISABLED", raising=False)
    assert upload.read_receipt(repo, sha)["status"] != "confirmed"
    assert not doctor.build_report(repo)["ok"]
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    assert git(repo, "ls-remote", str(remotes[1]), "refs/heads/auto-track").startswith(sha)


def test_pushurl_is_both_the_upload_and_acknowledgment_destination(project):
    repo, sha, remotes = project
    git(repo, "remote", "set-url", "--push", "origin", str(remotes[1]))
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    assert not git(repo, "ls-remote", str(remotes[0]), "refs/heads/auto-track")
    assert git(repo, "ls-remote", str(remotes[1]), "refs/heads/auto-track").startswith(sha)


def test_multiple_push_destinations_fail_closed_without_upload(project):
    repo, sha, remotes = project
    git(repo, "remote", "set-url", "--add", "--push", "origin", str(remotes[0]))
    git(repo, "remote", "set-url", "--add", "--push", "origin", str(remotes[1]))
    assert upload.push_snapshot(repo, sha)["status"] != "confirmed"
    assert upload.read_receipt(repo, sha)["status"] != "confirmed"
    assert not any(git(repo, "ls-remote", str(remote), "refs/heads/auto-track")
                   for remote in remotes)


@pytest.mark.parametrize("secondary_rule", ["insteadOf", "pushInsteadOf"])
def test_chained_url_rewrites_fail_closed_before_upload(project, secondary_rule):
    repo, sha, remotes = project
    alias = "synthetic:upload-target"
    git(repo, "remote", "set-url", "origin", alias)
    git(repo, "config", f"url.{remotes[0]}.insteadOf", alias)
    git(repo, "config", f"url.{remotes[1]}.{secondary_rule}", str(remotes[0]))
    assert git(repo, "remote", "get-url", "--push", "origin") == str(remotes[0])
    assert upload.push_snapshot(repo, sha)["status"] != "confirmed"
    assert upload.read_receipt(repo, sha)["status"] != "confirmed"
    for remote in remotes:
        # Inspect bare refs directly: using a URL here could itself rewrite.
        assert not git(remote, "for-each-ref", "--format=%(objectname)",
                       "refs/heads/auto-track")


def test_single_stable_url_alias_can_upload_and_confirm(project):
    repo, sha, remotes = project
    alias = "synthetic:stable-upload-target"
    git(repo, "remote", "set-url", "origin", alias)
    git(repo, "config", f"url.{remotes[0]}.insteadOf", alias)
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    assert git(remotes[0], "rev-parse", "refs/heads/auto-track") == sha


def test_origin_change_between_push_and_ack_cannot_confirm_new_destination(project, monkeypatch):
    repo, sha, remotes = project
    original = git_ops.run_git

    def change_origin(args, *a, **kw):
        result = original(args, *a, **kw)
        if args[0] == "push":
            git(repo, "remote", "set-url", "origin", str(remotes[1]))
        return result

    monkeypatch.setattr(git_ops, "run_git", change_origin)
    assert upload.push_snapshot(repo, sha)["status"] != "confirmed"
    assert upload.read_receipt(repo, sha)["status"] != "confirmed"


def test_receipt_contains_no_raw_destination_and_acknowledgment_is_monotonic(project):
    repo, sha, remotes = project
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    receipt_files = (repo / ".git/capture-upload").rglob("*.json")
    for path in receipt_files:
        assert str(remotes[0]) not in path.read_text()
    assert upload.write_receipt(repo, sha, "failed", error="push_failed")["status"] == "confirmed"
    assert upload.read_receipt(repo, sha)["status"] == "confirmed"


def test_legacy_receipt_without_destination_is_not_trusted(project):
    repo, sha, _ = project
    old = repo / ".git/capture-upload" / (sha + ".confirmed.json")
    old.parent.mkdir()
    old.write_text(json.dumps({"status": "confirmed", "requested_sha": sha,
                               "acknowledged_sha": sha}))
    assert upload.read_receipt(repo, sha)["status"] != "confirmed"


def test_missing_origin_is_unknown_without_raising(project):
    repo, sha, _ = project
    git(repo, "remote", "remove", "origin")
    assert upload.read_receipt(repo, sha)["status"] == "unknown"
    assert upload.push_snapshot(repo, sha)["status"] != "confirmed"


@pytest.mark.parametrize("field,value", [
    ("redaction_ok", None), ("redaction_ok", "true"),
    ("snapshot_ok", None), ("snapshot_ok", 1),
    ("capture_package", None), ("capture_package", "unknown"),
    ("capture_package", "not-a-digest"),
])
def test_doctor_does_not_promote_missing_or_nonaffirmative_health(project, monkeypatch, field, value):
    repo, sha, _ = project
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    monkeypatch.delenv("CAPTURE_DISABLED", raising=False)
    path = repo / ".ai-traces/capture-health.json"
    health = json.loads(path.read_text())
    if value is None:
        health.pop(field)
    else:
        health[field] = value
    path.write_text(json.dumps(health))
    assert not doctor.build_report(repo)["ok"]


def test_doctor_accepts_affirmative_health_with_current_destination_ack(project, monkeypatch):
    repo, sha, _ = project
    assert upload.push_snapshot(repo, sha)["status"] == "confirmed"
    monkeypatch.delenv("CAPTURE_DISABLED", raising=False)
    assert doctor.build_report(repo)["ok"]
