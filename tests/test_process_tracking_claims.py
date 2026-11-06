"""PROCESS_TRACKING.md's factual claims must match the capture layer.

Audit C3 flagged absolute claims in the students' primary disclosure doc
that are verifiably false against the code that actually runs. Each
assertion below derives its expectation from the capture-layer source
(``tests._capture.metadata.TRIGGERS``, ``tests.conftest._EXIT_STATUS``,
the codex/aider adapters' ``force_paths()``, ``metadata.hostname_hash``)
instead of a second hardcoded copy of the same literal -- so a future
code change that reintroduces the mismatch fails this test again instead
of the doc silently drifting out of sync.

Not pinned in tools/INTEGRITY_HASHES.txt: PROCESS_TRACKING.md is a
disclosure doc, not shipped guardrail code, so editing it needs no
baseline regen. (tests/_capture/metadata.py, where TRIGGERS now lives,
*is* pinned -- its hash was regenerated via
`tools/verify_integrity.py --update` as part of this change.)
"""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

from tests.conftest import _EXIT_STATUS
from tests._capture.metadata import TRIGGERS, hostname_hash
from tests._capture.agent_adapters.aider import AiderAdapter
from tests._capture.agent_adapters.codex import CodexAdapter
from tests._capture import ai_traces

_ROOT = Path(__file__).resolve().parent.parent
_DOC_PATH = _ROOT / "PROCESS_TRACKING.md"


def _doc_text() -> str:
    return _DOC_PATH.read_text(encoding="utf-8")


def _kwarg_literal(path: Path, func_name: str, kwarg: str):
    """Literal string value(s) passed as `kwarg=` on a call to `func_name`
    in the source file at `path`. Grounds a doc assertion in the actual
    call site instead of a second hand-typed copy of the value.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else None)
        if name != func_name:
            continue
        for kw in node.keywords:
            if kw.arg == kwarg and isinstance(kw.value, ast.Constant):
                values.append(kw.value.value)
    return values


def _positional_literal(path: Path, func_name: str, index: int):
    """The literal value of the `index`-th positional arg on a bare call to
    `func_name` (e.g. `request_snapshot(repo, "codex_stop")`).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == func_name and len(node.args) > index):
            arg = node.args[index]
            if isinstance(arg, ast.Constant):
                values.append(arg.value)
    return values


def test_bold_absolute_nothing_outside_project_claim_is_removed():
    """RED anchor. The doc bolds 'Nothing outside the project directory is
    captured... no system information' -- directly contradicted two
    paragraphs above by the doc's own list of captured commit metadata
    (Python version, OS type, a hostname hash), which IS system
    information (tests/_capture/metadata.py::format_commit_message).
    """
    text = _doc_text()
    assert "Nothing outside the project directory is captured" not in text
    assert "no system information" not in text.lower()


def test_codex_trigger_claim_matches_manual_cli_seam():
    """The doc claims the Codex Stop hook's snapshot body lists
    `trigger: codex_stop`. In reality `.codex/hooks/stop.py` calls the
    shared `request_snapshot` helper with a *reason* string, which shells
    out to `python -m tests._capture snapshot --reason ...` -- and that
    CLI seam always calls the orchestrator with `trigger="manual"`
    (tests/_capture/__main__.py), attaching the reason as a separate
    `manual_reason:` line (tests/_capture/orchestrator.py). There is no
    `trigger: codex_stop` in any real snapshot body.
    """
    main_path = _ROOT / "tests" / "_capture" / "__main__.py"
    stop_hook = _ROOT / ".codex" / "hooks" / "stop.py"

    cli_triggers = _kwarg_literal(main_path, "take_snapshot", "trigger")
    assert cli_triggers == ["manual"], (
        "the CLI seam's trigger literal changed; update PROCESS_TRACKING.md "
        "to match if this was intentional"
    )
    codex_reasons = _positional_literal(stop_hook, "request_snapshot", 1)
    assert "codex_stop" in codex_reasons

    text = _doc_text()
    # The false claim was that the *snapshot body* lists `trigger:
    # codex_stop` -- the corrected doc may still mention the string
    # "codex_stop" (as a manual_reason, in prose explaining the old
    # claim was wrong) so only the specific false framing is banned.
    assert "lists `trigger: codex_stop`" not in text
    assert "trigger: manual" in text
    assert "manual_reason: codex_stop" in text


def test_gitignore_claim_reflects_force_paths_exception():
    """The doc claims gitignored files 'count as outside the project ...
    are not captured'. tests/_capture/agent_adapters/aider.py force-stages
    its transcript files into the snapshot specifically so a student can't
    hide an aider session by gitignoring it -- and those exact filenames
    are covered by the repo's own `.aider*` .gitignore pattern. That's a
    real, code-verified counterexample to the doc's unqualified claim.
    """
    forced = AiderAdapter().force_paths()
    assert forced, "aider adapter no longer force-stages anything -- re-check this test"
    gitignore_text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".aider*" in gitignore_text
    assert all(fnmatch.fnmatch(name, ".aider*") for name in forced), (
        "aider's force-staged filenames no longer match the .gitignore "
        "pattern that is supposed to cover them"
    )

    text = _doc_text()
    assert "you are not submitting them, so they are not captured" not in text
    # The corrected doc must name the concrete exception, not just soften
    # the language -- every force-staged filename should be traceable.
    for name in forced:
        assert name in text, f"{name} (force-staged despite .gitignore) not disclosed in doc"
    assert "force" in text.lower()


def test_hang_status_vocabulary_matches_exit_status_table():
    """The doc's test-hang section claims a per-test timeout 'usually'
    records `status: pytest_exit_1`. tests/conftest.py's `_EXIT_STATUS`
    table is the actual source of that status string, and it maps pytest
    exit code 1 to the human-readable `tests_failed`, not a raw
    `pytest_exit_1` fallback (that fallback only fires for exit codes
    outside the mapped 0-5 range).
    """
    assert _EXIT_STATUS[1] == "tests_failed"
    text = _doc_text()
    assert "usually `pytest_exit_1`" not in text
    assert "`status: pytest_exit_1`" not in text
    # The corrected doc must cite the real mapped status for the common case.
    assert "`status: tests_failed`" in text or "status: tests_failed" in text

    # Every status the doc names must be one the code can record, and every
    # status the code can record must be named -- a SET comparison, so a
    # renamed, added, or dropped table entry fails here instead of drifting.
    documented = set(re.findall(r"`status: ([^`]+)`", text))
    # The watchdog records its own status outside the pytest session.
    watchdog_statuses = set(_kwarg_literal(
        _ROOT / "tests" / "_capture" / "watchdog.py",
        "format_commit_message", "status"))
    assert watchdog_statuses, "watchdog status literal not found"
    # Exit codes outside the table fall back to `pytest_exit_{code}`; the
    # doc spells that as a placeholder, not a concrete code.
    fallback = "pytest_exit_<code>"
    expected = set(_EXIT_STATUS.values()) | watchdog_statuses | {fallback}
    assert documented == expected, (
        f"undocumented: {sorted(expected - documented)}; "
        f"not recordable: {sorted(documented - expected)}")


def test_when_capture_fires_trigger_list_matches_triggers_map():
    """The doc must disclose a `trigger: X` value for EVERY trigger the
    capture layer can emit. `TRIGGERS` (tests/_capture/metadata.py) is the
    single source of truth; this test iterates the whole map rather than a
    retyped copy of it, so adding a new trigger there without disclosing it
    here fails.

    Iterating every key is the point. An earlier version compared the doc
    against a hardcoded set of the four then-known names, which silently
    accepted a newly added fifth trigger -- exactly the drift this test
    exists to prevent.
    """
    text = _doc_text()
    undisclosed = sorted(
        name for name in TRIGGERS if f"trigger: {name}" not in text
    )
    assert not undisclosed, (
        "TRIGGERS entries not disclosed in PROCESS_TRACKING.md: "
        f"{undisclosed}. Every trigger the capture layer can emit must be "
        "named in the doc."
    )


def test_codex_and_ai_traces_dirs_are_force_staged_over_gitignore():
    """The doc must disclose that `.codex` and `.ai-traces` themselves are
    force-staged (`git add -Af`) into every snapshot, overriding any
    `.gitignore` rule that would otherwise exclude them -- including one a
    student adds themselves. Derived from CodexAdapter.force_paths(),
    which is the actual call site that produces this behavior (see
    tests/_capture/orchestrator.py's force_set collection).
    """
    forced = set(CodexAdapter().force_paths())
    assert forced == {ai_traces.AI_TRACES_DIR, ".codex"}

    text = _doc_text()
    # Not just "mentioned somewhere" -- both force-staged directory names
    # must appear together, close to the word describing the mechanism, so
    # a coincidental unrelated mention elsewhere in the doc can't satisfy
    # this check.
    idx = text.lower().find("force-staged")
    assert idx != -1, "doc never explains the force-staging mechanism"
    window = text[max(0, idx - 400): idx + 400]
    for path in sorted(forced):
        assert path in window, (
            f"{path} not disclosed near the force-staging explanation"
        )


def test_hostname_hash_wording_is_not_an_irrecoverability_claim():
    """The doc claims 'the instructor cannot recover your hostname from the
    hash'. hostname_hash() is a keyless, unsalted sha256 of
    `f"{hostname}|{project_path}"` truncated to 64 bits -- project_path is
    public (it's the repo's own path), so anyone who *suspects* a specific
    hostname can hash that guess and compare, i.e. the hash is
    guess-confirmable even though it can't be blindly inverted. The doc
    should not claim outright irrecoverability.
    """
    # No secret salt: identical inputs always produce the identical hash,
    # so a guessed hostname can be confirmed by recomputing it.
    assert hostname_hash.__doc__ and "Deterministic" in hostname_hash.__doc__
    text = _doc_text()
    assert "the instructor cannot recover your hostname from the hash" not in text
    assert "guess" in text.lower()


def _hook_stop_reasons() -> dict:
    """{tool: [reason, ...]} for every shipped hook tree's stop script.

    The Claude tree is authored at .claude-student/ in the instructor repo
    and shipped as .claude/, so resolve it through the integrity mapping.
    """
    from tools import verify_integrity

    claude_dir = verify_integrity.source_path(_ROOT, ".claude/settings.json").parent
    trees = {
        "Codex": _ROOT / ".codex" / "hooks",
        "Claude Code": claude_dir / "hooks",
        "Cursor": _ROOT / ".cursor" / "hooks",
    }
    return {
        tool: _positional_literal(hooks / "stop.py", "request_snapshot", 1)
        for tool, hooks in trees.items()
    }


def test_every_hook_snapshot_reason_is_disclosed():
    """Each shipped hook tree takes its own snapshot. A student reading the
    trigger list must be able to find every one of them, not just Codex's.
    """
    text = _doc_text()
    for tool, reasons in _hook_stop_reasons().items():
        assert reasons, f"{tool} stop hook no longer calls request_snapshot"
        for reason in reasons:
            assert f"manual_reason: {reason}" in text, (
                f"PROCESS_TRACKING.md does not disclose the {tool} hook snapshot "
                f"(manual_reason: {reason})"
            )


def test_cursor_is_not_described_as_uncaptured():
    """Cursor ships project hooks (.cursor/hooks) that log typed prompts and
    agent edits, so no disclosure may tell students Cursor is not captured.
    """
    assert (_ROOT / ".cursor" / "hooks.json").is_file()
    policy = (_ROOT / "AI_POLICY.md").read_text(encoding="utf-8")
    for label, text in [("PROCESS_TRACKING.md", _doc_text()),
                        ("AI_POLICY.md", policy),
                        ("ATTESTATION_TEMPLATE", ai_traces.ATTESTATION_TEMPLATE)]:
        flat = " ".join(text.split())
        assert "Cursor is NOT captured" not in flat, label
        assert "Cursor in particular is NOT captured" not in flat, label
        assert "Cursor sessions are NOT captured" not in flat, label
        assert "Claude web, Cursor" not in flat, label
        assert "Claude.ai, Cursor" not in flat, label


def test_tool_injected_instructions_are_disclosed_as_not_captured():
    """Instructor decision 35e: text a tool injects into a conversation --
    system prompts, instruction files such as AGENTS.md and CLAUDE.md
    (global ones in the home folder included), environment and tool
    descriptions -- is withheld; only a project's own AGENTS.md can stay,
    because it is submitted anyway. Both disclosure docs must say so.
    """
    from tests._capture import redaction

    assert redaction.INJECTED_CONTEXT == "injected_context"
    policy = (_ROOT / "AI_POLICY.md").read_text(encoding="utf-8")
    for label, text in [("PROCESS_TRACKING.md", _doc_text()),
                        ("AI_POLICY.md", policy)]:
        flat = " ".join(text.split())
        assert "tool-injected instructions" in flat.lower(), label
        assert "not captured" in flat, label
        for global_file in ("~/.codex/AGENTS.md", "~/.claude/CLAUDE.md"):
            assert global_file in flat, (label, global_file)
        assert "injected_context" in flat, label
