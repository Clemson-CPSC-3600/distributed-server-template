"""The attestation seed text has exactly one definition.

Three places can put `.ai-traces/external-attestation.txt` on disk:

  * `create-assignment.sh` seeds it when a student repo is generated,
  * `bootstrap-reference.sh` seeds it when a reference impl is generated,
  * `tests/_capture/ai_traces.py::ensure_attestation` regenerates it if a
    student deletes it and then runs any AI adapter.

AI_POLICY.md makes a hard claim about that file ("write 'no uncaptured AI
use' if you used none"), so the regenerated copy must carry the same
instructions as the seeded one. It did not: the regenerated fallback was a
two-line stub that omitted the instruction the policy depends on.

These tests execute the *actual* seed block out of each shell script (the
bytes between the `attestation-seed` markers, run by bash with TARGET_DIR
pointed at a tmp dir) and compare against what `ensure_attestation` writes.
Editing either side alone fails the comparison.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests._capture import ai_traces

REPO_ROOT = Path(__file__).resolve().parents[1]

BEGIN_MARKER = "# --- attestation-seed: BEGIN"
END_MARKER = "# --- attestation-seed: END"

SEEDING_SCRIPTS = ["create-assignment.sh", "bootstrap-reference.sh"]


def _distribution_mode() -> str:
    try:
        data = json.loads(
            (REPO_ROOT / "project-template-config.json").read_text(encoding="utf-8"))
        return str(data.get("distribution_mode", ""))
    except (OSError, ValueError):
        return ""


def _require_script(script_name: str) -> Path:
    """Both generator scripts are instructor-only and are not copied into a
    student repo, where this test file nonetheless ships. Absent + student
    mode is expected; absent + instructor mode is a broken checkout, not a
    reason to quietly stop checking parity."""
    path = REPO_ROOT / script_name
    if path.is_file():
        return path
    if _distribution_mode() == "student":
        pytest.skip(f"{script_name} is instructor-only; not present in a student repo")
    pytest.fail(f"{script_name} is missing from this instructor-mode repo")


def _extract_seed_block(script_name: str) -> str:
    """Return the shell lines between the attestation-seed markers.

    Marker-delimited rather than pattern-matched so the block can change
    shape (heredoc -> python extraction) without the test needing to know
    which shape it currently has. A missing/empty block is a failure, not a
    skip: removing the markers would otherwise silently disable parity.
    """
    lines = _require_script(script_name).read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith(BEGIN_MARKER)]
    ends = [i for i, ln in enumerate(lines) if ln.startswith(END_MARKER)]
    assert len(starts) == 1, (
        f"{script_name}: expected exactly one {BEGIN_MARKER!r} marker, "
        f"found {len(starts)}"
    )
    assert len(ends) == 1, (
        f"{script_name}: expected exactly one {END_MARKER!r} marker, "
        f"found {len(ends)}"
    )
    block = "\n".join(lines[starts[0] + 1:ends[0]])
    assert block.strip(), f"{script_name}: attestation-seed block is empty"
    return block


def _run_seed_block(script_name: str, tmp_path: Path) -> str:
    """Run one script's seed block against a tmp TARGET_DIR; return the text."""
    target = tmp_path / "target"
    target.mkdir(parents=True, exist_ok=True)
    runner = tmp_path / "seed_block.sh"
    runner.write_text(
        "set -eu\n" + _extract_seed_block(script_name) + "\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["TARGET_DIR"] = str(target)
    result = subprocess.run(
        ["bash", str(runner)],
        # cwd is the repo root: the seed block resolves the template out of
        # this checkout, exactly as it does mid-generation.
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{script_name} seed block failed:\n{result.stdout}\n{result.stderr}"
    )
    seeded = target / ai_traces.ATTESTATION_FILE
    assert seeded.is_file(), f"{script_name} seed block wrote no attestation file"
    return seeded.read_text(encoding="utf-8")


@pytest.mark.parametrize("script_name", SEEDING_SCRIPTS)
def test_seeded_attestation_matches_regenerated_attestation(script_name, tmp_path):
    seeded = _run_seed_block(script_name, tmp_path / "seed")
    regen_repo = tmp_path / "regen"
    regen_repo.mkdir()
    regenerated = ai_traces.ensure_attestation(regen_repo).read_text(encoding="utf-8")
    assert seeded == regenerated


def test_regenerated_attestation_carries_the_policy_instructions(tmp_path):
    """AI_POLICY.md tells students to write a specific sentence in this file
    when they used no uncaptured AI. The regenerated copy has to say so too,
    or a student who deleted the file is held to an instruction they were
    never shown. Whitespace-normalized: the source is hard-wrapped prose."""
    text = ai_traces.ensure_attestation(tmp_path).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert 'write "no uncaptured AI use"' in flat
    assert "NOT locally captured" in flat


def test_ensure_attestation_does_not_clobber_existing_entries(tmp_path):
    """Student disclosures must survive a later adapter run."""
    path = tmp_path / ai_traces.ATTESTATION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("2026-01-01 09:00 | ChatGPT | asked about loops\n", encoding="utf-8")
    ai_traces.ensure_attestation(tmp_path)
    assert path.read_text(encoding="utf-8").startswith("2026-01-01 09:00 | ChatGPT")
