"""The integrity check cannot be pointed at a pristine decoy tree.

INFRASTRUCTURE tests -- deliberately unmarked, so the student-facing runner
(run_tests.py) never grades them.

The evasion these tests pin down: verify_integrity.py reads the Claude hook
tree from ``.claude-student/`` in instructor mode and from ``.claude/`` in
student mode. project-template-config.json says which mode applies, and that
file is not hash-pinned. So a student could set it to "instructor", add an
untouched copy of the hooks at ``.claude-student/``, edit the real
``.claude/`` hooks, and the check would hash the untouched copy.

Two independent defenses close it, and each is tested on its own:

- the shipped workflow forces ``--mode student`` for every distributed copy,
  fork and unknown identity; only the exact authoring repository is exempt;
- ``distribution_mode()`` only believes "instructor" when the repo is laid
  out like one: ``.claude-student/`` AND ``create-assignment.sh`` both exist
  (the generator script never ships to students).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools import verify_integrity

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "integrity.yml"
EDITED_HOOK = ".claude/hooks/stop.py"


def _student_repo(base: Path) -> Path:
    """A throwaway student repo whose baseline matches its own files.

    Pinned files get small placeholder contents: the check compares hashes,
    so what the bytes say does not matter, only whether they changed.
    """
    repo = base / "student-repo"
    for rel in verify_integrity.TRACKED + verify_integrity.SOFT_TRACKED:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"pristine contents of {rel}\n", encoding="utf-8")
    (repo / "tools").mkdir(exist_ok=True)
    shutil.copy2(REPO_ROOT / "tools" / "verify_integrity.py", repo / "tools")
    (repo / "project-template-config.json").write_text(
        json.dumps({"distribution_mode": "student", "capture_enabled": True}), encoding="utf-8")

    # Record the clean baseline the same way an instructor would.
    assert _verify(repo, "--update").returncode == 0
    assert _verify(repo).returncode == 0, "fresh repo should verify clean"
    return repo


def _apply_bypass(repo: Path) -> None:
    """Edit a real hook, then try to make the check look at a decoy copy."""
    shutil.copytree(repo / ".claude", repo / ".claude-student")
    (repo / EDITED_HOOK).write_text("# capture disabled\n", encoding="utf-8")
    (repo / "project-template-config.json").write_text(
        json.dumps({"distribution_mode": "instructor", "capture_enabled": True}), encoding="utf-8")


def _verify(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "verify_integrity.py"), *args],
        capture_output=True, text=True, timeout=60,
    )


def test_config_alone_cannot_switch_a_student_repo_to_instructor_mode(tmp_path: Path):
    repo = _student_repo(tmp_path)
    _apply_bypass(repo)

    result = _verify(repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"MODIFIED: {EDITED_HOOK}" in result.stdout


def test_student_mode_flag_ignores_an_instructor_layout(tmp_path: Path):
    """Even a student who also fakes create-assignment.sh is caught by CI."""
    repo = _student_repo(tmp_path)
    _apply_bypass(repo)
    (repo / "create-assignment.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    # Without the flag the faked layout is believed ...
    assert _verify(repo).returncode == 0

    result = _verify(repo, "--mode", "student")

    # ... with it, the real .claude/ tree is what gets hashed.
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"MODIFIED: {EDITED_HOOK}" in result.stdout


def test_mode_flag_only_accepts_student(tmp_path: Path):
    """The flag exists to tighten the check; it must never loosen it."""
    repo = _student_repo(tmp_path)

    result = _verify(repo, "--mode", "instructor")

    assert result.returncode == 2
    assert "student" in result.stdout + result.stderr


def test_update_refuses_a_forced_mode(tmp_path: Path):
    """--update must hash the files the baseline is keyed to for THIS repo.

    Forcing student mode during an update in the instructor repo would bake
    the live .claude/ hashes into the baseline, so the combination is refused.
    """
    repo = _student_repo(tmp_path)
    before = (repo / "tools" / "INTEGRITY_HASHES.txt").read_bytes()

    result = _verify(repo, "--update", "--mode", "student")

    assert result.returncode == 2, result.stdout + result.stderr
    assert (repo / "tools" / "INTEGRITY_HASHES.txt").read_bytes() == before


def test_instructor_mode_requires_the_instructor_layout(tmp_path: Path):
    instructor_config = json.dumps({"distribution_mode": "instructor"})

    decoy_only = tmp_path / "decoy-only"
    (decoy_only / ".claude-student").mkdir(parents=True)
    (decoy_only / "project-template-config.json").write_text(instructor_config, encoding="utf-8")
    assert verify_integrity.distribution_mode(decoy_only) == "student"

    script_only = tmp_path / "script-only"
    script_only.mkdir()
    (script_only / "create-assignment.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (script_only / "project-template-config.json").write_text(instructor_config, encoding="utf-8")
    assert verify_integrity.distribution_mode(script_only) == "student"

    real = tmp_path / "instructor"
    (real / ".claude-student").mkdir(parents=True)
    (real / "create-assignment.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (real / "project-template-config.json").write_text(instructor_config, encoding="utf-8")
    assert verify_integrity.distribution_mode(real) == "instructor"

# Workflow mode selection is executed against trusted, student and fork
# identities in test_capture_validation_regressions.py. A literal command-line
# assertion here would incorrectly reject the instructor-only exception.
