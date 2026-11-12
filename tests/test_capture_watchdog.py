"""Tests for the watchdog hang-commit path.

The watchdog runs in a detached subprocess and bypasses the orchestrator
(per orchestrator spec section 11) to avoid deadlocking on a lock the
hung parent may still hold. These tests exercise the same primitives
the watchdog calls, without spawning a real watchdog subprocess against
a hanging parent.
"""
import subprocess

from tests._capture import git_ops, metadata


def test_watchdog_hang_path_writes_to_auto_track(tmp_git_repo_with_capture):
    repo = tmp_git_repo_with_capture
    # Simulate the watchdog's hang-commit code path directly (don't spawn it)
    head_ref, head_sha = git_ops.current_head_info(repo)
    git_state = git_ops.detect_git_state(repo)
    msg = metadata.format_commit_message(
        session_id="hang01", status="hang_watchdog_killed",
        result=metadata.TestResult(),
        diff_added=0, diff_removed=0, files_changed=[],
        hostname_hash=metadata.hostname_hash(str(repo)),
        current_head_ref=head_ref,
        current_head_sha=head_sha if head_sha else "unborn",
        git_state=git_state,
        trigger="pytest_watchdog",
    )
    new_sha = git_ops.snapshot_to_auto_track(
        repo, msg, ["src", "tests"], head_ref, head_sha)
    assert new_sha is not None
    body = subprocess.run(
        ["git", "log", "-1", "--format=%B", "refs/auto-track/snapshots"],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    assert "status: hang_watchdog_killed" in body
    assert "session_id: hang01" in body
    # Watchdog snapshots use a distinct trigger label so research scripts
    # filtering by trigger correctly classify them as watchdog-fired
    # rather than ordinary pytest sessions.
    assert "trigger: pytest_watchdog" in body


def test_watchdog_snapshot_never_commits_unredacted_trace_content(
        tmp_git_repo_with_capture):
    """The watchdog skips the orchestrator (and so the redaction pass), so
    it must omit the whole AI-trace namespace. Previously redacted output
    and content-free metadata are unverified while the parent may be writing
    them; safe evidence from earlier snapshots remains in Git parents."""
    import time
    from tests._capture import watchdog

    repo = tmp_git_repo_with_capture
    traces = repo / ".ai-traces"
    files = {
        "codex/raw/rollouts/r.jsonl": "RAW",
        "codex/normalized/s1.jsonl": "UNREDACTED-NORMALIZED",
        "interaction-stream.jsonl": "UNREDACTED-STREAM",
        "codex/redacted/rollouts/r.jsonl": "REDACTED",
        "codex/heartbeat/s1.json": "{}",
    }
    for rel, text in files.items():
        (traces / rel).parent.mkdir(parents=True, exist_ok=True)
        (traces / rel).write_text(text + "\n")

    sha = watchdog.record_hang_snapshot(repo, "hang02", time.time() - 5)

    assert sha is not None
    tree = set(subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", sha],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.splitlines())
    assert not [p for p in tree if p.startswith(".ai-traces/")]
    assert "src/.gitkeep" in tree
