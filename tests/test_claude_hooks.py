"""Tests for the shipped Claude Code hooks (hooks v2, Claude adapter).

INFRASTRUCTURE tests -- deliberately unmarked, so the student-facing runner
(run_tests.py, which collects only @pytest.mark.bundle tests) never grades
them.

Packaging note, which drives most of the odd-looking paths below: the hook
tree lives at ``.claude-student/`` in the INSTRUCTOR repo and is installed
as ``.claude/`` in student repos. ``.claude/`` in this repo is the
instructor's own live Claude Code directory (agents, skills, settings that
reference instructor-only tooling) and must never be the source of a
shipped file. Every test therefore reads the SHIPPED tree and copies it
into a throwaway repo AS ``.claude/`` -- which also proves the tree is
location-independent, per the epic's standing constraint that the same
hooks work in a non-Python assignment repo.

This file itself ships to students, whose repo has ``.claude/`` and no
``.claude-student/``. So the tree is located with
``verify_integrity.source_path()`` -- the same mechanism the integrity check
uses -- which picks the right directory from ``distribution_mode``. The few
checks about the instructor repo's own packaging skip in student mode.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import verify_integrity

REPO_ROOT = Path(__file__).resolve().parents[1]
# .claude-student/ in an instructor repo, .claude/ in a student repo.
SOURCE_DIR = verify_integrity.source_path(REPO_ROOT, ".claude/settings.json").parent
HOOK_DIR = SOURCE_DIR / "hooks"
SETTINGS = SOURCE_DIR / "settings.json"

# Checks about how the INSTRUCTOR repo packages the tree (create-assignment.sh,
# the .claude-student/ source dir) have nothing to inspect in a student repo.
instructor_only = pytest.mark.skipif(
    verify_integrity.distribution_mode(REPO_ROOT) == "student",
    reason="instructor-repo packaging check; not applicable in a student repo",
)


def hook_names() -> list:
    return sorted(p.stem for p in HOOK_DIR.glob("*.py") if not p.stem.startswith("_"))


@pytest.fixture()
def hook_repo(tmp_path: Path) -> Path:
    """A throwaway project root carrying the hook tree installed as .claude/."""
    repo = tmp_path / "assignment"
    repo.mkdir(parents=True)
    if SOURCE_DIR.is_dir():
        shutil.copytree(SOURCE_DIR, repo / ".claude")
    else:  # not shipped yet -- let the assertions report that, not the fixture
        (repo / ".claude" / "hooks").mkdir(parents=True)
    return repo


def run_hook(repo: Path, hook_name: str, payload: dict, cwd: Path | None = None):
    """Invoke one hook exactly as Claude Code would; return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(repo / ".claude" / "hooks" / "_run.py"), hook_name],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd or repo),
        timeout=60,
    )


def make_transcript(tmp_path: Path, name: str = "1a2b3c4d-0000-4000-8000-000000000001.jsonl") -> Path:
    """A Claude Code session transcript at ~/.claude/projects/<slug>/<uuid>.jsonl."""
    path = tmp_path / "claude-home" / "projects" / "-Users-s-assignment" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "user", "cwd": "/x", "sessionId": "s"}) + "\n",
        encoding="utf-8",
    )
    return path


# --- transcript copy ----------------------------------------------------


def test_session_end_hook_copies_transcript(hook_repo: Path, tmp_path: Path):
    """The SessionEnd payload carries transcript_path; the hook copies it in-repo.

    This is the whole point of push-based capture: no rescan of
    ~/.claude/projects/ by cwd, and no 50-line probe of every session on the
    machine (claude_ingest's scavenger path, which stays only as backfill).

    The destination must be the one claude_ingest/claude_normalize already
    use, or the copy would never be normalized.
    """
    transcript = make_transcript(tmp_path)
    result = run_hook(hook_repo, "session_end", {
        "hook_event_name": "SessionEnd",
        "session_id": "sess-end",
        "reason": "exit",
        "cwd": str(hook_repo),
        "transcript_path": str(transcript),
    })

    assert result.returncode == 0, result.stderr
    dest = hook_repo / ".ai-traces" / "claude" / "raw" / "sessions" / transcript.name
    assert dest.is_file(), "SessionEnd hook did not copy the transcript into .ai-traces/claude/raw/sessions/"
    assert dest.read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8")


def test_stop_hook_copies_transcript_and_survives_a_missing_snapshot_seam(
    hook_repo: Path, tmp_path: Path
):
    """Stop is the per-turn copy; a non-Python assignment repo has no seam."""
    assert not (hook_repo / "tests").exists()
    transcript = make_transcript(tmp_path, "1a2b3c4d-0000-4000-8000-000000000002.jsonl")
    result = run_hook(hook_repo, "stop", {
        "hook_event_name": "Stop",
        "session_id": "stop-1",
        "transcript_path": str(transcript),
        "stop_hook_active": False,
    })

    assert result.returncode == 0, result.stderr
    assert (hook_repo / ".ai-traces" / "claude" / "raw" / "sessions" / transcript.name).is_file()


def test_transcript_recopied_when_session_grows(hook_repo: Path, tmp_path: Path):
    """Claude appends to one session JSONL all session long; a one-shot copy freezes it."""
    transcript = make_transcript(tmp_path)
    payload = {"session_id": "grow", "transcript_path": str(transcript)}
    run_hook(hook_repo, "stop", payload)

    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {}}) + "\n")
    run_hook(hook_repo, "stop", payload)

    dest = hook_repo / ".ai-traces" / "claude" / "raw" / "sessions" / transcript.name
    assert dest.read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8")


# --- heartbeat: the untrusted / untrusted-workspace signal --------------


def test_session_start_writes_heartbeat(hook_repo: Path):
    result = run_hook(hook_repo, "session_start", {
        "hook_event_name": "SessionStart", "session_id": "abc-123", "source": "startup",
    })

    assert result.returncode == 0, result.stderr
    beats = sorted((hook_repo / ".ai-traces" / "claude" / "heartbeat").glob("*"))
    assert beats, "no heartbeat written; declined workspace trust would be indistinguishable from never-used"
    body = json.loads(beats[0].read_text(encoding="utf-8"))
    assert body["adapter_name"] == "claude"
    assert body["session_id"] == "abc-123"


def test_heartbeat_content_is_stable_across_refires(hook_repo: Path):
    """Snapshots dedupe by tree SHA -- a varying heartbeat would kill dedupe."""
    payload = {"session_id": "abc-123", "hook_event_name": "SessionStart"}
    run_hook(hook_repo, "session_start", payload)
    beat = hook_repo / ".ai-traces" / "claude" / "heartbeat" / "abc-123.json"
    first = beat.read_bytes()

    run_hook(hook_repo, "user_prompt_submit", dict(payload, prompt="hi"))
    run_hook(hook_repo, "stop", payload)

    assert beat.read_bytes() == first
    assert set(json.loads(first)) == {"adapter_name", "schema_version", "session_id"}


def test_every_hook_writes_a_heartbeat(hook_repo: Path):
    beat_dir = hook_repo / ".ai-traces" / "claude" / "heartbeat"
    names = hook_names()
    assert names, "no hook scripts shipped"
    for name in names:
        if beat_dir.is_dir():
            for stale in beat_dir.glob("*"):
                stale.unlink()
        run_hook(hook_repo, name, {"session_id": f"s-{name}"})
        assert list(beat_dir.glob("*")), f"{name} hook wrote no heartbeat"


def test_heartbeat_is_readable_by_capture_health(hook_repo: Path, tmp_path: Path):
    """The manifest's conjunction (evidence AND heartbeat) must see both halves."""
    from tests._capture import capture_health

    transcript = make_transcript(tmp_path)
    run_hook(hook_repo, "stop", {"session_id": "joint", "transcript_path": str(transcript)})

    assert capture_health.has_heartbeat(hook_repo, "claude") is True
    assert capture_health.has_evidence(hook_repo, "claude") is True
    health = {"adapters": {"claude": {
        "heartbeat": capture_health.has_heartbeat(hook_repo, "claude"),
        "evidence": capture_health.has_evidence(hook_repo, "claude"),
    }}}
    assert capture_health.hooks_look_unauthorized(health, "claude") is False


# --- per-edit provenance ------------------------------------------------


def test_post_tool_use_records_edit_provenance_without_content(hook_repo: Path):
    run_hook(hook_repo, "post_tool_use", {
        "hook_event_name": "PostToolUse",
        "session_id": "edits",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "src/server.py",
            "old_string": "x",
            "new_string": "SECRET-TOKEN-VALUE",
        },
        "tool_response": {"success": True},
    })

    edits = hook_repo / ".ai-traces" / "claude" / "raw" / "hooks" / "edits-edits.jsonl"
    rows = [json.loads(line) for line in edits.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "file_edit"
    assert row["file"] == "src/server.py"
    assert row["extent_chars"] == len("SECRET-TOKEN-VALUE")
    assert len(row["content_sha256"]) == 64
    # Proportionality: the edit's text lives in the transcript, not in this record.
    assert "SECRET-TOKEN-VALUE" not in json.dumps(row)


def test_post_tool_use_measures_multiedit_extent(hook_repo: Path):
    """MultiEdit carries an edits[] list; a naive reader records extent 0."""
    run_hook(hook_repo, "post_tool_use", {
        "session_id": "multi",
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "src/a.py",
            "edits": [
                {"old_string": "a", "new_string": "AAA"},
                {"old_string": "b", "new_string": "BB"},
            ],
        },
    })

    edits = hook_repo / ".ai-traces" / "claude" / "raw" / "hooks" / "edits-multi.jsonl"
    row = json.loads(edits.read_text(encoding="utf-8").splitlines()[0])
    assert row["extent_chars"] == 5
    assert "AAA" not in json.dumps(row)


def test_post_tool_use_ignores_non_edit_tools(hook_repo: Path):
    run_hook(hook_repo, "post_tool_use", {
        "session_id": "reads", "tool_name": "Read",
        "tool_input": {"file_path": "README.md"},
    })
    assert not list((hook_repo / ".ai-traces" / "claude" / "raw" / "hooks").glob("edits-*.jsonl"))


def test_hook_event_payload_is_logged_verbatim(hook_repo: Path):
    run_hook(hook_repo, "user_prompt_submit", {
        "session_id": "log", "hook_event_name": "UserPromptSubmit",
        "prompt": "how do sockets work",
    })
    log = hook_repo / ".ai-traces" / "claude" / "raw" / "hooks" / "hooks-log.jsonl"
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["hook_event_name"] == "UserPromptSubmit"
    assert row["payload"]["prompt"] == "how do sockets work"


# --- fail-open behavior -------------------------------------------------


@pytest.mark.parametrize("stdin_text", ["", "not json at all", "[]", "null"])
def test_hooks_exit_zero_on_garbage_stdin(hook_repo: Path, stdin_text: str):
    for name in hook_names():
        result = subprocess.run(
            [sys.executable, str(hook_repo / ".claude" / "hooks" / "_run.py"), name],
            input=stdin_text, capture_output=True, text=True, cwd=str(hook_repo), timeout=60,
        )
        assert result.returncode == 0, f"{name}: rc={result.returncode} {result.stderr}"


def test_hooks_write_nothing_to_stdout(hook_repo: Path, tmp_path: Path):
    """Hooks are observational: stdout is a CONTROL channel in Claude Code.

    A JSON object on stdout can block a tool call or inject context. These
    hooks audit, they do not police.
    """
    transcript = make_transcript(tmp_path, "1a2b3c4d-0000-4000-8000-000000000003.jsonl")
    for name in hook_names():
        result = run_hook(hook_repo, name, {
            "session_id": "quiet", "tool_name": "Edit",
            "tool_input": {"file_path": "src/a.py", "new_string": "x"},
            "transcript_path": str(transcript),
        })
        assert result.stdout == "", f"{name} wrote to stdout: {result.stdout!r}"


def test_unknown_hook_name_is_a_no_op(hook_repo: Path):
    assert run_hook(hook_repo, "does_not_exist", {"session_id": "x"}).returncode == 0


def test_hooks_work_when_cwd_is_a_subdirectory(hook_repo: Path, tmp_path: Path):
    """Claude Code may run a hook with cwd below the project root."""
    sub = hook_repo / "src" / "deep"
    sub.mkdir(parents=True)
    transcript = make_transcript(tmp_path, "1a2b3c4d-0000-4000-8000-000000000004.jsonl")

    result = run_hook(hook_repo, "stop", {
        "session_id": "subdir", "cwd": str(sub), "transcript_path": str(transcript),
    }, cwd=sub)

    assert result.returncode == 0, result.stderr
    assert (hook_repo / ".ai-traces" / "claude" / "raw" / "sessions" / transcript.name).is_file()


def test_hooks_import_nothing_from_the_python_capture_layer():
    """Standing constraint: the hook tree must run in a non-Python repo too."""
    import re

    bad = re.compile(r"^\s*(import|from)\s+tests\b", re.MULTILINE)
    for path in HOOK_DIR.glob("*.py"):
        assert not bad.search(path.read_text(encoding="utf-8")), f"{path.name} imports the capture layer"


# --- settings.json configuration ----------------------------------------

KNOWN_CLAUDE_EVENTS = {
    "PreToolUse", "PostToolUse", "Notification", "UserPromptSubmit",
    "Stop", "SubagentStop", "PreCompact", "SessionStart", "SessionEnd",
}


def load_settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def iter_hook_entries():
    for event, groups in load_settings()["hooks"].items():
        for group in groups:
            for entry in group["hooks"]:
                yield event, group, entry


def test_settings_registers_only_known_events_and_the_required_ones():
    events = set(load_settings()["hooks"])
    unknown = events - KNOWN_CLAUDE_EVENTS
    assert not unknown, f"unknown event names risk the whole settings file failing to load: {unknown}"
    # These carry the acceptance behaviors: heartbeat, per-edit provenance,
    # transcript copy (Stop per turn, SessionEnd as the backstop).
    assert {"SessionStart", "PostToolUse", "Stop", "SessionEnd"} <= events


def test_settings_holds_only_hooks():
    """Project settings MERGE below user scope; anything else we add here
    silently overrides the student's own Claude configuration."""
    assert set(load_settings()) == {"hooks"}


def test_every_registered_hook_points_at_a_shipped_script():
    names = hook_names()
    for event, _group, entry in iter_hook_entries():
        assert entry["type"] == "command"
        command = entry["command"]
        assert any(f"_run.py\" {name}" in command for name in names), f"{event}: {command}"


def test_every_hook_sets_an_explicit_timeout():
    for event, _group, entry in iter_hook_entries():
        assert isinstance(entry.get("timeout"), int), f"{event} has no explicit timeout"
        assert entry["timeout"] >= 5, f"{event} timeout is too tight for a file copy"


def test_hook_commands_contain_no_machine_specific_paths():
    """settings.json is hash-pinned and shipped verbatim; it cannot be templated."""
    text = SETTINGS.read_text(encoding="utf-8")
    assert "/Users/" not in text and "C:\\" not in text
    # Resolved at run time instead, from the env var Claude Code sets (with
    # a git/pwd fallback for the surfaces that do not set it).
    assert "${CLAUDE_PROJECT_DIR:-" in text


def test_hook_commands_force_exit_zero():
    for event, _group, entry in iter_hook_entries():
        assert "exit 0" in entry["command"], event


def test_settings_never_registers_the_instructor_hook_directory():
    """.claude/hooks/ in THIS repo is the instructor's live directory."""
    text = SETTINGS.read_text(encoding="utf-8")
    assert ".claude-student" not in text, "shipped settings must reference the INSTALLED path"


# --- packaging / integrity ---------------------------------------------


@instructor_only
def test_source_tree_is_not_the_instructor_claude_dir():
    assert SOURCE_DIR.is_dir(), "expected the shipped tree at .claude-student/"
    assert SETTINGS.is_file()
    assert HOOK_DIR.is_dir()


def test_shipped_tree_carries_no_injected_tool_integration_blocks():
    """Instructor-side developer tooling must never reach a student repo.

    Deliberately GENERIC (it names no tool, since this file itself ships):
    the tools in question announce themselves by appending a marker block to
    config files. The exhaustive, name-based scan lives in
    create-assignment.sh, which is instructor-only for exactly that reason.
    """
    import re

    marker = re.compile(r"<!--\s*BEGIN\s+\w+\s+INTEGRATION\s*-->", re.IGNORECASE)
    for path in SOURCE_DIR.rglob("*"):
        if path.is_file():
            assert not marker.search(path.read_text(encoding="utf-8", errors="replace")), path


def test_hook_files_are_integrity_tracked_under_their_student_paths():
    """Un-pinned hooks would let a student silently disable their own capture.

    The baseline is keyed by the SHIPPED path, so the same
    INTEGRITY_HASHES.txt verifies in a student repo (where the tree lives at
    .claude/) and here (where the source lives at .claude-student/).
    """
    tracked = set(verify_integrity.TRACKED)
    expected = {".claude/settings.json"} | {
        f".claude/hooks/{p.name}" for p in HOOK_DIR.glob("*.py")
    }
    assert expected <= tracked, f"not integrity-tracked: {sorted(expected - tracked)}"


def test_integrity_resolves_divergent_paths_by_distribution_mode(tmp_path: Path):
    """The mechanism: one baseline key, two source locations."""
    instructor = tmp_path / "instructor"
    (instructor / ".claude-student" / "hooks").mkdir(parents=True)
    # The generator script is part of what makes a repo an instructor repo
    # (see verify_integrity.INSTRUCTOR_LAYOUT); the config alone is not enough.
    (instructor / "create-assignment.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (instructor / "project-template-config.json").write_text(
        json.dumps({"distribution_mode": "instructor"}), encoding="utf-8")
    student = tmp_path / "student"
    (student / ".claude" / "hooks").mkdir(parents=True)
    (student / "project-template-config.json").write_text(
        json.dumps({"distribution_mode": "student"}), encoding="utf-8")

    assert verify_integrity.distribution_mode(instructor) == "instructor"
    assert verify_integrity.distribution_mode(student) == "student"
    assert verify_integrity.source_path(instructor, ".claude/settings.json") == \
        instructor / ".claude-student" / "settings.json"
    assert verify_integrity.source_path(student, ".claude/settings.json") == \
        student / ".claude" / "settings.json"
    # Non-divergent entries are unaffected in either mode.
    assert verify_integrity.source_path(instructor, "AGENTS.md") == instructor / "AGENTS.md"
    assert verify_integrity.source_path(student, "AGENTS.md") == student / "AGENTS.md"


@instructor_only
def test_integrity_never_hashes_the_instructor_live_claude_dir():
    """Pinning .claude/settings.json as a literal path here would hash the
    instructor's own Claude settings -- a hash no student repo can match."""
    for rel in verify_integrity.TRACKED:
        resolved = verify_integrity.source_path(REPO_ROOT, rel)
        assert REPO_ROOT / ".claude" not in resolved.parents, f"{rel} resolves into the instructor .claude/"


def test_baseline_matches_the_shipped_tree():
    """Regenerating with --update is a documented step; forgetting it fails
    every student's server-side integrity check."""
    expected = verify_integrity.load_expected()
    for rel in sorted(f".claude/hooks/{p.name}" for p in HOOK_DIR.glob("*.py")):
        src = verify_integrity.source_path(REPO_ROOT, rel)
        assert expected.get(rel) == verify_integrity.sha256_of(src), f"stale baseline for {rel}"


@instructor_only
def test_create_assignment_installs_the_tree_as_dot_claude():
    text = (REPO_ROOT / "create-assignment.sh").read_text(encoding="utf-8")
    assert 'cp -r .claude-student "$TARGET_DIR/.claude"' in text
    # The old removed-claim is false once the student .claude/ ships.
    assert ".claude/ directory (AI agent configurations)" not in text
