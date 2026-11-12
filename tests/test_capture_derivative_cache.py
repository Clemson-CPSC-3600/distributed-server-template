"""Cached derivatives must prove the bytes actually entering snapshots."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from tests._capture import redaction
from tests._capture import watchdog
from tests.test_capture_malformed_records import project, snapshot, git


CANARY = "sk-" + "synthetic012345678901234567890"


def derivative(project, malformed=False):
    raw = project / ".ai-traces/codex/raw/rollouts/rollout-cache.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("{bad\n" if malformed else json.dumps({
        "type": "event_msg", "payload": {"type": "agent_message",
        "message": "X" * len(CANARY)}}) + "\n")
    snapshot(project, "bootstrap")
    out = project / ".ai-traces/codex/redacted/rollouts/rollout-cache.jsonl"
    return raw, out.with_name(out.name + ".withheld.json") if malformed else out


def replace_preserving_metadata(path, payload):
    state = path.stat()
    assert len(payload) == state.st_size
    path.write_bytes(payload)
    os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns))


@pytest.mark.parametrize("marker", [False, True])
def test_changed_cached_output_is_regenerated_before_real_snapshot(project, marker):
    raw, out = derivative(project, marker)
    healthy = out.read_bytes()
    corrupted = CANARY.encode().ljust(len(healthy), b" ")
    replace_preserving_metadata(out, corrupted)
    sha, health = snapshot(project, "copied-output")
    assert health["redaction_ok"] is True
    assert CANARY not in git(project, "show", sha + ":" + out.relative_to(project).as_posix())
    assert out.read_bytes() == healthy


def test_legacy_output_metadata_cannot_qualify(project):
    raw, out = derivative(project)
    cache = redaction.RedactionCache(project)
    key = raw.relative_to(project).as_posix()
    cache.entries[key]["outputs"] = {out.relative_to(project).as_posix(): redaction._file_state(out),
        out.relative_to(project).as_posix() + ".withheld.json": None}
    assert not cache.is_fresh(key, raw, [out, out.with_name(out.name + ".withheld.json")])


def test_healthy_output_cache_is_idempotent(project):
    raw, out = derivative(project)
    state = out.stat()
    report = redaction.redact_traces(project)
    assert raw.relative_to(project).as_posix() in report.skipped_unchanged
    assert out.stat().st_mtime_ns == state.st_mtime_ns


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_nonregular_cached_output_is_never_staged(project, kind):
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    raw, out = derivative(project)
    target = project.parent / "synthetic-secret"
    target.write_text(CANARY)
    out.unlink()
    if kind == "symlink":
        try:
            out.symlink_to(target)
        except OSError:
            pytest.skip("symlinks unavailable")
    else:
        os.mkfifo(out)
    sha, health = snapshot(project, "nonregular")
    assert health["redaction_ok"] is False
    assert out.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert target.read_text() == CANARY
    out.unlink()
    sha, health = snapshot(project, "nonregular-repaired")
    assert health["redaction_ok"] is True
    assert CANARY not in git(project, "show", sha + ":" + out.relative_to(project).as_posix())


@pytest.mark.parametrize("fault", ["read", "write", "remove"])
@pytest.mark.parametrize("marker", [False, True])
def test_output_io_failure_withholds_tier_and_repair_recovers(project, monkeypatch, fault, marker):
    raw, out = derivative(project, marker)
    healthy = out.read_bytes()
    corrupted = CANARY.encode().ljust(len(healthy), b" ")
    replace_preserving_metadata(out, corrupted)
    with monkeypatch.context() as faults:
        if fault == "remove":
            raw.write_text(json.dumps({"type": "event_msg", "payload": {
                "type": "agent_message", "message": "repaired"}}) + "\n" if marker else "{malformed\n")
            original = Path.unlink
            def unavailable(self, *args, **kwargs):
                if self == out:
                    raise OSError("synthetic removal failure")
                return original(self, *args, **kwargs)
            faults.setattr(Path, "unlink", unavailable)
        elif fault == "write":
            original = Path.write_text
            def unavailable(self, *args, **kwargs):
                if self.parent == out.parent and self.name.endswith(redaction.TEMP_SUFFIX):
                    raise OSError("synthetic write failure")
                return original(self, *args, **kwargs)
            faults.setattr(Path, "write_text", unavailable)
        else:
            original = os.open
            def unavailable(path, *args, **kwargs):
                if Path(path) == out:
                    raise OSError("synthetic read failure")
                return original(path, *args, **kwargs)
            faults.setattr(os, "open", unavailable)
        sha, health = snapshot(project, "output-failure")
        assert health["redaction_ok"] is False
        assert out.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
        assert hashlib.sha256(corrupted).hexdigest() not in (
            project / ".git" / redaction.CACHE_FILENAME).read_text()
    sha, health = snapshot(project, "output-repaired")
    assert health["redaction_ok"] is True
    assert CANARY not in git(project, "show", "--format=", sha)


@pytest.mark.parametrize("marker", [False, True])
def test_orphan_derivative_requires_current_byte_attestation(project, marker):
    raw, out = derivative(project, marker)
    raw.unlink()
    raw.parent.rmdir()
    raw.parent.parent.rmdir()
    state = out.stat()
    os.utime(out, ns=(state.st_atime_ns, state.st_mtime_ns + 5_000_000_000))
    sha, health = snapshot(project, "healthy-orphan")
    assert health["redaction_ok"] is True
    assert out.relative_to(project).as_posix() in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    replace_preserving_metadata(out, CANARY.encode().ljust(out.stat().st_size, b" "))
    sha, health = snapshot(project, "changed-orphan")
    assert health["redaction_ok"] is False
    assert out.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()


@pytest.mark.parametrize("legacy", [False, True])
def test_orphan_without_hash_provenance_is_withheld(project, legacy):
    raw, out = derivative(project)
    cache_path = project / ".git" / redaction.CACHE_FILENAME
    if legacy:
        data = json.loads(cache_path.read_text())
        for entry in data["entries"].values():
            for state in entry["outputs"].values():
                if isinstance(state, dict):
                    state.pop("sha256", None)
        cache_path.write_text(json.dumps(data))
    else:
        cache_path.unlink()
    raw.unlink()
    sha, health = snapshot(project, "unattested-orphan")
    assert health["redaction_ok"] is False
    assert out.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()


def test_unexpected_derivative_is_withheld(project):
    raw, out = derivative(project)
    unexpected = out.parent / "unexpected.jsonl"
    unexpected.write_text(CANARY)
    sha, health = snapshot(project, "unexpected-output")
    assert health["redaction_ok"] is False
    assert unexpected.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()


def test_ancestor_output_alias_is_withheld_without_following_it(project):
    raw, out = derivative(project)
    external = project.parent / "synthetic-external"
    out.parent.rename(external)
    (external / out.name).write_text(CANARY)
    try:
        out.parent.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    sha, health = snapshot(project, "ancestor-alias")
    assert health["redaction_ok"] is False
    assert not any("/redacted/" in name for name in git(project, "ls-tree", "-r", "--name-only", sha).splitlines())
    assert (external / out.name).read_text() == CANARY


def test_output_namespace_listing_failure_withholds_tier(project, monkeypatch):
    raw, out = derivative(project)
    replace_preserving_metadata(out, CANARY.encode().ljust(out.stat().st_size, b" "))
    original = Path.iterdir
    with monkeypatch.context() as faults:
        def unavailable(self):
            if self == project / ".ai-traces":
                raise OSError("synthetic directory listing failure")
            return original(self)
        faults.setattr(Path, "iterdir", unavailable)
        sha, health = snapshot(project, "listing-failure")
        assert health["redaction_ok"] is False
        assert out.relative_to(project).as_posix() not in git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    sha, health = snapshot(project, "listing-repaired")
    assert health["redaction_ok"] is True
    assert CANARY not in git(project, "show", sha + ":" + out.relative_to(project).as_posix())


def test_output_namespace_root_alias_is_rejected_before_listing(project, monkeypatch):
    raw, out = derivative(project)
    root = project / ".ai-traces"
    external = project.parent / "synthetic-aliased-traces"
    root.rename(external)
    try:
        root.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    original = Path.iterdir
    def reject_alias_listing(self):
        assert self != root, "output root alias was followed"
        return original(self)
    monkeypatch.setattr(Path, "iterdir", reject_alias_listing)
    report = redaction.redact_traces(project)
    assert report.errors == ["derivative_output_invalid"]


@pytest.mark.parametrize("marker", [False, True])
def test_watchdog_omits_unverified_derivatives_and_preserves_hang_evidence(project, monkeypatch, marker):
    raw, out = derivative(project, marker)
    baseline = git(project, "rev-parse", "refs/auto-track/snapshots")
    replace_preserving_metadata(out, CANARY.encode().ljust(out.stat().st_size, b" "))
    evidence = project / "src/hang_evidence.py"
    evidence.write_text("# synthetic code at the time of the hang\n")
    (project / ".codex").mkdir(exist_ok=True)
    (project / ".codex/auth.json").write_text(CANARY)
    (project / ".codex/hooks.json").write_text("{}")
    def no_sanitizer(*args, **kwargs):
        raise AssertionError("watchdog must not race the hung parent's sanitizer")
    monkeypatch.setattr(redaction, "redact_traces", no_sanitizer)
    original_exists = Path.exists
    def no_trace_probe(self):
        assert self != project / ".ai-traces", "watchdog probed the unverified trace root"
        return original_exists(self)
    monkeypatch.setattr(Path, "exists", no_trace_probe)
    sha = watchdog.record_hang_snapshot(project, "synthetic-hang", time.time() - 5)
    assert sha
    names = git(project, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert not any(name.startswith(".ai-traces/") for name in names)
    assert ".codex/auth.json" not in names
    assert ".codex/hooks.json" in names
    assert "src/hang_evidence.py" in names
    assert git(project, "show", sha + ":src/hang_evidence.py") == evidence.read_text().strip()
    message = git(project, "show", "-s", "--format=%B", sha)
    assert "status: hang_watchdog_killed" in message
    assert "trigger: pytest_watchdog" in message
    git(project, "merge-base", "--is-ancestor", baseline, sha)
