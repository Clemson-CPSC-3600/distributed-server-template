"""End-to-end check that @pytest.mark.parametrize variants get graded.

The bug we're guarding against: pytest's JSON report writes nodeids like
`test_foo[case_a]` for parametrized variants, but the marker scan keys
entries by the bare function name `test_foo`. Without the suffix-strip in
the JSON parser, every parametrize variant looked unrecognized and got
silently dropped from the bundle counts -- a bundle could read 0/0 even
though pytest had run dozens of variants and they had all passed.

We exercise the path end-to-end by writing a synthetic parametrized test
into a temp project, invoking BundleTestRunner.run_tests_with_json against
it, and asserting every variant landed in the right bundle.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_tests_under_test", PROJECT_ROOT / "run_tests.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner_module():
    return _load_runner_module()


def test_parametrize_variants_are_graded(tmp_path, runner_module, monkeypatch):
    """A parametrized test with @pytest.mark.bundle(N) should contribute
    every variant to bundle N's count, not 0."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    # conftest registers the bundle marker so --strict-markers doesn't fail
    # under the inner pytest process.
    (tests_dir / "conftest.py").write_text(
        "def pytest_configure(config):\n"
        "    config.addinivalue_line('markers', 'bundle(level): bundle')\n"
    )
    (tests_dir / "test_param.py").write_text(
        "import pytest\n"
        "@pytest.mark.bundle(1)\n"
        "@pytest.mark.parametrize('value', ['alpha', 'beta', 'gamma'])\n"
        "def test_each(value):\n"
        "    assert value in {'alpha', 'beta', 'gamma'}\n"
    )

    runner = runner_module.BundleTestRunner.__new__(runner_module.BundleTestRunner)
    runner.root_dir = tmp_path
    runner.solution_dir = tmp_path / "solution"
    runner.src_dir = tmp_path / "src"
    runner.backup_dir = None
    runner.verbose = False
    runner.bundle = None
    runner.show_all = False
    runner.pytest_args = []
    runner._capture_ctx = None
    runner._pytest_proc = None
    runner._component_groups = []
    runner._cached_test_markers = None
    runner._unmarked_count = 0

    exit_code, bundles_data = runner.run_tests_with_json()

    assert exit_code == 0
    bundle_1_names = [t["name"] for t in bundles_data[1]]
    assert sorted(bundle_1_names) == [
        "test_each[alpha]",
        "test_each[beta]",
        "test_each[gamma]",
    ], (
        "All three parametrize variants should grade into Bundle 1, not "
        f"silently disappear. Got: {bundle_1_names}"
    )
    assert all(t["passed"] for t in bundles_data[1])
    assert bundles_data[2] == []
    assert bundles_data[3] == []
