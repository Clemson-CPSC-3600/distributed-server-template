"""Git GUI commits need the project interpreter without shell activation."""
import os
import shutil
import subprocess
import json
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX interpreter-selection fixture")
@pytest.mark.parametrize("interpreter", ["venv/bin/python", ".venv/bin/python", "python3", "python"])
def test_commit_launcher_finds_interpreter_without_activation(tmp_path, interpreter):
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "project with spaces"
    repo.mkdir()
    git = shutil.which("git")
    subprocess.run([git, "init", "-q", str(repo)], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(git)
    selected = repo / interpreter if "/" in interpreter else bin_dir / interpreter
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_INTERPRETER_PROBE"\n')
    selected.chmod(0o755)
    probe = tmp_path / "invoked"
    env = dict(os.environ, PATH=str(bin_dir), CAPTURE_INTERPRETER_PROBE=str(probe),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    result = subprocess.run(["/bin/sh", str(root / ".githooks/post-commit")],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert probe.read_text().splitlines() == ["-m", "tests._capture.post_commit_entry"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX interpreter-selection fixture")
def test_unavailable_interpreter_is_visible_and_never_blocks_commit(tmp_path):
    root = Path(__file__).resolve().parents[1]
    git = shutil.which("git")
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)
    bins = tmp_path / "only-git"
    bins.mkdir()
    (bins / "git").symlink_to(git)
    result = subprocess.run(["/bin/sh", str(root / ".githooks/post-commit")], cwd=tmp_path,
                            env=dict(os.environ, PATH=str(bins), GIT_CONFIG_GLOBAL=os.devnull),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "Activity capture unavailable" in result.stderr


def test_real_git_commit_uses_project_venv_without_activation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "synthetic project"
    repo.mkdir()
    git = Path(shutil.which("git"))
    venv = repo / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True, capture_output=True, timeout=120)
    hooks = repo / ".githooks"
    hooks.mkdir()
    shutil.copy2(root / ".githooks/post-commit", hooks / "post-commit")
    (hooks / "post-commit").chmod(0o755)
    package = repo / "tests/_capture"
    package.mkdir(parents=True)
    (repo / "tests/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "post_commit_entry.py").write_text(
        "import os, sys, json\nfrom pathlib import Path\n"
        "Path(os.environ['CAPTURE_INTERPRETER_PROBE']).write_text(json.dumps({'prefix': sys.prefix}))\n")
    probe = tmp_path / "invocation.json"
    paths = [str(git.parent), str(git.parent.parent / "usr/bin"), os.defpath]
    if os.name == "nt":
        paths.append(str(Path(os.environ["SystemRoot"]) / "System32"))
    env = dict(os.environ, PATH=os.pathsep.join(paths),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Synthetic launcher test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Synthetic launcher test", GIT_COMMITTER_EMAIL="test@example.invalid",
               CAPTURE_INTERPRETER_PROBE=str(probe))
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    subprocess.run([str(git), "init", "-q"], cwd=repo, env=env, check=True, timeout=30)
    subprocess.run([str(git), "-c", "core.hooksPath=.githooks", "commit", "--allow-empty",
                    "-qm", "Synthetic commit"], cwd=repo, env=env,
                   check=True, capture_output=True, timeout=120)
    assert Path(json.loads(probe.read_text())["prefix"]).resolve() == venv.resolve()
