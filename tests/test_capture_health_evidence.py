"""The evidence/heartbeat conjunction that identifies unauthorized hooks.

Hooks are silently inactive until authorized, and authorization can only be
granted from the CLI -- an editor-only student is never prompted. The
symptom is therefore: the tool clearly ran (evidence captured) but no hook
ever fired (no heartbeat). Bare heartbeat absence is NOT enough, because a
student who never used the tool here looks identical.

See issue 5yu and docs/superpowers/plans/2026-08-11-plan-review-amendments.md.
"""
from __future__ import annotations

from pathlib import Path

from tests._capture import ai_traces, capture_health


def _adapter_root(repo: Path, name: str = "codex") -> Path:
    root = ai_traces.adapter_dir(repo, name)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_evidence(repo: Path, name: str = "codex") -> None:
    dest = _adapter_root(repo, name) / "raw" / "rollouts"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "rollout-x.jsonl").write_text("{}\n", encoding="utf-8")


def _write_heartbeat(repo: Path, name: str = "codex") -> None:
    beat = _adapter_root(repo, name) / capture_health.HEARTBEAT_SUBDIR
    beat.mkdir(parents=True, exist_ok=True)
    (beat / "session-abc").write_text("ok\n", encoding="utf-8")


def test_heartbeat_files_are_not_mistaken_for_evidence(tmp_path):
    """The two signals must stay independent, or the conjunction collapses."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_heartbeat(repo)

    assert capture_health.has_heartbeat(repo, "codex") is True
    assert capture_health.has_evidence(repo, "codex") is False, (
        "a heartbeat must never count as captured evidence"
    )


def test_evidence_without_heartbeat_flags_unauthorized(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_evidence(repo)

    health = capture_health.build_health(repo, present_names=["codex"])
    assert health["adapters"]["codex"]["evidence"] is True
    assert health["adapters"]["codex"]["heartbeat"] is False
    assert capture_health.hooks_look_unauthorized(health, "codex") is True


def test_evidence_with_heartbeat_is_healthy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_evidence(repo)
    _write_heartbeat(repo)

    health = capture_health.build_health(repo, present_names=["codex"])
    assert capture_health.hooks_look_unauthorized(health, "codex") is False


def test_no_evidence_is_not_an_accusation(tmp_path):
    """Never-used-here must not be reported as unauthorized hooks."""
    repo = tmp_path / "repo"
    repo.mkdir()

    health = capture_health.build_health(repo, present_names=["codex"])
    assert health["adapters"]["codex"]["evidence"] is False
    assert capture_health.hooks_look_unauthorized(health, "codex") is False, (
        "absence of both signals is ambiguous, not proof of anything"
    )


def test_manifest_stays_deterministic(tmp_path):
    """Determinism is load-bearing: tree-SHA dedupe dies if bytes vary."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_evidence(repo)

    first = capture_health.serialize(
        capture_health.build_health(repo, present_names=["codex"]))
    second = capture_health.serialize(
        capture_health.build_health(repo, present_names=["codex"]))
    assert first == second
