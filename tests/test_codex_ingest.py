"""Unit tests for codex_ingest — fake $CODEX_HOME, no real Codex binary needed.

The module under test scans ``$CODEX_HOME/sessions/`` recursively for Codex
rollout JSONL files, filters them by cwd match and (when configured) a
course-start floor, and copies matches into
``<repo>/.ai-traces/codex/raw/rollouts/``. Dedup is mtime+size-aware: skip
only when the destination is at least as large AND at least as new as the
source; re-copy when the source has grown OR was rewritten in place at an
unchanged size. We don't filter against the pytest session start (as
opposed to the course-start floor, which is a different, file-mtime-based
check) because the realistic workflow writes the rollout BEFORE the pytest
session starts.

All fixture rollouts use the actual session-meta shape discovered in Task 1:
``{"type": "session_meta", "payload": {"id": sid, "cwd": cwd, ...}}``.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests._capture import codex_ingest


@pytest.fixture
def fake_codex_home(tmp_path, monkeypatch):
    """Create a fake $CODEX_HOME with an empty sessions/ tree."""
    home = tmp_path / "codex_home"
    (home / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _write_rollout(home: Path, sid: str, cwd: str, mtime: float,
                   date_subdir: str = "2026/04/22") -> Path:
    """Write a minimally-valid rollout JSONL file under sessions/YYYY/MM/DD/.

    The first line is a session_meta record with payload.cwd, matching the
    real shape discovered in Task 1. Sets the file's mtime so the filter
    can be exercised.
    """
    sessions_root = home / "sessions"
    subdir = sessions_root / date_subdir
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"rollout-{sid}.jsonl"
    header = {
        "timestamp": "2026-04-22T13:28:53.120Z",
        "type": "session_meta",
        "payload": {
            "id": sid,
            "timestamp": "2026-04-22T13:28:46.061Z",
            "cwd": cwd,
            "originator": "codex_exec",
            "cli_version": "0.119.0-alpha.28",
        },
    }
    second = {"type": "response_item", "payload": {"role": "user"}}
    path.write_text(
        json.dumps(header) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def test_ingest_copies_rollouts_whose_cwd_matches_repo(tmp_path, fake_codex_home):
    repo = tmp_path / "repo"
    repo.mkdir()

    matching = _write_rollout(
        fake_codex_home, sid="aaa", cwd=str(repo), mtime=time.time() - 30,
    )
    _write_rollout(
        fake_codex_home, sid="bbb", cwd=str(tmp_path / "elsewhere"),
        mtime=time.time() - 30,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name
    dest = repo / ".ai-traces" / "codex" / "raw" / "rollouts"
    assert (dest / matching.name).exists()
    assert not (dest / "rollout-bbb.jsonl").exists()


def test_ingest_captures_rollouts_created_before_session_start(tmp_path, fake_codex_home):
    """Regression guard for the smoke-test design flaw: a rollout written
    BEFORE the pytest session started (the common case — student uses Codex,
    then runs pytest) MUST still be captured. The previous mtime-based filter
    silently dropped every such rollout.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    session_started_at = time.time()

    _write_rollout(
        fake_codex_home, sid="earlier", cwd=str(repo),
        mtime=session_started_at - 3600,  # one hour before pytest started
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert len(copied) == 1, (
        "Rollouts finalized before pytest session start must still be captured "
        "— that's the typical workflow. The cwd match + idempotency are the "
        "canonical selection criteria; an mtime cutoff would defeat the point."
    )


def test_ingest_ignores_rollouts_with_different_cwd(tmp_path, fake_codex_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other_project"
    other.mkdir()

    _write_rollout(
        fake_codex_home, sid="other", cwd=str(other),
        mtime=time.time() - 30,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert copied == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_copies_rollout_with_cwd_in_subdir_of_repo(tmp_path, fake_codex_home):
    """A rollout whose cwd is a subdirectory of the repo (e.g. the student
    ran Codex from ``repo/src``) must still be captured — the cwd match is
    "within the repo", not "exactly equal to the repo root"."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    matching = _write_rollout(
        fake_codex_home, sid="subdir", cwd=str(repo / "src"),
        mtime=time.time() - 30,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name


def test_ingest_is_idempotent_when_source_unchanged(tmp_path, fake_codex_home):
    repo = tmp_path / "repo"
    repo.mkdir()

    _write_rollout(
        fake_codex_home, sid="dup", cwd=str(repo), mtime=time.time() - 30,
    )

    first = codex_ingest.ingest_transcripts(repo)
    second = codex_ingest.ingest_transcripts(repo)

    assert len(first) == 1
    assert second == []  # source size unchanged → skip


def test_ingest_recopies_when_source_grew(tmp_path, fake_codex_home):
    """Codex IDE / desktop append turns to a single rollout file across a
    session. After the first ingest, the source keeps growing; subsequent
    ingests must overwrite the destination so the latest turns are captured.
    A pure ``dest.exists() → skip`` check freezes the captured copy at
    whatever size it had on first ingest and silently loses every later turn.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    src = _write_rollout(
        fake_codex_home, sid="growing", cwd=str(repo), mtime=time.time() - 30,
    )
    first = codex_ingest.ingest_transcripts(repo)
    assert len(first) == 1
    dest = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / src.name
    initial_size = dest.stat().st_size

    appended = {"type": "response_item", "payload": {"role": "assistant"}}
    with src.open("a", encoding="utf-8") as f:
        f.write(json.dumps(appended) + "\n")
    grown_size = src.stat().st_size
    assert grown_size > initial_size

    second = codex_ingest.ingest_transcripts(repo)
    assert len(second) == 1, (
        "Source rollout grew between ingests — destination must be refreshed."
    )
    assert dest.stat().st_size == grown_size


def test_ingest_returns_empty_when_codex_home_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nonexistent"))
    repo = tmp_path / "repo"
    repo.mkdir()

    assert codex_ingest.ingest_transcripts(repo) == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_swallows_errors_on_malformed_jsonl(tmp_path, fake_codex_home):
    """Capture layer MUST never raise — matches capture.py's error contract."""
    # Create a rollout with unreadable content under a date subdir so rglob finds it.
    bad_subdir = fake_codex_home / "sessions" / "2026" / "04" / "22"
    bad_subdir.mkdir(parents=True, exist_ok=True)
    bad = bad_subdir / "rollout-bad.jsonl"
    bad.write_bytes(b"\xff\xfenot-utf8-not-json\n")
    os.utime(bad, (time.time() - 30, time.time() - 30))
    repo = tmp_path / "repo"
    repo.mkdir()

    # Must not raise even though the rollout is garbage; malformed rollouts
    # are silently skipped because their cwd can't be extracted.
    result = codex_ingest.ingest_transcripts(repo)
    assert result == []


def test_ingest_dedup_recopies_when_mtime_newer_despite_equal_size(tmp_path, fake_codex_home):
    """Equal-size rewrite: source is edited in place so its byte count is
    unchanged but its content (and mtime, since it was rewritten) changed.
    Size-only dedup would freeze the stale destination copy forever; the
    mtime+size check must catch this and re-copy.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    src = _write_rollout(
        fake_codex_home, sid="rewrite", cwd=str(repo), mtime=time.time() - 120,
    )
    first = codex_ingest.ingest_transcripts(repo)
    assert len(first) == 1
    dest = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / src.name
    original_bytes = dest.read_bytes()

    # Rewrite src with different content but the SAME byte length, and a
    # newer mtime (as any genuine rewrite would have).
    replacement = original_bytes[:-1] + b"\n"
    assert len(replacement) == len(original_bytes)
    src.write_bytes(replacement)
    newer_mtime = time.time() + 5
    os.utime(src, (newer_mtime, newer_mtime))

    second = codex_ingest.ingest_transcripts(repo)

    assert len(second) == 1, (
        "equal-size rewrite must be re-copied — size-only dedup would skip it "
        "and leave stale evidence in place."
    )
    assert dest.read_bytes() == replacement


def test_ingest_floor_skips_rollout_older_than_course_start(tmp_path, fake_codex_home):
    """A rollout whose file mtime predates assignment-config.json's
    course_start must not be captured — that's the privacy floor.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "assignment-config.json").write_text(
        json.dumps({"course_start": "2026-01-12"}), encoding="utf-8"
    )
    pre_course = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()

    _write_rollout(
        fake_codex_home, sid="stale-semester", cwd=str(repo), mtime=pre_course,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert copied == [], (
        "a rollout with mtime before course_start must be excluded by the "
        "time floor"
    )


def test_ingest_floor_captures_rollout_at_or_after_course_start(tmp_path, fake_codex_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "assignment-config.json").write_text(
        json.dumps({"course_start": "2026-01-12"}), encoding="utf-8"
    )
    post_course = datetime(2026, 1, 13, tzinfo=timezone.utc).timestamp()

    matching = _write_rollout(
        fake_codex_home, sid="this-semester", cwd=str(repo), mtime=post_course,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name


def test_ingest_no_floor_when_assignment_config_absent(tmp_path, fake_codex_home):
    """No assignment-config.json at all -> no floor -> capture everything,
    matching today's behavior (see course_floor module docstring)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()

    matching = _write_rollout(
        fake_codex_home, sid="ancient", cwd=str(repo), mtime=long_ago,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert len(copied) == 1
    assert copied[0].name == matching.name


def test_ingest_matches_cwd_with_mismatched_drive_case(tmp_path, fake_codex_home):
    """Windows drive-letter casing varies by Codex source: ``codex exec`` emits
    ``C:\\...`` but the VS Code extension emits ``c:\\...``. Path normalization
    must treat these as equal, otherwise half of all student rollouts silently
    fail to match.

    On non-Windows platforms this test still runs; ``Path.resolve()`` is a
    no-op for casing there so the assertion reduces to "identical path strings
    match", which is trivially true and does not hurt anything.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    repo_str = str(repo.resolve())
    # Flip the casing of the drive letter if one exists (Windows); otherwise
    # just use the path as-is (POSIX — this branch is effectively a no-op
    # since resolve() already canonicalizes case-sensitive paths).
    if len(repo_str) >= 2 and repo_str[1] == ":":
        drive = repo_str[0]
        flipped = (drive.lower() if drive.isupper() else drive.upper()) + repo_str[1:]
    else:
        flipped = repo_str

    _write_rollout(
        fake_codex_home, sid="case", cwd=flipped, mtime=time.time() - 30,
    )

    copied = codex_ingest.ingest_transcripts(repo)

    assert len(copied) == 1, (
        f"Expected cwd with flipped drive case {flipped!r} to match "
        f"repo {repo_str!r} after normalization; got copied={copied}."
    )


def test_ingest_reports_matched_but_skipped_rollouts(tmp_path, fake_codex_home):
    """An up-to-date match is 'matched, not copied'. Dropping it made the
    capture_counts trailer vanish in steady state (bead 8g5.7)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_rollout(fake_codex_home, sid="steady", cwd=str(repo),
                   mtime=time.time() - 60)

    skipped: list = []
    first = codex_ingest.ingest_transcripts(repo, skipped=skipped)
    assert len(first) == 1
    assert skipped == []

    skipped_second: list = []
    second = codex_ingest.ingest_transcripts(repo, skipped=skipped_second)
    assert second == []
    assert len(skipped_second) == 1, (
        "an up-to-date match must be reported as skipped, not dropped"
    )


def test_skipped_excludes_rollouts_outside_the_repo(tmp_path, fake_codex_home):
    """A rollout from another project must land in NEITHER list -- counting
    it would leak the student's AI use outside this course."""
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write_rollout(fake_codex_home, sid="foreign", cwd=str(other),
                   mtime=time.time() - 60)

    skipped: list = []
    copied = codex_ingest.ingest_transcripts(repo, skipped=skipped)

    assert copied == []
    assert skipped == []
