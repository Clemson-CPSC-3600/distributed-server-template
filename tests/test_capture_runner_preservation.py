"""Preserve assignment diagnostics when updating shared capture tooling."""
import json
import subprocess

import pytest

from run_tests import BundleTestRunner


@pytest.fixture
def runner(tmp_path):
    result = BundleTestRunner()
    result.root_dir = tmp_path
    return result


def result_row(passed=False, **extra):
    return {"file": "test_synthetic.py", "class": None,
            "name": "test_example", "passed": passed, "longrepr": "", **extra}


@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_focused_run_preserves_full_suite_cache(runner, capsys, passed, existing):
    runner.bundle = 2
    cache = runner.root_dir / runner.STATUS_CACHE_RELATIVE
    previous = b'{"grade":"A","points":100}\n'
    if existing:
        cache.parent.mkdir()
        cache.write_bytes(previous)
    runner.print_bundle_results({1: [], 2: [result_row(passed)], 3: []})
    output = capsys.readouterr().out
    assert "FOCUSED BUNDLE 2 RESULTS" in output
    assert "Grade Level Achieved" not in output
    assert "Grade Score" not in output
    assert f"{int(passed)}/1 tests passed" in output
    assert cache.exists() is existing
    if existing:
        assert cache.read_bytes() == previous


def test_rerun_hint_preserves_relative_quoted_parametrized_nodeid(runner, capsys):
    nodeid = str(runner.root_dir / "tests/nested/test_synthetic.py") + "::test_example[case with spaces]"
    runner._render_group_failures({"tests": [result_row(nodeid=nodeid)]})
    output = capsys.readouterr().out
    assert 'python -m pytest "tests/nested/test_synthetic.py::test_example[case with spaces]" -v' in output
    assert str(runner.root_dir) not in output


def test_legacy_result_rerun_hint_has_tests_directory_and_class(runner, capsys):
    runner._render_group_failures({"tests": [result_row(**{"class": "TestSynthetic"})]})
    assert 'python -m pytest "tests/test_synthetic.py::TestSynthetic::test_example" -v' in capsys.readouterr().out


def test_json_results_keep_complete_nodeid(runner, monkeypatch):
    nodeid = "tests/nested/test_synthetic.py::TestSynthetic::test_example[one]"
    markers = {"test_synthetic.py::test_example": {"bundle": 1, "points": 1}}
    monkeypatch.setattr(runner, "get_test_markers", lambda: markers)
    monkeypatch.setattr(runner, "get_selected_test_nodeids", lambda _: [nodeid])
    def run(command):
        (runner.root_dir / "test_results.json").write_text(json.dumps({"tests": [
            {"nodeid": nodeid, "outcome": "failed"}]}))
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
    monkeypatch.setattr(runner, "run_subprocess", run)
    _, bundles = runner.run_tests_with_json()
    assert bundles[1][0]["nodeid"] == nodeid


def test_verbose_fallback_results_have_rerunnable_nodeid(runner):
    bundles = runner.parse_pytest_verbose_output(
        "tests/test_synthetic.py::TestSynthetic::test_example FAILED",
        {"test_synthetic.py::test_example": {"bundle": 1, "points": 1}})
    assert bundles[1][0]["nodeid"] == "tests/test_synthetic.py::TestSynthetic::test_example"


def test_default_quiet_command_keeps_summary_suppression(runner):
    command = runner.build_pytest_command(["tests/test_synthetic.py::test_example"])
    assert "--no-summary" in command
    assert "-v" in runner.build_pytest_command(
        ["tests/test_synthetic.py::test_example"], force_verbose=True)
