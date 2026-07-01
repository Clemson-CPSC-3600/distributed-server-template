"""Tests for vscode_chat_ingest — fake VSCODE_CONFIG_DIR, no real VS Code needed.

The module under test scans
``$VSCODE_CONFIG_DIR/workspaceStorage/<id>/chatSessions/`` for matching
workspaces (resolved via each workspace's ``workspace.json`` ``folder``
URI) and copies the session files into
``<repo>/.ai-traces/vscode_chat/raw/sessions/``. Dedup is size-aware.
"""
import json
import time
from pathlib import Path

import pytest

from tests._capture import vscode_chat_ingest


@pytest.fixture
def fake_vscode_home(tmp_path, monkeypatch):
    """Create a fake VS Code User dir with empty workspaceStorage/."""
    user_dir = tmp_path / "vscode_user"
    (user_dir / "workspaceStorage").mkdir(parents=True)
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(user_dir))
    return user_dir


def _write_workspace(user_dir: Path, ws_id: str, folder: Path,
                     sessions: dict[str, str] | None = None,
                     legacy_sessions: dict[str, str] | None = None) -> Path:
    """Create a workspaceStorage entry mapping ws_id to folder, optionally
    with chat session files."""
    ws_dir = user_dir / "workspaceStorage" / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    folder_uri = "file://" + str(folder)
    (ws_dir / "workspace.json").write_text(
        json.dumps({"folder": folder_uri}), encoding="utf-8",
    )
    if sessions or legacy_sessions:
        chat = ws_dir / "chatSessions"
        chat.mkdir(parents=True, exist_ok=True)
        for sid, content in (sessions or {}).items():
            (chat / f"{sid}.jsonl").write_text(content, encoding="utf-8")
        for sid, content in (legacy_sessions or {}).items():
            (chat / f"{sid}.json").write_text(content, encoding="utf-8")
    return ws_dir


def test_ingest_copies_sessions_for_matching_workspace(tmp_path, fake_vscode_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_workspace(fake_vscode_home, "ws-match", repo,
                     sessions={"sess-1": '{"kind":0,"v":{}}\n'})
    other = tmp_path / "other"
    other.mkdir()
    _write_workspace(fake_vscode_home, "ws-other", other,
                     sessions={"sess-other": '{"kind":0,"v":{}}\n'})

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert len(copied) == 1
    assert copied[0].name == "sess-1.jsonl"
    dest = repo / ".ai-traces" / "vscode_chat" / "raw" / "sessions"
    assert (dest / "sess-1.jsonl").exists()
    assert not (dest / "sess-other.jsonl").exists()


def test_ingest_picks_up_both_jsonl_and_legacy_json(tmp_path, fake_vscode_home):
    """VS Code 1.x stored sessions as .json; 2.x uses .jsonl. Both should be copied."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_workspace(fake_vscode_home, "ws", repo,
                     sessions={"new": '{"kind":0,"v":{}}\n'},
                     legacy_sessions={"old": '{"version":1}'})

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    names = sorted(p.name for p in copied)
    assert names == ["new.jsonl", "old.json"]


def test_ingest_skips_non_session_files_in_chat_dir(tmp_path, fake_vscode_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = _write_workspace(fake_vscode_home, "ws", repo,
                          sessions={"real": '{"kind":0,"v":{}}\n'})
    (ws / "chatSessions" / "scratch.txt").write_text("noise", encoding="utf-8")

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert [p.name for p in copied] == ["real.jsonl"]


def test_ingest_ignores_workspace_with_different_folder(tmp_path, fake_vscode_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write_workspace(fake_vscode_home, "ws", other,
                     sessions={"x": '{"kind":0,"v":{}}\n'})

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert copied == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_handles_workspace_without_workspace_json(tmp_path, fake_vscode_home):
    """workspaceStorage subdirs without workspace.json (just state.vscdb)
    must be skipped quietly, not raise."""
    ws = fake_vscode_home / "workspaceStorage" / "orphan"
    ws.mkdir(parents=True)
    (ws / "state.vscdb").write_text("fake-sqlite", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert copied == []


def test_ingest_handles_malformed_workspace_json(tmp_path, fake_vscode_home):
    ws = fake_vscode_home / "workspaceStorage" / "broken"
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text("{ not valid json", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert copied == []


def test_ingest_is_idempotent_when_source_unchanged(tmp_path, fake_vscode_home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_workspace(fake_vscode_home, "ws", repo,
                     sessions={"s": '{"kind":0,"v":{}}\n'})

    first = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)
    second = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert len(first) == 1
    assert second == []


def test_ingest_recopies_when_source_grew(tmp_path, fake_vscode_home):
    """VS Code chat is a mutation log — the file grows as turns are added.
    Mirrors the Bug 2 lesson: re-copy when source has grown."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = _write_workspace(fake_vscode_home, "ws", repo,
                          sessions={"grow": '{"kind":0,"v":{}}\n'})
    src = ws / "chatSessions" / "grow.jsonl"

    first = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)
    assert len(first) == 1
    dest = repo / ".ai-traces" / "vscode_chat" / "raw" / "sessions" / "grow.jsonl"
    initial_size = dest.stat().st_size

    with src.open("a", encoding="utf-8") as f:
        f.write('{"kind":1,"k":["customTitle"],"v":"Grown"}\n')
    grown_size = src.stat().st_size
    assert grown_size > initial_size

    second = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)
    assert len(second) == 1
    assert dest.stat().st_size == grown_size


def test_ingest_returns_empty_when_no_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(tmp_path / "missing"))
    repo = tmp_path / "repo"
    repo.mkdir()

    assert vscode_chat_ingest.ingest_transcripts(repo, time.time()) == []
    assert not (repo / ".ai-traces").exists()


def test_ingest_handles_uri_with_percent_encoding(tmp_path, fake_vscode_home):
    """Workspace folder URIs encode spaces as %20, etc. Path resolution
    must decode them before comparing to the repo path."""
    repo = tmp_path / "with space"
    repo.mkdir()
    ws = fake_vscode_home / "workspaceStorage" / "spaced"
    ws.mkdir(parents=True)
    encoded = "file://" + str(repo).replace(" ", "%20")
    (ws / "workspace.json").write_text(
        json.dumps({"folder": encoded}), encoding="utf-8",
    )
    (ws / "chatSessions").mkdir()
    (ws / "chatSessions" / "s.jsonl").write_text(
        '{"kind":0,"v":{}}\n', encoding="utf-8",
    )

    copied = vscode_chat_ingest.ingest_transcripts(repo, time.time() - 60)

    assert len(copied) == 1
