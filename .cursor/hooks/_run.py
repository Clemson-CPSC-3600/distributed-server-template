#!/usr/bin/env python3
"""Cross-platform Cursor hook launcher.

``.cursor/hooks.json`` invokes this with the hook script basename as the
only argument::

    python3 "$r/.cursor/hooks/_run.py" stop

We resolve the project's venv interpreter for the current OS and, if we are
not already running under it, re-exec there before dispatching. The hooks
themselves import nothing from the project (stdlib only, so this tree also
works in a non-Python assignment repo), but running under the venv keeps
``sys.executable`` pointing at the interpreter the capture CLI seam expects
when a hook shells out to it.

Bootstrap interpreter expectations: ``python3`` ships with the Xcode
Command Line Tools on macOS and is universal on Linux; hooks.json falls
back to ``python`` where ``python3`` is absent (the python.org and
Microsoft Store installs on Windows register ``python``).

Never fails a Cursor turn: every path returns 0, and nothing is written to
stdout (Cursor reads stdout as a control channel).
"""
from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path


HOOK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOK_DIR.parent.parent

# Hooks whose interpreter exit should NOT trigger an auto-track snapshot.
# Their event data is written to the trace JSONLs and folded into the next
# snapshot (the stop hook takes one explicitly). _CAPTURE_SUBPROCESS is read
# by the venv's sitecustomize.py, which skips its atexit snapshot trigger.
#
# Tab hooks are in here for a second reason: they fire once per accepted
# completion, which is orders of magnitude more often than a turn ends.
SNAPSHOT_SUPPRESSED_HOOKS = {
    "session_start",
    "session_end",
    "before_submit_prompt",
    "after_file_edit",
    "after_tab_file_edit",
}


def _venv_python() -> "Path | None":
    for venv_name in ("venv", ".venv"):
        if os.name == "nt":
            candidate = PROJECT_ROOT / venv_name / "Scripts" / "python.exe"
        else:
            candidate = PROJECT_ROOT / venv_name / "bin" / "python"
        if candidate.is_file():
            return candidate
    return None


def _running_in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _reexec_under_venv(venv: Path, hook_name: str) -> int:
    argv = [str(venv), str(Path(__file__).resolve()), hook_name]
    if os.name == "nt":
        subprocess.call(argv, stdin=sys.stdin)
        return 0
    os.execv(str(venv), argv)
    return 0  # unreachable on POSIX


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    hook_name = sys.argv[1]
    hook_script = HOOK_DIR / f"{hook_name}.py"
    if not hook_script.is_file():
        return 0

    if hook_name in SNAPSHOT_SUPPRESSED_HOOKS:
        os.environ["_CAPTURE_SUBPROCESS"] = "1"

    venv = _venv_python()
    if venv is not None and not _running_in_venv():
        return _reexec_under_venv(venv, hook_name)

    try:
        sys.argv = [str(hook_script)]
        runpy.run_path(str(hook_script), run_name="__main__")
    except SystemExit:
        pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
