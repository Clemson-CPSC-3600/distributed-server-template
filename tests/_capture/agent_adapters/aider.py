"""Aider CLI adapter for the vendor-neutral AI trace layer.

Aider (the AI pair-programming CLI) writes its transcript directly into
the project's working directory as ``.aider.chat.history.md`` (full
conversation) and ``.aider.input.history`` (raw user inputs). Unlike the
Codex/Claude/vscode_chat adapters, there is no external session store to
scan or match by timestamp/cwd -- if the files exist in the repo, aider
ran here. This adapter's only job is to declare those paths so the
orchestrator force-stages them into the snapshot despite ``.aider*``
being gitignored (see ``.gitignore``).

``.aider.tags.cache.v3/`` (aider's repo-map cache directory) is
deliberately NOT captured: it is disposable, regenerable cache state
(not evidence of AI assistance) and can grow large across a semester.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from tests._capture import ai_traces
from tests._capture.agent_adapters.base import AdapterMetadata, IngestResult

CHAT_HISTORY = ".aider.chat.history.md"
INPUT_HISTORY = ".aider.input.history"


class AiderAdapter:
    """Aider CLI adapter. No ingest/normalize step -- files are already
    in place where the orchestrator expects them (the "freebie" case)."""

    name = "aider"
    # Originals live at the root; the sanitizer writes the archived evidence
    # into the common trace directory, independently of any other adapter.
    transcripts_dir = ai_traces.AI_TRACES_DIR

    def is_present(self) -> bool:
        # No reliable machine-wide signal (aider has no persistent daemon
        # or config dir we can probe outside a repo). Presence is decided
        # per-repo by is_present_for_repo.
        return False

    def is_present_for_repo(self, repo: Path) -> bool:
        return (repo / CHAT_HISTORY).exists() or (repo / INPUT_HISTORY).exists()

    def ingest(self, repo: Path, since: Optional[float]) -> IngestResult:
        try:
            present = [
                p for p in (CHAT_HISTORY, INPUT_HISTORY)
                if (repo / p).exists()
            ]
            if not present:
                return IngestResult(adapter_name=self.name)
            ai_traces.ensure_attestation(repo)
            # No rollouts_skipped here, deliberately: aider writes its
            # histories INTO the repo, so there is no copy step and hence
            # no "matched but already current" population. Every present
            # file is matched every run, so this adapter's capture_counts
            # trailer never vanishes in steady state — the failure mode
            # rollouts_skipped exists to fix elsewhere (bead 8g5.7).
            return IngestResult(
                adapter_name=self.name,
                rollouts_copied=[Path(p) for p in present],
            )
        except Exception as e:  # best-effort; never block a snapshot
            return IngestResult(adapter_name=self.name, errors=[str(e)])

    def detect_active_session(self) -> Optional[AdapterMetadata]:
        # Aider has no Stop-hook equivalent; only the pytest trigger path
        # picks up its transcripts.
        return None

    def stage_paths(self) -> List[str]:
        return [CHAT_HISTORY, INPUT_HISTORY, self.transcripts_dir]

    def force_paths(self) -> List[str]:
        return [CHAT_HISTORY, INPUT_HISTORY, self.transcripts_dir]

    def unstage_after(self) -> List[str]:
        # The originals are force-STAGED (so gitignoring them cannot hide
        # that aider ran) and then dropped from the index: what gets
        # COMMITTED is the redacted derivative the redaction pass writes to
        # `.ai-traces/aider/redacted/` (see tests/_capture/redaction.py).
        return [CHAT_HISTORY, INPUT_HISTORY]
