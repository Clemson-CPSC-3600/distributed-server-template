"""Copy VS Code chat-panel session transcripts for this repo into .ai-traces/.

Captures conversations from any chat provider that uses VS Code's built-in
chat panel — primarily GitHub Copilot Chat, but also Cody, Continue, and
similar extensions whose chats land in
``<user-dir>/workspaceStorage/<workspace-id>/chatSessions/``.

This adapter is INTENTIONALLY raw-only: it copies the on-disk session files
verbatim into ``.ai-traces/vscode_chat/raw/sessions/`` without parsing
them. VS Code's chat session format is a versioned mutation log internal
to the editor, and the schema bumps between releases. Replaying the
mutation log to reconstruct a clean transcript is straightforward but
requires version-specific code; the audit-trail value is in preserving
the raw evidence, which the instructor can always replay later (or feed
into a third-party tool like ``fengzehan.vscode-copilot-exporter`` or
``imperium-dev.copilot-chat-to-markdown``).

Supported variants: Code, Code - Insiders, VSCodium — on macOS, Linux,
and Windows. Cursor is intentionally excluded: it stores chats in
SQLite (``state.vscdb``), not as ``chatSessions/*.jsonl``, and would
need its own adapter. ``VSCODE_CONFIG_DIR`` overrides the discovery
list (testing hook; mirrors ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``).
When set, its value points to the User-config root (the directory
containing ``workspaceStorage/``), without the ``User`` subdir suffix
that's used for the actual VS Code installations.

Workspace-to-repo matching: VS Code stores per-workspace state under
``workspaceStorage/<md5-hash>/``. The hash isn't computable from the
path (it's salted with folder birthtime on Windows), but every
workspace's directory contains a ``workspace.json``. Single-root
workspaces have a ``folder`` key (a URI). Multi-root workspaces instead
have a ``workspace`` key: a URI pointing at a ``.code-workspace`` file
elsewhere on disk, whose own ``folders`` array lists the roots. Both
shapes are read and every resulting folder path is checked against the
repo, in BOTH containment directions — see ``_workspace_matches_repo``.

Empirical storage-layout verification (2026-08-11, macOS, VS Code
stable, this development machine): ``~/Library/Application
Support/Code/User`` exists; no "Code - Insiders" or VSCodium install
present here. Every ``workspaceStorage/<32-hex-md5>/`` directory
contains exactly: ``chatSessions/``, ``chatEditingSessions/``,
``state.vscdb``, ``workspace.json``. All 70 local ``workspace.json``
files carry a single ``folder`` key (a ``file://`` URI) — this machine
has NO multi-root ("workspace" key) and NO remote (``vscode-remote://``)
workspace, so those two cases are covered by SYNTHETIC FIXTURES ONLY
(see ``tests/test_vscode_chat_ingest.py``), not direct observation.
``chatEditingSessions/`` (inline-edit / agent-edit history, distinct
from chat transcripts) is observed to exist alongside ``chatSessions/``
but is intentionally out of scope for this raw-only chat-transcript
ingester.

Windows and WSL-mounted-Windows layouts are NOT independently verified
on this machine; the ``chatSessions``/``chatEditingSessions``/
``state.vscdb``/``workspace.json`` layout is assumed identical to macOS
based on published community documentation of VS Code's per-workspace
storage scheme — the same ``workspaceStorage/<hash>/chatSessions``
directory name is referenced by third-party chat-export tools
(``fengzehan.vscode-copilot-exporter``, ``imperium-dev.copilot-chat-to-
markdown``) and by community write-ups of VS Code's extension-host
storage layout — not verified by direct inspection of a Windows machine.

WSL support: when running under WSL (``/proc/version`` contains
"microsoft", case-insensitive — see ``_is_wsl``), a student's VS Code
frequently runs as a genuinely separate Windows-side installation
whose data lives on the Windows filesystem, reachable from WSL at
``/mnt/<drive>/Users/<user>/AppData/Roaming/{Code,Code - Insiders,
VSCodium}/User``. URI parsing alone (handling ``vscode-remote://
wsl+<distro>/...`` workspace URIs) is NOT sufficient to cover this
cohort: those URIs only appear in workspace.json files that already
live inside a discovered user dir, so if the Windows-side store itself
is never enumerated, the whole cohort silently captures zero. Both
mechanisms are implemented: URI authority-strip resolution
(``_folder_uri_to_path``) for reading ``vscode-remote://`` folder
URIs, and Windows-side store enumeration (``_wsl_windows_user_dirs``)
for finding the workspaceStorage directories in the first place.

Destination namespacing: multiple discovered user dirs (Code vs. Code
- Insiders vs. VSCodium; native vs. WSL-mounted-Windows) can each have
a workspace matching the same repo, and workspace-local session
filenames are not globally unique across those installs. Destination
copies are namespaced by ``<variant-label>/<workspace-id>/<filename>``
so that two installs' identically-named sessions never collide (one
silently overwriting or shadowing the other).

Contract: never raises. Returns the list of copied destination paths.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from tests._capture import ai_traces, copy_dedup, course_floor, platform_compat

VSCODE_CHAT_DIR = Path(ai_traces.AI_TRACES_DIR) / "vscode_chat" / "raw" / "sessions"
_VARIANTS = ("Code", "Code - Insiders", "VSCodium")

# Test-injectable via monkeypatching these module attributes (mirrors the
# VSCODE_CONFIG_DIR env-var override pattern used elsewhere in this file).
PROC_VERSION_PATH = Path("/proc/version")
WSL_MNT_ROOT = Path("/mnt")


def _is_wsl(proc_version_path: Optional[Path] = None) -> bool:
    """True when running under WSL (Windows Subsystem for Linux).

    Detection reads ``/proc/version`` (or ``proc_version_path`` when
    explicitly passed, for direct unit testing) and looks for
    "microsoft" case-insensitively — the standard WSL detection idiom;
    both WSL1 and WSL2 kernels include it. When no explicit path is
    passed, the module-level ``PROC_VERSION_PATH`` constant is read at
    call time, so callers that want to fake WSL in an integration test
    (exercising the downstream enumeration, not just this predicate)
    can monkeypatch that module attribute instead.

    Never raises — a missing/unreadable file means "not WSL".
    """
    path = proc_version_path if proc_version_path is not None else PROC_VERSION_PATH
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "microsoft" in content.lower()


def _wsl_windows_user_dirs() -> List[Tuple[str, Path]]:
    """Enumerate the WINDOWS-side VS Code User dirs reachable from WSL's
    mounted Windows filesystem: ``/mnt/<drive>/Users/*/AppData/Roaming/
    {Code,Code - Insiders,VSCodium}/User``.

    Returns ``(label, path)`` pairs; label is ``"wsl-<variant>"`` (hyphen,
    not colon, so the label is also safe to use as a path segment on a
    Windows-mounted destination). Honors ``WSL_MNT_ROOT`` (default
    ``/mnt``) so tests can point it at a fixture directory instead of a
    real mount. Never raises.
    """
    results: List[Tuple[str, Path]] = []
    try:
        if not WSL_MNT_ROOT.is_dir():
            return results
        roaming_dirs = list(WSL_MNT_ROOT.glob("*/Users/*/AppData/Roaming"))
    except OSError:
        return results
    for roaming in roaming_dirs:
        for variant in _VARIANTS:
            p = roaming / variant / "User"
            try:
                if p.is_dir():
                    results.append((f"wsl-{variant}", p))
            except OSError:
                continue
    return results


def _vscode_user_dirs() -> List[Tuple[str, Path]]:
    """Return candidate ``(label, User-dir)`` pairs across variants,
    platforms, and (when applicable) the WSL-mounted Windows store.
    Honors ``VSCODE_CONFIG_DIR`` for testing or non-default installations
    — when set, it short-circuits everything else and is labeled
    ``"override"``. The override is treated as the User-config root
    directly (no extra ``User/`` subdir append), to match the test
    fixture layout used by ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``.
    """
    explicit = os.environ.get("VSCODE_CONFIG_DIR")
    if explicit:
        p = Path(explicit)
        return [("override", p)] if p.is_dir() else []

    candidates: List[Tuple[str, Path]] = []
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = None

    if home is not None:
        # macOS
        mac_base = home / "Library" / "Application Support"
        if mac_base.is_dir():
            for variant in _VARIANTS:
                p = mac_base / variant / "User"
                if p.is_dir():
                    candidates.append((variant, p))

        # Linux (and any platform with ~/.config)
        linux_base = home / ".config"
        if linux_base.is_dir():
            for variant in _VARIANTS:
                p = linux_base / variant / "User"
                if p.is_dir():
                    candidates.append((variant, p))

    # Windows
    appdata = os.environ.get("APPDATA")
    if appdata:
        win_base = Path(appdata)
        if win_base.is_dir():
            for variant in _VARIANTS:
                p = win_base / variant / "User"
                if p.is_dir():
                    candidates.append((variant, p))

    # WSL: the student's real VS Code is often the Windows-side install,
    # reachable only via the /mnt mount — see module docstring.
    if _is_wsl():
        candidates.extend(_wsl_windows_user_dirs())

    return candidates


def _folder_uri_to_path(uri: str) -> Optional[Path]:
    """Convert a workspace folder URI to a ``Path``.

    Handles two schemes:
      * ``file://`` — the ordinary local-workspace case.
      * ``vscode-remote://`` — Remote-WSL (and other VS Code Remote
        variants) record the machine's authority as netloc (e.g.
        ``wsl+Ubuntu``) and the POSIX path on that machine as the URI
        path. We authority-strip and use the path directly; this is
        only meaningful when the code doing the matching runs on the
        SAME machine the workspace was opened on (the only case this
        ingester can act on — it can't reach into a genuinely separate
        remote host).
    """
    try:
        parsed = urlparse(uri)
    except (ValueError, TypeError):
        return None
    if parsed.scheme == "vscode-remote":
        path_str = unquote(parsed.path)
        try:
            return Path(path_str)
        except (OSError, ValueError):
            return None
    if parsed.scheme != "file":
        return None
    path_str = unquote(parsed.path)
    # Windows file URIs look like ``file:///C:/...`` — urlparse leaves a
    # leading slash that must be stripped for Path() to interpret correctly.
    if os.name == "nt" and len(path_str) >= 3 and path_str.startswith("/") and path_str[2] == ":":
        path_str = path_str[1:]
    try:
        return Path(path_str)
    except (OSError, ValueError):
        return None


def _resolve_multiroot_folders(workspace_uri: str) -> List[Path]:
    """Read a multi-root ``.code-workspace`` file (referenced by a
    workspace.json ``workspace`` key) and return its ``folders`` entries
    as absolute paths.

    Each entry is either ``{"uri": "file://..."}`` (absolute, already a
    URI) or ``{"path": "..."}`` (a path resolved relative to the
    ``.code-workspace`` file's own directory when not already absolute —
    this is VS Code's documented resolution rule for multi-root folder
    entries). Never raises; unreadable/malformed files yield ``[]``.
    """
    workspace_file = _folder_uri_to_path(workspace_uri)
    if workspace_file is None:
        return []
    try:
        data = json.loads(workspace_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    folders = data.get("folders")
    if not isinstance(folders, list):
        return []
    base_dir = workspace_file.parent
    resolved: List[Path] = []
    for entry in folders:
        if not isinstance(entry, dict):
            continue
        uri = entry.get("uri")
        if isinstance(uri, str):
            p = _folder_uri_to_path(uri)
            if p is not None:
                resolved.append(p)
            continue
        raw_path = entry.get("path")
        if isinstance(raw_path, str):
            try:
                p = Path(raw_path)
            except (OSError, ValueError):
                continue
            resolved.append(p if p.is_absolute() else base_dir / raw_path)
    return resolved


def _workspace_matches_repo(folder: Path, repo: Path) -> bool:
    """True if ``folder`` (a workspace root) matches ``repo``, in EITHER
    containment direction.

    Normal direction: the workspace folder is ``repo`` itself or a
    subdirectory of it (the student opened VS Code on ``repo`` or a
    subfolder like ``repo/src``).

    Ancestor direction: the workspace folder is a PARENT of ``repo``
    (a "parent-folder workspace" — e.g. a course-wide directory
    containing several assignment repos, opened as one VS Code window).
    Reuses ``platform_compat.path_within_repo`` with the arguments
    reversed and ``exclude_nested_git=False`` — the repo's own ``.git``
    would otherwise read as a foreign boundary in this direction and
    always return False (see the design note in
    ``platform_compat.path_within_repo``'s docstring).
    """
    if platform_compat.path_within_repo(folder, repo):
        return True
    return platform_compat.path_within_repo(repo, folder, exclude_nested_git=False)


def _matching_workspaces(user_dir: Path, repo: Path) -> Iterator[Path]:
    """Yield workspaceStorage subdirs whose workspace (single- or
    multi-root, in either containment direction) matches ``repo``."""
    storage_dir = user_dir / "workspaceStorage"
    try:
        if not storage_dir.is_dir():
            return
    except OSError:
        return
    try:
        workspaces = list(storage_dir.iterdir())
    except OSError:
        return
    for workspace in workspaces:
        try:
            if not workspace.is_dir():
                continue
        except OSError:
            continue
        manifest = workspace / "workspace.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue

        candidate_folders: List[Path] = []
        folder_uri = data.get("folder")
        if isinstance(folder_uri, str):
            folder = _folder_uri_to_path(folder_uri)
            if folder is not None:
                candidate_folders.append(folder)
        else:
            workspace_uri = data.get("workspace")
            if isinstance(workspace_uri, str):
                candidate_folders.extend(_resolve_multiroot_folders(workspace_uri))

        if any(_workspace_matches_repo(folder, repo) for folder in candidate_folders):
            yield workspace


def ingest_transcripts(
    repo: Path, skipped: Optional[List[Path]] = None,
) -> List[Path]:
    """Copy matching chat sessions into repo/.ai-traces/vscode_chat/raw/sessions/.

    When ``skipped`` is provided, every session whose workspace MATCHED
    this repo and which cleared the course floor but was already up to
    date is appended to it — the "matched, not copied" population the
    capture_counts trailer needs so a steady-state run stays
    distinguishable from broken ingest (see
    ``claude_ingest.ingest_transcripts`` for the full rationale). Sessions
    rejected by workspace matching or the floor appear in NEITHER list.

    Selection: any session file under
    ``<user-dir>/workspaceStorage/<id>/chatSessions/`` whose owning
    workspace matches ``repo`` (single- or multi-root, either
    containment direction — see ``_matching_workspaces``), and whose file
    mtime is at or after the course-start floor when one is configured
    (see ``course_floor``). Destinations are namespaced
    ``<variant-label>/<workspace-id>/<filename>`` to avoid collisions
    between installs/workspaces sharing a session filename. Mtime+size
    -aware re-copy (see ``copy_dedup``). Never raises.
    """
    try:
        floor = course_floor.get_course_start_floor(repo)

        copied: List[Path] = []
        dest_root = repo / VSCODE_CHAT_DIR
        for variant, user_dir in _vscode_user_dirs():
            for workspace in _matching_workspaces(user_dir, repo):
                chat_dir = workspace / "chatSessions"
                try:
                    if not chat_dir.is_dir():
                        continue
                except OSError:
                    continue
                try:
                    sessions = list(chat_dir.iterdir())
                except OSError:
                    continue
                dest_dir = dest_root / variant / workspace.name
                for src in sessions:
                    try:
                        if not src.is_file():
                            continue
                        if src.suffix not in (".json", ".jsonl"):
                            continue
                    except OSError:
                        continue
                    try:
                        src_stat = src.stat()
                    except OSError:
                        continue
                    if course_floor.file_predates_course(src_stat.st_mtime, floor):
                        continue
                    try:
                        dest_dir.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        continue
                    dest = dest_dir / src.name
                    if dest.exists():
                        try:
                            dest_stat = dest.stat()
                            if copy_dedup.dest_is_up_to_date(
                                dest_stat.st_size, dest_stat.st_mtime,
                                src_stat.st_size, src_stat.st_mtime,
                            ):
                                # Matched and current — counted, not re-copied.
                                if skipped is not None:
                                    skipped.append(dest)
                                continue
                        except OSError:
                            continue
                    try:
                        shutil.copy2(src, dest)
                        copied.append(dest)
                    except OSError:
                        continue
        return copied
    except Exception:
        return []
