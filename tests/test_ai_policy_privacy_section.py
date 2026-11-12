"""Doc-claim checks for AI_POLICY.md's "How to keep work private" section.

These are instructor-side sanity checks on shipped disclosure prose, not
student-graded tests, so they deliberately carry no @pytest.mark.bundle()
marker (see CLAUDE.md: only bundle-marked tests are collected by
run_tests.py). Run directly with `pytest tests/test_ai_policy_privacy_section.py`.

Scoped to the privacy section specifically (not a file-wide grep) because
all three trigger names already appear *elsewhere* in AI_POLICY.md (in the
"What is captured" section) — a file-wide grep for trigger names would pass
vacuously even against a privacy section that says nothing useful, which is
exactly the failure mode audit C3 flagged (see
docs/superpowers/plans/2026-08-11-plan-review-amendments.md, uvd.1 entry).
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _policy_text() -> str:
    """Return the whole of AI_POLICY.md with whitespace runs collapsed."""
    return re.sub(
        r"\s+", " ", (REPO_ROOT / "AI_POLICY.md").read_text(encoding="utf-8")
    )


def _privacy_section_text() -> str:
    """Return the body of the "## How to keep work private" section only,
    with whitespace runs (including markdown's mid-sentence line wraps)
    collapsed to single spaces.

    Without normalization, a literal substring check against a hard-wrapped
    markdown paragraph can miss a real phrase split across a line break (or
    worse, silently pass a retired one for the same reason) -- collapsing
    whitespace first makes the checks robust to reflowing the prose.
    """
    text = (REPO_ROOT / "AI_POLICY.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## How to keep work private\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "AI_POLICY.md is missing the 'How to keep work private' section"
    return re.sub(r"\s+", " ", match.group(1))


def test_privacy_section_does_not_send_students_to_a_sibling_clone():
    """The retired advice ('do it in a separate clone of the repository')
    told students to do private work in a place that would itself become
    a captured, instructor-visible clone (same project-template-config.json,
    same remote) — the worst possible failure of a privacy section. That
    exact phrase must be gone.

    Asserted file-wide, not just within the privacy section: the advice is
    only retired if it appears nowhere in the document, and a section-scoped
    check would let it reappear under any other heading.
    """
    assert "separate clone" not in _policy_text()


def test_privacy_section_directs_students_outside_the_project_folder():
    """Replacement advice must point outside the project folder, the one
    location the capture layer's own gates (sitecustomize's project-root
    walk + relative_to check, and the orchestrator's config+git-repo
    requirement) guarantee is never captured.
    """
    section = _privacy_section_text()
    assert "outside the project folder" in section


def test_privacy_section_warns_in_tree_scratch_work_is_still_captured():
    """Scratch work done *inside* the repository is not private, even if
    deleted before the next commit -- a trigger firing while the file
    exists on disk has already snapshotted it.
    """
    section = _privacy_section_text()
    assert "never private" in section


def test_privacy_section_names_all_three_capture_triggers():
    """All three real triggers (tests/conftest.py's pytest_sessionfinish,
    tests/_capture/sitecustomize_payload.py's atexit hook, and
    tests/_capture/post_commit_entry.py via .githooks/post-commit) must be
    named in the privacy section so students know every path that can
    capture their work, not just the most visible one.
    """
    section = _privacy_section_text()
    for trigger in ("pytest", "sitecustomize", "git_post_commit"):
        assert trigger in section, (
            f"privacy section does not name the {trigger!r} trigger"
        )
