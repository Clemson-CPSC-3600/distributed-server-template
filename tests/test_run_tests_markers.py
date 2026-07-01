"""Cover marker resolution edge cases in BundleTestRunner.get_test_markers().

The runner imports each test_*.py module and inspects pytestmark / decorator
state to decide which bundle a test belongs to. Three behaviors are
load-bearing:

1. Tests with no @pytest.mark.bundle decoration are excluded (they're
   template infrastructure, not graded). The runner counts them so the
   "X infrastructure tests skipped" line is accurate.

2. Class-level `pytestmark = [...]` is inherited by methods that don't
   override it.

3. A method-level @pytest.mark.bundle overrides a class-level one. This is
   how an instructor lifts a single test out of a class's default bundle.

4. Tests in subdirectories (rglob, not glob) are scanned. A reorganized
   test layout shouldn't silently disappear from the grade.

5. Parametrized test names like `test_foo[case_a]` resolve back to the
   bundle marker on the underlying `test_foo`. (Tested at the JSON-parse
   layer in test_run_tests_parametrize.py for now -- this file covers the
   marker-extraction side.)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_runner_module():
    """Import run_tests.py once for use by every test in this file."""
    spec = importlib.util.spec_from_file_location(
        "run_tests_under_test", PROJECT_ROOT / "run_tests.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner_module():
    return _load_runner_module()


@pytest.fixture
def fake_project(tmp_path, runner_module):
    """Build a fake project tree with a tests/ dir and an instantiated
    BundleTestRunner pointed at it. Caller writes test files into
    `tests_dir` then calls `scan()` to get back the marker dict."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")

    runner = runner_module.BundleTestRunner.__new__(runner_module.BundleTestRunner)
    runner.root_dir = tmp_path
    runner._cached_test_markers = None
    runner._unmarked_count = 0

    def scan():
        runner._cached_test_markers = None
        return runner.get_test_markers()

    return tests_dir, runner, scan


def test_unmarked_function_is_excluded_and_counted(fake_project):
    tests_dir, runner, scan = fake_project
    (tests_dir / "test_x.py").write_text(
        "def test_no_marker():\n"
        "    assert True\n"
    )
    markers = scan()
    assert markers == {}
    assert runner._unmarked_count == 1


def test_marked_function_resolves_to_its_bundle(fake_project):
    tests_dir, runner, scan = fake_project
    (tests_dir / "test_x.py").write_text(
        "import pytest\n"
        "@pytest.mark.bundle(2)\n"
        "def test_marked():\n"
        "    assert True\n"
    )
    markers = scan()
    assert "test_x.py::test_marked" in markers
    assert markers["test_x.py::test_marked"]["bundle"] == 2
    assert runner._unmarked_count == 0


def test_class_pytestmark_is_inherited_by_methods(fake_project):
    tests_dir, runner, scan = fake_project
    (tests_dir / "test_x.py").write_text(
        "import pytest\n"
        "class TestThing:\n"
        "    pytestmark = [pytest.mark.bundle(2)]\n"
        "    def test_a(self):\n"
        "        assert True\n"
        "    def test_b(self):\n"
        "        assert True\n"
    )
    markers = scan()
    assert markers["test_x.py::test_a"]["bundle"] == 2
    assert markers["test_x.py::test_b"]["bundle"] == 2


def test_method_level_bundle_overrides_class_pytestmark(fake_project):
    tests_dir, runner, scan = fake_project
    (tests_dir / "test_x.py").write_text(
        "import pytest\n"
        "class TestThing:\n"
        "    pytestmark = [pytest.mark.bundle(2)]\n"
        "    def test_inherits(self):\n"
        "        assert True\n"
        "    @pytest.mark.bundle(3)\n"
        "    def test_overrides(self):\n"
        "        assert True\n"
    )
    markers = scan()
    assert markers["test_x.py::test_inherits"]["bundle"] == 2
    assert markers["test_x.py::test_overrides"]["bundle"] == 3


def test_subdirectory_test_files_are_scanned(fake_project):
    """rglob, not glob -- a reorganized test/ tree shouldn't disappear
    from grading."""
    tests_dir, runner, scan = fake_project
    nested = tests_dir / "protocol"
    nested.mkdir()
    (nested / "__init__.py").write_text("")
    (nested / "test_message.py").write_text(
        "import pytest\n"
        "@pytest.mark.bundle(1)\n"
        "def test_parse():\n"
        "    assert True\n"
    )
    markers = scan()
    assert "test_message.py::test_parse" in markers
    assert markers["test_message.py::test_parse"]["bundle"] == 1


def test_class_without_explicit_pytestmark_marks_unmarked(fake_project):
    """A TestClass with no class-level pytestmark and no method-level
    bundle markers should produce no entries -- and should count its
    methods toward the unmarked total so the runner reports them."""
    tests_dir, runner, scan = fake_project
    (tests_dir / "test_x.py").write_text(
        "class TestThing:\n"
        "    def test_a(self):\n"
        "        assert True\n"
        "    def test_b(self):\n"
        "        assert True\n"
    )
    markers = scan()
    assert markers == {}
    assert runner._unmarked_count == 2
