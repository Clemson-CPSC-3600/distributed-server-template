"""Audit capture-layer integrity by comparing file hashes against INTEGRITY_HASHES.txt.

There are two tiers of tracking:

- TRACKED files have their content hashes pinned. Any modification, even
  whitespace, is reported as MODIFIED. These are course-infrastructure
  files students should never edit.
- SOFT_TRACKED files are checked only for presence and non-emptiness; the
  content is expected to be student-edited. Used for files like
  `.ai-traces/external-attestation.txt` where deletion or wholesale
  emptying is the signal we care about, not content changes.

Path divergence between instructor and student repos
----------------------------------------------------
A few pinned files live at DIFFERENT paths in the two repos. The first case
is the Claude Code hook tree: the instructor repo holds it at
``.claude-student/`` (because ``.claude/`` there is the instructor's own
live Claude directory, full of instructor-only tooling), and
``create-assignment.sh`` installs it as ``.claude/`` in the student repo.

A single literal path list cannot express that: pinning the source path
reports MISSING in every student repo, and pinning the destination path
hashes the *instructor's* live settings -- a hash no student can match.

The mechanism: every entry in ``TRACKED`` is the **shipped (student) path**,
which is the key written to INTEGRITY_HASHES.txt, and ``INSTRUCTOR_SOURCE``
maps that key to where the same bytes live in an instructor repo.
``source_path()`` resolves the key to a real file according to
``distribution_mode()``. The baseline is therefore identical in both repos
and the student-side check (run server-side by
.github/workflows/integrity.yml) needs no rewriting at generation time.

Reusable: any future file whose instructor and student locations diverge --
or which is generated from a differently-named instructor source -- adds one
entry to ``INSTRUCTOR_SOURCE`` and nothing else.

Trusting the mode
-----------------
project-template-config.json is NOT pinned, so a student can edit it. If
the mode came from that file alone, a student could claim "instructor", park
an untouched copy of the hooks at ``.claude-student/``, and edit the real
``.claude/`` hooks: the check would hash the untouched copy. Two defenses:

- ``distribution_mode()`` only answers "instructor" when the repo is laid
  out like one -- ``.claude-student/`` AND ``create-assignment.sh`` both
  exist. The generator script never ships to students.
- ``--mode student`` skips the question entirely. The shipped student
  workflow (.github/workflows/integrity.yml) always passes it, so even a
  student who fakes the whole instructor layout is checked against
  ``.claude/``. The flag accepts only "student": it can tighten the check,
  never loosen it.

Usage:
    python tools/verify_integrity.py            # check current file hashes
    python tools/verify_integrity.py --mode student
                                                # always check the shipped
                                                # paths (what CI runs)
    python tools/verify_integrity.py --update   # regenerate INTEGRITY_HASHES.txt
                                                # (instructor use only, after
                                                # intentional capture changes)

Exit code 0 on match, 1 on mismatch / soft-track violation, 2 on missing
hard-tracked file or missing INTEGRITY_HASHES.txt.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HASH_FILE = ROOT / "tools" / "INTEGRITY_HASHES.txt"
CONFIG_FILE = "project-template-config.json"


def configuration_errors(root: Path, mode: str) -> list[str]:
    """Validate mutable configuration semantically instead of hash-pinning it."""
    try:
        config = json.loads((root / CONFIG_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ["capture_configuration_missing"]
    except (OSError, ValueError):
        return ["capture_configuration_invalid"]
    if not isinstance(config, dict):
        return ["capture_configuration_invalid"]
    if type(config.get("capture_enabled")) is not bool:
        return ["capture_enabled_must_be_boolean"]
    if mode == "student" and config["capture_enabled"] is not True:
        return ["student_capture_must_be_enabled"]
    return []

# Both must exist before a repo counts as an instructor repo. The generator
# script is the key one: create-assignment.sh never copies itself into the
# student repo, so its presence is a signal the config file cannot fake.
INSTRUCTOR_LAYOUT = (".claude-student", "create-assignment.sh")

# Files the Claude Code hook tree ships, relative to the tree's root. Kept as
# one list so the TRACKED entries and the instructor-source mapping below
# cannot drift apart.
CLAUDE_HOOK_TREE = [
    "settings.json",
    "hooks/_common.py",
    "hooks/_run.py",
    "hooks/post_tool_use.py",
    "hooks/pre_compact.py",
    "hooks/pre_tool_use.py",
    "hooks/session_end.py",
    "hooks/session_start.py",
    "hooks/stop.py",
    "hooks/user_prompt_submit.py",
]

# shipped (student) path -> path holding the same bytes in an instructor repo
INSTRUCTOR_SOURCE = {
    f".claude/{rel}": f".claude-student/{rel}" for rel in CLAUDE_HOOK_TREE
}

TRACKED = [
    "tests/_capture/doctor.py",
    "tests/_capture/report.py",
    "tests/_capture/upload.py",
    "tools/verify_integrity.py",
    "tools/capture_release.py",
    "AGENTS.md",
    "AI_POLICY.md",
    ".codex/config.toml",
    # Codex hooks (push-based capture). Pinned because an un-pinned hook
    # tree is a one-line edit away from silently disabling a student's own
    # capture. NOTE for instructors: Codex trust is keyed on a hash of each
    # hook definition, so editing hooks.json mid-semester un-trusts every
    # student machine and they must re-approve via `codex` -> `/hooks`.
    ".codex/hooks.json",
    ".codex/hooks/_common.py",
    ".codex/hooks/_run.py",
    ".codex/hooks/post_tool_use.py",
    ".codex/hooks/pre_compact.py",
    ".codex/hooks/pre_tool_use.py",
    ".codex/hooks/session_end.py",
    ".codex/hooks/session_start.py",
    ".codex/hooks/stop.py",
    ".codex/hooks/user_prompt_submit.py",
    # Claude Code hooks. These paths are the SHIPPED ones; in an instructor
    # repo the same bytes live under .claude-student/ (see INSTRUCTOR_SOURCE
    # and the module docstring). Never read .claude/ directly in instructor
    # mode -- that is the instructor's own live Claude directory.
    *sorted(f".claude/{rel}" for rel in CLAUDE_HOOK_TREE),
    # Cursor hooks. Unlike .claude/, `.cursor/` is not a live instructor
    # directory, so these need no INSTRUCTOR_SOURCE divergence: the
    # instructor path and the shipped path are the same. Pinning matters
    # more here than for the other two adapters -- Cursor's scavenger route
    # was excluded, so these hooks are its ONLY capture channel.
    ".cursor/hooks.json",
    ".cursor/hooks/_common.py",
    ".cursor/hooks/_run.py",
    ".cursor/hooks/after_file_edit.py",
    ".cursor/hooks/after_tab_file_edit.py",
    ".cursor/hooks/before_submit_prompt.py",
    ".cursor/hooks/session_end.py",
    ".cursor/hooks/session_start.py",
    ".cursor/hooks/stop.py",
    ".gitignore",
    "tests/conftest.py",
    "tests/_capture/__init__.py",
    "tests/_capture/__main__.py",
    "tests/_capture/ai_traces.py",
    "tests/_capture/agent_adapters/__init__.py",
    "tests/_capture/agent_adapters/aider.py",
    "tests/_capture/agent_adapters/base.py",
    "tests/_capture/agent_adapters/claude.py",
    "tests/_capture/agent_adapters/claude_normalize.py",
    "tests/_capture/agent_adapters/codex.py",
    "tests/_capture/agent_adapters/codex_normalize.py",
    "tests/_capture/agent_adapters/registry.py",
    "tests/_capture/agent_adapters/vscode_chat.py",
    "tests/_capture/audit.py",
    "tests/_capture/auth.py",
    "tests/_capture/capture.py",
    "tests/_capture/capture_health.py",
    "tests/_capture/claude_ingest.py",
    "tests/_capture/codex_ingest.py",
    "tests/_capture/vscode_chat_ingest.py",
    "tests/_capture/copy_dedup.py",
    "tests/_capture/course_floor.py",
    "tests/_capture/git_ops.py",
    "tests/_capture/metadata.py",
    "tests/_capture/orchestrator.py",
    "tests/_capture/platform_compat.py",
    "tests/_capture/post_commit_entry.py",
    # Redaction pass. Pinned for BOTH directions: editing it out leaks the
    # student's out-of-project content into a permanent ref, and editing it
    # up (withhold everything) is an evasion route that would look like
    # ordinary redaction.
    "tests/_capture/redaction.py",
    "tests/_capture/runtime_triggers.py",
    "tests/_capture/sitecustomize_payload.py",
    "tests/_capture/state.py",
    "tests/_capture/watchdog.py",
    ".githooks/post-commit",
]

# Files whose presence + non-emptiness is required, but whose content is
# student-edited and therefore NOT hash-pinned. Listed here primarily to
# detect students who delete the file or empty it to evade detection of
# non-codex AI use.
SOFT_TRACKED = [
    ".ai-traces/external-attestation.txt",
    # Written per-assignment by create-assignment.sh, so its content (the
    # course_start date) legitimately differs between assignments and cannot
    # be hash-pinned. Presence IS checked: deleting it silently disables the
    # capture time floor, so its absence must be visible.
    "assignment-config.json",
]


def distribution_mode(root: Path = ROOT) -> str:
    """"student" or "instructor" for the repo rooted at ``root``.

    The file layout decides first: without BOTH entries in INSTRUCTOR_LAYOUT
    this is a student repo, whatever project-template-config.json claims.
    That config is not hash-pinned, so trusting it alone would let a student
    redirect the check to a decoy copy of the hooks (see module docstring).

    Only in an instructor-shaped repo is the config consulted, so an
    instructor can still flip a working copy to "student" to test the
    student view. A missing or unreadable config there means "instructor".
    """
    looks_like_instructor_repo = all(
        (root / name).exists() for name in INSTRUCTOR_LAYOUT
    )
    if not looks_like_instructor_repo:
        return "student"
    try:
        config = json.loads((root / CONFIG_FILE).read_text(encoding="utf-8"))
        mode = config.get("distribution_mode")
        if mode in ("student", "instructor"):
            return mode
    except (OSError, ValueError, AttributeError):
        pass
    return "instructor"


def source_path(root: Path, rel: str, mode: str = None) -> Path:
    """Where the bytes pinned under baseline key ``rel`` actually live."""
    if mode is None:
        mode = distribution_mode(root)
    if mode == "instructor":
        return root / INSTRUCTOR_SOURCE.get(rel, rel)
    return root / rel


def sha256_of(path: Path) -> str:
    # Normalize CRLF -> LF so hashes are stable across Windows working-tree
    # autocrlf expansion and Linux CI checkouts of the same git blob.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_expected() -> dict:
    if not HASH_FILE.exists():
        return {}
    out = {}
    for line in HASH_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h, _, path = line.partition("  ")
        out[path] = h
    return out


def write_expected(mapping: dict) -> None:
    HASH_FILE.write_text(
        "# Known-good SHA-256 hashes of capture-layer files.\n"
        "# Regenerated with: python tools/verify_integrity.py --update\n"
        + "".join(f"{h}  {path}\n" for path, h in sorted(mapping.items())),
        encoding="utf-8",
        newline="\n",
    )


def forced_mode(argv: list):
    """The mode named by ``--mode``, or None when the flag is absent.

    Only "student" is accepted. The flag exists so CI can refuse to trust
    anything a student can edit; allowing "instructor" here would hand back
    exactly the redirect the flag is meant to close. Raises ValueError with a
    message for the operator on any other use.
    """
    if "--mode" not in argv:
        return None
    position = argv.index("--mode")
    value = argv[position + 1] if position + 1 < len(argv) else None
    if value != "student":
        raise ValueError(
            f"--mode only accepts 'student' (got {value!r}); "
            "omit the flag to detect the mode from the repo layout."
        )
    return value


def main() -> int:
    update = "--update" in sys.argv
    try:
        forced = forced_mode(sys.argv[1:])
    except ValueError as error:
        print(f"ERROR: {error}")
        return 2
    # --mode exists to tighten a CHECK. A baseline must be written from the
    # files this repo's layout actually maps to, so refuse to combine them.
    if update and forced:
        print("ERROR: --update cannot be combined with --mode")
        return 2
    mode = forced or distribution_mode(ROOT)
    config_errors = configuration_errors(ROOT, mode)
    if config_errors:
        for error in config_errors:
            print("CONFIGURATION:", error)
        return 2
    current = {}
    for rel in TRACKED:
        p = source_path(ROOT, rel, mode)
        if not p.exists():
            # Report the path the operator can actually go look at, which in
            # instructor mode is the source, not the shipped name.
            print(f"MISSING: {p.relative_to(ROOT)}")
            return 2
        current[rel] = sha256_of(p)

    if update:
        write_expected(current)
        print(f"Wrote {len(current)} hashes to {HASH_FILE}")
        return 0

    expected = load_expected()
    if not expected:
        print("No INTEGRITY_HASHES.txt found. Run with --update to create it.")
        return 2

    mismatches = [p for p in current if current[p] != expected.get(p)]
    missing = [p for p in expected if p not in current]

    soft_missing = []
    soft_empty = []
    for rel in SOFT_TRACKED:
        p = ROOT / rel
        if not p.exists():
            soft_missing.append(rel)
        elif p.stat().st_size == 0:
            soft_empty.append(rel)

    if mismatches or missing or soft_missing or soft_empty:
        for p in mismatches:
            print(f"MODIFIED: {p}")
            print(f"  expected: {expected.get(p)}")
            print(f"  actual:   {current[p]}")
        for p in missing:
            print(f"MISSING_FROM_REPO: {p}")
        for p in soft_missing:
            print(f"SOFT_MISSING: {p}")
        for p in soft_empty:
            print(f"SOFT_EMPTY: {p}")
        return 1

    print(f"OK - {len(current)} capture files unchanged.")
    if SOFT_TRACKED:
        print(f"OK - {len(SOFT_TRACKED)} soft-tracked file(s) present and non-empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
