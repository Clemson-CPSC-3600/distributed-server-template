#!/usr/bin/env python3
"""
Test Runner with Specification Grading Support
Runs tests organized by bundles (1, 2, 3) and reports grade level achieved.

This script:
- If the 'solution' directory contains Python files: copies them to src/, runs tests,
  then restores the original student files
- If no solution files are present: runs tests with the existing src/ implementation
- Supports bundle-focused runs while keeping pytest passthrough arguments available
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Belt-and-suspenders capture -- real commits happen in tests/conftest.py,
# this layer only fires if pytest itself never started (e.g., src/ won't import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from tests._capture import capture as _capture
except ImportError:
    _capture = None

try:
    from tools import preflight as _preflight
except ImportError:
    _preflight = None


def assert_in_venv() -> None:
    """Refuse to run unless invoked from the project's local venv.

    Strict check: sys.prefix must be a `venv/` or `.venv/` directory
    directly inside the project root. A global venv, a conda env, or a
    venv belonging to another project is rejected — capture infrastructure
    must not be installed outside the project boundary.

    Honors CAPTURE_FORCE_NO_VENV=<anything> as a test-only override that
    simulates the no-venv condition.
    """
    project_root = Path(__file__).resolve().parent
    if os.environ.get("CAPTURE_FORCE_NO_VENV"):
        in_local_venv = False
    else:
        sys.path.insert(0, str(project_root))
        try:
            from tests._capture.runtime_triggers import is_local_venv
        except ImportError:
            is_local_venv = None
        if is_local_venv is None:
            in_local_venv = False
        else:
            in_local_venv = is_local_venv(Path(sys.prefix), project_root)
    if not in_local_venv:
        sys.stderr.write(
            "ERROR: run_tests.py must be run from inside this project's "
            "local venv (a 'venv/' or '.venv/' directory inside this folder).\n"
            "\n"
            "Set up the venv:\n"
            "  python -m venv venv\n"
            "  venv\\Scripts\\activate    (Windows)\n"
            "  source venv/bin/activate   (macOS/Linux)\n"
            "  pip install -r requirements.txt\n"
            "\n"
            "If you haven't already, copy the contents of template/ into src/\n"
            "to begin working:\n"
            "  cp -r template/. src/             (macOS/Linux)\n"
            "  xcopy template\\* src\\ /E /I    (Windows)\n"
            "\n"
            "Then re-run: python run_tests.py\n"
        )
        sys.exit(2)


# ANSI color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


class BundleTestRunner:
    def __init__(
        self,
        verbose=False,
        bundle=None,
        pytest_args=None,
        failed_only=False,
        show_all=False,
    ):
        self.root_dir = Path(__file__).parent.absolute()
        self.solution_dir = self.root_dir / "solution"
        self.src_dir = self.root_dir / "src"
        self.backup_dir = None
        self.verbose = verbose
        self.bundle = bundle
        self.show_all = show_all
        self.pytest_args = list(pytest_args or [])
        self._capture_ctx = None
        self._pytest_proc = None
        self._component_groups = self._load_component_groups()
        self._cached_test_markers = None

        if failed_only:
            self.pytest_args.append("--lf")

    def _load_component_groups(self):
        """Read optional component_groups from project-template-config.json.

        Schema (all fields optional; absent -> auto-group by filename):
            "component_groups": [
                {"file": "test_x.py", "label": "Protocol", "depends_on": []},
                {"file": "test_y.py", "label": "Agent",
                 "depends_on": ["test_x.py"]},
                ...
            ]

        Returns a list preserving config order. Each entry has keys
        file/label/depends_on. Returns [] on any parse error or absent key
        so the runner falls back to alphabetical-by-file grouping.
        """
        path = self.root_dir / "project-template-config.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw = data.get("component_groups") or []
        groups = []
        for entry in raw:
            if not isinstance(entry, dict) or "file" not in entry:
                continue
            groups.append({
                "file": entry["file"],
                "label": entry.get("label", entry["file"]),
                "depends_on": list(entry.get("depends_on") or []),
            })
        return groups

    def _subprocess_env(self):
        """Return an env for pytest subprocesses with capture session vars
        set if an outer capture session is active."""
        env = os.environ.copy()
        if self._capture_ctx is not None:
            env["CAPTURE_SESSION_ID"] = self._capture_ctx.session_id
            env["CAPTURE_STARTED_AT"] = str(self._capture_ctx.started_at)
            # Always record run_tests.py results, even when the tracked tree
            # is unchanged from the previous snapshot. The inner pytest's
            # conftest reads this and forwards it through session_finish.
            env["CAPTURE_FORCE_SNAPSHOT"] = "1"
        return env

    def _count_tests(self) -> int:
        """Pre-count the test suite so the watchdog deadline scales to reality.

        Runs pytest --collect-only -q. When test markers exist, collects
        only the marked nodeids so the count reflects what the runner will
        actually grade -- otherwise the deadline would be sized for hundreds
        of unmarked infrastructure tests that get filtered out, leaving the
        watchdog window oversized for student work. Returns the integer
        count, or 10 on any error (preserving the existing default). The
        probe costs about half a second -- trivial next to a test run that
        may be minutes long.
        """
        probe_env = os.environ.copy()
        probe_env["CAPTURE_DISABLED"] = "1"
        cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
        markers = self.get_test_markers()
        if markers:
            cmd.extend(m["nodeid"] for m in markers.values())
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
                env=probe_env,
            )
        except (subprocess.SubprocessError, OSError):
            return 10
        # Scan the last 10 lines for the summary. pytest may emit earlier output.
        tail = "\n".join(result.stdout.splitlines()[-10:])
        m = re.search(r"(\d+)\s+tests?\s+collected", tail)
        if m:
            return int(m.group(1))
        return 10

    def _capture_is_enabled(self) -> bool:
        """Cheap read of project-template-config.json. Conservative: returns
        False on any error so we don't incur the count probe unnecessarily."""
        path = self.root_dir / "project-template-config.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(data.get("capture_enabled"))

    def get_python_files(self, directory):
        """Get all top-level Python files in a directory."""
        if not directory.exists():
            return []
        return [f.name for f in directory.glob("*.py") if f.is_file()]

    def has_solution_files(self):
        """Return True when the solution directory contains actual Python files."""
        return bool(self.get_python_files(self.solution_dir))

    def create_backup(self):
        """Backup original src files."""
        if not self.src_dir.exists():
            return

        self.backup_dir = Path(tempfile.mkdtemp(prefix="test_backup_"))
        print(f"Creating backup in: {self.backup_dir}")

        for filename in self.get_python_files(self.src_dir):
            src = self.src_dir / filename
            dst = self.backup_dir / filename
            shutil.copy2(src, dst)
            if self.verbose:
                print(f"  Backed up: {filename}")

    def copy_solution_files(self):
        """Copy solution files to src directory."""
        if not self.solution_dir.exists():
            raise RuntimeError(f"Solution directory not found: {self.solution_dir}")

        self.src_dir.mkdir(exist_ok=True)

        print("Copying solution files to src directory...")
        solution_files = self.get_python_files(self.solution_dir)

        if not solution_files:
            print("  Warning: No Python files found in solution directory")
            return

        for filename in solution_files:
            src = self.solution_dir / filename
            dst = self.src_dir / filename
            shutil.copy2(src, dst)
            if self.verbose:
                print(f"  Copied: {filename}")

    def restore_backup(self):
        """Restore original files from backup."""
        if not self.backup_dir or not self.backup_dir.exists():
            return

        print("\nRestoring original files...")

        for filename in self.get_python_files(self.src_dir):
            (self.src_dir / filename).unlink()

        for filename in self.get_python_files(self.backup_dir):
            src = self.backup_dir / filename
            dst = self.src_dir / filename
            shutil.copy2(src, dst)
            if self.verbose:
                print(f"  Restored: {filename}")

        shutil.rmtree(self.backup_dir)
        print("Backup cleaned up")

    def get_test_markers(self):
        """Extract bundle markers by importing test modules.

        Handles both top-level test_ functions and Test* classes with
        test_ methods. Class-level pytestmark decorators are inherited
        by the methods unless the method overrides them.

        Only tests with an explicit @pytest.mark.bundle(N) decoration are
        returned. Unmarked tests (typically template infrastructure -- the
        capture system, orchestrator, codex ingest, preflight, etc.) are
        omitted so they don't contribute to the student's grade. We also
        track the count of unmarked test files so the runner can tell the
        student "X infrastructure tests were skipped" instead of silently
        dropping them.

        Result is cached on self._cached_test_markers so callers (the
        watchdog probe, the JSON path, the verbose path) share a single
        scan -- module imports during the scan can be expensive.
        """
        if self._cached_test_markers is not None:
            return self._cached_test_markers

        def _resolve(obj, inherited=None):
            bundle, points = None, 0
            marks = list(inherited or [])
            marks.extend(getattr(obj, "pytestmark", []))
            for mark in marks:
                if mark.name == "bundle" and mark.args:
                    bundle = mark.args[0]
                elif mark.name == "points" and mark.args:
                    points = mark.args[0]
            return bundle, points

        test_markers = {}
        unmarked_count = 0
        tests_dir = self.root_dir / "tests"

        # rglob (not glob) so a project that organizes tests into
        # subdirectories like tests/protocol/, tests/agent/, ... still has
        # its markers picked up. Pytest collects recursively by default; if
        # the marker scan didn't, every nested test would be silently
        # treated as unmarked and excluded from grading.
        for test_file in tests_dir.rglob("test_*.py"):
            spec = importlib.util.spec_from_file_location(
                f"tests.{test_file.stem}", test_file
            )
            if not spec or not spec.loader:
                continue

            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue

            for name in dir(module):
                obj = getattr(module, name)

                if name.startswith("test_") and callable(obj):
                    bundle, points = _resolve(obj)
                    if bundle is None:
                        unmarked_count += 1
                        continue
                    test_markers[f"{test_file.name}::{name}"] = {
                        "bundle": bundle,
                        "points": points,
                        "nodeid": str(test_file) + f"::{name}",
                    }
                    continue

                if name.startswith("Test") and isinstance(obj, type):
                    class_marks = getattr(obj, "pytestmark", [])
                    for attr_name in dir(obj):
                        if not attr_name.startswith("test_"):
                            continue
                        method = getattr(obj, attr_name)
                        if not callable(method):
                            continue
                        bundle, points = _resolve(method, class_marks)
                        if bundle is None:
                            unmarked_count += 1
                            continue
                        test_markers[f"{test_file.name}::{attr_name}"] = {
                            "bundle": bundle,
                            "points": points,
                            "nodeid": (
                                f"{test_file}::{name}::{attr_name}"
                            ),
                        }

        self._unmarked_count = unmarked_count
        self._cached_test_markers = test_markers
        return test_markers

    def get_selected_test_nodeids(self, test_markers):
        """Return pytest node ids for the active selection.

        Default (no --bundle): every test that carries a bundle marker.
        Unmarked tests are filtered upstream in get_test_markers, so this
        just reads off everything in test_markers.

        --bundle N: filter further to tests in that bundle.
        """
        if self.bundle is None:
            selected = [m["nodeid"] for m in test_markers.values()]
        else:
            selected = [
                m["nodeid"] for m in test_markers.values()
                if m["bundle"] == self.bundle
            ]
        return selected or None

    def build_pytest_command(self, test_nodeids):
        """Build the pytest command for the current run.

        test_nodeids must be a non-empty list -- the runner now always
        passes an explicit selection (only @pytest.mark.bundle()-tagged
        tests). Returns None when there is nothing graded to run; the
        caller is expected to skip the pytest invocation in that case.

        Quiet by default: pytest emits dots/Fs as tests run, and the JSON
        report carries the failure detail we render afterwards. -v opts back
        into the per-test PASSED/FAILED stream and inline tracebacks.
        """
        if not test_nodeids:
            return None

        cmd = [sys.executable, "-m", "pytest"]
        cmd.extend(test_nodeids)
        cmd.extend(["--color=yes", "--strict-markers"])
        if self.verbose:
            cmd.extend(["-v", "--tb=short"])
        else:
            cmd.extend(["-q", "--tb=no", "--no-header"])
        cmd.extend(self.pytest_args)
        return cmd

    def run_subprocess(self, cmd):
        """Run a subprocess and print its output safely.

        cwd is pinned to self.root_dir so pytest's --json-report-file
        (a relative path) lands where run_tests_with_json looks for it
        regardless of where the runner was invoked from.
        """
        print(f"\nRunning: {' '.join(cmd)}")
        print("=" * 80)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            cwd=str(self.root_dir),
            env=self._subprocess_env(),
        )
        self._pytest_proc = proc
        try:
            stdout, stderr = proc.communicate()
        finally:
            self._pytest_proc = None

        if stdout:
            print(stdout)
        if stderr:
            print(stderr)

        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    def run_tests_with_json(self):
        """Run pytest with JSON output to get detailed test information."""
        test_markers = self.get_test_markers()
        selected_tests = self.get_selected_test_nodeids(test_markers)

        cmd = self.build_pytest_command(selected_tests)
        if cmd is None:
            return 0, {1: [], 2: [], 3: []}

        cmd.extend([
            "--json-report",
            "--json-report-file=test_results.json",
        ])

        result = self.run_subprocess(cmd)

        bundles_data = {1: [], 2: [], 3: []}
        json_path = self.root_dir / "test_results.json"

        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as file_handle:
                    json_data = json.load(file_handle)

                for test in json_data.get("tests", []):
                    nodeid = test.get("nodeid", "")
                    outcome = test.get("outcome", "")
                    parts = nodeid.split("::")
                    filename = Path(parts[0]).name if parts else "unknown"
                    test_name = parts[-1] if len(parts) > 1 else "unknown"
                    test_class = parts[1] if len(parts) > 2 else None

                    # Strip @pytest.mark.parametrize suffix so all variants of
                    # `test_foo[case_a]`, `test_foo[case_b]`, ... resolve to
                    # the same marker entry under `test_foo`.
                    base_name = re.sub(r"\[.*\]$", "", test_name)
                    markers = test_markers.get(f"{filename}::{base_name}")
                    if markers is None:
                        # Test wasn't picked up by our marker scan -- e.g., a
                        # dynamically generated test or a name we couldn't
                        # parse. Skip rather than misclassify into Bundle 1.
                        continue
                    bundle = markers["bundle"]
                    points = markers["points"]

                    longrepr = ""
                    for phase in ("setup", "call", "teardown"):
                        phase_data = test.get(phase) or {}
                        if phase_data.get("outcome") == "failed":
                            longrepr = phase_data.get("longrepr") or ""
                            break

                    bundles_data[bundle].append({
                        "file": filename,
                        "class": test_class,
                        "name": test_name,
                        "passed": outcome == "passed",
                        "points": points,
                        "longrepr": longrepr,
                    })

                    if self.verbose:
                        status_icon = "[PASS]" if outcome == "passed" else "[FAIL]"
                        print(f"  {status_icon} Bundle {bundle}: {test_name}")
            except Exception as exc:
                if self.verbose:
                    print(f"Note: Could not parse JSON results: {exc}")
                    print("Falling back to basic test output parsing")
                bundles_data = self.parse_pytest_verbose_output(result.stdout, test_markers)
            finally:
                json_path.unlink(missing_ok=True)
        else:
            bundles_data = self.parse_pytest_verbose_output(result.stdout, test_markers)

        return result.returncode, bundles_data

    def run_tests_standard(self):
        """Run pytest and collect bundle information."""
        test_markers = self.get_test_markers()
        selected_tests = self.get_selected_test_nodeids(test_markers)

        try:
            import pytest_jsonreport  # noqa: F401
            return self.run_tests_with_json()
        except ImportError:
            pass

        cmd = self.build_pytest_command(selected_tests)
        if cmd is None:
            return 0, {1: [], 2: [], 3: []}

        result = self.run_subprocess(cmd)
        bundles_data = self.parse_pytest_verbose_output(result.stdout, test_markers)
        return result.returncode, bundles_data

    def parse_pytest_verbose_output(self, output, test_markers):
        """Parse verbose pytest output to extract test results and markers."""
        bundles = {1: [], 2: [], 3: []}

        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        clean_output = ansi_escape.sub("", output)

        lines = clean_output.splitlines()
        for line in lines:
            if "::test_" not in line or ("PASSED" not in line and "FAILED" not in line):
                continue

            match = re.search(
                r"(test_\w+\.py)::(?:(\w+)::)?(test_\w+)(?:\[.*\])?(?:\s+<-[^\r\n]+)?\s+(PASSED|FAILED)",
                line,
            )
            if not match:
                continue

            filename = match.group(1)
            test_class = match.group(2)
            test_name = match.group(3)
            status = match.group(4)

            metadata = test_markers.get(f"{filename}::{test_name}")
            if metadata is None:
                continue
            bundle = metadata["bundle"]

            bundles[bundle].append({
                "file": filename,
                "class": test_class,
                "name": test_name,
                "passed": status == "PASSED",
                "points": metadata["points"],
                "longrepr": "",
            })

            if self.verbose:
                status_icon = "[PASS]" if status == "PASSED" else "[FAIL]"
                print(f"  {status_icon} Bundle {bundle}: {test_name}")

        return bundles

    def _summarize_longrepr(self, longrepr, max_len=140):
        """Pull the most useful one-liner out of a pytest longrepr string.

        Prefer pytest's `E   ...` error marker; fall back to the last line
        containing 'assert' or 'Error:'; final fallback is the last non-empty
        line. Truncated to max_len so the focus view stays scannable.
        """
        if not longrepr:
            return ""
        if not isinstance(longrepr, str):
            longrepr = str(longrepr)
        lines = [line.rstrip() for line in longrepr.splitlines() if line.strip()]
        if not lines:
            return ""
        candidate = ""
        for line in reversed(lines):
            stripped = line.lstrip()
            if stripped.startswith("E "):
                candidate = stripped[2:].strip()
                break
        if not candidate:
            for line in reversed(lines):
                if "assert " in line or "Error:" in line:
                    candidate = line.strip()
                    break
        if not candidate:
            candidate = lines[-1].strip()
        if len(candidate) > max_len:
            candidate = candidate[: max_len - 1] + "…"
        return candidate

    def _build_component_groups(self, bundle_tests):
        """Group a bundle's tests into components and order them.

        Configured components (from project-template-config.json) come first
        in declared order. Any test files not in the config get appended in
        alphabetical order, each as its own group with its filename as label.

        Returns a list of dicts: file, label, depends_on, tests, total, passed,
        complete (bool). Only groups that contain at least one test in this
        bundle are returned.
        """
        by_file = {}
        for test in bundle_tests:
            by_file.setdefault(test["file"], []).append(test)

        groups = []
        seen_files = set()

        for entry in self._component_groups:
            fname = entry["file"]
            if fname not in by_file:
                continue
            tests = by_file[fname]
            passed = sum(1 for t in tests if t["passed"])
            groups.append({
                "file": fname,
                "label": entry["label"],
                "depends_on": list(entry["depends_on"]),
                "tests": tests,
                "total": len(tests),
                "passed": passed,
                "complete": passed == len(tests),
            })
            seen_files.add(fname)

        for fname in sorted(f for f in by_file if f not in seen_files):
            tests = by_file[fname]
            passed = sum(1 for t in tests if t["passed"])
            groups.append({
                "file": fname,
                "label": fname,
                "depends_on": [],
                "tests": tests,
                "total": len(tests),
                "passed": passed,
                "complete": passed == len(tests),
            })

        # Mark each group "locked" iff any depends_on group present in this
        # bundle has a failure. Groups whose deps aren't in this bundle (or
        # whose deps are all green) are unlocked.
        status_by_file = {g["file"]: g for g in groups}
        for group in groups:
            blockers = []
            for dep in group["depends_on"]:
                dep_group = status_by_file.get(dep)
                if dep_group is not None and not dep_group["complete"]:
                    blockers.append(dep_group["label"])
            group["locked"] = bool(blockers)
            group["blockers"] = blockers

        return groups

    def _render_group_failures(self, group, indent="    "):
        """Print each failed test in a group with a one-line assertion gist."""
        for test in group["tests"]:
            if test["passed"]:
                continue
            qualname = test["name"]
            if test.get("class"):
                qualname = f"{test['class']}::{test['name']}"
            print(f"{indent}{RED}[FAIL]{RESET} {qualname}")
            gist = self._summarize_longrepr(test.get("longrepr", ""))
            if gist:
                print(f"{indent}       {gist}")
            print(f"{indent}       {BLUE}-> pytest {test['file']}::{qualname} -v{RESET}")

    def _render_focus_bundle(self, groups, show_all):
        """Render the body of the focus bundle: grouped, with sub-focus.

        Sub-focus = first group that has failures AND is not locked. Only that
        group's failures are listed in detail. Other failing groups collapse
        to a one-line status with a hint. --all (show_all) bypasses sub-focus
        and renders every failing group's failures.
        """
        sub_focus = next(
            (g for g in groups if not g["complete"] and not g["locked"]),
            None,
        )

        for group in groups:
            label = group["label"]
            count = f"{group['passed']}/{group['total']}"
            if group["complete"]:
                print(f"  {GREEN}[PASS]{RESET} {label}: {count}")
                continue
            if group["locked"] and not show_all:
                deps = ", ".join(group["blockers"])
                print(
                    f"  {YELLOW}[--]{RESET}   {label}: {count} "
                    f"(locked - finish {deps} first)"
                )
                continue

            is_subfocus = group is sub_focus
            tag = " <- start here" if is_subfocus and not show_all else ""
            if group["locked"]:
                deps = ", ".join(group["blockers"])
                tag = f" (locked - normally cascades from {deps})"
            print(f"  {RED}[FAIL]{RESET} {label}: {count}{tag}")

            if show_all or is_subfocus:
                self._render_group_failures(group)
            else:
                remaining = group["total"] - group["passed"]
                print(
                    f"         ({remaining} failing - rerun with --all to see)"
                )

    # Path that downstream graders (github_grader.py in a specialized
    # assignment template) read to learn the latest bundle outcome without
    # re-running pytest. Gitignored under .test-run-state/.
    STATUS_CACHE_RELATIVE = Path(".test-run-state") / "last_bundle_status.json"

    def compute_bundle_status(self, bundles_data):
        """Return per-bundle pass/fail and overall grade, derived from bundles_data.

        Sole authority for "did bundle N pass?" -- every other consumer
        (print_bundle_results, write_status_cache, downstream graders) calls
        this so they cannot disagree.
        """
        bundle_status = {}
        for bundle in [1, 2, 3]:
            tests = bundles_data[bundle]
            total = len(tests)
            passed = sum(1 for test in tests if test["passed"])
            bundle_status[bundle] = {
                "total": total,
                "passed": passed,
                "complete": total > 0 and passed == total,
            }

        grade = "Not Passing"
        points = 0
        if bundle_status[1]["complete"]:
            grade, points = "C", 70
            if bundle_status[2]["complete"]:
                grade, points = "B", 85
                if bundle_status[3]["complete"]:
                    grade, points = "A", 100

        return {"bundles": bundle_status, "grade": grade, "points": points}

    def write_status_cache(self, status):
        """Persist the last bundle status so downstream graders can grade
        without re-running pytest. Best-effort; failures are non-fatal."""
        cache_path = self.root_dir / self.STATUS_CACHE_RELATIVE
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                "bundles": {str(k): v for k, v in status["bundles"].items()},
                "grade": status["grade"],
                "points": status["points"],
            }
            cache_path.write_text(
                json.dumps(serializable, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def print_bundle_results(self, bundles_data):
        """Print test results organized by bundle.

        Default (no --all): the lowest incomplete bundle is the "focus";
        within it, failures are grouped by component (test file), and only
        the first unblocked failing component shows individual failure
        detail. Higher bundles collapse to one-line "locked" rollups so
        cascading failures don't drown out actionable signal.

        --all: every bundle and every component renders full failure detail.
        -v: keeps full pytest verbose output as well (handled in
        build_pytest_command).
        """
        status = self.compute_bundle_status(bundles_data)
        bundle_status = status["bundles"]
        grade = status["grade"]
        points = status["points"]
        grade_color = {
            "A": GREEN, "B": YELLOW, "C": BLUE, "Not Passing": RED,
        }.get(grade, RED)

        self.write_status_cache(status)

        print("\n" + "=" * 80)
        print(f"{BOLD}SPECIFICATION GRADING RESULTS{RESET}")
        print("=" * 80)
        print(f"\n{BOLD}Grade Level Achieved: {grade_color}{grade}{RESET}")
        print(f"{BOLD}Grade Score: {points}/100{RESET}\n")

        skipped = getattr(self, "_unmarked_count", 0)
        if skipped:
            print(
                f"  ({skipped} unmarked test{'s' if skipped != 1 else ''} "
                "skipped - template infrastructure, not part of the grade)\n"
            )

        bundle_names = {
            1: "Bundle 1 (Core Requirements)",
            2: "Bundle 2 (Intermediate Features)",
            3: "Bundle 3 (Advanced Features)",
        }

        focus_bundle = next(
            (b for b in (1, 2, 3)
             if bundle_status[b]["total"] > 0 and not bundle_status[b]["complete"]),
            None,
        )

        for bundle in [1, 2, 3]:
            status = bundle_status[bundle]
            if status["total"] == 0:
                print(f"[ ] {BOLD}{bundle_names[bundle]}{RESET}: No tests found")
                continue

            icon = f"{GREEN}[PASS]{RESET}" if status["complete"] else f"{RED}[FAIL]{RESET}"
            completion = f"{status['passed']}/{status['total']}"
            percentage = (status["passed"] / status["total"] * 100) if status["total"] > 0 else 0

            header_line = (
                f"{icon} {BOLD}{bundle_names[bundle]}{RESET}: "
                f"{completion} tests passed ({percentage:.0f}%)"
            )

            # Spec grading: any bundle above the focus bundle is effectively
            # locked, regardless of whether its own tests pass independently.
            # A green Bundle 2 next to a red Bundle 1 misleads students into
            # thinking they've earned credit they actually haven't.
            locked_by_lower = (
                focus_bundle is not None and bundle > focus_bundle
            )
            if locked_by_lower and not self.show_all:
                lower_label = bundle_names[focus_bundle].split(" (")[0]
                if status["complete"]:
                    suffix = (
                        f"(passing - won't count toward grade "
                        f"until {lower_label} is complete)"
                    )
                else:
                    suffix = f"(locked - finish {lower_label} first)"
                print(
                    f"{YELLOW}[--]{RESET} {BOLD}{bundle_names[bundle]}{RESET}: "
                    f"{completion} tests passed {suffix}"
                )
                continue

            if status["complete"]:
                print(header_line)
                continue

            # Bundle is incomplete and not locked-by-lower (or --all) -- so
            # this is the focus bundle. Render with component grouping.
            print(header_line)
            groups = self._build_component_groups(bundles_data[bundle])
            if len(groups) <= 1 and not self.show_all:
                # Single-file bundle: skip the grouping ceremony, just list
                # failures with the same gist treatment.
                if groups:
                    self._render_group_failures(groups[0], indent="  ")
            else:
                self._render_focus_bundle(groups, show_all=self.show_all)

        print(f"\n{BOLD}Grading Requirements:{RESET}")
        print("- Each bundle is pass/fail: you must pass ALL of its tests to clear it")
        print("- Higher bundles require completion of all lower bundles")
        print("- Pass Bundle 1 -> C, Bundles 1+2 -> B, all three -> A")

        print(f"\n{BOLD}Next Steps:{RESET}")
        if not bundle_status[1]["complete"]:
            print("-> Focus on Bundle 1 tests (core requirements)")
        elif not bundle_status[2]["complete"]:
            print("-> Work on Bundle 2 tests (intermediate features)")
        elif not bundle_status[3]["complete"]:
            print("-> Complete Bundle 3 tests (advanced features)")
        else:
            print(f"{GREEN}-> Congratulations! All bundles complete!{RESET}")

    def _install_sigterm_handler(self):
        """On Unix, ensure SIGTERM kills the pytest subprocess before we exit.

        The watchdog sends SIGTERM to this process when a hung session trips
        the deadline. Without this handler, subprocess.run's pytest child would
        be reparented and leak on macOS/Linux. On Windows, `taskkill /F /T /PID`
        already kills the whole tree, so no handler is needed.
        """
        if os.name == "nt":
            return
        import signal

        def _on_sigterm(signum, frame):
            proc = self._pytest_proc
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except (OSError, ProcessLookupError):
                    pass
            # Restore default handler and re-raise so we exit with the
            # conventional 128+SIGTERM exit code.
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)

        signal.signal(signal.SIGTERM, _on_sigterm)

    def run(self):
        """Main execution method."""
        self._install_sigterm_handler()
        self._capture_ctx = None
        exit_code = 1
        try:
            if _capture is not None and self._capture_is_enabled():
                n_tests = self._count_tests()
                self._capture_ctx = _capture.session_start(
                    self.root_dir, estimated_tests=n_tests,
                )
            print("=" * 80)
            print(f"{BOLD}Test Runner with Specification Grading{RESET}")
            print("=" * 80)
            print(f"Root directory: {self.root_dir}")

            if not self.has_solution_files():
                if self.solution_dir.exists():
                    # Directory exists but is empty -- worth flagging as a
                    # likely misconfiguration. Otherwise (the normal student
                    # case where solution/ does not exist at all), say nothing
                    # so the output focuses on the test run itself.
                    print("\n[INFO] solution/ exists but contains no Python files.")
                    print("   Running tests with existing src/ implementation.")

                exit_code, bundles_data = self.run_tests_standard()
                self.print_bundle_results(bundles_data)
                return exit_code

            print(f"Solution directory: {self.solution_dir}")
            print()

            self.create_backup()
            self.copy_solution_files()

            print("\n[WARN] Running tests with solution files copied to src/ directory")
            print("   Original files have been backed up and will be restored after tests")

            time.sleep(0.5)

            exit_code, bundles_data = self.run_tests_standard()
            self.print_bundle_results(bundles_data)
            return exit_code

        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            exit_code = 2
            return exit_code
        except Exception as exc:
            print(f"\nError: {exc}")
            if self.verbose:
                import traceback
                traceback.print_exc()
            exit_code = 3
            return exit_code
        finally:
            if self.backup_dir and self.backup_dir.exists():
                self.restore_backup()
            if _capture is not None and self._capture_ctx is not None:
                status = "completed" if exit_code == 0 else f"pytest_exit_{exit_code}"
                # force=True ensures a snapshot lands even when the inner
                # pytest crashed before its sessionfinish hook fired and the
                # tracked tree happens to be unchanged.
                _capture.session_finish(self.root_dir, self._capture_ctx,
                                        status=status, force=True)


def main():
    assert_in_venv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        description="Run tests with specification grading support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script runs tests organized by specification grading bundles:
- Bundle 1: Core requirements      -> grade C
- Bundle 2: Intermediate features  -> grade B (when 1+2 pass)
- Bundle 3: Advanced features      -> grade A (when 1+2+3 pass)

Each bundle is pass/fail. Tests are assigned to bundles using pytest markers:
  @pytest.mark.bundle(1)   # Assigns test to Bundle 1

You must pass ALL tests in a bundle to clear that level.

Examples:
  python run_tests.py            # auto-focus: details for the lowest incomplete bundle
  python run_tests.py --all      # show every failure, every bundle
  python run_tests.py --bundle 1 # run only Bundle 1 tests
  python run_tests.py -v         # full pytest verbose output
  python run_tests.py -k basic
  python run_tests.py --failed

Note: If the 'solution' directory contains Python files, it will be tested automatically.
""",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output (shows failed test details)",
    )
    parser.add_argument(
        "--bundle",
        type=int,
        choices=[1, 2, 3],
        help="Run only tests assigned to the selected bundle",
    )
    parser.add_argument(
        "--failed",
        action="store_true",
        help="Run only the tests that failed on the previous pytest run",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help=(
            "Show every failure in detail. Without this flag, the runner "
            "auto-focuses on the lowest incomplete bundle (and, within it, "
            "the first unblocked component) so cascading failures from "
            "later bundles don't drown out actionable signal."
        ),
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help=(
            "Skip the environment check that gates test runs (instructor / "
            "edge-case use only)."
        ),
    )

    args, pytest_args = parser.parse_known_args()

    if not args.skip_preflight and _preflight is not None:
        repo = Path(__file__).resolve().parent
        failures = _preflight.check_environment(repo)
        if failures:
            _preflight.report(repo, failures)
            return 4

    runner = BundleTestRunner(
        verbose=args.verbose,
        bundle=args.bundle,
        pytest_args=pytest_args,
        failed_only=args.failed,
        show_all=args.show_all,
    )
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
