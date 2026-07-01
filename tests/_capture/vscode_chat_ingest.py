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
workspace's directory contains a single-key ``workspace.json`` mapping
the hash back to the folder URI. We iterate, read each, and match
against the repo.

Contract: never raises. Returns the list of copied destination paths.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterator, List, Optional
from urllib.parse import unquote, urlparse

from tests._capture import ai_traces

VSCODE_CHAT_DIR = Path(ai_traces.AI_TRACES_DIR) / "vscode_chat" / "raw" / "sessions"
_VARIANTS = ("Code", "Code - Insiders", "VSCodium")


def _vscode_user_dirs() -> List[Path]:
    """Return candidate VS Code User directories across variants/platforms.
    Honors ``VSCODE_CONFIG_DIR`` for testing or non-default installations.
    The override is treated as the User-config root directly (no extra
    ``User/`` subdir append), to match the test fixture layout used by
    ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``.
    """
    explicit = os.environ.get("VSCODE_CONFIG_DIR")
    if explicit:
        p = Path(explicit)
        return [p] if p.is_dir() else []

    candidates: List[Path] = []
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        return candidates

    # macOS
    mac_base = home / "Library" / "Application Support"
    if mac_base.is_dir():
        for variant in _VARIANTS:
            p = mac_base / variant / "User"
            if p.is_dir():
                candidates.append(p)

    # Linux (and any platform with ~/.config)
    linux_base = home / ".config"
    if linux_base.is_dir():
        for variant in _VARIANTS:
            p = linux_base / variant / "User"
            if p.is_dir():
                candidates.append(p)

    # Windows
    appdata = os.environ.get("APPDATA")
    if appdata:
        win_base = Path(appdata)
        if win_base.is_dir():
            for variant in _VARIANTS:
                p = win_base / variant / "User"
                if p.is_dir():
                    candidates.append(p)

    return candidates


def _folder_uri_to_path(uri: str) -> Optional[Path]:
    """Convert a workspace folder URI (``file:///...``) to a ``Path``."""
    try:
        parsed = urlparse(uri)
    except (ValueError, TypeError):
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


def _matching_workspaces(user_dir: Path, repo: Path) -> Iterator[Path]:
    """Yield workspaceStorage subdirs whose ``workspace.json`` folder matches repo."""
    storage_dir = user_dir / "workspaceStorage"
    try:
        if not storage_dir.is_dir():
            return
    except OSError:
        return
    try:
        repo_resolved = repo.resolve()
    except (OSError, ValueError):
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
        folder_uri = data.get("folder")
        if not isinstance(folder_uri, str):
            continue
        folder = _folder_uri_to_path(folder_uri)
        if folder is None:
            continue
        try:
            if folder.resolve() == repo_resolved:
                yield workspace
        except (OSError, ValueError):
            continue


def ingest_transcripts(repo: Path, session_started_at: float) -> List[Path]:
    """Copy matching chat sessions into repo/.ai-traces/vscode_chat/raw/sessions/.

    Selection: any session file under
    ``<user-dir>/workspaceStorage/<id>/chatSessions/`` whose owning
    workspace's ``workspace.json`` declares a ``folder`` URI matching
    ``repo``. Size-aware re-copy. Never raises.

    ``session_started_at`` is accepted for API stability but unused — see
    codex_ingest for the same rationale.
    """
    del session_started_at
    try:
        copied: List[Path] = []
        dest_dir = repo / VSCODE_CHAT_DIR
        for user_dir in _vscode_user_dirs():
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
                for src in sessions:
                    try:
                        if not src.is_file():
                            continue
                        if src.suffix not in (".json", ".jsonl"):
                            continue
                    except OSError:
                        continue
                    try:
                        src_size = src.stat().st_size
                    except OSError:
                        continue
                    try:
                        dest_dir.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        return copied
                    dest = dest_dir / src.name
                    if dest.exists():
                        try:
                            if dest.stat().st_size >= src_size:
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
