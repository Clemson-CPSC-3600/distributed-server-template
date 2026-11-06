"""Workspace-root mismatch: student opened a folder ABOVE the assignment.

Both capture paths miss that case -- Codex reports the parent as its cwd,
which fails codex_ingest's repo-or-subdirectory filter, and hook discovery
is an upward walk that never reaches the repo's .codex/. The result is that
real AI use is indistinguishable from no AI use. These tests pin the
detection signal that makes it visible.

See docs/superpowers/plans/2026-08-11-plan-review-amendments.md (cyq.1
verification record) and issue 69q.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests._capture import capture_health, codex_ingest
from tests._capture.agent_adapters.codex import CodexAdapter


def _make_layout(tmp_path: Path):
    """parent/assignment-1 plus a sibling, and a Codex session store."""
    parent = tmp_path / "projects"
    repo = parent / "assignment-1"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (parent / "other-project").mkdir()
    sessions = tmp_path / "sessions" / "2026" / "08" / "13"
    sessions.mkdir(parents=True)
    return parent, repo, sessions


def _rollout(sessions: Path, name: str, cwd: Path) -> Path:
    path = sessions / f"rollout-{name}.jsonl"
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}}) + "\n",
        encoding="utf-8",
    )
    return path


def test_ancestor_cwd_detected_but_never_copied(tmp_path, monkeypatch):
    parent, repo, sessions = _make_layout(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(sessions, "in-repo", repo)
    _rollout(sessions, "ancestor", parent)

    ancestor: list = []
    copied = codex_ingest.ingest_transcripts(
        repo, skipped=[], ancestor_cwd=ancestor)

    assert [p.name for p in copied] == ["rollout-in-repo.jsonl"]
    assert [p.name for p in ancestor] == ["rollout-ancestor.jsonl"], (
        "a session run from the parent folder must be recorded as a "
        "mismatch, not dropped silently"
    )
    # Detected is not the same as captured: its content must stay out.
    assert not (repo / ".ai-traces" / "codex" / "raw" / "rollouts"
                / "rollout-ancestor.jsonl").exists()


def test_subdirectory_is_not_a_mismatch(tmp_path, monkeypatch):
    """Opening repo/src is fine -- hook discovery walks up. Not a mismatch."""
    _parent, repo, sessions = _make_layout(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(sessions, "subdir", repo / "src")

    ancestor: list = []
    copied = codex_ingest.ingest_transcripts(
        repo, skipped=[], ancestor_cwd=ancestor)

    assert [p.name for p in copied] == ["rollout-subdir.jsonl"]
    assert ancestor == []


def test_sibling_project_is_not_counted(tmp_path, monkeypatch):
    """The leak guard: unrelated work under the same parent must not count.

    This is what keeps the trailer count from becoming a store-wide total
    of the student's AI use outside this course.
    """
    parent, repo, sessions = _make_layout(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(sessions, "sibling", parent / "other-project")
    _rollout(sessions, "unrelated", tmp_path / "somewhere-else")

    ancestor: list = []
    copied = codex_ingest.ingest_transcripts(
        repo, skipped=[], ancestor_cwd=ancestor)

    assert copied == []
    assert ancestor == [], (
        "only STRICT ANCESTORS of the repo may count; siblings and "
        "unrelated directories must stay invisible"
    )


def test_mismatch_survives_the_empty_early_return(tmp_path, monkeypatch):
    """Regression: the adapter early-returns when nothing was copied.

    A mismatch-only run has exactly that shape (nothing copied, no
    evidence dir), so an early return that drops the signal would re-hide
    it in the one case it exists to expose.
    """
    parent, repo, sessions = _make_layout(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(sessions, "ancestor", parent)

    result = CodexAdapter().ingest(repo, since=None)

    assert result.rollouts_copied == []
    assert len(result.rollouts_ancestor_cwd) == 1, (
        "mismatch must survive the nothing-copied early return"
    )


def test_manifest_flag_and_trailer_count(tmp_path):
    """Boolean in the manifest; run-varying count only in the trailers."""
    repo = tmp_path / "repo"
    repo.mkdir()

    clean = capture_health.build_health(repo, present_names=["codex"])
    assert clean["workspace_root_mismatch"] is False

    flagged = capture_health.build_health(
        repo, present_names=["codex"], workspace_root_mismatch=True)
    assert flagged["workspace_root_mismatch"] is True

    # The manifest must carry no count -- it has to stay byte-stable so
    # tree-SHA dedupe survives.
    assert "ancestor" not in capture_health.serialize(flagged).lower().replace(
        "workspace_root_mismatch", "")

    lines = capture_health.format_counts(
        {"codex": {"matched": 1, "copied": 1, "ancestor_cwd": 3}})
    assert any("ancestor_cwd=3" in line for line in lines)

    quiet = capture_health.format_counts(
        {"codex": {"matched": 1, "copied": 1, "ancestor_cwd": 0}})
    assert not any("ancestor_cwd" in line for line in quiet), (
        "a zero count must not be emitted at all"
    )
