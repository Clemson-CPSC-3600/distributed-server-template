#!/usr/bin/env python3
"""Pre-flight environment checks for run_tests.py.

Refuse to run tests until the student's environment is set up:

  1. A virtual environment (or conda env) is active.
  2. pytest is importable in the active interpreter.
  3. (Student distributions only) `origin` remote is configured.
  4. (Student distributions only) HTTPS push destinations have a credential helper.

Exits 0 when ready, 1 otherwise. When invoked directly, prints a short
diagnostic. run_tests.py invokes :func:`check_environment` programmatically
and aborts before the first pytest call when any check fails.

The git checks are skipped when capture is disabled (instructor mode), so
the template itself stays usable without a remote.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List

# ANSI colors -- match style used by setup_credentials.py and run_tests.py.
if os.environ.get("NO_COLOR"):
    GREEN = RED = YELLOW = BOLD = RESET = ""
else:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _in_virtualenv() -> bool:
    """True iff the running interpreter is inside a venv/conda/pipenv env."""
    # PEP 405 venv: sys.prefix differs from sys.base_prefix.
    if getattr(sys, "base_prefix", sys.prefix) != sys.prefix:
        return True
    if os.environ.get("VIRTUAL_ENV"):
        return True
    if os.environ.get("CONDA_PREFIX"):
        return True
    return False


def _pytest_importable() -> bool:
    try:
        import pytest  # noqa: F401
    except ImportError:
        return False
    return True


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _git_push_urls(repo: Path) -> List[str]:
    """Resolve every origin push destination, including Git URL rewrites."""
    try:
        result = _run_git(["remote", "get-url", "--push", "--all", "origin"], cwd=repo)
    except (subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0:
        return []
    return [url for url in result.stdout.splitlines() if url]


def _git_credential_helper(repo: Path, url: str) -> bool:
    """Check helper configuration only; never invoke a helper or validate secrets.

    Credential helpers accumulate in configuration order across matching URL
    scopes. Keep NUL-delimited values intact: only an empty value resets the
    list, not a newline within a shell helper. Delegate each scope's matching
    to Git using a unique transient marker, without executing any helper.
    """
    try:
        result = _run_git(
            ["config", "--null", "--get-regexp", r"^credential(\..*)?\.helper$"], cwd=repo)
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    configured = False
    marker = "preflight" + uuid.uuid4().hex
    matches = {}
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        key, separator, helper = entry.partition("\n")
        if not separator:
            return False
        if key != "credential.helper":
            scope = key[len("credential."):-len(".helper")]
            if scope not in matches:
                try:
                    probe = _run_git(
                        ["-c", f"credential.{scope}.{marker}=1", "config",
                         "--get-urlmatch", f"credential.{marker}", url], cwd=repo)
                except (subprocess.SubprocessError, OSError):
                    return False
                if probe.returncode not in (0, 1):
                    return False
                matches[scope] = probe.returncode == 0
            if not matches[scope]:
                continue
        configured = helper != ""
    return configured


def _running_in_ci() -> bool:
    """True when running on GitHub Actions (or another CI advertising CI=true).

    Used to skip preflight checks that gate *student* setup steps which are
    handled differently on CI: e.g. the credential helper check exists so a
    student's `git push` of capture snapshots doesn't hang on a prompt; on CI
    the checkout action installs an authenticated extraheader instead.
    """
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("CI") == "true"
    )


def _capture_enabled(repo: Path) -> bool:
    """Mirror tests/_capture/capture._capture_enabled, sans env override.

    The env override exists for nested pytest probes; preflight should
    apply the gate based on the project config alone.
    """
    config_path = repo / "project-template-config.json"
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("capture_enabled"))


def check_environment(repo: Path) -> List[str]:
    """Return a list of human-readable failure messages.

    Empty list means the environment is ready. Each message is multi-line
    and self-contained — printed verbatim to the student.
    """
    failures: List[str] = []

    if not _in_virtualenv():
        failures.append(
            "No virtual environment is active.\n"
            "    Activate before running tests:\n"
            "        macOS/Linux:  source venv/bin/activate\n"
            "        Windows:      venv\\Scripts\\activate\n"
            "    If you don't have a venv yet, create one first:\n"
            "        python -m venv venv\n"
            "        (activate, then) pip install -r requirements.txt"
        )

    if not _pytest_importable():
        failures.append(
            "pytest is not installed in the active environment.\n"
            "    Install dependencies:  pip install -r requirements.txt"
        )

    # Git checks are only relevant when capture is enabled (student mode).
    # Instructor copies of the template don't push, so don't gate them on
    # credentials being configured. CI runs also skip the
    # credential-helper check because the checkout action
    # configures token auth via http.extraheader, not credential.helper.
    if (repo / ".git").exists() and _capture_enabled(repo):
        push_urls = _git_push_urls(repo)
        if not push_urls:
            failures.append(
                "No git remote `origin` is configured. Test snapshots can't "
                "be uploaded.\n"
                "    Run:  python tools/setup_credentials.py"
            )
        elif not _running_in_ci() and any(
            url.lower().startswith(("http://", "https://"))
            and not _git_credential_helper(repo, url) for url in push_urls
        ):
            failures.append(
                "Git credential helper is not configured for an HTTPS push "
                "destination. Upload authentication may require setup.\n"
                "    Run:  python tools/setup_credentials.py"
            )

    return failures


def _codex_home() -> Path:
    """Honor $CODEX_HOME; fall back to ~/.codex."""
    home = os.environ.get("CODEX_HOME")
    return Path(home) if home else Path.home() / ".codex"


def _codex_hooks_untrusted(repo: Path) -> bool:
    """True if this repo ships Codex hooks that are not trusted here.

    Two independent signals, either of which is enough to warn:

    1. No ``[hooks.state]`` entry naming this repo's ``hooks.json`` in
       ``$CODEX_HOME/config.toml``. Presence-only: we do NOT validate
       ``trusted_hash``, because what bytes it covers is not yet
       reverse-engineered, so a stale entry left by an edited hooks.json
       can read as trusted.
    2. The manifest conjunction -- Codex evidence captured but no hook
       heartbeat (see capture_health.hooks_look_unauthorized). This one
       catches the case signal 1 misses: trust was granted once, the
       instructor then edited hooks.json, and the stale entry still reads
       as trusted while the hooks no longer run.

    Deliberately conservative: this only ever warns, never fails.
    """
    hooks_json = repo / ".codex" / "hooks.json"
    if not hooks_json.is_file():
        return False  # nothing to trust yet (hooks v2 has not shipped)

    manifest = repo / ".ai-traces" / "capture-health.json"
    try:
        health = json.loads(manifest.read_text(encoding="utf-8"))
        entry = (health.get("adapters") or {}).get("codex") or {}
        if entry.get("evidence") and not entry.get("heartbeat"):
            return True
    except (OSError, ValueError):
        pass

    config = _codex_home() / "config.toml"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False  # no Codex config at all -- student may not use Codex
    return f'{hooks_json}:' not in text


def _last_workspace_root_mismatch(repo: Path) -> bool:
    """True if the last capture run saw a session running above the repo."""
    manifest = repo / ".ai-traces" / "capture-health.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("workspace_root_mismatch"))


def check_warnings(repo: Path) -> List[str]:
    """Non-blocking capture-configuration advisories.

    These NEVER gate the test run. A hard gate on capture configuration
    would block coursework for instructor observability, lock out students
    who never use Codex, and -- because hook trust is sha256-keyed per
    machine -- turn any mid-semester hooks.json edit into a class-wide
    lockout. `--skip-preflight` also makes such a gate trivially
    bypassable, so it would only ever slow down the honest.
    """
    warnings: List[str] = []
    if not (repo / ".git").exists():
        return warnings
    from tools.verify_integrity import configuration_errors, distribution_mode
    problems = configuration_errors(repo, distribution_mode(repo))
    if problems:
        warnings.append(
            "Activity logging is not configured correctly. Your tests can still run, "
            "but the activity record may be incomplete. Ask your instructor to restore "
            "the assignment's capture configuration; do not recreate it by guessing."
        )
        return warnings
    if not _capture_enabled(repo):
        return warnings

    if _last_workspace_root_mismatch(repo):
        warnings.append(
            "Your editor looks like it is open on a FOLDER ABOVE this "
            "assignment.\n"
            "    Your work still runs fine, but AI-assistance logging for "
            "this\n"
            "    course cannot see it, so your effort may not be recorded.\n"
            "    Fix: open THIS folder as the workspace root\n"
            f"        {repo.name}/  (the folder containing run_tests.py)\n"
            "    rather than a parent folder that merely contains it."
        )

    if _codex_hooks_untrusted(repo):
        warnings.append(
            "This project ships Codex hooks, but they are not trusted on "
            "this machine.\n"
            "    Your work still runs fine, but AI-assistance logging may "
            "be incomplete.\n"
            "    Fix: open Codex in this folder and run `/hooks`, then "
            "approve\n"
            "    the listed hooks. You may need to re-approve after the\n"
            "    instructor updates them."
        )
    return warnings


def report_warnings(warnings: List[str]) -> None:
    """Print advisories prominently WITHOUT blocking the run."""
    if not warnings:
        return
    print()
    print(f"{YELLOW}{BOLD}Heads up -- AI-logging setup looks incomplete.{RESET}")
    print(f"{YELLOW}Your tests will still run.{RESET}")
    print()
    for index, msg in enumerate(warnings, 1):
        print(f"{BOLD}{index}.{RESET} {msg}")
        print()


def report(repo: Path, failures: List[str]) -> None:
    """Pretty-print a list of failures, with a footer pointing at the override."""
    print()
    print(f"{RED}{BOLD}Cannot run tests yet -- setup is incomplete.{RESET}")
    print()
    for index, msg in enumerate(failures, 1):
        print(f"{BOLD}{index}.{RESET} {msg}")
        print()
    print(
        f"{YELLOW}Once each item above is fixed, re-run "
        f"`python run_tests.py`.{RESET}"
    )
    print(
        f"{YELLOW}Instructors can bypass with `python run_tests.py "
        f"--skip-preflight` once they understand the consequences.{RESET}"
    )


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    failures = check_environment(repo)
    if not failures:
        print(f"{GREEN}[OK]{RESET} Environment ready.")
        return 0
    report(repo, failures)
    return 1


if __name__ == "__main__":
    sys.exit(main())
