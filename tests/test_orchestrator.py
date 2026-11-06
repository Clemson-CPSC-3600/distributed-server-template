"""Tests for the snapshot orchestrator (`tests._capture.orchestrator`)."""
import json
import subprocess
from pathlib import Path

import pytest

from tests._capture import metadata, orchestrator
from tests._capture.agent_adapters.base import AdapterMetadata, IngestResult


@pytest.fixture
def tmp_git_repo(tmp_path):
    """Plain tmp git repo with no capture config — capture should be off."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _read_auto_track_body(repo: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%B", "refs/auto-track/snapshots"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def test_take_snapshot_pytest_trigger_creates_one_snapshot(
        tmp_git_repo_with_capture):
    repo = tmp_git_repo_with_capture
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="sess-1",
        test_result=metadata.TestResult(passed=2, total=2),
    )
    assert sha is not None
    body = _read_auto_track_body(repo)
    assert "trigger: pytest" in body
    assert "agent_name: none" in body  # no adapter ingested anything
    assert "session_id: sess-1" in body


def test_take_snapshot_dedup_by_session_id(tmp_git_repo_with_capture):
    repo = tmp_git_repo_with_capture
    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="sess-1")
    s2 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="sess-1")
    assert s1 is not None
    assert s2 is None  # dedup'd


def test_take_snapshot_manual_trigger_skips_dedup(tmp_git_repo_with_capture):
    """Manual trigger always produces a snapshot per spec decision row 6."""
    repo = tmp_git_repo_with_capture
    s1 = orchestrator.take_snapshot(
        repo, trigger="manual", pytest_session_id=None)
    s2 = orchestrator.take_snapshot(
        repo, trigger="manual", pytest_session_id=None)
    assert s1 is not None
    assert s2 is not None  # manual always snapshots
    assert s1 != s2


def test_take_snapshot_returns_none_when_capture_disabled(tmp_git_repo):
    """No project-template-config.json or capture_enabled false → None."""
    sha = orchestrator.take_snapshot(
        tmp_git_repo, trigger="pytest", pytest_session_id="x")
    assert sha is None


def test_take_snapshot_records_adapter_session(tmp_git_repo_with_capture):
    """Codex Stop trigger labels the snapshot with the agent session id."""
    repo = tmp_git_repo_with_capture
    meta = AdapterMetadata(
        adapter_name="codex",
        agent_session_id="abc-123",
        rollout_path=None, extra={},
    )
    sha = orchestrator.take_snapshot(
        repo, trigger="codex_stop", pytest_session_id=None,
        adapter_metadata=meta,
    )
    assert sha is not None
    body = _read_auto_track_body(repo)
    assert "trigger: codex_stop" in body
    assert "agent_name: codex" in body
    assert "agent_session_id: abc-123" in body


def test_take_snapshot_pytest_dedupe_by_tree_sha_when_no_session_match(
        tmp_git_repo_with_capture):
    """A second pytest call with a fresh session_id but identical tree
    is dedupe'd by tree-SHA when no adapter session is present."""
    repo = tmp_git_repo_with_capture
    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="sess-1")
    assert s1 is not None
    s2 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="sess-2")
    assert s2 is None  # tree unchanged, no adapter session


def test_take_snapshot_writes_to_log_on_failure(
        tmp_git_repo_with_capture, monkeypatch):
    """If the snapshot fails internally, an error line is appended to
    .test-runs.log and None is returned."""
    repo = tmp_git_repo_with_capture
    monkeypatch.setattr(
        "tests._capture.git_ops.snapshot_to_auto_track",
        lambda *a, **kw: None,
    )
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="boom-1")
    assert sha is None
    log = (repo / ".test-runs.log").read_text(encoding="utf-8")
    assert "snapshot failed" in log


def test_take_snapshot_manual_reason_appended_to_body(tmp_git_repo_with_capture):
    repo = tmp_git_repo_with_capture
    sha = orchestrator.take_snapshot(
        repo, trigger="manual", pytest_session_id=None,
        manual_reason="instructor checkpoint",
    )
    assert sha is not None
    body = _read_auto_track_body(repo)
    assert "manual_reason: instructor checkpoint" in body


def test_take_snapshot_lock_contention_returns_none_when_held(
        tmp_git_repo_with_capture, monkeypatch):
    """If the lock can't be acquired (simulated), return None cleanly."""
    import contextlib

    @contextlib.contextmanager
    def fake_lock(repo):
        yield False  # signal contention

    monkeypatch.setattr(orchestrator, "_orchestrator_lock", fake_lock)
    sha = orchestrator.take_snapshot(
        tmp_git_repo_with_capture, trigger="pytest",
        pytest_session_id="contended")
    assert sha is None


def test_take_snapshot_swallows_top_level_exception(
        tmp_git_repo_with_capture, monkeypatch):
    """Never raise — even on unexpected internal errors."""
    monkeypatch.setattr(
        "tests._capture.git_ops.is_git_repo",
        lambda repo: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )
    sha = orchestrator.take_snapshot(
        tmp_git_repo_with_capture, trigger="pytest", pytest_session_id="x")
    assert sha is None


# --- Task 2.5.8 end-to-end integration ------------------------------------

def test_three_triggers_each_produce_correctly_labeled_snapshots(
        tmp_git_repo_with_capture):
    """pytest, codex_stop, and manual triggers each produce a snapshot
    whose body fields match the trigger when the tree changes between
    each call."""
    repo = tmp_git_repo_with_capture

    # 1. pytest trigger
    (repo / "src" / "a.py").write_text("a = 1\n")
    s_pytest = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="p1",
        test_result=metadata.TestResult(passed=1, total=1),
    )
    assert s_pytest is not None
    body_p = subprocess.run(
        ["git", "log", "-1", "--format=%B", s_pytest],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    assert "trigger: pytest" in body_p
    assert "agent_name: none" in body_p

    # 2. codex_stop trigger with adapter metadata
    (repo / "src" / "b.py").write_text("b = 2\n")
    meta = AdapterMetadata(
        adapter_name="codex", agent_session_id="codex-1",
        rollout_path=None, extra={},
    )
    s_codex = orchestrator.take_snapshot(
        repo, trigger="codex_stop", pytest_session_id=None,
        adapter_metadata=meta,
    )
    assert s_codex is not None
    body_c = subprocess.run(
        ["git", "log", "-1", "--format=%B", s_codex],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    assert "trigger: codex_stop" in body_c
    assert "agent_name: codex" in body_c
    assert "agent_session_id: codex-1" in body_c

    # 3. manual trigger
    s_manual = orchestrator.take_snapshot(
        repo, trigger="manual", pytest_session_id=None,
        manual_reason="hand checkpoint",
    )
    assert s_manual is not None
    body_m = subprocess.run(
        ["git", "log", "-1", "--format=%B", s_manual],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    assert "trigger: manual" in body_m
    assert "manual_reason: hand checkpoint" in body_m

    # All three SHAs are distinct.
    assert len({s_pytest, s_codex, s_manual}) == 3


def test_multi_trigger_collapse_when_tree_unchanged(
        tmp_git_repo_with_capture):
    """Fire pytest then codex_stop in rapid succession against the same
    tree. The codex_stop carries an adapter session and therefore is NOT
    tree-SHA-deduped (the agent attribution is new information). But a
    second pytest call on top of that — with no adapter context and an
    unchanged tree — must dedupe."""
    repo = tmp_git_repo_with_capture
    (repo / "src" / "x.py").write_text("x = 1\n")

    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="p-1")
    assert s1 is not None

    # codex_stop on unchanged tree but new agent session — DOES snapshot.
    meta = AdapterMetadata(
        adapter_name="codex", agent_session_id="cx-1",
        rollout_path=None, extra={},
    )
    s2 = orchestrator.take_snapshot(
        repo, trigger="codex_stop", pytest_session_id=None,
        adapter_metadata=meta,
    )
    assert s2 is not None
    assert s2 != s1

    # Another pytest call on still-unchanged tree, no adapter — DEDUPES.
    s3 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="p-2")
    assert s3 is None


def test_tree_changes_between_triggers_produce_distinct_snapshots(
        tmp_git_repo_with_capture):
    """Sanity: each tree change yields a distinct snapshot SHA."""
    repo = tmp_git_repo_with_capture
    (repo / "src" / "a.py").write_text("a = 1\n")
    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="p1")
    (repo / "src" / "a.py").write_text("a = 2\n")
    s2 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="p2")
    (repo / "src" / "a.py").write_text("a = 3\n")
    s3 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="p3")
    assert s1 is not None and s2 is not None and s3 is not None
    assert len({s1, s2, s3}) == 3


# --- aider chat history capture --------------------------------------------

def _list_tree(repo: Path, sha: str) -> set:
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", sha],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    return set(out.splitlines())


def test_snapshot_tree_contains_aider_chat_history_when_present(
        tmp_git_repo_with_capture):
    """Aider writes its transcript straight into the project cwd -- no
    scanning/matching adapter step needed (the "freebie" case). The
    transcript must reach the snapshot tree even though `.aider*` is
    gitignored, proving the aider adapter's force_paths() wins over
    .gitignore via the existing force-staging mechanism.

    Since the redaction decision (uvd.5) what LANDS is the redacted
    derivative under `.ai-traces/aider/redacted/`, not the raw original:
    force-staging still defeats gitignore evasion (the transcript cannot be
    hidden), and `unstage_after` then drops the unredacted bytes.

    Aider's chat history is prose with no verified record vocabulary, so
    under default-deny recognition the derivative is a WITHHELD MARKER
    rather than scrubbed text. The anti-evasion property is unchanged and is
    what this test pins: a gitignored aider transcript still forces an
    artifact into the snapshot, so its existence cannot be hidden."""
    repo = tmp_git_repo_with_capture
    (repo / ".gitignore").write_text(".aider*\n")
    (repo / ".aider.chat.history.md").write_text(
        "# aider chat log\nOPENAI_API_KEY=sk-abcdefghijklmnop0123456789\n")

    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="aider-1")
    assert sha is not None
    files = _list_tree(repo, sha)
    marker = ".ai-traces/aider/redacted/.aider.chat.history.md.withheld.json"
    assert marker in files
    assert ".aider.chat.history.md" not in files
    blob = subprocess.run(
        ["git", "show", f"{sha}:{marker}"],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    assert "sk-abcdefghijklmnop0123456789" not in blob
    assert json.loads(blob)["reason"] == "unrecognized_record_vocabulary"


class _TraceStagingAdapter:
    """Stages AND force-stages `.ai-traces`, like the real Codex/Claude
    adapters do (Attack K), so raw/ WOULD land in the commit unless the
    orchestrator drops it again."""

    name = "codex"
    transcripts_dir = ".ai-traces"

    def is_present(self):
        return True

    def ingest(self, repo, since):
        return IngestResult(adapter_name=self.name)

    def stage_paths(self):
        return [".ai-traces"]

    def force_paths(self):
        return [".ai-traces"]

    def unstage_after(self):
        return []


def test_snapshot_commit_tree_contains_no_raw_trace_paths(
        tmp_git_repo_with_capture, monkeypatch):
    """The unredacted raw/ bytes must never be committed -- checked on the
    ACTUAL snapshot tree, for a pull-side adapter (codex) and a hook-only
    one (cursor, which has no adapter class to declare its paths)."""
    repo = tmp_git_repo_with_capture
    (repo / ".gitignore").write_text(".ai-traces/*/raw/\n")
    for rel, row in [
        ("codex/raw/rollouts/r.jsonl",
         {"type": "event_msg",
          "payload": {"type": "user_message", "message": "RAW-ONLY"}}),
        ("cursor/raw/hooks/hooks-c1.jsonl",
         {"adapter_name": "cursor", "hook_event_name": "stop",
          "payload": {"conversation_id": "c1"}}),
    ]:
        path = repo / ".ai-traces" / rel
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(orchestrator, "installed_adapters",
                        lambda r: [_TraceStagingAdapter()])

    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="raw-1")

    assert sha is not None
    files = _list_tree(repo, sha)
    # Non-vacuous: .ai-traces WAS staged -- the derivatives are there.
    assert ".ai-traces/codex/redacted/rollouts/r.jsonl" in files
    assert ".ai-traces/cursor/redacted/hooks/hooks-c1.jsonl" in files
    raw = [f for f in files
           if f.startswith(".ai-traces/") and "/raw/" in f]
    assert raw == []


def test_aider_force_paths_do_not_break_tree_dedupe(tmp_git_repo_with_capture):
    """REGRESSION GUARD: gitignored force-paths must not defeat tree-SHA
    dedupe. preview_tree used to run a plain `git add -A` over every
    stage_path, including aider's gitignored `.aider*`; git exits 1 on an
    explicitly-ignored pathspec, so the preview returned None, the dedupe
    guard (`new_tree is not None`) was skipped, and every trigger wrote a
    fresh commit with an identical tree -- with diff stats computed against
    the empty tree. preview_tree now mirrors _build_and_commit's
    force-staging, so the second unchanged run dedupes."""
    repo = tmp_git_repo_with_capture
    (repo / ".gitignore").write_text(".aider*\n")
    (repo / ".aider.chat.history.md").write_text("# aider chat log\n")

    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="aider-dedupe-1")
    assert s1 is not None
    # Post-uvd.5 the committed artifact is the redacted derivative (here a
    # withheld marker, since aider prose has no verified vocabulary); the
    # force-staging path being exercised is the same one.
    redacted = (".ai-traces/aider/redacted/"
                ".aider.chat.history.md.withheld.json")
    assert redacted in _list_tree(repo, s1)

    s2 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="aider-dedupe-2")
    assert s2 is None, "unchanged tree must dedupe even with aider files"

    # And a real change still snapshots, with sane diff stats (not a
    # whole-tree diff against EMPTY_TREE).
    (repo / ".aider.chat.history.md").write_text("# aider chat log\nmore\n")
    s3 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="aider-dedupe-3")
    assert s3 is not None
    body = _read_auto_track_body(repo)
    assert f"files_changed: {redacted}\n" in body, body


# --- Task: capture-health manifest ----------------------------------------

HEALTH_PATH = ".ai-traces/capture-health.json"


class _StubAdapter:
    """Minimal AgentAdapter used to drive health-manifest assertions."""

    transcripts_dir = ".ai-traces"

    def __init__(self, name, *, raises=None, result=None):
        self.name = name
        self._raises = raises
        self._result = result

    def is_present(self):
        return True

    def is_present_for_repo(self, repo):
        return True

    def ingest(self, repo, since):
        if self._raises is not None:
            raise self._raises
        return self._result or IngestResult(adapter_name=self.name)

    def detect_active_session(self):
        return None

    def stage_paths(self):
        return []


def _read_health(repo: Path) -> dict:
    return json.loads((repo / HEALTH_PATH).read_text(encoding="utf-8"))


def test_health_manifest_records_raising_adapter_error(
        tmp_git_repo_with_capture, monkeypatch):
    """A raising adapter yields a content-free error code in the manifest."""
    repo = tmp_git_repo_with_capture
    monkeypatch.setattr(
        orchestrator, "installed_adapters",
        lambda r: [_StubAdapter("codex", raises=RuntimeError("ingest exploded"))],
    )
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="health-1")
    assert sha is not None

    health = _read_health(repo)
    entry = health["adapters"]["codex"]
    assert entry["present"] is True
    assert entry["errors"] == ["adapter_ingest_failed"]
    # The manifest is in the snapshot tree, not just the working dir.
    assert HEALTH_PATH in _list_tree(repo, sha)


def test_health_manifest_records_adapter_reported_errors(
        tmp_git_repo_with_capture, monkeypatch):
    """Non-raising adapters that report errors also surface in the manifest."""
    repo = tmp_git_repo_with_capture
    monkeypatch.setattr(
        orchestrator, "installed_adapters",
        lambda r: [_StubAdapter(
            "claude",
            result=IngestResult(adapter_name="claude", errors=["store unreadable"]),
        )],
    )
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="health-2")
    assert sha is not None
    assert _read_health(repo)["adapters"]["claude"]["errors"] == ["adapter_ingest_failed"]


def test_health_manifest_lists_all_registered_adapters_with_presence(
        tmp_git_repo_with_capture):
    """Every registered adapter appears, present or not — absence of traces
    must be interpretable, not silent."""
    repo = tmp_git_repo_with_capture
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="health-3")
    assert sha is not None
    adapters = _read_health(repo)["adapters"]
    for name in ("codex", "claude", "vscode_chat", "aider"):
        assert name in adapters
        assert isinstance(adapters[name]["present"], bool)
        assert isinstance(adapters[name]["heartbeat"], bool)


def test_health_manifest_reports_bootstrap_state(tmp_git_repo_with_capture):
    """Bootstrap booleans (sitecustomize / hooksPath / git-dir-is-file)."""
    repo = tmp_git_repo_with_capture
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="health-4")
    assert sha is not None
    bootstrap = _read_health(repo)["bootstrap"]
    for key in ("sitecustomize_installed", "hooks_path_set", "git_dir_is_file"):
        assert isinstance(bootstrap[key], bool)
    assert bootstrap["git_dir_is_file"] is False  # plain repo: .git is a dir


def test_health_manifest_contains_no_volatile_fields(tmp_git_repo_with_capture):
    """Only booleans, strings and containers thereof — no timestamps,
    counters, durations or ids. A single int would break tree-dedupe."""
    repo = tmp_git_repo_with_capture
    assert orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="health-5") is not None
    health = _read_health(repo)

    banned = ("time", "stamp", "count", "duration", "elapsed", "session",
              "scanned", "matched", "copied")

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                assert isinstance(k, str)
                assert not any(b in k.lower() for b in banned), \
                    f"volatile-looking key {path}{k}"
                walk(v, f"{path}{k}.")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        else:
            assert isinstance(node, (bool, str)), \
                f"non-stable value type at {path}: {type(node).__name__}"

    walk(health)


def test_health_manifest_stable_across_runs_so_tree_dedupe_survives(
        tmp_git_repo_with_capture):
    """REGRESSION GUARD: two runs with nothing else changed must produce
    byte-identical manifests and therefore dedupe. Adding any run-varying
    field to the manifest fails this test."""
    repo = tmp_git_repo_with_capture
    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="dedupe-1")
    assert s1 is not None
    first_bytes = (repo / HEALTH_PATH).read_bytes()

    s2 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="dedupe-2")
    assert s2 is None, "unchanged tree must dedupe"
    assert (repo / HEALTH_PATH).read_bytes() == first_bytes
    assert first_bytes.endswith(b"\n")


def test_health_counts_go_in_commit_trailers_matched_only(
        tmp_git_repo_with_capture, monkeypatch):
    """Volatile counts live in the commit message, scoped to this repo's
    matches — never a store-wide scan total."""
    repo = tmp_git_repo_with_capture
    monkeypatch.setattr(
        orchestrator, "installed_adapters",
        lambda r: [_StubAdapter("codex", result=IngestResult(
            adapter_name="codex",
            rollouts_copied=[Path("a.jsonl")],
            rollouts_skipped=[Path("b.jsonl"), Path("c.jsonl")],
        ))],
    )
    sha = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="counts-1")
    assert sha is not None
    body = _read_auto_track_body(repo)
    assert "capture_counts:" in body
    assert "codex: matched=3 copied=1" in body
    assert "scanned" not in body  # store-wide totals leak out-of-course usage


def test_real_codex_adapter_keeps_counts_trailer_in_steady_state(
        tmp_git_repo_with_capture, tmp_path, monkeypatch):
    """REGRESSION GUARD (bead 8g5.7): with a REAL adapter -- not the stub
    that fabricates rollouts_skipped -- a second snapshot that copies
    nothing new must still emit capture_counts, as matched=N copied=0.

    Before the fix every adapter left rollouts_skipped empty, so
    matched == copied, and `if not matched: continue` dropped the whole
    trailer on every steady-state run. A student whose ingest silently
    broke and a student who simply used no AI that session produced
    byte-identical evidence -- the exact ambiguity epic 8g5 exists to
    remove.
    """
    import time
    from tests._capture.agent_adapters.codex import CodexAdapter

    repo = tmp_git_repo_with_capture
    codex_home = tmp_path / "codex_home"
    sessions = codex_home / "sessions" / "2026" / "08" / "12"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-2026-08-12T10-00-00-abc.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta",
                    "payload": {"cwd": str(repo), "id": "abc"}}) + "\n",
        encoding="utf-8",
    )
    old = time.time() - 60
    import os as _os
    _os.utime(rollout, (old, old))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(orchestrator, "installed_adapters",
                        lambda r: [CodexAdapter()])

    s1 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="steady-1")
    assert s1 is not None
    assert "codex: matched=1 copied=1" in _read_auto_track_body(repo)

    # Nothing changed at the source: the second pass copies nothing.
    s2 = orchestrator.take_snapshot(
        repo, trigger="pytest", pytest_session_id="steady-2", force=True)
    assert s2 is not None
    body = _read_auto_track_body(repo)
    assert "capture_counts:" in body, (
        "steady-state run must still report counts -- a missing trailer "
        "is indistinguishable from ingest being broken"
    )
    assert "codex: matched=1 copied=0" in body
