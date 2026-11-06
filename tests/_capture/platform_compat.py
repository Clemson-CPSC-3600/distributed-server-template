"""Cross-platform process management and path-comparison primitives.

The path-comparison half (``path_within_repo``) is the single shared
containment helper used by ``codex_ingest``, ``claude_ingest``, and
``vscode_chat_ingest`` to decide whether a recorded session cwd / workspace
folder belongs to the course repo. It replaces three independent
``_same_path`` copies that did brittle exact-equality matching (see the
2026-04-22 Codex rollout notes, where ``is_relative_to`` was recommended
but never implemented).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def is_process_alive(pid: int) -> bool:
    """True if a process with this pid is still running.

    Does not require psutil. Uses os.kill(pid, 0) on Unix and
    OpenProcess/GetExitCodeProcess on Windows.
    """
    if sys.platform == "win32":
        import ctypes
        # PROCESS_QUERY_LIMITED_INFORMATION lets us call GetExitCodeProcess
        # without demanding SYNCHRONIZE rights (which WaitForSingleObject
        # would need). GetExitCodeProcess returns STILL_ACTIVE (259) while
        # the process runs and the real exit code once it's exited.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but we can't signal it


def terminate_process(pid: int, timeout: float = 5.0) -> bool:
    """Attempt to terminate. Returns True if the process is gone afterwards."""
    import time
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        # Grace period, then SIGKILL
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not is_process_alive(pid):
                return True
            time.sleep(0.2)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
    # Verify
    time.sleep(0.5)
    return not is_process_alive(pid)


# --------------------------------------------------------------------------
# Path containment
# --------------------------------------------------------------------------

def _is_relative_to(candidate: Path, base: Path) -> bool:
    """Python 3.8-compatible equivalent of ``Path.is_relative_to`` (3.9+).

    The project's floor is Python 3.8 (see ``pyproject.toml``), so we can't
    call ``Path.is_relative_to`` directly. ``relative_to`` + ``ValueError``
    is exactly what ``is_relative_to`` does internally on 3.9+, and both
    ``relative_to`` and catching ``ValueError`` have existed since Python
    3.4, so this is not a "workaround" so much as the pre-3.9 spelling of
    the same check. Both paths must already be resolved by the caller —
    this function does no normalization of its own.
    """
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _is_within_via_samefile(candidate: Path, base: Path) -> bool:
    """Ancestor-walk containment check using filesystem identity, not
    strings. Used as the darwin-only fallback for ``path_within_repo``
    (see its docstring) when the string-based ``_is_relative_to`` check
    misses a match caused by case differences. Requires every path walked
    to exist on disk; any ``OSError`` (missing path, permission denied) is
    treated as "not contained" rather than propagating.
    """
    try:
        if os.path.samefile(candidate, base):
            return True
    except OSError:
        return False
    current = candidate
    while True:
        parent = current.parent
        if parent == current:
            return False
        try:
            if os.path.samefile(parent, base):
                return True
        except OSError:
            return False
        current = parent


def _crosses_foreign_git_boundary(candidate: Path, container: Path) -> bool:
    """True if a directory strictly between ``container`` and ``candidate``
    (inclusive of ``candidate``) is itself the root of a separate git
    checkout: a normal clone (``.git`` directory) or a worktree/submodule
    gitlink (``.git`` FILE containing ``gitdir: <path>``).

    Both directory and file ``.git`` entries mark a foreign boundary. A
    worktree's ``.git`` is deliberately a file rather than a directory, but
    the subtree it roots is still independently version-controlled (its own
    index, its own current branch) and its AI transcripts must not be swept
    into the course repo's captured commits — so worktrees are excluded the
    same way an ordinary nested clone is.

    ``container`` itself is exempt: its own ``.git`` (expected — it's the
    course repo) does not make it "foreign". Comparison uses
    ``os.path.samefile`` rather than ``==`` so this stays correct even when
    ``candidate``/``container`` were matched via the darwin case-insensitive
    fallback in ``path_within_repo`` (where their resolved string forms can
    differ in case despite naming the same directory).
    """
    current = candidate
    while True:
        try:
            if os.path.samefile(current, container):
                return False
        except OSError:
            # current no longer exists / unreadable — fail closed (treat as
            # a boundary) rather than silently including it.
            return True
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            # Walked off the filesystem root without reaching `container` —
            # should not happen given the caller already confirmed
            # containment, but fail closed rather than loop forever.
            return True
        current = parent


def path_within_repo(candidate, container, *, exclude_nested_git: bool = True) -> bool:
    """True if ``candidate`` resolves to ``container`` itself or a location
    nested inside it.

    This is the single shared containment predicate for cwd/workspace
    matching across the capture ingesters. It is deliberately bidirectional
    in the sense that swapping the arguments answers the mirror-image
    question: ``path_within_repo(repo, workspace_folder)`` asks "is the repo
    within the workspace folder?" (the parent-folder-workspace case used by
    a future VS Code task). Callers doing that reversed check should pass
    ``exclude_nested_git=False``, since the repo's own ``.git`` is expected
    there and is not a foreign boundary in that direction — the asymmetric
    exclusion semantics for that direction are that consumer's
    responsibility, not this helper's.

    Matching:
      * Both paths are resolved (``Path.resolve()``) before comparison, so
        ``..`` segments, symlinks, and relative paths are normalized first.
      * Containment is a post-resolve ``Path.is_relative_to``-equivalent
        check (``_is_relative_to``), NOT a string-prefix / ``startswith``
        test — a sibling directory whose name happens to start with the
        same characters (e.g. ``repo-2`` vs ``repo``) must NOT match.
      * On darwin, if the string-based check finds no match, we additionally
        try a ``samefile``-based ancestor walk. Default macOS volumes
        (APFS/HFS+) are case-insensitive but case-preserving: two
        differently-cased path strings can name the same file on disk while
        ``Path.resolve()`` does not canonicalize casing there, so the
        string check alone can miss a real match.
      * When ``exclude_nested_git`` is True (the default — the direction
        used for cwd-in-repo checks), a candidate is excluded if any
        directory strictly between ``container`` and ``candidate``
        (inclusive of ``candidate``) is the root of a separately
        version-controlled project (its own ``.git``, whether a directory
        or a worktree gitlink file). This is a privacy requirement: a
        foreign project nested inside the course repo must not have its
        transcripts swept into a graded commit.

    Never raises — unresolvable paths (e.g. permission errors) are treated
    as "not within".
    """
    try:
        candidate_resolved = Path(candidate).resolve()
        container_resolved = Path(container).resolve()
    except (OSError, ValueError):
        return False

    within = _is_relative_to(candidate_resolved, container_resolved)

    if not within and sys.platform == "darwin":
        within = _is_within_via_samefile(candidate_resolved, container_resolved)

    if not within:
        return False

    if exclude_nested_git and _crosses_foreign_git_boundary(
        candidate_resolved, container_resolved
    ):
        return False

    return True
