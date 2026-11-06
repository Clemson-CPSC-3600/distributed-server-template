"""AI_POLICY.md's private-work advice ("How to keep work private"), executable.

The policy tells students that work they don't want captured belongs in a
scratch directory OUTSIDE the project folder, and warns that making a second
clone of the assignment is NOT a way to work unobserved (the clone
self-bootstraps capture and pushes to the same remote). Both halves of that
advice are claims about one mechanism: the sitecustomize payload the capture
layer installs into the project venv, which fires at interpreter exit and
snapshots to `refs/auto-track/snapshots`.

These two tests are the claim, checked end to end:

  (a) an interpreter with the payload installed, exiting in a scratch
      directory that has no `project-template-config.json` above it, creates
      no auto-track ref -- even though the scratch dir is itself a git repo,
      so a ref *could* have been written there;
  (b) the same interpreter, exiting in a second working copy that does carry
      `project-template-config.json` + `.git`, creates one.

Nothing is stubbed: the real `runtime_triggers.install_sitecustomize()` lands
the real `PAYLOAD` in a site-packages directory, a real subprocess
interpreter imports it via `site`, and the assertion reads real git refs. If
the advice in AI_POLICY.md ever stops being true, one of these fails.

Isolation: every path is under tmp_path, HOME and the git config env are
redirected into tmp, and `CAPTURE_PROJECT_ROOT` is cleared so root discovery
runs for real. The developer's own repo and venv are never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._capture import runtime_triggers, sitecustomize_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTO_TRACK_NAMESPACE = "refs/auto-track"


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), env=env,
        capture_output=True, text=True, timeout=60,
    )


def _isolated_env(tmp_home: Path, site_packages: Path) -> dict:
    """Parent env minus every capture/pytest signal, with HOME + git config
    redirected into tmp so the developer's real config can't influence (or be
    influenced by) the run."""
    drop = (
        "PYTEST_CURRENT_TEST",   # would make the payload no-op
        "_CAPTURE_SUBPROCESS",   # ditto
        "CAPTURE_PROJECT_ROOT",  # force real root discovery by walking up
        "CAPTURE_DISABLED",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    )
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PYTHONPATH"] = str(site_packages)
    env["HOME"] = str(tmp_home)
    env["USERPROFILE"] = str(tmp_home)
    env["GIT_CONFIG_GLOBAL"] = str(tmp_home / "gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


@pytest.fixture
def payload_site_packages(tmp_path_factory) -> Path:
    """Install the real payload into a venv-shaped tmp dir, via the real
    installer, and return the site-packages directory to put on PYTHONPATH.

    PYTHONPATH (not the developer's actual venv) because `site` imports the
    FIRST `sitecustomize` on sys.path, and PYTHONPATH precedes site-packages
    -- so this shadows any copy already installed in the project venv rather
    than adding a second one, and nothing outside tmp is written.
    """
    venv = tmp_path_factory.mktemp("fakevenv")
    py = f"python{sys.version_info.major}.{sys.version_info.minor}"
    (venv / "lib" / py / "site-packages").mkdir(parents=True)
    installed = runtime_triggers.install_sitecustomize(
        venv, sitecustomize_payload.PAYLOAD)
    assert installed is not None and installed.is_file()
    assert installed.read_text(encoding="utf-8") == sitecustomize_payload.PAYLOAD
    return installed.parent


def _make_git_dir(path: Path, *, as_project_copy: bool, env: dict) -> Path:
    """A git repo with one commit. `as_project_copy` adds exactly the two
    things that distinguish a second working copy of the assignment from an
    unrelated scratch directory: the project marker/config and the capture
    layer the payload imports."""
    (path / "src").mkdir(parents=True)
    (path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    if as_project_copy:
        (path / "project-template-config.json").write_text(
            json.dumps({"distribution_mode": "student", "capture_enabled": True}),
            encoding="utf-8",
        )
        shutil.copytree(
            REPO_ROOT / "tests" / "_capture", path / "tests" / "_capture",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copy(REPO_ROOT / "tests" / "__init__.py", path / "tests" / "__init__.py")
    _git(path, "init", "-q", env=env)
    _git(path, "config", "user.email", "student@example.invalid", env=env)
    _git(path, "config", "user.name", "Student", env=env)
    _git(path, "add", "-A", env=env)
    committed = _git(path, "commit", "-q", "-m", "initial", env=env)
    assert committed.returncode == 0, committed.stderr
    return path


def _exit_an_interpreter_in(cwd: Path, env: dict, site_packages: Path) -> None:
    """Start and immediately exit a Python interpreter. `site` imports the
    installed sitecustomize at startup; the snapshot runs from its atexit
    hook. This is what a student's `python whatever.py` does.

    The interpreter reports which sitecustomize it actually loaded, and we
    assert it is the one this test installed. Without that check the test
    machine's own venv -- which the bootstrap has usually already
    payload-installed -- could be the thing doing the capturing, and both
    tests below would keep passing with the installation step removed.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import sitecustomize, sys; sys.stdout.write(sitecustomize.__file__)"],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )
    # The payload must never disturb the student's own Python invocation.
    assert result.returncode == 0, result.stderr
    loaded = os.path.realpath(result.stdout.strip())
    expected = os.path.realpath(site_packages / "sitecustomize.py")
    assert loaded == expected, (
        f"interpreter loaded {loaded!r}, not the payload installed by this "
        f"test at {expected!r}"
    )


def _auto_track_refs(repo: Path, env: dict) -> list:
    listed = _git(repo, "for-each-ref", "--format=%(refname)",
                  AUTO_TRACK_NAMESPACE, env=env)
    assert listed.returncode == 0, listed.stderr
    return [ln for ln in listed.stdout.splitlines() if ln.strip()]


@pytest.mark.timeout(180)
def test_scratch_dir_outside_the_project_is_not_captured(tmp_path, payload_site_packages):
    """AI_POLICY.md: private work belongs in a directory outside the project."""
    home = tmp_path / "home"
    home.mkdir()
    env = _isolated_env(home, payload_site_packages)
    scratch = _make_git_dir(home / "scratch", as_project_copy=False, env=env)

    _exit_an_interpreter_in(scratch, env, payload_site_packages)

    assert _auto_track_refs(scratch, env) == []
    assert not (scratch / ".ai-traces").exists()


@pytest.mark.timeout(180)
def test_second_working_copy_of_the_project_is_captured(tmp_path, payload_site_packages):
    """AI_POLICY.md: a separate clone is NOT a way to work unobserved."""
    home = tmp_path / "home"
    home.mkdir()
    env = _isolated_env(home, payload_site_packages)
    clone = _make_git_dir(home / "assignment-clone", as_project_copy=True, env=env)

    _exit_an_interpreter_in(clone, env, payload_site_packages)

    refs = _auto_track_refs(clone, env)
    assert refs == ["refs/auto-track/snapshots"], (
        "a second working copy of the assignment must still snapshot; "
        f"got {refs!r}"
    )
    message = _git(clone, "log", "-1", "--format=%B",
                   "refs/auto-track/snapshots", env=env).stdout
    assert "trigger=sitecustomize" in message, message
