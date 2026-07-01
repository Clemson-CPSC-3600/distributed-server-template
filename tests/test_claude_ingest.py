"""Unit tests for claude_ingest — fake $CLAUDE_CONFIG_DIR, no real Claude binary needed.

Mirrors test_codex_ingest in style. The module under test scans
``$CLAUDE_CONFIG_DIR/projects/<slug>/*.jsonl``, filters by the cwd in the
first cwd-bearing event, and copies matches into
``<repo>/.ai-traces/claude/raw/sessions/``. Dedup is size-aware (re-copy
when source has grown).
"""
import json
import os
import time
from pathlib import Path

import pytest

from tests._capture import claude_ingest


@pytest.fixture
def fake_claude_home(tmp_path, monkeypatch):
    """Create a fake $CLAUDE_CONFIG_DIR with an empty projects/ tree."""
    home = tmp_path / "claude_home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def _slug(repo: Path) -> str:
    return str(repo).replace("/", "-")


def _write_session(home: Path, sid: str, cwd: str, mtime: float,
                   slug: str | None = None,
                   extra_events: list[dict] | None = None) -> Path:
    """Write a minimally-valid session JSONL with a cwd-bearing first user event."""
    if slug is None:
        slug = cwd.replace("/", "-")
    project_dir = home / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{sid}.jsonl"
    rows = [
        {"type": "permission-mode", "permissionMode": "auto", "sessionId": sid},
        {
            "parentUuid": None,
            "isSidechain": False,
            "promptId": "p-1",
            "type": "user",
            "message": {"role": "user", "content": "Hello"},
            "uuid": "u-1",
            "timestamp": "2026-05-09T01:00:00.000Z",
            "cwd": cwd,
            "sessionId": sid,
            "version": "2.1.0",
            "gitBranch": "main",
        },
    ]
    if extra_events:
        rows.extend(extra_events)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def test_ingest_copies_sessions_whose_cwd_matches_repo(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    matching = _write_session(fake_claude_home, sid="aaa", cwd=str(repo),
                              mtime=time.time() - 30)
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write_session(fake_claude_home, sid="bbb", cwd=str(other),
                   mtime=time.time() - 30)

    copied = claude_ingest.ingest_transcripts(repo, time.time() - 60)

    assert len(copied) == 1
    assert copied[0].name == matching.name
    dest = repo / ".ai-traces" / "claude" / "raw" / "sessions"
    assert (dest / matching.name).exists()
    assert not (dest / "bbb.jsonl").exists()


def test_ingest_captures_sessions_created_before_session_start(tmp_path, fake_claude_home):
    """Mirror of the codex ingest regression: a session written BEFORE
    pytest started must still be captured. The cwd match is the canonical
    selection criterion, not mtime.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    started = time.time()
    _write_session(fake_claude_home, sid="earlier", cwd=str(repo),
                   mtime=started - 3600)

    copied = claude_ingest.ingest_transcripts(repo, started)

    assert len(copied) == 1


def test_ingest_ignores_sessions_with_different_cwd(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _write_session(fake_claude_home, sid="other", cwd=str(other),
                   mtime=time.time() - 30)

    copied = claude_ingest.ingest_transcripts(repo, time.time() - 60)

    assert copied == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_is_idempotent_when_source_unchanged(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(fake_claude_home, sid="dup", cwd=str(repo),
                   mtime=time.time() - 30)

    first = claude_ingest.ingest_transcripts(repo, time.time() - 60)
    second = claude_ingest.ingest_transcripts(repo, time.time() - 60)

    assert len(first) == 1
    assert second == []


def test_ingest_recopies_when_source_grew(tmp_path, fake_claude_home):
    """Claude Code keeps one session JSONL open and appends turns. Bug 2's
    lesson: subsequent ingests must overwrite when the source has grown,
    not silently skip on filename match.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    src = _write_session(fake_claude_home, sid="growing", cwd=str(repo),
                         mtime=time.time() - 30)
    first = claude_ingest.ingest_transcripts(repo, time.time() - 60)
    assert len(first) == 1
    dest = repo / ".ai-traces" / "claude" / "raw" / "sessions" / src.name
    initial_size = dest.stat().st_size

    appended = {
        "type": "assistant",
        "message": {"role": "assistant",
                    "content": [{"type": "text", "text": "second turn"}]},
        "uuid": "u-2",
        "timestamp": "2026-05-09T01:00:30.000Z",
        "cwd": str(repo),
        "sessionId": "growing",
    }
    with src.open("a", encoding="utf-8") as f:
        f.write(json.dumps(appended) + "\n")
    grown_size = src.stat().st_size
    assert grown_size > initial_size

    second = claude_ingest.ingest_transcripts(repo, time.time() - 60)
    assert len(second) == 1, (
        "Source session grew between ingests — destination must be refreshed."
    )
    assert dest.stat().st_size == grown_size


def test_ingest_copies_sibling_spillover_dir(tmp_path, fake_claude_home):
    """Claude Code spills oversized tool outputs into a sibling dir at
    ``<uuid>/`` next to ``<uuid>.jsonl``. The adapter must mirror it so
    captured tool outputs aren't dangling references."""
    repo = tmp_path / "repo"
    repo.mkdir()
    src = _write_session(fake_claude_home, sid="spill", cwd=str(repo),
                         mtime=time.time() - 30)
    spillover = src.with_suffix("")
    spillover.mkdir()
    (spillover / "tool-results").mkdir()
    big_output = spillover / "tool-results" / "abc.txt"
    big_output.write_text("a" * 1000, encoding="utf-8")

    claude_ingest.ingest_transcripts(repo, time.time() - 60)

    dest_root = repo / ".ai-traces" / "claude" / "raw" / "sessions"
    assert (dest_root / "spill" / "tool-results" / "abc.txt").exists()
    assert (dest_root / "spill" / "tool-results" / "abc.txt").read_text(encoding="utf-8") == "a" * 1000


def test_ingest_returns_empty_when_claude_home_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
    repo = tmp_path / "repo"
    repo.mkdir()

    assert claude_ingest.ingest_transcripts(repo, time.time()) == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_swallows_errors_on_malformed_jsonl(tmp_path, fake_claude_home):
    """Capture layer MUST never raise."""
    project_dir = fake_claude_home / "projects" / "-bad"
    project_dir.mkdir(parents=True)
    bad = project_dir / "rollout-bad.jsonl"
    bad.write_bytes(b"\xff\xfenot-utf8-not-json\n")
    repo = tmp_path / "repo"
    repo.mkdir()

    # Should not raise, should not match (no cwd extractable), should
    # leave dest dir absent.
    result = claude_ingest.ingest_transcripts(repo, time.time())
    assert result == []
