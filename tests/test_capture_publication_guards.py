"""Publication validation must preserve credential and starter-content guards."""
import json
import shutil

import pytest

from tools import validate_student_repo


@pytest.fixture
def student(tmp_path):
    (tmp_path / "project-template-config.json").write_text(json.dumps({
        "distribution_mode": "student", "capture_enabled": False,
    }))
    (tmp_path / "README.md").write_text("Synthetic assignment\n")
    (tmp_path / "run_tests.py").write_text("# synthetic runner\n")
    (tmp_path / "tests").mkdir()
    for directory in ("template", "src"):
        (tmp_path / directory / "nested").mkdir(parents=True)
        (tmp_path / directory / "nested/main.py").write_text("pass\n")
        (tmp_path / directory / "data.bin").write_bytes(b"\x00synthetic starter\xff")
    return tmp_path


def test_clean_matching_starter_passes(student):
    assert validate_student_repo.main([str(student)]) == 0


@pytest.mark.parametrize("capture_enabled", [False, True])
def test_authentication_file_is_always_rejected(student, capsys, capture_enabled):
    config = student / "project-template-config.json"
    config.write_text(json.dumps({"distribution_mode": "student",
                                  "capture_enabled": capture_enabled}))
    (student / ".codex").mkdir()
    (student / ".codex/auth.json").write_text('{"synthetic":"not-a-real-credential"}')
    assert validate_student_repo.main([str(student)]) == 1
    assert ".codex/auth.json is present" in capsys.readouterr().out


@pytest.mark.parametrize("drift", ["missing_file", "extra_file", "changed_file", "changed_binary"])
def test_starter_drift_is_rejected(student, capsys, drift):
    if drift == "missing_file":
        (student / "src/nested/main.py").unlink()
    elif drift == "extra_file":
        (student / "src/completed.py").write_text("# synthetic implementation\n")
    elif drift == "changed_file":
        (student / "src/nested/main.py").write_text("print('synthetic implementation')\n")
    else:
        (student / "src/data.bin").write_bytes(b"\x00changed synthetic data\xff")
    assert validate_student_repo.main([str(student)]) == 1
    assert "src/ does not match template/ starter content" in capsys.readouterr().out


@pytest.mark.parametrize("directory", ["src", "template"])
def test_missing_starter_directory_is_rejected(student, capsys, directory):
    shutil.rmtree(student / directory)
    assert validate_student_repo.main([str(student)]) == 1
    assert directory + "/ directory is missing" in capsys.readouterr().out


def test_bytecode_and_cache_files_are_not_starter_content(student):
    (student / "src/__pycache__").mkdir()
    (student / "src/__pycache__/stale.pyc").write_bytes(b"synthetic cache")
    (student / "template/orphan.pyc").write_bytes(b"synthetic orphan")
    assert validate_student_repo.main([str(student)]) == 0


@pytest.mark.parametrize("name", ["AGENTS.md", "AI_POLICY.md", "PROCESS_TRACKING.md", "README.md"])
def test_injected_integration_guard_remains_active(student, capsys, name):
    (student / name).write_text("<!-- BEGIN SYNTHETIC INTEGRATION -->\n")
    assert validate_student_repo.main([str(student)]) == 1
    assert "contains an injected tool-integration block" in capsys.readouterr().out
