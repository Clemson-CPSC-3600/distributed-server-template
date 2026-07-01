"""Audit capture-layer integrity by comparing file hashes against INTEGRITY_HASHES.txt.

There are two tiers of tracking:

- TRACKED files have their content hashes pinned. Any modification, even
  whitespace, is reported as MODIFIED. These are course-infrastructure
  files students should never edit.
- SOFT_TRACKED files are checked only for presence and non-emptiness; the
  content is expected to be student-edited. Used for files like
  `.ai-traces/external-attestation.txt` where deletion or wholesale
  emptying is the signal we care about, not content changes.

Usage:
    python tools/verify_integrity.py            # check current file hashes
    python tools/verify_integrity.py --update   # regenerate INTEGRITY_HASHES.txt
                                                # (instructor use only, after
                                                # intentional capture changes)

Exit code 0 on match, 1 on mismatch / soft-track violation, 2 on missing
hard-tracked file or missing INTEGRITY_HASHES.txt.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HASH_FILE = ROOT / "tools" / "INTEGRITY_HASHES.txt"

TRACKED = [
    "AGENTS.md",
    "AI_POLICY.md",
    ".codex/config.toml",
    ".gitignore",
    "tests/conftest.py",
    "tests/_capture/__init__.py",
    "tests/_capture/__main__.py",
    "tests/_capture/ai_traces.py",
    "tests/_capture/agent_adapters/__init__.py",
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
    "tests/_capture/claude_ingest.py",
    "tests/_capture/codex_ingest.py",
    "tests/_capture/vscode_chat_ingest.py",
    "tests/_capture/git_ops.py",
    "tests/_capture/metadata.py",
    "tests/_capture/orchestrator.py",
    "tests/_capture/platform_compat.py",
    "tests/_capture/post_commit_entry.py",
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
]


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


def main() -> int:
    update = "--update" in sys.argv
    current = {}
    for rel in TRACKED:
        p = ROOT / rel
        if not p.exists():
            print(f"MISSING: {rel}")
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
