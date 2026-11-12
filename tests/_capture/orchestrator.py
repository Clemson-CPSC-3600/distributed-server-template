"""Single entry point for creating a capture snapshot.

Called by:
    - tests/_capture/capture.py::session_finish (pytest trigger)        [Phase 3]
    - .claude/hooks/stop.py (claude_code_stop trigger)                  [Plan 3]
    - python -m tests._capture snapshot (manual trigger)                [Task 2.5.7]

Note on Codex: Codex 0.129+ requires interactive trust approval (TUI
``/hooks`` slash-command) before any project-local hook will fire, and the
non-interactive ``codex exec`` and current VS Code extension paths do not
surface that prompt. Codex sessions are therefore captured exclusively via
the pytest-trigger path — students get full coverage by running
``python run_tests.py`` after each Codex session.

See orchestrator spec §4.3 for step ordering. Never raises.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from tests._capture import auth, capture_health, git_ops, metadata, redaction
from tests._capture.agent_adapters import installed_adapters
from tests._capture.agent_adapters.base import AdapterMetadata, IngestResult

LOCK_FILENAME = "auto-track.lock"
# 30s absorbs paired triggers from a single pytest exit (pytest plugin's
# session_finish and sitecustomize's atexit both call the orchestrator
# within ~100ms) plus typical git op latency. Push is already detached,
# so the lock holder only does local work — there's no deadlock risk.
LOCK_TIMEOUT_SECONDS = 30.0
LOG_FILENAME = ".test-runs.log"


def take_snapshot(
    repo: Path,
    *,
    trigger: str,
    pytest_session_id: Optional[str],
    test_result: Optional[metadata.TestResult] = None,
    status: str = "completed",
    adapter_metadata: Optional[AdapterMetadata] = None,
    manual_reason: Optional[str] = None,
    force: bool = False,
) -> Optional[str]:
    """See module docstring + spec §4.3. Returns snapshot SHA or None."""
    try:
        if not _capture_enabled(repo):
            return None
        if not git_ops.is_git_repo(repo):
            return None

        with _orchestrator_lock(repo) as locked:
            if not locked:
                _log(repo, f"orchestrator: lock contention, trigger={trigger} -- skipped")
                return None

            # Non-pytest triggers do not pass through capture.session_start.
            # Refresh their ancestry too, so a fresh clone extends the remote
            # archive instead of starting a disconnected snapshot chain.
            if trigger != "pytest":
                try:
                    fetched = git_ops.fetch_auto_track(repo)
                except (OSError, subprocess.TimeoutExpired):
                    fetched = None
                if not fetched:
                    # Offline clones can still preserve the last fetched
                    # remote ancestry. This is cached evidence, not upload
                    # acknowledgment; the receipt continues to say failed.
                    cached = git_ops.run_git(
                        ["rev-parse", "--verify", "refs/remotes/origin/auto-track"],
                        cwd=repo, timeout=5.0)
                    if cached.returncode == 0:
                        git_ops.run_git(["update-ref", git_ops.AUTO_TRACK_ORIGIN_TIP_REF,
                                         cached.stdout.strip()], cwd=repo, timeout=5.0)

            # Session-id dedupe (skip for manual trigger per spec row 6).
            # Match the body's `session_id:` line exactly: substring match
            # would false-positive against the `agent_session_id:` line if
            # a pytest session_id (8 hex chars from secrets.token_hex(4))
            # happened to equal an agent session id. Match per-line so the
            # `session_id` key isn't confused with `agent_session_id`.
            if trigger != "manual" and pytest_session_id is not None:
                last = git_ops.run_git(
                    ["log", "-1", "--format=%B", git_ops.AUTO_TRACK_REF],
                    cwd=repo, timeout=5.0,
                )
                if last.returncode == 0:
                    target = f"session_id: {pytest_session_id}"
                    if any(line.strip() == target
                           for line in last.stdout.splitlines()):
                        return None

            # Run each installed adapter's ingest pass.
            since = _last_snapshot_time(repo)
            installed = installed_adapters(repo)
            adapter_results: List[IngestResult] = []
            adapter_paths: List[str] = []
            for adapter in installed:
                try:
                    result = adapter.ingest(repo, since=since)
                    adapter_results.append(result)
                    adapter_paths.extend(adapter.stage_paths())
                    for err in result.errors:
                        _log(repo, f"orchestrator: adapter={adapter.name} err={err}")
                except Exception as e:
                    _log(repo, f"orchestrator: adapter={adapter.name} crashed: {e}")
                    # Per spec §12: one bad adapter cannot block snapshots --
                    # but the crash is recorded as an IngestResult so it
                    # surfaces in the capture-health manifest instead of
                    # looking identical to "this tool was never used".
                    adapter_results.append(
                        IngestResult.from_exception(adapter.name, e))
                    continue

            # Redaction (uvd.5). Runs AFTER every adapter has written its
            # raw artifacts and BEFORE the health manifest and any staging,
            # so nothing unredacted can enter a snapshot. Raw artifacts stay
            # on disk (gitignored + unstaged); the committed derivative is
            # `.ai-traces/<adapter>/redacted/`. Never raises.
            redaction_ok = True
            try:
                redaction_report = redaction.redact_traces(repo)
                redaction_ok = not bool(redaction_report.errors)
                for err in redaction_report.errors:
                    _log(repo, f"orchestrator: redaction err={err}")
            except Exception as e:
                redaction_ok = False
                _log(repo, f"orchestrator: redaction crashed: {e}")

            # Capture-health manifest. Written BEFORE preview_tree so it is
            # part of the snapshot tree. Its content is deliberately stable
            # run-to-run (see capture_health) so tree-SHA dedupe survives.
            health_written = _write_health_manifest(repo, installed, adapter_results, redaction_ok=redaction_ok)
            if not health_written:
                redaction_ok = False
                _log(repo, "orchestrator: health manifest unavailable")

            head_ref, head_sha = git_ops.current_head_info(repo)
            git_state = git_ops.detect_git_state(repo)

            base_paths = ["src", "tests", "AGENTS.md", "AI_POLICY.md",
                          capture_health.HEALTH_FILE, "tools/CAPTURE_RELEASE.json"]
            all_paths = base_paths + sorted(set(adapter_paths))
            existing = [p for p in all_paths if (repo / p).exists()]

            # Collect force_paths + unstage_after across adapters. We probe
            # via getattr so adapters predating Task 2.5.10 (no override)
            # cleanly inherit empty defaults. Collected HERE, before
            # preview_tree, because the preview must stage exactly what the
            # commit will stage -- a gitignored force-path fed to a plain
            # `git add -A` makes git exit 1, which would return a None
            # preview and silently disable tree-SHA dedupe.
            force_set: set = set()
            unstage_set: set = set()
            if health_written:
                force_set.add(capture_health.HEALTH_FILE)
            for adapter in installed:
                try:
                    fp = getattr(adapter, "force_paths", lambda: [])()
                    force_set.update(fp or [])
                except Exception:
                    pass
                try:
                    ua = getattr(adapter, "unstage_after", lambda: [])()
                    unstage_set.update(ua or [])
                except Exception:
                    pass

            # Raw trace bytes are staging only (uvd.5): the trace dirs are
            # still FORCE staged (Attack K -- a student must not be able to
            # hide traces by gitignoring them), but the unredacted raw/
            # subdirs are dropped from the index right afterwards, leaving
            # the redacted derivative as the committed evidence. Derived
            # from disk so hook-only tools (Cursor) are covered too.
            try:
                unstage_set.update(redaction.raw_unstage_paths(repo))
            except Exception as e:
                redaction_ok = False
                _log(repo, f"orchestrator: raw unstage list failed: {e}")

            if not redaction_ok:
                # A failed sanitizer may have left raw payloads in normalized
                # files. Keep the code state and an explicit health record,
                # but stage no conversation tier from this attempt.
                def safe_path(path):
                    return not (path == ".ai-traces" or path.startswith(".ai-traces/")
                                or path.startswith(".codex-transcripts")
                                or path.startswith(".claude-transcripts"))
                existing = [path for path in existing if safe_path(path)]
                force_set = {path for path in force_set if safe_path(path)}
                health_written = _write_health_manifest(repo, installed, adapter_results, redaction_ok=False)
                if health_written:
                    existing.append(capture_health.HEALTH_FILE)
                    force_set.add(capture_health.HEALTH_FILE)

            new_tree = git_ops.preview_tree(
                repo, existing,
                force_paths=sorted(force_set),
                unstage_after=sorted(unstage_set),
            )

            # Tree-SHA dedupe (skip for manual; skip when adapter session is
            # new; skip when force=True so run_tests.py always logs a result
            # even if the tracked tree is unchanged from the prior snapshot).
            if (trigger != "manual" and not force and adapter_metadata is None
                    and new_tree is not None):
                prev_tip = git_ops.read_auto_track_tip(repo)
                if prev_tip:
                    prev_tree_sha = git_ops.run_git(
                        ["rev-parse", f"{prev_tip}^{{tree}}"],
                        cwd=repo, timeout=5.0,
                    ).stdout.strip()
                    if prev_tree_sha == new_tree:
                        return None

            prev_tip = git_ops.read_auto_track_tip(repo)
            prev_tree_sha = (
                git_ops.run_git(
                    ["rev-parse", f"{prev_tip}^{{tree}}"],
                    cwd=repo, timeout=5.0,
                ).stdout.strip()
                if prev_tip else git_ops.EMPTY_TREE_SHA
            )
            stats = git_ops.diff_stats_trees(
                repo, prev_tree_sha, new_tree or git_ops.EMPTY_TREE_SHA)

            agent_name = (
                adapter_metadata.adapter_name if adapter_metadata else "none")
            agent_session_id = (
                adapter_metadata.agent_session_id if adapter_metadata else "none")
            session_id_for_msg = (
                pytest_session_id or f"{trigger}-{int(time.time() * 1000)}")

            # Aggregate per-adapter source_hashes into the
            # {adapter_name: {repo_relative_path: sha256_hex}} shape that
            # format_commit_message renders. Order + 50-entry cap live in
            # metadata.py — the orchestrator only assembles the dict.
            artifact_hashes: dict = {}
            for result in adapter_results:
                if not result.source_hashes:
                    continue
                files = {
                    entry["path"]: entry["sha256"]
                    for entry in result.source_hashes
                    if isinstance(entry, dict)
                    and "path" in entry and "sha256" in entry
                }
                if files:
                    artifact_hashes[result.adapter_name] = files

            msg = metadata.format_commit_message(
                session_id=session_id_for_msg,
                status=status,
                result=test_result or metadata.TestResult(),
                diff_added=stats.added, diff_removed=stats.removed,
                files_changed=stats.files,
                hostname_hash=metadata.hostname_hash(str(repo)),
                current_head_ref=head_ref,
                current_head_sha=head_sha if head_sha else "unborn",
                git_state=git_state,
                trigger=trigger,
                agent_name=agent_name,
                agent_session_id=agent_session_id,
                artifact_hashes=artifact_hashes or None,
                capture_counts=_capture_counts(adapter_results) or None,
            )
            if manual_reason:
                msg = msg.rstrip("\n") + f"\nmanual_reason: {manual_reason}\n"
            msg = msg.rstrip("\n") + f"\nredaction_status: {'passed' if redaction_ok else 'failed'}\n"

            # force_set / unstage_set were collected above, before
            # preview_tree, so the previewed tree and the committed tree
            # stage exactly the same paths.
            new_sha = git_ops.snapshot_to_auto_track(
                repo, msg, existing, head_ref, head_sha,
                force_paths=sorted(force_set),
                unstage_after=sorted(unstage_set),
            )
            if new_sha is None:
                _write_health_manifest(repo, installed, adapter_results,
                                       redaction_ok=redaction_ok, snapshot_ok=False)
                _log(repo, f"orchestrator: snapshot failed trigger={trigger}")
                return None

            log_path = repo / LOG_FILENAME
            git_ops.push_auto_track_background(repo, log_path)
            hint = auth.diagnose_push_log(log_path)
            if hint:
                print(hint, file=sys.stderr)
            return new_sha
    except Exception as e:
        _log(repo, f"orchestrator: top-level exception {type(e).__name__}: {e}")
        return None


# --- helpers --------------------------------------------------------------

def _write_health_manifest(
    repo: Path,
    installed: List,
    adapter_results: List[IngestResult],
    *,
    redaction_ok: bool = True,
    snapshot_ok: bool = True,
) -> bool:
    """Refresh `.ai-traces/capture-health.json`. Never raises."""
    try:
        errors_by_adapter: dict = {}
        for result in adapter_results:
            if result.errors:
                errors_by_adapter.setdefault(result.adapter_name, []).extend(
                    str(e) for e in result.errors)
        health = capture_health.build_health(
            repo,
            present_names=[a.name for a in installed],
            errors_by_adapter=errors_by_adapter,
            workspace_root_mismatch=_workspace_root_mismatch(adapter_results),
        )
        health["redaction_ok"] = redaction_ok
        health["snapshot_ok"] = snapshot_ok
        try:
            package = json.loads((repo / "tools/CAPTURE_RELEASE.json").read_text(encoding="utf-8"))
            digest = package.get("package_sha256")
            health["capture_package"] = digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else "unknown"
        except (OSError, ValueError, AttributeError):
            health["capture_package"] = "unknown"
        return capture_health.write_health(repo, health) is not None
    except Exception as e:
        _log(repo, f"orchestrator: health manifest failed: {e}")
        return False


def _capture_counts(adapter_results: List[IngestResult]) -> dict:
    """Per-adapter MATCHED-ONLY counts for the commit-message trailers.

    `matched` counts artifacts this repo's ingest matched (copied +
    already-present/skipped); `copied` counts the new ones. Store-wide scan
    totals are never computed here — they would leak the student's AI use
    outside this course.
    """
    counts: dict = {}
    for result in adapter_results:
        copied = len(result.rollouts_copied)
        matched = copied + len(result.rollouts_skipped)
        ancestor = len(getattr(result, "rollouts_ancestor_cwd", ()) or ())
        # A mismatch-only run has matched == 0 by definition (that IS the
        # bug: nothing matched because the cwd was above the repo), so the
        # `matched` short-circuit must not swallow it.
        if not matched and not ancestor:
            continue
        entry = counts.setdefault(
            result.adapter_name,
            {"matched": 0, "copied": 0, "ancestor_cwd": 0},
        )
        entry["matched"] += matched
        entry["copied"] += copied
        entry["ancestor_cwd"] = entry.get("ancestor_cwd", 0) + ancestor
    return counts


def _workspace_root_mismatch(adapter_results: List[IngestResult]) -> bool:
    """True if any adapter saw a session running from above the repo."""
    return any(
        getattr(r, "rollouts_ancestor_cwd", None) for r in adapter_results
    )


def _capture_enabled(repo: Path) -> bool:
    """Same gate as capture.py — read project-template-config.json + env.

    Lifted verbatim from capture.py to avoid an import cycle. If the gate
    logic ever changes, change it in both places.
    """
    if os.environ.get("CAPTURE_DISABLED") == "1":
        return False
    config_path = repo / "project-template-config.json"
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("capture_enabled"))


_TIMESTAMP_RE = re.compile(r"^timestamp:\s*(\S+)\s*$", re.MULTILINE)


def _last_snapshot_time(repo: Path) -> Optional[float]:
    """Read the latest auto-track snapshot's commit body, parse the
    `timestamp:` field, return as Unix epoch float; on any failure
    return None.

    Adapters use this as `since` — a None value means "ingest everything
    not already captured" (matching codex_ingest's existing behavior).
    """
    last = git_ops.run_git(
        ["log", "-1", "--format=%B", git_ops.AUTO_TRACK_REF],
        cwd=repo, timeout=5.0,
    )
    if last.returncode != 0:
        return None
    m = _TIMESTAMP_RE.search(last.stdout)
    if not m:
        return None
    raw = m.group(1)
    try:
        # format_commit_message emits ISO-8601 ending in Z (UTC).
        if raw.endswith("Z"):
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return datetime.fromisoformat(raw).timestamp()
    except (ValueError, OSError):
        return None


def _log(repo: Path, msg: str) -> None:
    try:
        with (repo / LOG_FILENAME).open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


@contextlib.contextmanager
def _orchestrator_lock(repo: Path) -> Iterator[bool]:
    """Repo-scoped advisory lock at `<repo>/.git/auto-track.lock`.

    Yields True if the lock was acquired within LOCK_TIMEOUT_SECONDS,
    False otherwise. Cross-platform:
      - POSIX: `fcntl.flock(LOCK_EX | LOCK_NB)` with retry loop
      - Windows: `msvcrt.locking(LK_NBLCK, 1)` with retry loop

    Always closes the lock file in finally; releases the lock first
    when it was acquired. Never raises — the orchestrator is gated
    on the boolean yield.
    """
    lock_path = repo / ".git" / LOCK_FILENAME
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Can't even create .git/ — yield False so orchestrator falls through.
        yield False
        return

    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        f = open(lock_path, "a+b")
    except OSError:
        yield False
        return

    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            while time.monotonic() < deadline:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield locked
            finally:
                if locked:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
        else:
            import fcntl
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield locked
            finally:
                if locked:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
    finally:
        try:
            f.close()
        except OSError:
            pass
