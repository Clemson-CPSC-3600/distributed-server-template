"""The Codex hook commands must find a Python interpreter on any POSIX machine.

Some systems ship only ``python`` (no ``python3``). The Claude and Cursor hook
configs already fall back from one to the other; the Codex config must too,
or its hooks silently do nothing on those machines.

Editing .codex/hooks.json changes its trust hash, which only affects repos
generated after the change: already-distributed student repos are separate
copies and never receive template updates.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_JSON = Path(__file__).resolve().parents[1] / ".codex" / "hooks.json"


def _posix_commands():
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event, groups in config["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                yield event, hook["command"]


def test_every_posix_hook_command_falls_back_from_python3_to_python():
    commands = list(_posix_commands())
    assert commands
    for event, command in commands:
        assert "command -v python3 || command -v python" in command, event
        assert "python3 \\\"" not in command and 'python3 "' not in command, (
            f"{event} still calls python3 directly")


def _run_all_hooks(tmp_path: Path, python_names):
    """Run every POSIX hook command under /bin/sh in a throwaway repo.

    PATH holds only sh, git, and the requested interpreter names, so the
    fallback is exercised for real rather than by string matching.
    """
    repo = tmp_path / "repo"
    (repo / ".codex").mkdir(parents=True)
    shutil.copytree(HOOKS_JSON.parent / "hooks", repo / ".codex" / "hooks")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sh").symlink_to("/bin/sh")
    (bin_dir / "git").symlink_to(shutil.which("git"))
    for name in python_names:
        (bin_dir / name).symlink_to(os.path.realpath(sys.executable))
    for event, command in _posix_commands():
        inner = command[len("sh -c '"):-1]  # the script sh -c would receive
        result = subprocess.run(
            ["/bin/sh", "-c", inner], cwd=repo, input='{"session_id": "s"}',
            env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (event, result.stderr)
    return list((repo / ".ai-traces" / "codex" / "heartbeat").glob("*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook commands")
def test_hooks_exit_zero_and_write_nothing_when_no_python_exists(tmp_path: Path):
    """A missing interpreter must never fail the student's turn."""
    assert _run_all_hooks(tmp_path, []) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook commands")
@pytest.mark.parametrize("names", [["python3"], ["python"]])
def test_hooks_run_with_either_interpreter_name(tmp_path: Path, names):
    assert _run_all_hooks(tmp_path, names), f"no heartbeat with only {names}"
