"""Unit tests for course_floor — the course_start time-floor helper shared
by the codex/claude/vscode_chat ingesters.

The floor lives in ``<repo>/assignment-config.json`` (deliberately NOT
``project-template-config.json`` — see module docstring under test for why).
Missing file, missing key, and malformed values must all resolve to "no
floor" (None) rather than raising.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests._capture import course_floor


def _write_config(repo: Path, course_start) -> None:
    payload = {} if course_start is None else {"course_start": course_start}
    (repo / "assignment-config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_config_file_yields_no_floor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert course_floor.get_course_start_floor(repo) is None


def test_missing_course_start_key_yields_no_floor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, None)
    assert course_floor.get_course_start_floor(repo) is None


def test_malformed_json_yields_no_floor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "assignment-config.json").write_text("{not json", encoding="utf-8")
    assert course_floor.get_course_start_floor(repo) is None


def test_malformed_course_start_value_yields_no_floor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, "not-a-date")
    assert course_floor.get_course_start_floor(repo) is None


def test_valid_iso_date_resolves_to_a_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, "2026-01-12")

    floor = course_floor.get_course_start_floor(repo)

    assert floor is not None
    # 2026-01-12T00:00:00Z
    assert floor == 1768176000.0


def test_valid_iso_datetime_with_z_suffix_resolves(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, "2026-01-12T09:30:00Z")

    floor = course_floor.get_course_start_floor(repo)

    assert floor == 1768210200.0


def test_file_predates_course_with_no_floor_is_always_false() -> None:
    assert course_floor.file_predates_course(0.0, None) is False


def test_file_predates_course_compares_against_floor() -> None:
    assert course_floor.file_predates_course(100.0, 200.0) is True
    assert course_floor.file_predates_course(200.0, 200.0) is False
    assert course_floor.file_predates_course(300.0, 200.0) is False
