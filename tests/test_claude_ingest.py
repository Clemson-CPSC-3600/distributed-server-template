"""Unit tests for claude_ingest — fake $CLAUDE_CONFIG_DIR, no real Claude binary needed.

Mirrors test_codex_ingest in style. The module under test scans
``$CLAUDE_CONFIG_DIR/projects/<slug>/*.jsonl``, filters by the cwd in the
first cwd-bearing event, and copies matches into
``<repo>/.ai-traces/claude/raw/sessions/``. Dedup is mtime+size-aware
(re-copy when source has grown OR was rewritten in place at an unchanged
size), and a course-start floor (assignment-config.json's course_start)
can exclude sessions with an older file mtime.
"""
import json
import os
import time
from datetime import datetime, timezone
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

    copied = claude_ingest.ingest_transcripts(repo)

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

    copied = claude_ingest.ingest_transcripts(repo)

    assert len(copied) == 1


def test_ingest_copies_session_with_cwd_in_subdir_of_repo(tmp_path, fake_claude_home):
    """A session whose cwd is a subdirectory of the repo (e.g. the student
    ran Claude Code from ``repo/src``) must still be captured."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    matching = _write_session(fake_claude_home, sid="subdir", cwd=str(repo / "src"),
                              mtime=time.time() - 30)

    copied = claude_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name


def test_ingest_ignores_sessions_with_different_cwd(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _write_session(fake_claude_home, sid="other", cwd=str(other),
                   mtime=time.time() - 30)

    copied = claude_ingest.ingest_transcripts(repo)

    assert copied == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_is_idempotent_when_source_unchanged(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(fake_claude_home, sid="dup", cwd=str(repo),
                   mtime=time.time() - 30)

    first = claude_ingest.ingest_transcripts(repo)
    second = claude_ingest.ingest_transcripts(repo)

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
    first = claude_ingest.ingest_transcripts(repo)
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

    second = claude_ingest.ingest_transcripts(repo)
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

    claude_ingest.ingest_transcripts(repo)

    dest_root = repo / ".ai-traces" / "claude" / "raw" / "sessions"
    assert (dest_root / "spill" / "tool-results" / "abc.txt").exists()
    assert (dest_root / "spill" / "tool-results" / "abc.txt").read_text(encoding="utf-8") == "a" * 1000


def test_ingest_dedup_recopies_when_mtime_newer_despite_equal_size(tmp_path, fake_claude_home):
    """Equal-size rewrite must be re-copied — size-only dedup would freeze
    stale evidence in place (mirrors the codex_ingest test)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    src = _write_session(fake_claude_home, sid="rewrite", cwd=str(repo),
                         mtime=time.time() - 120)
    first = claude_ingest.ingest_transcripts(repo)
    assert len(first) == 1
    dest = repo / ".ai-traces" / "claude" / "raw" / "sessions" / src.name
    original_bytes = dest.read_bytes()

    replacement = original_bytes[:-1] + b"\n"
    assert len(replacement) == len(original_bytes)
    src.write_bytes(replacement)
    newer_mtime = time.time() + 5
    os.utime(src, (newer_mtime, newer_mtime))

    second = claude_ingest.ingest_transcripts(repo)

    assert len(second) == 1, (
        "equal-size rewrite must be re-copied, not skipped by size-only dedup"
    )
    assert dest.read_bytes() == replacement


def test_ingest_floor_skips_session_older_than_course_start(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "assignment-config.json").write_text(
        json.dumps({"course_start": "2026-01-12"}), encoding="utf-8"
    )
    pre_course = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    _write_session(fake_claude_home, sid="stale-semester", cwd=str(repo),
                   mtime=pre_course)

    copied = claude_ingest.ingest_transcripts(repo)

    assert copied == []


def test_ingest_floor_captures_session_at_or_after_course_start(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "assignment-config.json").write_text(
        json.dumps({"course_start": "2026-01-12"}), encoding="utf-8"
    )
    post_course = datetime(2026, 1, 13, tzinfo=timezone.utc).timestamp()
    matching = _write_session(fake_claude_home, sid="this-semester", cwd=str(repo),
                              mtime=post_course)

    copied = claude_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name


def test_ingest_no_floor_when_assignment_config_absent(tmp_path, fake_claude_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
    matching = _write_session(fake_claude_home, sid="ancient", cwd=str(repo),
                              mtime=long_ago)

    copied = claude_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name


def test_ingest_returns_empty_when_claude_home_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
    repo = tmp_path / "repo"
    repo.mkdir()

    assert claude_ingest.ingest_transcripts(repo) == []
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
    result = claude_ingest.ingest_transcripts(repo)
    assert result == []


def test_ingest_reports_matched_but_skipped_sessions(tmp_path, fake_claude_home):
    """A session that cleared cwd matching AND the course floor but is
    already up to date is 'matched, not copied'. Discarding it made the
    capture_counts trailer vanish on every steady-state run, which is
    indistinguishable from ingest being broken (bead 8g5.7)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_session(fake_claude_home, sid="steady", cwd=str(repo),
                   mtime=time.time() - 60)

    skipped: list = []
    first = claude_ingest.ingest_transcripts(repo, skipped=skipped)
    assert len(first) == 1
    assert skipped == [], "first ingest copies; nothing is skipped yet"

    skipped_second: list = []
    second = claude_ingest.ingest_transcripts(repo, skipped=skipped_second)
    assert second == [], "unchanged source must not be re-copied"
    assert len(skipped_second) == 1, (
        "an up-to-date match must be reported as skipped, not dropped"
    )


def test_skipped_excludes_non_matching_and_pre_course_sessions(
        tmp_path, fake_claude_home):
    """Counts stay matched-only: a foreign-cwd session and a pre-floor
    session must appear in NEITHER copied nor skipped, or the trailer
    would leak the student's AI use outside this course."""
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "somewhere-else"
    other.mkdir()
    (repo / "assignment-config.json").write_text(
        json.dumps({"course_start": datetime.now(timezone.utc)
                    .replace(microsecond=0).isoformat()}),
        encoding="utf-8",
    )
    _write_session(fake_claude_home, sid="foreign", cwd=str(other),
                   mtime=time.time() + 5)
    _write_session(fake_claude_home, sid="ancient", cwd=str(repo),
                   mtime=time.time() - 86400 * 30)

    skipped: list = []
    copied = claude_ingest.ingest_transcripts(repo, skipped=skipped)

    assert copied == []
    assert skipped == [], (
        "non-matching and pre-course sessions must not be counted as matched"
    )
