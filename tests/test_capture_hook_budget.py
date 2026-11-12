"""Ancestry refresh must leave editor hooks time to preserve a local snapshot."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests._capture import git_ops


@pytest.mark.parametrize("hook_tree", [".codex", ".claude-student", ".cursor"])
def test_network_fetch_fits_actual_hook_snapshot_budget(tmp_path, monkeypatch, hook_tree):
    root = Path(__file__).resolve().parents[1]
    path = root / hook_tree / "hooks/_common.py"
    if not path.exists() and hook_tree == ".claude-student":
        path = root / ".claude/hooks/_common.py"
    spec = importlib.util.spec_from_file_location("synthetic_hook_budget", path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    seam = tmp_path / "tests/_capture/__main__.py"
    seam.parent.mkdir(parents=True)
    seam.write_text("")
    observed = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs:
                        observed.append(kwargs["timeout"]) or subprocess.CompletedProcess(args, 0))
    hook.request_snapshot(tmp_path, "synthetic budget")
    assert len(observed) == 1
    hook_budget = observed[0]
    git_calls = []
    def fake_git(args, cwd, **kwargs):
        git_calls.append((args, kwargs["timeout"]))
        return subprocess.CompletedProcess(args, 0, "a" * 40, "")
    monkeypatch.setattr(git_ops, "run_git", fake_git)
    git_ops.fetch_auto_track(tmp_path)
    fetch_budgets = [timeout for args, timeout in git_calls if args[0] == "fetch"]
    assert len(fetch_budgets) == 1
    assert fetch_budgets[0] < hook_budget
