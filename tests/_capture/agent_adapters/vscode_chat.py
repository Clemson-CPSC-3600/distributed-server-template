"""VS Code chat-panel adapter: opportunistic raw-only capture.

Captures session JSONLs from VS Code's built-in chat panel (used by
GitHub Copilot Chat and similar extensions). We DO NOT parse the
mutation-log schema — the on-disk file is preserved as-is in
``.ai-traces/vscode_chat/raw/sessions/``, and the instructor can replay
later if needed. See ``vscode_chat_ingest`` module docstring for the
rationale (versioned internal format, ongoing maintenance burden if
parsed).

Because nothing is normalized, this adapter does not contribute events
to ``interaction-stream.jsonl``. Auditors should look at the raw files
directly. ``external-attestation.txt`` remains the canonical disclosure
mechanism for non-VS-Code-chat tools (ChatGPT browser, Cursor — whose
chats live in SQLite, not the VS Code chat-panel format — etc.).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from tests._capture import ai_traces, vscode_chat_ingest
from tests._capture.agent_adapters.base import AdapterMetadata, IngestResult


class VSCodeChatAdapter:
    """VS Code chat-panel adapter (Copilot Chat & friends)."""

    name = "vscode_chat"
    transcripts_dir = ai_traces.AI_TRACES_DIR

    def is_present(self) -> bool:
        try:
            return bool(vscode_chat_ingest._vscode_user_dirs())
        except Exception:
            return False

    def is_present_for_repo(self, repo: Path) -> bool:
        return (
            ai_traces.adapter_dir(repo, self.name).exists()
            or self.is_present()
        )

    def ingest(self, repo: Path, since: Optional[float]) -> IngestResult:
        try:
            evidence_root = ai_traces.adapter_dir(repo, self.name)
            if not self.is_present() and not evidence_root.exists():
                return IngestResult(adapter_name=self.name)
            copied = vscode_chat_ingest.ingest_transcripts(repo, since or 0.0)
            if not copied and not evidence_root.exists():
                return IngestResult(adapter_name=self.name)
            ai_traces.ensure_attestation(repo)
            return IngestResult(
                adapter_name=self.name,
                rollouts_copied=list(copied),
                # No normalizer — see module docstring.
                summaries_built=[],
                source_hashes=ai_traces.collect_hashes(repo),
            )
        except Exception as e:
            return IngestResult(adapter_name=self.name, errors=[str(e)])

    def detect_active_session(self) -> Optional[AdapterMetadata]:
        return None

    def stage_paths(self) -> List[str]:
        return [self.transcripts_dir]

    def force_paths(self) -> List[str]:
        return [self.transcripts_dir]

    def unstage_after(self) -> List[str]:
        return []
