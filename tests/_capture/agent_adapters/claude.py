"""Claude Code adapter for the vendor-neutral AI trace layer.

Mirrors ``CodexAdapter``, but reads from ``~/.claude/projects/<slug>/`` and
does not declare a per-turn hook trigger. Capture happens through the same
on-action triggers (test runs, sitecustomize atexit, post-commit) that fire
the orchestrator; each trigger re-ingests whatever Claude Code has appended
to its session JSONL since the last snapshot. See the orchestrator module
docstring for why we don't go down the hook-trust road.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from tests._capture import ai_traces, claude_ingest
from tests._capture.agent_adapters import claude_normalize
from tests._capture.agent_adapters.base import AdapterMetadata, IngestResult


class ClaudeAdapter:
    """Claude Code adapter."""

    name = "claude"
    transcripts_dir = ai_traces.AI_TRACES_DIR

    def is_present(self) -> bool:
        try:
            home = os.environ.get("CLAUDE_CONFIG_DIR")
            if home:
                return (Path(home) / "projects").is_dir()
            return (Path.home() / ".claude" / "projects").is_dir()
        except (OSError, RuntimeError):
            return False

    def is_present_for_repo(self, repo: Path) -> bool:
        return (
            ai_traces.adapter_dir(repo, self.name).exists()
            or self.is_present()
        )

    def ingest(self, repo: Path, since: Optional[float]) -> IngestResult:
        try:
            evidence_root = ai_traces.adapter_dir(repo, self.name)
            explicit_home = bool(os.environ.get("CLAUDE_CONFIG_DIR"))
            if not explicit_home and not evidence_root.exists() and not self.is_present():
                return IngestResult(adapter_name=self.name)

            copied = claude_ingest.ingest_transcripts(repo, since or 0.0)
            if not copied and not evidence_root.exists():
                return IngestResult(adapter_name=self.name)

            ai_traces.ensure_attestation(repo)
            built = claude_normalize.normalize_all(repo)
            return IngestResult(
                adapter_name=self.name,
                rollouts_copied=list(copied),
                summaries_built=list(built),
                source_hashes=ai_traces.collect_hashes(repo),
            )
        except Exception as e:
            return IngestResult(adapter_name=self.name, errors=[str(e)])

    def detect_active_session(self) -> Optional[AdapterMetadata]:
        # We don't ship Claude Code hooks (see orchestrator docstring),
        # so there's no hook-payload-driven active session to detect.
        return None

    def stage_paths(self) -> List[str]:
        return [self.transcripts_dir]

    def force_paths(self) -> List[str]:
        return [self.transcripts_dir]

    def unstage_after(self) -> List[str]:
        return []
