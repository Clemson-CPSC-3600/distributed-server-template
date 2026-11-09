"""Redaction pass for captured AI transcripts.

Instructor decision (2026-08-14, recorded in
``docs/superpowers/plans/2026-08-11-plan-review-amendments.md``): SCRUB,
scoped by the project boundary. The instructor's own statement of the
principle, verbatim, because it explains all three rules at once:

    "The student decides what to put in the transcript. The agent must not
    surface what they did not agree to. Anything not gitignored in the
    project folder is already committed and submitted, so it is already
    visible."

That single sentence is why prompts are kept, why in-project content is
kept, and why content the AGENT READ from outside the project is withheld.

Concretely:

1. ``user_email``-style keys are stripped.
2. Prompt text is KEPT verbatim -- what the student typed and what the
   agent wrote are not withheld for what they say. The student chose to
   type or paste them; that is their decision to make. Only credentials
   inside them are scrubbed. Text a TOOL put into a user or developer
   message (AGENTS.md bodies, environment blocks, skill bodies, system
   prompts, command output, a sub-agent's whole prompt) is not the
   student's and is not captured -- see "tool-injected context" below
   (instructor decision 35e, 2026-09-16). Two narrow exceptions, both
   disclosed in AI_POLICY.md: a message holding a PATCH whose file is not
   provably in the project is withheld whole (rule 4's patch-blob case --
   the body may be that file's text), and a Claude Code message that
   starts with a markup tag is indistinguishable from the tool's own
   wrappers (see CLAUDE CODE TEXT NOBODY TYPED).
3. PATH ANONYMIZATION, applied EVERYWHERE including prompts, because it
   protects identity, which is a separate concern from content. Absolute
   paths under the repo root are rewritten repo-relative; paths outside it
   become an opaque, STABLE sentinel ``<external:sha8>`` so repeated
   references still correlate for the attribution pass without ever naming
   the file. This is what removes the ``/Users/<username>/`` identity leak.
   Paths are classified WHEREVER they appear, key position included
   (Codex maps file edits by path: ``changes = {<path>: {...diff...}}``).
4. CONTENT WITHHOLDING IS A JOIN AND AN ALLOW-LIST (instructor decision
   d5w, Option A). A tool OUTPUT is kept only when the call it joins
   (``call_id``) PROVES that the output holds nothing but submitted project
   content: a recognized read-only command on project files, the student's
   own tracked project code, or a measured tool (a file read, an edit) on a
   project file. Everything else -- inline code the assistant writes,
   archivers, pipes into a shell, unrecognized programs and tools, and any
   output whose call cannot be found -- is withheld wholesale, keeping the
   record, its join and its skeleton so the gap stays visible. The CALL is
   always kept with its paths sentinel-ized, so the transcript still shows
   what ran. See WHICH OUTPUT IS KEPT below.
5. Gitignored in-project files count as EXTERNAL: a gitignored ``.env`` or
   ``venv/`` is not submitted, so its contents must not be captured.
6. FAIL CLOSED. Unresolvable path, unresolvable env var, missing/duplicate
   join key, orphan output, undeterminable gitignore status, or unparseable
   transcript -> redact / withhold rather than commit.

DO NOT confuse this with the force-add control in ``git_ops`` (Attack K).
That one governs the trace FILES THEMSELVES -- a student must not be able to
hide traces by gitignoring them. This module governs the student CONTENT
QUOTED INSIDE those traces. Different objects; both must stay.

Data flow: hooks and ingest stay dumb and write verbatim artifacts to
``.ai-traces/<adapter>/raw/`` (gitignored, never committed). This pass emits
the committed derivative at ``.ai-traces/<adapter>/redacted/`` and redacts
``normalized/*.jsonl`` plus ``interaction-stream.jsonl`` in place.

ACCEPTED RESIDUALS (documented, deliberately not solved -- do not "fix"
these by enumerating names, which is how three earlier designs failed):

* **Pasted content.** Anything the student pastes into a prompt is fair
  game by instructor decision, and content pasted with no path attached is
  undetectable by a path-based rule anyway. (A pasted patch names its file,
  so rule 2's patch exception does apply to it.)
* **The agent quoting external content back in its own message.** Agent
  messages are kept by instructor decision, so an assistant that reads an
  outside file and then quotes it in prose is not withheld. Same family as
  the pasted-content residual.
* **The student's own code.** Running a tracked project script, pytest or
  ``run_tests.py`` keeps its output: that code is the submission
  (instructor decision d5w). A tracked file edited to read a secret and
  then run surfaces the secret; the edited code is itself captured in the
  snapshots, so the evidence is visible. pytest also imports whatever test
  and ``conftest.py`` files sit in the test folder when it runs, untracked
  ones included (the snapshot that run takes captures them, unless they
  are gitignored), and the code a script imports is not checked at all.
  The runners are trusted BY PATH, the project's own ``venv/bin/python``
  and ``.venv/bin/pytest`` included, and the venv is gitignored: an
  interpreter replaced by a wrapper that also prints other files keeps its
  output. A bare ``python`` or ``pytest`` is that same file while the venv
  is active, so closing this would mean withholding all student-code output
  or detecting writes into the venv -- block-list reasoning. The command
  or edit that replaced the interpreter stays in the transcript (an edit's
  text is withheld, the venv being ignored).
* **Configuration the transcript does not record.** A login shell can
  alias ``cat`` or set ``CDPATH``; ``RIPGREP_CONFIG_PATH``, a ``.ignore``
  file, ``PYTEST_ADDOPTS``, git's config, hooks and ``.gitattributes``
  drivers can change what an allowed command reads or runs. A student who
  configures that exposes their own files, like one who pastes them.
  (Commands that print content with no file at all -- ``env``,
  ``printenv``, ``whoami`` -- are not a residual any more: they are not on
  the allow-list, so their output is withheld.)
* **What the checks cannot see from the transcript.** Whether a file is
  tracked or ignored is asked when the transcript is redacted, not when the
  command ran, and an unchanged transcript keeps its earlier decision. A
  web address whose public name resolves to this machine can reach a local
  server. And a pathless ``git grep`` or ``git diff`` reads every tracked
  file, including trace files a student committed, in the moment between
  the normalizer writing them and this pass rewriting them.
* **Adapters with no call/result join.** Cursor's hooks have no tool
  call/result pair to join. They DO store text, though: ``record_edit``
  writes only path, extents and a hash, but ``append_hook_event`` logs the
  whole ``afterFileEdit`` payload, ``edits[].old_string/new_string``
  included. That text is covered by the EDIT-RECORD rule (see
  ``EDIT_TARGET_KEYS``): an edit whose target is outside the project keeps
  its skeleton and loses its text. An earlier version of this docstring
  said Cursor stored no text; that was wrong.

  vscode_chat is a DIFFERENT case, and an earlier version of this docstring
  got it wrong: its sessions are real JSON and DO parse, so the
  "unparseable" rule never fired, and because its records match no
  recognized vocabulary the taint join never fired either -- tool-result
  content passed through with path anonymization only. That is why
  recognition is now DEFAULT DENY (see RECOGNIZED_VOCABULARIES): vscode_chat
  transcripts are withheld wholesale, with a marker and a
  ``withheld_unrecognized`` flag in the capture-health manifest, until
  somebody measures the format against a real session file and adds it.
"""
from __future__ import annotations

import functools
import hashlib
import ipaddress
import json
import os
import re
import shlex
import stat
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import (Any, Dict, FrozenSet, Iterable, Iterator, List,
                    Mapping, NamedTuple, Optional, Set, Tuple)

from tests._capture import ai_traces

REDACTED_SUBDIR = "redacted"
RAW_SUBDIR = "raw"
WITHHELD_SUFFIX = ".withheld.json"
# Suffix of the temp files `_write_if_changed` renames into place.
TEMP_SUFFIX = ".redaction-tmp"

EXTERNAL_PREFIX = "<external:"
REDACTED_EMAIL = "<redacted:user_email>"

# Keys whose value is a filesystem path. All of these get rewritten.
PATH_KEYS = frozenset({
    "cwd", "working_dir", "workdir", "project_root", "workspace_root",
    "workspace_roots", "transcript_path", "rollout_path", "raw_path",
    "log_path", "path", "paths", "file", "files", "filename", "file_path",
    "filepath", "files_touched", "notebook_path", "target_file", "dir",
    "directory",
})

# Path keys that additionally TRIGGER content withholding when the path is
# external. Deliberately narrower than PATH_KEYS: `cwd` and friends describe
# where a session ran, not what a record quotes. Letting `cwd` trigger
# withholding would delete prompt text for any session started one directory
# above the repo, which decision rule 2 (prompts are kept) forbids.
EMAIL_KEYS = frozenset({"user_email", "account_email", "email", "user_mail"})


# --- tool-call / tool-output taint join -----------------------------------
#
# CONTENT WITHHOLDING IS A JOIN, NOT AN INFERENCE. Three review rounds died
# on adjacency heuristics ("this content field sits near that path field"),
# each one leaking under a shape the previous position had not enumerated.
# A survey of 150 real Codex rollouts on the instructor's machine settled it:
#
#   * Codex has NO read_file tool. All 3,134 tool calls were `name="exec"`,
#     whose `input` is a JAVASCRIPT SOURCE STRING wrapping a single SHELL
#     STRING (`tools.exec_command({cmd:"sed -n '1,240p' /abs/a && cat b"})`).
#     One call can read several files, mixing in-project and external, and
#     the output comes back as ONE undifferentiated blob. For the mechanism
#     carrying essentially all file reads, the path->content mapping DOES
#     NOT EXIST IN THE DATA. Splitting it is unrecoverable, not merely hard.
#   * `call_id` joins calls to outputs EXACTLY: 3,530 pairs, 0 orphans on
#     either side, 0 duplicates within a file. Claude Code is the same shape
#     via `tool_use.id` -> `tool_result.tool_use_id` (verified: 85/85, no
#     orphans).
#
# So: decide per CALL whether its output may be kept, then withhold the
# joined OUTPUT wholesale unless it may. The call record itself is KEPT with
# its paths sentinel-ized, so the transcript shows what ran either way.
#
# Six review rounds decided "may be kept" with a BLOCK-LIST: the output was
# withheld when the check DETECTED a read outside the project. Every round
# found a new way past it (`python3 -c 'print(open(".env").read())'`,
# `tar -cf - .`, `cp .env x && cat x`, `curl ... | sh`). So the instructor
# chose an ALLOW-LIST (decision d5w, Option A): output is kept only when the
# call PROVES it read nothing but submitted project files -- see WHICH
# OUTPUT IS KEPT below. Do not reintroduce detection as the deciding rule;
# extend the allow-list instead.
#
# Instructor scope (2026-08-14): "I only care about keeping file content THE
# AGENT READS out of the transcript. Anything the USER pastes in is fair
# game." What the student typed and what the agent wrote are therefore NEVER
# withheld by this join -- only tool OUTPUT, the channel file contents enter
# through. (Text a tool injected into a message is a separate rule: see
# INJECTED_CONTEXT.)

CALL_PAYLOAD_TYPES = frozenset({
    "custom_tool_call", "function_call", "local_shell_call",
})
OUTPUT_PAYLOAD_TYPES = frozenset({
    "custom_tool_call_output", "function_call_output",
    "local_shell_call_output",
})
# `event_msg` mirrors of the same pairs; `*_begin` carries the invocation,
# `*_end` the result.
BEGIN_EVENT_TYPES = frozenset({
    "exec_command_begin", "patch_apply_begin", "mcp_tool_call_begin",
    "web_search_begin",
})
END_EVENT_TYPES = frozenset({
    "exec_command_end", "patch_apply_end", "mcp_tool_call_end",
    "web_search_end",
})

# CODEX `event_msg` TYPES -- DEFAULT DENY. Review finding sx3: current Codex
# CLIs log every finished turn item as an `item_completed` event, a type this
# module did not know, so the tool output inside it was committed with path
# anonymization only. A new event type is exactly where new output text
# turns up, so an `event_msg` whose type is not listed here keeps only its
# skeleton (see `_skeleton_unrecognized`).
#
# Measured over 843 real rollouts (Codex CLI 0.129 through 0.154), plus the
# begin/end mirrors above. Pinned by `test_codex_item_and_event_types_are_pinned`.
CODEX_ITEM_EVENT_TYPE = "item_completed"
CODEX_EVENT_MSG_TYPES = frozenset({
    # authored text -- never withheld (decision rule 2)
    "user_message", "agent_message", "agent_reasoning",
    # bookkeeping: ids, timings, token counts, session settings
    "task_started", "task_complete", "token_count",
    "thread_settings_applied", "turn_aborted", "context_compacted",
    # a finished turn item; its fields are declared per item type below
    CODEX_ITEM_EVENT_TYPE,
}) | BEGIN_EVENT_TYPES | END_EVENT_TYPES

# CODEX ROW TYPES AND RESPONSE ITEM TYPES -- DEFAULT DENY (review finding
# rp7: any `{type, payload}` row counted as Codex, so `world_state` rows
# were committed with path anonymization only). Measured over 844 real
# rollouts (Codex CLI 0.129 through 0.154). A row of any other type keeps
# only its skeleton, and so does a response item of any other type --
# including one inside a `compacted` row's saved history. Pinned by
# `test_codex_row_and_response_item_types_are_pinned`.
CODEX_ROW_TYPES = frozenset({
    "session_meta", "turn_context", "event_msg", "response_item",
    "compacted", "world_state", "inter_agent_communication_metadata",
    "token_usage_record",
})
# Every top-level key a measured row has. Any other is withheld.
CODEX_ROW_KEYS = frozenset({"type", "timestamp", "ordinal", "payload"})
# 98 real response items also carry row-level `metadata`: these two flags,
# nothing else. Any other key in it is withheld, key included.
CODEX_ROW_METADATA_KEYS = frozenset({
    "client_authored", "fallback_token_limit_override"})
CODEX_RESPONSE_ITEM_TYPES = frozenset({
    # authored text and opaque model state
    "message", "reasoning", "agent_message", "compaction",
    # model-side tools whose results are not local files
    "image_generation_call", "web_search_call", "tool_search_call",
    "tool_search_output",
}) | CALL_PAYLOAD_TYPES | OUTPUT_PAYLOAD_TYPES
# `compacted` payload keys that hold a list of response items.
CODEX_HISTORY_KEYS = frozenset({"replacement_history", "guardian_history"})

# PER-TYPE FIELD SCHEMAS -- DEFAULT DENY INSIDE THE MEASURED ROW TYPES.
#
# Knowing a row's type is not enough: `world_state` rows (Codex 0.146 and
# later) carry the text of the user's own skills (`host_skills.body`, 270 of
# 741 real rows) and the AGENTS.md instructions (up to ~23k characters), and
# a round-2 review found text in dict KEYS and in fields nobody declared. So
# the row types below are described field by field. A schema is a dict from
# each measured key to what its value may be; every other key is withheld,
# key included -- it is replaced by a stable `<withheld-key-N>` placeholder,
# because a key can be content too (a command, a skill name).
#
# What a declared value may be (measured over 844 real rollouts):
SCALAR = "scalar"           # a boolean, a number or null
PATH = "path"               # an absolute path -> repo-relative or a sentinel
TEXT = "text"               # authored text (a search query): kept, scrubbed
URL = "url"                 # an http(s) address
HIDDEN = "hidden"           # never project content: always withheld
ITEMS = "items"             # a list of response items, redacted as such
AGENTS_TEXT = "agents_text" # kept only if it IS the project's AGENTS.md
# Identifier fields keep only a value of their measured shape, so such a
# field can carry at most one short token -- never a sentence or a path.
IDENTIFIER_SHAPES: Mapping[str, "re.Pattern[str]"] = {
    "name": re.compile(r"[a-z][a-z0-9.:-]{0,63}"),      # gpt-5.3-codex
    "profile": re.compile(r":?[a-z][a-z0-9_.:-]{0,63}"),  # :workspace
    "snake": re.compile(r"[a-z][a-z0-9_]{0,63}"),       # web_search_call
    "camel": re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}"),  # a mode, CamelCase
    "id": re.compile(r"[A-Za-z0-9_-]{1,128}"),          # resp_..., call_...
    "uuid": re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
                       r"|[a-z][a-z0-9-]{0,63}"),       # or turn-1
    "date": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "timezone": re.compile(r"[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+-]+){1,2}|UTC"),
    "digits": re.compile(r"\d{1,20}"),
    "subpath": re.compile(r"[.a-z0-9_-]{1,64}"),        # .git
    "timestamp": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
                            r"(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})?"),
    "version": re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}"
                          r"(?:[-+][0-9A-Za-z.-]{1,32})?"),  # 0.146.0-alpha.9
    "label": re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,31}"
                        r"(?: [A-Za-z0-9_.-]{1,31}){0,2}"),  # Codex Desktop
    "hex": re.compile(r"[0-9a-f]{7,64}"),                # a commit hash
    "branch": re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,127}"),
    # A git remote, without a user name or token in it.
    "git_remote": re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?"
                             r"/[A-Za-z0-9._/~-]*"
                             r"|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._/~-]+"),
}


@dataclass(frozen=True)
class ListOf:
    """A list whose every element follows `item`."""
    item: Any


@dataclass(frozen=True)
class NamedMap:
    """A dict keyed by measured names (environment names), each value
    following `value`. Any other key is withheld."""
    names: FrozenSet[str]
    value: Any


@dataclass(frozen=True)
class Either:
    """A value that is a single string of kind `scalar`, or a dict
    following `fields` (real rows use both forms for some settings)."""
    scalar: str
    fields: Any


_TOKEN_COUNTS = {name: SCALAR for name in (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens")}
_TOKEN_USAGE_RECORD = {
    "response_id": "id", "root_turn_id": "uuid", "session_id": "uuid",
    "thread_id": "uuid", "turn_id": "uuid", "usage": _TOKEN_COUNTS,
    "turn_token_usage": _TOKEN_COUNTS, "thread_token_usage": _TOKEN_COUNTS,
}
_SANDBOX_ENTRY = {
    "access": "name", "missing_path_behavior": "name",
    "path": {"path": PATH, "type": "name",
             "value": {"kind": "snake", "subpath": "subpath"}},
}
_TURN_CONTEXT = {
    "turn_id": "uuid", "root_turn_id": "uuid", "cwd": PATH,
    "workspace_roots": ListOf(PATH), "current_date": "date",
    "timezone": "timezone", "model": "name", "effort": "name",
    "summary": "name", "personality": "name", "comp_hash": "digits",
    "approvals_reviewer": "snake", "realtime_active": SCALAR,
    "multi_agent_mode": "camel", "multi_agent_version": "name",
    "active_permission_profile": {"id": "profile"},
    "approval_policy": Either("name", {"granular": {
        name: SCALAR for name in (
            "mcp_elicitations", "request_permissions", "rules",
            "sandbox_approval", "skill_approval")}}),
    # Instructions the tool injects (decision 35e). Older CLIs put the
    # AGENTS.md text here -- kept only when it is the project's own file --
    # and developer instructions, which the student never writes.
    "user_instructions": AGENTS_TEXT, "developer_instructions": HIDDEN,
    # The developer instructions of a collaboration mode are a prompt the
    # student did not write (356 real rows).
    "collaboration_mode": {"mode": "name", "settings": {
        "developer_instructions": HIDDEN, "model": "name",
        "reasoning_effort": "name"}},
    "file_system_sandbox_policy": {
        "entries": ListOf(_SANDBOX_ENTRY), "kind": "name"},
    "permission_profile": {
        "file_system": {"entries": ListOf(_SANDBOX_ENTRY), "type": "name"},
        "network": "name", "type": "name"},
    "sandbox_policy": {
        "type": "name", "writable_roots": ListOf(PATH),
        "network_access": SCALAR, "exclude_slash_tmp": SCALAR,
        "exclude_tmpdir_env_var": SCALAR},
}
_WORLD_STATE = {
    "full": SCALAR,
    "state": {
        "agents_md": {"directory": PATH, "text": AGENTS_TEXT},
        "host_skills": {"body": HIDDEN, "includeInstructions": SCALAR},
        "model": "name",
        "personality": {"model": "name", "personality": "name"},
        "collaboration_mode": Either("name", {
            "instructions": HIDDEN, "mode": "name", "model": "name"}),
        "environments": {
            "current_date": "date", "timezone": "timezone",
            "environments": NamedMap(frozenset({"local"}), {
                "cwd": PATH, "shell": "name", "status": "name"}),
            # A description of the sandbox, and of the other agents.
            "filesystem": HIDDEN, "subagents": HIDDEN,
        },
        "multi_agent_mode": {"mode": "camel", "usage_hint_hash": HIDDEN},
        "multi_agent_usage_hint": HIDDEN,
        # The user's approved commands, and hashes of prompt text.
        "permissions": Either(HIDDEN, {
            "approved_command_prefixes": HIDDEN, "instructions": HIDDEN}),
        "context_window_guidance": HIDDEN,
        "realtime": {"active": SCALAR},
        "skills": {"includeInstructions": SCALAR},
        "orchestrator_skills": {"enabled": SCALAR,
                                "includeInstructions": SCALAR},
        # Always empty in the data: any key is unmeasured.
        "managed_developer_instructions": {}, "persistent_mode": {},
        **{name: SCALAR for name in (
            "apps_instructions", "environments_instructions",
            "git_attribution", "plugins_instructions")},
    },
}
_COMPACTED = {
    # The compaction summary is the model's own writing (always empty in
    # the data); the saved histories are response items.
    "message": TEXT, "replacement_history": ITEMS, "guardian_history": ITEMS,
    "window_id": "uuid", "first_window_id": "uuid",
    "previous_window_id": "uuid", "window_number": SCALAR,
    "compaction_response_id": "id",
    "latest_token_usage_record": _TOKEN_USAGE_RECORD,
    "retained_context": {
        "incomplete": SCALAR, "next_order": SCALAR,
        "user_messages_incomplete": SCALAR,
        # Always empty in the data; what they would hold is unmeasured.
        "user_messages": HIDDEN, "verified_answers": HIDDEN},
}
# The documentation of the user's installed tools (apps, MCP servers).
_DYNAMIC_TOOL: Dict[str, Any] = {
    "type": "snake", "name": "snake", "deferLoading": SCALAR,
    "description": HIDDEN, "inputSchema": HIDDEN,
}
_DYNAMIC_TOOL["tools"] = ListOf(_DYNAMIC_TOOL)
# `session_meta` (820 real rows). Its settings are kept; the text the tool
# injects is not (decision 35e): the system prompt -- Codex's own, or a
# custom one from the user's configuration (`base_instructions.text`, over
# 20k characters) -- older CLIs' `instructions`, and tool documentation.
_SESSION_META = {
    "id": "uuid", "session_id": "uuid", "forked_from_id": "uuid",
    "parent_thread_id": "uuid", "timestamp": "timestamp", "cwd": PATH,
    "originator": "label", "cli_version": "version",
    "model_provider": "name", "history_mode": "name",
    "thread_source": "snake", "multi_agent_version": "name",
    "subagent_history_start_ordinal": SCALAR,
    "context_window": {"window_id": "uuid"},
    "git": {"commit_hash": "hex", "branch": "branch",
            "repository_url": "git_remote"},
    # Where the session came from; a sub-agent's is a small object. Agent
    # nicknames are free text the tool made up.
    "source": Either("name", {"subagent": Either("name", {
        "other": "name",
        "thread_spawn": {"parent_thread_id": "uuid", "depth": SCALAR,
                         "agent_role": "name", "agent_path": PATH,
                         "agent_nickname": HIDDEN}})}),
    "agent_role": "name", "agent_path": PATH, "agent_nickname": HIDDEN,
    "base_instructions": Either(HIDDEN, {
        "text": HIDDEN, "provenance": {"type": "name", "model": "name"}}),
    "instructions": HIDDEN,
    "dynamic_tools": ListOf(_DYNAMIC_TOOL),
}
CODEX_ROW_SCHEMAS: Mapping[str, Any] = {
    "session_meta": _SESSION_META,
    "world_state": _WORLD_STATE,
    "turn_context": _TURN_CONTEXT,
    "token_usage_record": _TOKEN_USAGE_RECORD,
    "inter_agent_communication_metadata": {"trigger_turn": SCALAR},
    "compacted": _COMPACTED,
}

_PASSTHROUGH = {"turn_id": "uuid", "create_time": SCALAR,
                "content_item_kinds": ListOf("snake")}
_TOOL_ENTRY: Dict[str, Any] = {
    "type": "snake", "name": "snake", "defer_loading": SCALAR,
    # Tool documentation comes from the user's installed plugins.
    "description": HIDDEN, "parameters": HIDDEN,
}
_TOOL_ENTRY["tools"] = ListOf(_TOOL_ENTRY)
CODEX_RESPONSE_ITEM_SCHEMAS: Mapping[str, Any] = {
    "web_search_call": {
        "type": "snake", "id": "id", "status": "name",
        "internal_chat_message_metadata_passthrough": _PASSTHROUGH,
        "action": {"type": "snake", "query": TEXT, "queries": ListOf(TEXT),
                   "pattern": TEXT, "url": URL}},
    "tool_search_call": {
        "type": "snake", "call_id": "id", "execution": "name",
        "status": "name", "arguments": {"query": TEXT, "limit": SCALAR}},
    "tool_search_output": {
        "type": "snake", "call_id": "id", "execution": "name",
        "status": "name", "tools": ListOf(_TOOL_ENTRY)},
    "image_generation_call": {
        # The picture itself is opaque data; the revised prompt is the
        # model's own writing.
        "type": "snake", "id": "id", "status": "name",
        "revised_prompt": TEXT, "result": HIDDEN},
}


def schema_leaves(schema: Any, prefix: str = "",
                  _open: Optional[Set[int]] = None) -> Dict[str, str]:
    """{dotted path: kind} for every field a schema KEEPS (not HIDDEN).

    Used to pin what survives, so widening it is a visible edit.
    """
    _open = set() if _open is None else _open
    if id(schema) in _open:
        return {}  # a schema that contains itself (a tool's sub-tools)
    if isinstance(schema, Either):
        return {**schema_leaves(schema.scalar, prefix, _open),
                **schema_leaves(schema.fields, prefix, _open)}
    if isinstance(schema, ListOf):
        return schema_leaves(schema.item, prefix, _open)
    if not isinstance(schema, (dict, NamedMap)):
        return {} if schema in (HIDDEN, SCALAR) else {prefix: schema}
    _open = _open | {id(schema)}
    if isinstance(schema, NamedMap):
        children = {name: schema.value for name in sorted(schema.names)}
    else:
        children = schema
    out: Dict[str, str] = {}
    for key, child in children.items():
        out.update(schema_leaves(child, f"{prefix}.{key}" if prefix else key,
                                 _open))
    return out


# `agents_md.text` is kept only when it is exactly the project's own
# AGENTS.md (see `Redactor._agents_md_is_in_project`). Measured: where the
# directory still exists, the text equals `<directory>/AGENTS.md` in 232 of
# 299 rows; the rest changed since, or never came from the project.
AGENTS_MD_NAMES = ("AGENTS.override.md", "AGENTS.md")

# Before Codex 0.140 this template's own fixtures keyed a message on `role`
# instead of `type` (codex_normalize reads both). Such a payload is accepted
# only when it holds nothing but a role and its authored text.
LEGACY_MESSAGE_ROLES = frozenset({"user", "assistant", "agent"})
LEGACY_MESSAGE_KEYS = frozenset({"role", "text"})

# TOOL-INJECTED CONTEXT -- instructor decision 35e (2026-09-16): "tool-
# injected context is not captured; never commit a student's global
# ~/.codex/AGENTS.md." Codex puts a lot of text nobody typed into user- and
# developer-role messages. Measured over 814 real rollouts:
#
#   * developer role (3,962 items): permissions, skills, apps and plugins
#     instructions, collaboration and multi-agent modes -- never typed;
#   * user role: `<environment_context>` (843), `# AGENTS.md instructions
#     for <dir>` (413; 65 of 465 blocks have a global file joined in
#     front), `<skill>` bodies (136), sub-agent notifications (354), image
#     wrappers naming the image's path, and -- in approval-reviewer
#     sessions -- a copy of the whole conversation, tool output included
#     (`[N] tool exec_command result:`, 16,588 items).
#
# Recognizing wrappers by name would be the enumeration mistake again. The
# student's own words are recorded SEPARATELY: an `event_msg` user_message
# and, in current CLIs, an `item_completed` UserMessage. So a message item
# is kept only when its text JOINS one of those records -- in the real data
# every unjoined item was a wrapper. One exception, reusing rp7's rule: an
# AGENTS.md block that is exactly the project's own AGENTS.md is kept.
INJECTED_CONTEXT = "injected_context"
# Roles whose message text a tool may have written. `assistant` text is the
# model's own and is kept (decision rule 2).
INJECTING_ROLES = frozenset({"user", "developer", "system"})
# The one role whose text can be typed. Developer and system text never is.
TYPING_ROLE = "user"
# Content items with no text of their own: an image the student attached.
ATTACHED_ITEM_TYPES = frozenset({"input_image"})
USER_MESSAGE_ITEM = "UserMessage"
_AGENTS_BLOCK_RE = re.compile(
    r"\A\s*# AGENTS\.md instructions for (?P<directory>[^\n]*)\n+"
    r"<INSTRUCTIONS>\n?(?P<text>.*?)\n?</INSTRUCTIONS>\s*\Z", re.DOTALL)


@dataclass(frozen=True)
class ItemFields:
    """The declared keys of one Codex turn-item type (see CODEX_ITEM_FIELDS).

    kept:        ids, metadata, authored text, and the tool's own invocation.
    output:      what the tool returned. Withheld unless the item joins an
                 in-project call AND has an invocation to check (see
                 `_apply_item_rules`).
    invocation:  the kept keys that say what the tool was asked to act on.
                 They are checked for outside paths, like a call's input.
    kinds:       for a type that is really a family (Extension), the
                 measured values of its `kind`; any other kind is withheld.
    """
    kept: FrozenSet[str] = frozenset()
    output: FrozenSet[str] = frozenset()
    invocation: Tuple[str, ...] = ()
    kinds: FrozenSet[str] = frozenset()


# CODEX TURN ITEMS -- DEFAULT DENY, per field. An `item_completed` event
# looks like
#
#   {"type": "item_completed", "turn_id": ..., "item": {"type": <ItemType>,
#                                                        "id": ..., ...}}
#
# and a tool item holds its call AND its output in one object: a
# CommandExecution has `command` and `cwd` beside `aggregated_output`. Every
# key of every item type seen in the 843 rollouts above is declared here; any
# other key is withheld, and an item type not listed is withheld whole.
#
# How the output join works for items (see `_apply_item_rules`): the item's
# own invocation must pass the allow-list (WHICH OUTPUT IS KEPT), and its
# `id` must join exactly one call record whose output may be kept too -- or,
# for an id that joins no call, the item must vouch for itself
# (VOUCHABLE_ITEM_TYPE). In the real data, 3,605 of 3,636 CommandExecution
# items (and 464 of 563 McpToolCall items) carry an `exec-...` id minted
# inside the `exec` tool's JavaScript, which joins no call record, so their
# output is withheld -- unjoinable output fails closed (rule 6) -- unless the
# item VOUCHES for itself: its own invocation passes the allow-list.
CODEX_ITEM_COMMON_KEYS = frozenset({"type", "id"})
CODEX_ITEM_FIELDS: Mapping[str, ItemFields] = {
    # Authored text and bookkeeping -- never withheld (decision rule 2).
    "UserMessage": ItemFields(kept=frozenset({"content", "client_id"})),
    "AgentMessage": ItemFields(kept=frozenset({"content", "phase"})),
    "Reasoning": ItemFields(kept=frozenset({"summary_text", "raw_content"})),
    "ContextCompaction": ItemFields(),
    # Tools that return text.
    "CommandExecution": ItemFields(
        kept=frozenset({"command", "cwd", "parsed_cmd", "exit_code", "status",
                        "duration", "process_id", "source"}),
        output=frozenset({"aggregated_output", "stdout", "stderr",
                          "formatted_output"}),
        # `parsed_cmd` is Codex's own parse of the same script, so the
        # script itself is the one checked.
        invocation=("command",)),
    "McpToolCall": ItemFields(
        kept=frozenset({"server", "tool", "arguments", "status", "duration",
                        "pluginId", "readOnlyHint"}),
        output=frozenset({"result"}),
        invocation=("arguments",)),
    "FileChange": ItemFields(
        # `changes` is keyed BY path, so each entry keeps its own per-file
        # handling (see `_redact_dict`); its keys are also the invocation.
        kept=frozenset({"changes", "status"}),
        output=frozenset({"stdout", "stderr"}),
        invocation=("changes",)),
    "WebSearch": ItemFields(
        kept=frozenset({"query", "action"}),
        output=frozenset({"results"})),
    "Extension": ItemFields(
        kept=frozenset({"kind", "status", "query", "action", "durationMs",
                        "savedPath", "transparentBackground"}),
        output=frozenset({"result", "results", "revisedPrompt", "failure"}),
        # Every `kind` in the data. What another kind's fields mean is
        # unmeasured, so it is withheld whole (review probe R10).
        kinds=frozenset({"image_gen.generation", "clock.sleep",
                         "web.search"})),
    # Tools that return no text.
    "ImageView": ItemFields(kept=frozenset({"path"})),
    "CollabAgentToolCall": ItemFields(
        kept=frozenset({"tool", "status", "sender_thread_id",
                        "receiver_thread_ids", "receiver_agents"}),
        # Always an empty map in the data; what it would hold (the other
        # agents' state) is unmeasured, so it is treated as output.
        output=frozenset({"agents_states"})),
    "SubAgentActivity": ItemFields(kept=frozenset({
        "kind", "agent_path", "agent_thread_id"})),
}
# Codex runs every model command as [<shell>, "-lc", <script>] (measured:
# all 3,678 CommandExecution items). The shell binary is not a file the
# command reads, so only the script is checked (see `_argv_script`).
SHELL_SCRIPT_FLAGS = frozenset({"-c", "-lc"})

# WHICH OUTPUT IS KEPT -- AN ALLOW-LIST (instructor decision d5w, Option A).
# A tool's output is kept only when its call PROVES the output holds nothing
# but submitted project content; everything else is withheld, and the call
# is kept either way. The proof is presence-based: whatever the checker does
# not positively recognize means "not proven". One predicate decides every
# shell script -- `Redactor.shell_script_is_provable` -- wherever the script
# comes from: a Codex `exec` script's `exec_command` calls (`js_tool_calls`),
# a function call's JSON `cmd`, a Claude `Bash` call, a hook's `tool_input`,
# one of our normalized `tool_start` events and the vendor record it copies,
# a CommandExecution item, or an argv `[<shell>, -c|-lc, <script>]`.
# A script passes when
#
#   * every directory it may run in -- the stated `workdir` or `cwd`, else
#     the session's -- is known, absolute, in the project and not ignored.
#     An unknown or outside directory fails closed;
#   * it is PLAIN (`_plain_shell_segments`): no `$`, backtick, `~`,
#     backslash, heredoc, process substitution, unquoted glob, brace,
#     parenthesis, comment or `!`, no word starting with `=` (zsh expands
#     `=cat` to a program's path), and no directory change except one
#     leading `cd /abs` into the project (relative words must then be in
#     the project from both directories: a relative `cd` may follow
#     CDPATH anywhere);
#   * every redirection is a plain one: `<` reads a file, `>` and `>>`
#     write only to a stream device (`/dev/null`, `/dev/stderr`, ...), and
#     `2>&1` copies a stream. Anything else with `<` or `>` in it -- zsh's
#     `<->` glob, a write to a file -- is not plain;
#   * every command in it is a measured read-only program
#     (READ_ONLY_PROGRAMS, plus the sed and git forms below) or runs the
#     student's own code (STUDENT CODE below), given only options on that
#     program's list. The lists are ALLOW-lists: an option word must match
#     one exactly, so an abbreviation GNU tools would accept (`--rec` for
#     `--recursive`) or a value glued to a short option (`-f.env`) is an
#     unknown option, and the command is not provable. An environment
#     assignment (`FOO=1 cat x`) is not a program on the list either;
#   * every word it names -- options' `=values`, redirection targets, and
#     every other argument, whether or not a file by that name exists now
#     -- and every path in Codex's parse is a read inside the project (see
#     PROTECTED_READ_DIRS). A stream device holds no file content and is
#     allowed.
#
# So inline code (`python -c`, `node -e`, heredocs), archivers, `cp`,
# `find`, `curl`, a pipe into `sh`, `env` and any program nobody listed are
# withheld. Tools other than the shell have tables of their own
# (CLAUDE_TOOLS, CODEX_TOOLS); a tool in neither -- MCP tools, sub-agents,
# `write_stdin` (whose output belongs to an earlier command) -- is withheld.
#
# ACCEPTED RESIDUAL: the shell's own configuration is not in the transcript.
# A login shell (`-lc`) could alias `cat`, and RIPGREP_CONFIG_PATH could
# turn off rg's ignore rules; a student who configures that exposes their
# own files, like one who pastes them. Git's configuration is the same
# (review round 5): `core.fsmonitor` runs a program on `git status`,
# `diff.external` and a `diff.<driver>.textconv` filter named in
# `.gitattributes` run one on `git diff`, `show` and `log`, and
# `core.pager` or an alias can run anything. The transcript records none
# of it. Where the command line can turn such a program on
# (`--ext-diff`, `--textconv`), the option is not on any allow-list, so
# the command is not provable; `--no-ext-diff` is.
#
# VOUCHING FOR UNJOINABLE OUTPUT (instructor decision dxp). An item with an
# `exec-...` id joins no call (2,327 of 2,358 real CommandExecution items).
# Such an item carries its whole invocation -- the script, the directory it
# ran in and Codex's own parse -- so its output is kept when that
# invocation passes the predicate above. No other item type vouches: every
# measured unjoined McpToolCall runs JavaScript in a REPL.
VOUCHABLE_ITEM_TYPE = "CommandExecution"
# Every measured item runs `/bin/zsh -lc` (2,358 of 2,358). Another shell
# parses a script differently: fish and PowerShell run `(...)` as a command.
VOUCHABLE_SHELLS = frozenset({"/bin/sh", "/bin/bash", "/bin/zsh",
                              "sh", "bash", "zsh"})


def _options(*patterns: str) -> "re.Pattern[str]":
    """An allow-list of option words; each pattern must match a WHOLE word."""
    return re.compile("|".join(f"(?:{p})" for p in patterns) or r"(?!)")


# The options each program may be given. Measured over the 2,358 real
# CommandExecution items, then -- for the joined calls the allow-list now
# decides too -- over the 94,543 shell scripts in 816 real Codex rollouts
# and 2,212 real Claude Code sessions, and checked by hand: each one only
# changes how the named files (or standard input) are shown, never which
# files are read. Options that were measured but read elsewhere, recurse,
# read a list of names or patterns from a file, or write a file (`rg -g`,
# `rg --hidden`, `grep -r`, `grep -f`, `ls -R`, `git diff --output`,
# `git branch -d`) are left out. `-\d+` is a count (`head -20`,
# `git log -5`). Programs that write a file given a second name
# (`uniq in out`), or that no measured command used with a file, are not
# listed at all.
_GREP_FLAGS = "nivEFcolLwxhHsq"
READ_ONLY_PROGRAMS: Mapping[str, "re.Pattern[str]"] = {
    **{verb: _options() for verb in ("cat", "file")},
    # These print only their own words (or the directory), whatever the
    # options: `echo "---"` is a separator, not an option to vet.
    **{verb: _options("-.*") for verb in ("echo", "printf", "pwd", "true")},
    # Flags, then at most one letter whose value follows: a context or
    # match count (`-A3`, `-A 3`) or a pattern (`-e PATTERN`).
    "grep": _options(f"-[{_GREP_FLAGS}]+", f"-[{_GREP_FLAGS}]*[ABCm]\\d*",
                     f"-[{_GREP_FLAGS}]*e"),
    "sort": _options("-u", "-n", "-r", "-rn", "-nr"),
    "nl": _options("-ba"),
    "head": _options("-n", "-c", r"-\d+"),
    "tail": _options("-n", "-c", r"-\d+"),
    "wc": _options("-l", "-c", "-w", "-lc"),
    "cut": _options("-c", r"-[cf][\d,-]+"),
    "stat": _options("-f", "-t"),
    "ls": _options("-l", "-la", "-lh", "-ld", "-d", "-lde", "-1", "-a", "-t",
                   "-lt"),
    "rg": _options("-n", "-i", "-l", "-v", "-m", "--files", "-[ABC]",
                   r"-[ABC]\d+"),
}
# `sed` only as a line printer: `sed -n '1,200p' <files>`.
SED_OPTIONS = frozenset({"-n"})
_SED_PRINT_RE = re.compile(r"\d+(?:,\d+)?p")
# `git` only for subcommands that show the repository (tracked content is
# submitted content), each with its own measured options. A format string
# may use any placeholder except `%G`, which asks gpg about a signature.
_GIT_FORMAT = r"(?:[^%]|%[^G])*"
READ_ONLY_GIT_COMMANDS: Mapping[str, "re.Pattern[str]"] = {
    "status": _options("--short", "-s", "--branch", "-b", "-sb",
                       "--porcelain(?:=v[12])?",
                       "--untracked-files=(?:no|normal|all)"),
    "diff": _options("--check", "--stat", "--numstat", "--cached",
                     "--name-only", "--name-status", "--binary",
                     "--no-ext-diff", "--patch", "--exit-code"),
    "log": _options(r"-\d+", "--oneline", "--decorate", "--graph", "--all",
                    "--merges", "--simplify-by-decoration", "--stat",
                    r"--max-count=\d+", r"--date=[\w-]+", "--grep=.*",
                    "--since=.*", "--format=" + _GIT_FORMAT,
                    "--pretty=" + _GIT_FORMAT),
    "show": _options("--stat", "--oneline", "--no-ext-diff", "--no-patch",
                     "--find-renames", "--patch", "--binary", "--decorate",
                     "--summary", "--name-only", "--name-status",
                     "--format=" + _GIT_FORMAT, "--pretty=" + _GIT_FORMAT),
    "rev-parse": _options("--show-toplevel", "--git-common-dir", "--git-dir",
                          "--is-inside-work-tree", "--short", "--abbrev-ref"),
    "branch": _options("--list", "--show-current", "--all", "-a",
                       "--contains", "-vv?"),
    **{name: _options() for name in (
        "ls-files", "blame", "grep", "shortlog", "describe")},
}
GIT_GLOBAL_OPTIONS = frozenset({"--no-pager", "-P"})
# Subcommands whose other words may be revisions. None is vouched for with a
# revision -- anything before a `--` except `git grep`'s pattern -- because a
# revision can hold a file that is ignored now (see GIT REVISIONS).
GIT_REVISION_READERS = frozenset({"show", "diff", "log", "blame", "grep"})
# `git branch <name>` CREATES a branch; only these options make the names
# that follow patterns to list instead.
GIT_BRANCH_LIST_OPTIONS = frozenset({"--list", "--all", "-a", "--contains"})

# STUDENT CODE (instructor decision d5w). The student's own program is
# their submitted work, so what it prints counts as project content. A
# command runs student code when it is
#
#   * `python`, `python3` or `python3.N` -- or the project's own
#     `venv/bin/python*` or `.venv/bin/python*`, written relative to where
#     the command runs or absolutely -- given the path of a `.py` file that
#     is in the project, not ignored, TRACKED by git when the transcript is
#     redacted (an agent-written `peek.py` does not count), and not part of
#     the capture layer or its hooks (STUDENT_CODE_EXCLUDED); every word
#     after the script is then checked like any other argument;
#   * `pytest` (or the project venv's), or `python -m pytest`;
#   * `python run_tests.py` -- the project's own runner -- with only its
#     listed options, because it hands any other option to pytest.
#
# Interpreter options are refused (`-c`, `-m` other than pytest, `-`, or
# none at all: a script from standard input), and so is every pytest option
# not listed: `-p` loads a plugin, `-c`, `--rootdir`, `--confcutdir`, `-o`
# and `--import-mode` change which configuration and code it loads.
# ACCEPTED RESIDUALS (module docstring): a tracked file edited to read a
# secret and then run; the code pytest or a script imports; and a
# gitignored project venv interpreter (`venv/bin/python`,
# `.venv/bin/pytest`, ...) replaced by a wrapper -- runners are trusted by
# path, and a bare `python` is that same file while the venv is active.
PYTHON_PROGRAM_RE = re.compile(r"python(?:3(?:\.\d{1,2})?)?")
PYTEST_PROGRAM_RE = re.compile(r"pytest")
# The project's own virtual environment, relative to the project root.
PROJECT_VENV_PROGRAM_RE = re.compile(
    r"\.?venv/(?:bin|Scripts)/(?P<name>[^/]+?)(?:\.exe)?")
STUDENT_SCRIPT_SUFFIX = ".py"
STUDENT_CODE_EXCLUDED = ("tests/_capture/", ".codex/", ".claude/",
                         ".cursor/", ".githooks/", "tools/")
PYTEST_FLAGS = frozenset({
    "-q", "-qq", "-v", "-vv", "-x", "-s", "--lf", "--ff", "--no-header",
    "-rA", "--co", "--collect-only"})
PYTEST_GLUED_OPTIONS = _options(
    r"--tb=(?:auto|long|short|line|native|no)", r"--maxfail=\d+",
    r"--timeout=\d+")
# Their value is the next word: a test-name or marker expression.
PYTEST_VALUE_OPTIONS = frozenset({"-k", "-m"})
RUN_TESTS_SCRIPT = "run_tests.py"
RUN_TESTS_FLAGS = _options("--all", "-v", "--verbose", "--failed",
                           "--bundle=[123]")
RUN_TESTS_VALUE_OPTIONS = {"--bundle": frozenset({"1", "2", "3"}),
                           "-k": None}

# TOOLS OTHER THAN THE SHELL -- DEFAULT DENY BY NAME. A tool's output is
# kept only when the tool is listed here, its input has only the listed
# keys (and the required ones), and what it acts on is in the project.
SHELL_TOOL = "shell"        # `command`: a script, or an argv list
FILE_TOOL = "file"          # `file_path`: one project file, read or edited
SEARCH_TOOL = "search"      # Claude's Grep (see below)
WEB_TOOL = "web"            # the web: no local file at all


@dataclass(frozen=True)
class ToolShape:
    """The input a listed tool may be given (see CLAUDE_TOOLS)."""
    kind: str
    keys: FrozenSet[str]
    required: FrozenSet[str] = frozenset()


# Claude Code's tools, measured over 104,713 tool calls in 2,212 real
# sessions: every key each tool was given (keys seen once, on inputs Claude
# Code rejected, are left out). A tool not listed -- Agent, Skill, MCP
# tools, TaskOutput, AskUserQuestion, ..., and the file tools no measured
# session used (Glob, LS, MultiEdit, NotebookEdit) -- is withheld.
#
# Grep is the exception: no measured session used it, so its keys are the
# input schema of the Grep tool in Claude Code 2.1.274. That tool runs
# `rg --hidden` (VCS folders excluded), so it searches hidden files such as
# `.ai-traces/` and `.codex/auth.json`: it is kept only for a path below
# the project root that is itself a readable project path. `glob` and
# `type` are not listed: a glob given to rg overrides its ignore rules.
CLAUDE_TOOLS: Mapping[str, ToolShape] = {
    "Bash": ToolShape(SHELL_TOOL, frozenset({
        "command", "description", "timeout", "run_in_background",
        "dangerouslyDisableSandbox"}), frozenset({"command"})),
    "Read": ToolShape(FILE_TOOL, frozenset({
        "file_path", "offset", "limit", "pages"}), frozenset({"file_path"})),
    "Edit": ToolShape(FILE_TOOL, frozenset({
        "file_path", "old_string", "new_string", "replace_all"}),
        frozenset({"file_path"})),
    "Write": ToolShape(FILE_TOOL, frozenset({"file_path", "content"}),
                       frozenset({"file_path"})),
    "Grep": ToolShape(SEARCH_TOOL, frozenset({
        "pattern", "path", "output_mode", "-B", "-A", "-C", "context", "-n",
        "-i", "-o", "head_limit", "offset", "multiline"}),
        frozenset({"pattern"})),
    "WebFetch": ToolShape(WEB_TOOL, frozenset({"url", "prompt"}),
                          frozenset({"url"})),
    "WebSearch": ToolShape(WEB_TOOL, frozenset({"query", "allowed_domains"}),
                           frozenset({"query"})),
}
# Codex's tools, measured over 13,794 tool calls in 816 real rollouts (the
# `function_call` and `custom_tool_call` names, and the keys each was
# given). `exec` runs JavaScript (see THE TOOLS AN EXEC SCRIPT CALLS);
# `exec_command` runs `cmd` in `workdir`; `apply_patch` answers with which
# files it changed; `view_image` shows one file. The rest -- sub-agents,
# `wait`, `write_stdin`, `js`, `update_plan`, MCP tools -- are withheld.
EXEC_TOOL = "exec"
EXEC_COMMAND_TOOL = "exec_command"
APPLY_PATCH_TOOL = "apply_patch"
VIEW_IMAGE_TOOL = "view_image"
EXEC_COMMAND_KEYS = frozenset({
    "cmd", "workdir", "yield_time_ms", "max_output_tokens", "login", "tty",
    "justification", "sandbox_permissions", "prefix_rule"})
VIEW_IMAGE_KEYS = frozenset({"path", "detail"})
# The older function-call form of apply_patch (this template's fixtures).
APPLY_PATCH_KEYS = frozenset({"patch", "input"})
# Host names that are this machine or its network: a web tool pointed at
# one can read a local file through a local server. ACCEPTED RESIDUAL: a
# public name that resolves to such an address.
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan",
                        ".home", ".arpa")
_NUMERIC_HOST_LABEL_RE = re.compile(r"0x[0-9a-f]*|\d+")
# Every tool name the Codex rules below recognize.
CODEX_TOOLS = frozenset({EXEC_TOOL, EXEC_COMMAND_TOOL, APPLY_PATCH_TOOL,
                         VIEW_IMAGE_TOOL})
# Set on our normalized `tool_start` events: whether the call's output may
# be kept, decided on the event before its paths were rendered. The
# normalized tier is redacted again on every pass, when a rendered path
# (`cd /project/src` is now `cd src`) no longer proves what it proved.
# ACCEPTED RESIDUAL: `.ai-traces/<adapter>/normalized/` is not gitignored,
# so anything that can write in the project can add a made-up
# `tool_start`/`tool_result` pair carrying `output_kept: true`, and later
# passes trust it. That is the same as fabricating any other trace entry.
CALL_OUTPUT_KEPT_KEY = "output_kept"

# What makes a script not plain (see above). Checked on the raw text, and
# -- for the characters that are harmless inside quotes -- outside quotes.
_UNPLAIN_ANYWHERE = frozenset("$`~\\")
_UNPLAIN_UNQUOTED_RE = re.compile(r"[*?\[\]{}()#!'\"]|<<")
_SHELL_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_SEGMENT_SEP_RE = re.compile(r"&&|\|\||(?<![<>])&(?!>)|[|;\n]")
# A redirection word: an optional stream number (or `&`), the operator,
# and the target when it is written in the same word.
_REDIRECT_RE = re.compile(r"(\d?|&)(<|>>?)(.*)", re.DOTALL)
_STREAM_COPY_RE = re.compile(r"&\d")          # `2>&1`, `>&2`

# STREAM DEVICES hold no file content of their own: a command that writes
# to /dev/null or reads /dev/stdin reads nothing from any file by doing so.
# They never taint a call (review round 4; in 663 real rollouts, 23 calls
# were tainted by /dev/null alone). Any other device -- and any path that
# merely starts like one -- is classified like every other path.
_STREAM_DEVICE_RE = re.compile(r"/dev/(?:null|stdin|stdout|stderr|tty|fd/\d+)")


def _is_stream_device(path: str) -> bool:
    return bool(_STREAM_DEVICE_RE.fullmatch(path))


# READS OF THE CAPTURE LAYER'S OWN FILES, AND OF GIT'S (review round 4).
# `.ai-traces` holds unredacted transcripts until this pass rewrites them
# (raw/, normalized/, the interaction stream), and `.git` can hold
# credentials (a remote URL with a token in it). Both are inside the
# project, and their paths are still rendered like any project path -- the
# record names them -- but READING them is not reading the student's
# submitted work, so such a read counts as a read outside the project.
# The whole directory counts, not only its unredacted parts, so a directory
# read (`rg KEY .ai-traces`) cannot slip past, and names are compared
# ignoring case, as the default macOS and Windows file systems do. Aider's
# history files (AIDER_SOURCES) are raw transcripts at the project root.
PROTECTED_READ_DIRS = (ai_traces.AI_TRACES_DIR, ".git")


# FILES A SNAPSHOT NEVER COMMITS (review round 5). Each adapter lists the
# files git_ops drops from the index after staging (`unstage_after`):
# Codex's login token `.codex/auth.json`, Aider's raw histories. Reading
# one is a protected read too, whether or not the project's .gitignore
# lists it -- a student may have removed that line. So is reading a folder
# that holds one (`rg token .codex`: rg searches a hidden folder it is
# given), except the project root, whose hidden folders a search skips.
@functools.lru_cache(maxsize=1)
def unstaged_paths() -> FrozenSet[str]:
    """The files the registered adapters drop from every snapshot, in
    lower case."""
    # Imported here: the adapters import this module.
    from tests._capture.agent_adapters import registry
    return frozenset(path.lower().strip("/")
                     for adapter in registry._REGISTERED
                     for path in adapter().unstage_after())


def _is_protected_read(rendered: str) -> bool:
    """True for a repo-relative path a read must not reach (see above)."""
    lowered = rendered.lower().strip("/")
    return (any((lowered + "/").startswith(name + "/")
                for name in PROTECTED_READ_DIRS)
            or lowered in {name.lower() for name in AIDER_SOURCES}
            or any(lowered and (path + "/").startswith(lowered + "/")
                   for path in unstaged_paths()))

# Claude Code content blocks.
CLAUDE_CALL_TYPE = "tool_use"
CLAUDE_OUTPUT_TYPE = "tool_result"
# Row-level Claude field holding the structured result of the tool_result
# block in the same row.
CLAUDE_ROW_RESULT_KEY = "toolUseResult"

# Claude Code ATTACHMENTS are context Claude Code adds to a conversation on
# its own (decision 35e, see INJECTED_CONTEXT). Measured over 2,135 real
# sessions: 66,379 attachment rows of 45 types, among them the CLAUDE.md
# files it loaded (`instructions`: 1,058 of 1,827 files are the user's
# global ~/.claude/CLAUDE.md), the whole system prompt (`prompt_snapshot`),
# skill listings, hook output, IDE selections, the user's email address
# (`session_context`), and -- on 33,579 rows -- a `rendered` text copy.
# So an attachment keeps its type, its numbers and flags, and its path
# fields (rendered as paths); everything else is withheld, and so is
# `rendered`. Two types are the student's own:
#   * `queued_command` -- a prompt typed while the agent was busy; kept
#     unless another agent queued it (`isMeta`, or an `origin` other than
#     `{"kind": "human"}`);
#   * `edited_text_file` -- a note that the student's file changed; its
#     snippet follows the edit-record rule (kept only for an in-project file).
# A `user` row marked `isMeta` is Claude Code's text as well (skill bodies,
# command caveats, system reminders: 1,096 rows), so its text is withheld.
CLAUDE_ATTACHMENT_ROW = "attachment"
CLAUDE_RENDERED_KEY = "rendered"
CLAUDE_META_KEY = "isMeta"
CLAUDE_QUEUED_PROMPT = "queued_command"
CLAUDE_QUEUED_PROMPT_KEYS = frozenset({
    "type", "prompt", "commandMode", "timestamp", "source_uuid", "origin"})
CLAUDE_TYPED_ORIGIN = {"kind": "human"}
CLAUDE_EDIT_ATTACHMENTS = frozenset({"edited_text_file"})

# CLAUDE CODE TEXT NOBODY TYPED, OUTSIDE `isMeta` (review round 4). Claude
# Code has no separate record of what the student typed, so a user row is
# judged by what it says about itself. Measured over 2,149 real sessions
# (107,135 user rows):
#   * rows the tool marks: `isMeta`, a compaction summary
#     (`isCompactSummary`, 8 rows, always with `isVisibleInTranscriptOnly`),
#     a row of a sub-agent's conversation (`isSidechain`, whose prompts
#     another agent wrote -- review round 5; attachments included),
#     an `origin` other than a human one (1,557 background-task
#     notifications, 312 messages from other agents), a `promptSource` of
#     "system";
#   * text the tool wraps in markup: the same task notifications
#     (`<task-notification>`, 1,556), and -- in rows it does not mark at
#     all -- the output of a `/` command (`<local-command-stdout>`, 73) or
#     of a `!` command (`<bash-stdout>`, 36), and sub-agent boilerplate
#     (`<fork-boilerplate>`, 5).
# A typed prompt never started with a markup tag in the data -- except the
# two wrappers that hold what the student typed: the `!` command
# (`<bash-input>`, 36) and the `/` command (`<command-name>`, 333). So text
# that starts with a tag is kept only when it is exactly one of those; any
# other wrapper, known or new, is withheld. Pasted HTML that starts with a
# tag is withheld too -- an accepted cost, disclosed in AI_POLICY.md.
#
# The same wrappers turn up in two more places: `queue-operation` rows (a
# prompt waiting its turn, a task notification -- 4,344 -- or another
# agent's `<agent-message>` -- 187) and `system` rows (`/` command output:
# 161). A queue row says nothing about who wrote it, so -- like a Codex
# message -- its text is kept only when it joins a prompt the student typed
# in the same transcript. A `/` command waits there as the line the student
# typed (`/review 12`), so each typed command's line counts as typed too.
# Measured: 701 of the 716 queue texts without markup join; 4 of the other
# 15 are other agents' messages.
CLAUDE_USER_ROW = "user"
CLAUDE_QUEUE_ROW = "queue-operation"
CLAUDE_SYSTEM_ROW = "system"
CLAUDE_SIDECHAIN_KEY = "isSidechain"
CLAUDE_TOOL_ROW_FLAGS = ("isCompactSummary", "isVisibleInTranscriptOnly",
                         CLAUDE_SIDECHAIN_KEY)
CLAUDE_TYPED_PROMPT_SOURCES = frozenset({
    "typed", "queued", "sdk", "suggestion_accepted"})
# The normalized events claude_normalize builds from a typed `user` row.
CLAUDE_TYPED_EVENT_TYPES = frozenset({"user_prompt", "slash_command"})
# Queued prompts: "prompt" is the only mode a typed one had (39 rows); a
# queued task notification says "task-notification" (774 rows).
CLAUDE_TYPED_COMMAND_MODES = frozenset({"prompt"})
# A `system` row's subtype whose text is command output, apart from the
# echo of the command the student typed.
CLAUDE_LOCAL_COMMAND = "local_command"
# Set by claude_normalize on each event it builds from a `user` or `system`
# row: whether that row's text may be kept, decided on the raw row. The
# normalized tier is redacted again on every pass, and by then the paths in
# its text are rendered, so a later pass trusts the decision rather than
# the text.
CLAUDE_TEXT_KEPT_KEY = "text_kept"
_MARKUP_START_RE = re.compile(r"\s*<[A-Za-z]")

# CLAUDE CODE ROWS BESIDE THE CONVERSATION -- DEFAULT DENY, per field
# (review round 5). Measured over 2,159 real sessions, besides `user`,
# `assistant`, `attachment` and the undo checkpoints: 9,519 `last-prompt`,
# 8,455 `mode`, 7,799 `atis-latch`, 7,654 `queue-operation`, 6,269
# `ai-title`, 5,307 `bridge-session`, 3,993 `system` rows of ten subtypes,
# 2,589 `permission-mode`, 1,046 `custom-title`, 314 `frame-link`, 254
# `pr-link`, 204 `cost-state`, 149 `artifact-autoreact-ledger`, 125
# `agent-name`, 101 `artifact-comment-monitor` and one `continued-in`.
# Each is described field by field, like the Codex rows: any other field
# is withheld, key included, and a row type nobody measured keeps only its
# identity (`apply_claude_row_rules`).
#
# Kept: ids, settings, numbers, paths. Withheld as not project content:
# hook commands and their output, API error text, a scheduled task's
# prompt, the titles and names Claude Code makes up, account and
# organization ids, links to other repositories and to frames, cost
# details. Text that may be the student's own is decided with the rest of
# the conversation (`Redactor.withhold_injected_context`): a queued or last
# prompt, and a `/` command's arguments, only when the student typed them;
# a system row's text only for an `away_summary` (the model's recap) or the
# echo of a typed command.
_CLAUDE_ROW_IDENTITY = {"type": "name", "uuid": "uuid", "sessionId": "uuid",
                        "timestamp": "timestamp", "cwd": PATH}
_CLAUDE_SYSTEM_ROW = {
    **_CLAUDE_ROW_IDENTITY,
    "subtype": "snake", "parentUuid": "uuid", "logicalParentUuid": "uuid",
    "entrypoint": "name", "gitBranch": "branch", "version": "version",
    "userType": "name", "isMeta": SCALAR, "isSidechain": SCALAR,
    "level": "name", "slug": "name", "sessionKind": "name",
    "agentId": "id", "requestId": "id", "toolUseID": "id", "content": TEXT,
    # turn_duration
    "durationMs": SCALAR, "messageCount": SCALAR,
    "pendingBackgroundAgentCount": SCALAR, "pendingWorkflowCount": SCALAR,
    # stop_hook_summary: the hooks' commands and what they printed
    "hasOutput": SCALAR, "hookCount": SCALAR,
    "preventedContinuation": SCALAR, "hookInfos": HIDDEN,
    "hookErrors": HIDDEN, "hookAdditionalContext": HIDDEN,
    "stopReason": HIDDEN,
    # local_command: the `/` command the student ran
    "commandRun": {"command": "name", "args": TEXT},
    # compact_boundary
    "compactMetadata": {
        "trigger": "snake", "preTokens": SCALAR, "postTokens": SCALAR,
        "durationMs": SCALAR, "cumulativeDroppedTokens": SCALAR,
        "preCompactDiscoveredTools": ListOf("id"),
        "preservedMessages": {"anchorUuid": "uuid",
                              "uuids": ListOf("uuid"),
                              "allUuids": ListOf("uuid")},
        "preservedSegment": {"anchorUuid": "uuid", "headUuid": "uuid",
                             "tailUuid": "uuid"}},
    # api_error: its text may quote the request
    "error": {"formatted": HIDDEN, "message": HIDDEN, "rateLimits": HIDDEN,
              "isNetworkDown": SCALAR, "status": SCALAR,
              "connection": {"code": "id", "isSSLError": SCALAR,
                             "message": HIDDEN}},
    "maxRetries": SCALAR, "retryAttempt": SCALAR, "retryInMs": SCALAR,
    "source": "snake",
    # scheduled_task_fire
    "prompt": HIDDEN, "cron": HIDDEN, "cronKind": "snake", "taskId": "id",
    "taskKind": "snake",
    # model_refusal_fallback
    "apiRefusalCategory": "snake", "apiRefusalExplanation": HIDDEN,
    "direction": "snake", "scope": "snake", "trigger": "snake",
    "fallbackModel": "name", "originalModel": "name",
    "refusedUserMessageUuid": "uuid",
    "retractedMessageUuids": ListOf("uuid"),
}
CLAUDE_LAST_PROMPT_ROW = "last-prompt"
CLAUDE_ROW_SCHEMAS: Mapping[str, Any] = {
    "system": _CLAUDE_SYSTEM_ROW,
    "queue-operation": {**_CLAUDE_ROW_IDENTITY, "operation": "camel",
                        "reason": "snake", "content": TEXT},
    CLAUDE_LAST_PROMPT_ROW: {**_CLAUDE_ROW_IDENTITY, "leafUuid": "uuid",
                             "lastPrompt": TEXT},
    "mode": {**_CLAUDE_ROW_IDENTITY, "mode": "camel"},
    "permission-mode": {**_CLAUDE_ROW_IDENTITY, "permissionMode": "camel"},
    "continued-in": {**_CLAUDE_ROW_IDENTITY, "continuedInSessionId": "uuid"},
    "bridge-session": {**_CLAUDE_ROW_IDENTITY, "bridgeSessionId": "id",
                       "lastSequenceNum": SCALAR, "ownerAccountUuid": HIDDEN,
                       "ownerOrganizationUuid": HIDDEN},
    "pr-link": {**_CLAUDE_ROW_IDENTITY, "prNumber": SCALAR,
                "prRepository": HIDDEN, "prUrl": HIDDEN},
    "frame-link": {**_CLAUDE_ROW_IDENTITY, "artifactCount": SCALAR,
                   "path": PATH, "frameUrl": HIDDEN, "title": HIDDEN},
    "cost-state": {**_CLAUDE_ROW_IDENTITY, "modelUsage": HIDDEN, **{
        name: SCALAR for name in (
            "hasUnknownModelCost", "startTime", "totalCostUSD",
            "totalAPIDuration", "totalAPIDurationWithoutRetries",
            "totalDuration", "totalToolDuration", "totalLinesAdded",
            "totalLinesRemoved")}},
    "artifact-autoreact-ledger": {**_CLAUDE_ROW_IDENTITY, "v": SCALAR,
                                  "accountUuid": HIDDEN, "artifacts": HIDDEN},
    "artifact-comment-monitor": {**_CLAUDE_ROW_IDENTITY, "v": SCALAR,
                                 "artifacts": HIDDEN},
    **{kind: {**_CLAUDE_ROW_IDENTITY, name: HIDDEN} for kind, name in (
        ("ai-title", "aiTitle"), ("custom-title", "customTitle"),
        ("agent-name", "agentName"), ("atis-latch", "atis"))},
}
# The conversation's own rows, which have rules of their own (see
# CLAUDE_ATTACHMENT_ROW and CLAUDE CODE TEXT NOBODY TYPED).
CLAUDE_CONVERSATION_ROWS = frozenset({"user", "assistant", "attachment"})
# The only system rows whose text may be kept besides a command's echo.
CLAUDE_MODEL_TEXT_SUBTYPES = frozenset({"away_summary"})
_TYPED_BASH_RE = re.compile(
    r"\s*<bash-input>(?:(?!</bash-input>).)*</bash-input>\s*", re.DOTALL)
# A `/` command: its name and the tool's short message about it, in either
# order, and the arguments the student typed.
_TYPED_COMMAND_RE = re.compile(
    r"\s*(?:(?:<(command-name|command-message)>[\w:./ -]{0,80}</\1>"
    r"|<command-args>(?:(?!</command-args>).)*</command-args>)\s*)+",
    re.DOTALL)
_COMMAND_PART_RE = re.compile(r"<(command-name|command-args)>(.*?)</\1>",
                              re.DOTALL)
_ORIGIN_KIND_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")

# SESSIONS ANOTHER AGENT STARTED (review round 4). Codex runs sub-agents and
# approval reviewers as sessions of their own and records the prompt it
# gives them as a `user_message` event and a UserMessage item -- the records
# `typed_texts` reads as typed. An approval reviewer's prompt is a copy of
# the conversation, command output included (221 real files). So in such a
# session nothing counts as typed, and those records are withheld too.
# Measured over 814 real rollouts (their `session_meta`):
#   * the student's own sessions state `source` as "cli", "vscode" or
#     "exec", and `thread_source` as "user" or nothing (108 files);
#   * every other session states `source` as {"subagent": ...} and
#     `thread_source` as "subagent" or "guardian_review" (706 files); most
#     also name a parent thread, an agent role, path or nickname.
# A session_meta with no `source` at all (older rollouts, and this
# template's fixtures) is the student's unless it names a parent or an
# agent. Any other value means another agent's session.
#
# A FORK CHILD (review round 5) -- a sub-agent that starts from the
# student's conversation -- copies the parent's session_meta after its own
# (6 of 814 real rollouts). Every record in such a file is still the
# sub-agent's, so a file with any other agent's session_meta is another
# agent's file, whatever session a later record names.
#
# HOOK LOGS NAME NO SESSION SOURCE (review round 5). The Codex hooks log a
# UserPromptSubmit for every session that runs in the project, sub-agents
# and approval reviewers included, and the payload does not say which kind
# of session it is. So a hook prompt counts as typed only when the session's
# own rollout, ingested into this project's `.ai-traces/codex/raw/rollouts/`,
# shows it is the student's: a file whose first session_meta names that
# session (a rollout's own session comes first) and whose every
# session_meta is the student's. THE HOOK LOG DEPENDS ON THE ROLLOUT: while
# the rollout is not there, the prompt is withheld, and the hook log's
# redacted copy is made again once it is (see `_redact_file_cached`).
STUDENT_SESSION_SOURCES = frozenset({"cli", "vscode", "exec"})
STUDENT_THREAD_SOURCES = frozenset({"user"})
AGENT_SESSION_KEYS = ("parent_thread_id", "agent_role", "agent_path",
                      "agent_nickname")

# Claude Code's undo checkpoints. Real sessions interleave these rows with
# the conversation, and they have their own shape -- no uuid, no sessionId
# (measured over 574 real sessions: 1,616 snapshot rows, 440 delta rows):
#
#   file-history-snapshot:
#     {type, messageId, isSnapshotUpdate,
#      snapshot: {messageId, timestamp, [preCheckpoint],
#                 trackedFileBackups: {<path>: <backup>}}}
#   file-history-delta:
#     {type, messageId, snapshotMessageId, timestamp,
#      trackingPath: <path>, backup: <backup>}
#   <backup> = {backupFileName, backupTime, version, realParentDir}
#
# They never quote file text, but they DO name files and point at backup
# copies of them, so they get a DEFAULT-DENY treatment of their own: only the
# fields declared below survive, and a tracked file outside the project has
# its whole backup entry withheld. `<path>` is relative to the session cwd;
# `realParentDir` is the real directory the file lives in (measured: for
# absolute keys it equals dirname(key) in 1,039 of 1,048 cases), so BOTH are
# checked and either one being external makes the entry external.
CLAUDE_FILE_HISTORY_TYPES = frozenset({
    "file-history-snapshot", "file-history-delta",
})
FILE_HISTORY_ROW_KEYS = frozenset({
    "type", "messageId", "snapshotMessageId", "isSnapshotUpdate",
    "timestamp", "snapshot", "trackingPath", "backup",
    # Set by this module when it withheld something in the row; declared so
    # that redacting an already-redacted row (normalized/ is redacted in
    # place on every snapshot) leaves it byte-identical.
    "withheld",
})
FILE_HISTORY_SNAPSHOT_KEYS = frozenset({
    "messageId", "timestamp", "trackedFileBackups", "preCheckpoint",
})
FILE_HISTORY_BACKUP_KEYS = frozenset({
    "backupFileName", "backupTime", "version", "realParentDir",
})
# Our own normalized event schema (see agent_adapters/*_normalize.py), which
# `normalized/*.jsonl` and `interaction-stream.jsonl` are written in.
NORMALIZED_CALL_TYPE = "tool_start"
NORMALIZED_OUTPUT_TYPE = "tool_result"

# Where a call record keeps what it asked for (and so may state the
# directory it ran in, see `_call_workdirs`).
INPUT_FIELDS = ("input", "arguments", "command", "cmd", "tool_input",
                "parsed_cmd", "invocation", "params")
# Join keys, most specific first.
JOIN_KEYS = ("call_id", "tool_use_id", "toolu_id", "request_id", "id")

# What survives in a WITHHELD output record. Scoped to output records only
# (contrast the deleted whole-record sweep): enough skeleton to prove the
# call happened and to join it back to its invocation, nothing that can
# carry a file's bytes.
OUTPUT_SKELETON_KEYS = frozenset({
    "type", "call_id", "tool_use_id", "request_id", "id", "name",
    "tool_name", "exit_code", "status", "success", "is_error", "aborted",
    "duration", "duration_ms", "duration_seconds", "timestamp", "ts",
    # Normalized-schema IDENTITY. A withheld record must stay ATTRIBUTABLE:
    # the attribution pass (cyq.6) consumes these records, and a stub it
    # cannot identify is a gap it cannot label. None of these can carry a
    # file's bytes.
    "event_type", "event_id", "adapter_name", "session_id", "turn_id",
    "cwd", "evidence_refs",
    # `changes` is genuinely keyed BY path (patch_apply_end), so provenance
    # IS knowable there and it keeps its per-entry handling.
    "changes",
})
# A record of a type we do not recognize keeps its identity only. Not even
# `changes`: its in-project entries are file bodies, and nobody has checked
# what an unknown record means by them.
UNRECOGNIZED_SKELETON_KEYS = OUTPUT_SKELETON_KEYS - {"changes"}

# EDIT RECORDS. An edit invocation names exactly ONE target file and carries
# that file's old/new text in the same object, so -- like an `apply_patch`
# blob -- which file the text belongs to is readable from the record itself.
# Shapes covered (all verified against the hook/transcript code that writes
# them):
#
#   Cursor afterFileEdit payload   {file_path, edits: [{old_string, new_string}]}
#   Claude Edit / Write input      {file_path, old_string, new_string | content}
#   Claude MultiEdit input         {file_path, edits: [...]}
#   Claude NotebookEdit input      {notebook_path, new_source, ...}
#   Claude Edit/Write result       {filePath, oldString, newString, content, ...}
#   our own file_edit records      {file, content_sha256, ...extents}
#   Claude edited_text_file attachment
#                                  {type, filename, snippet, [displayPath]}
#
# A record is external if ANY of its target keys names an outside file, and
# a target we cannot read as a path (None, a number, a blank string, a
# container holding no strings) FAILS CLOSED -- see `_target_is_external`.
#
# When the target is outside the project, the record keeps ONLY the keys in
# EDIT_SKELETON_KEYS (ids, names, paths as sentinels, counts) and every other
# key is withheld -- declared-safe metadata in, everything else out, so a
# new text-bearing field cannot slip through.
EDIT_TARGET_KEYS = ("file_path", "filePath", "notebook_path", "target_file",
                    "path", "filename", "file")
EDIT_TEXT_KEYS = frozenset({
    # Deliberately NOT the bare key "text": prompt-shaped records use it,
    # and prompts are never withheld (decision rule 2).
    "old_string", "new_string", "content", "edits", "new_source",
    "old_source", "file_text", "patch", "diff", "oldString", "newString",
    "originalFile", "structuredPatch", "old_str", "new_str",
    # A hash of an outside file's new text lets anyone confirm a guess at
    # that text, so it is content for this purpose.
    "content_sha256",
    # Claude Code's `edited_text_file` attachment quotes part of the changed
    # file here (measured: 366 real rows, always a string beside `filename`).
    "snippet",
})
EDIT_SKELETON_KEYS = frozenset({
    "adapter_name", "captured_at", "conversation_id", "generation_id",
    "hook_event_name", "session_id", "tool_use_id", "tool_name", "name",
    "id", "type", "event_type", "modality", "model", "cursor_version",
    "timestamp", "ts", "cell_id", "cell_type", "edit_mode", "replace_all",
    "edit_count", "old_extent_chars", "new_extent_chars", "extent_chars",
    "user_email", "withheld",
}) | PATH_KEYS

# Path-shaped-but-not-plainly-absolute forms a stated directory, a patch
# header or an edit target can use to name a location outside the project
# (`~/x`, `$HOME/x`, `%USERPROFILE%\x`). Each is expanded, resolved and then
# classified by the same path classifier everything else uses. An env var
# we cannot resolve makes the target unknowable, so it FAILS CLOSED rather
# than being assumed in-project. (A shell script with `~` or `$` in it is
# not plain, so its output is withheld before any of this.)
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)|%([A-Za-z_]\w*)%")
# Env vars whose value we DO know, so they resolve rather than fail closed.
_HOME_VARS = frozenset({"HOME", "USERPROFILE", "HOMEPATH"})

# WHERE A CODEX EXEC SCRIPT RUNS ITS COMMANDS -- PRESENCE-BASED, FAIL CLOSED.
#
# The `exec` tool's input is JavaScript. A command runs in the `workdir` its
# `tools.exec_command({...})` call states, else in the turn's directory. Two
# review rounds hid a workdir from a reader that tried to follow the
# grammar: a variable, a template literal, a comment between key and colon,
# a computed or spread key, a property assigned later. So the reader no
# longer tries to understand the code. It accepts ONE plain shape and treats
# anything else it SEES as "directory unknown":
#
#   * `workdir` anywhere except as a top-level `workdir: "<plain literal>"`
#     pair of an exec_command object -- counted in the RAW text too, since
#     quotes inside comments can fool the masking below;
#   * a spread (`...`), a computed key (`{[k]: v}`), an assignment through
#     brackets (`o[k] = v`), or a dynamic-object helper (`Object.assign`);
#   * `tools` used any way other than `tools.<name>({...})`;
#   * anything `_js_mask` cannot read: an unterminated string, a template
#     literal that interpolates, a `/` outside every string literal (a
#     comment, a regular expression or a division: a quote inside one would
#     mislead the masking), or a backslash outside every string literal.
#     JavaScript lets an escape
#     spell an identifier (`w\u006frkdir` is `workdir`), so a word count
#     cannot see through one (round-3 review).
#
# String literals are masked first, so text INSIDE a command or a patch
# body is not read as code. A call with no workdir -- or any other tool
# call -- runs in the enclosing directory (INHERITED_DIR). Measured over
# 9,366 real exec scripts: 62 of the 88 with a relative patch header are in
# the plain shape.
#
# The runtime is documented by Codex itself (the `exec` tool description in
# Codex CLI 0.154): "a fresh V8 isolate ... no Node, no file system, no
# network access". So a script reaches files only through `tools.*`, and
# the only data it can print besides its own calls' results is what
# `load(key)` returns -- a value an EARLIER exec call stored -- and the
# `ALL_TOOLS` list (JS_STORED_DATA_RE).
_JS_PRAGMA_RE = re.compile(r"\A[ \t]*// @exec:[^\n]*(?:\n|\Z)")
JS_STORED_DATA_RE = re.compile(r"(?<![\w$.])(?:load|ALL_TOOLS)(?![\w$])")
_JS_DYNAMIC_RE = re.compile(
    r"\.\.\."                            # spread
    r"|[{,]\s*\["                        # computed key
    r"|\]\s*=(?![=>])"                   # assignment through brackets
    r"|__proto__|(?<![\w$])(?:Object|Reflect|globalThis)\s*[.\[]"
    r"|(?<![\w$.])(?:eval|Function|with)\s*\("
    # `[].constructor.constructor` is Function: it reaches the global
    # object, and through it `load` by any name.
    r"|(?<![\w$])constructor(?![\w$])")
_JS_TOOLS_WORD_RE = re.compile(r"(?<![\w$])tools(?![\w$])")
_JS_TOOL_CALL_RE = re.compile(
    r"(?<![\w$])tools\s*\.\s*([A-Za-z_$][\w$]*)\s*\(\s*(\{)?")
_JS_CLEAN_WORKDIR_RE = re.compile(
    r"(?:(?<![\w$.])workdir|\x00(?P<key>\d+)\x00)"
    r"\s*:\s*\x00(?P<value>\d+)\x00\s*(?=,|$)")
# Worth reading as an exec script: it mentions `workdir`, or calls a tool.
_JS_SCRIPT_HINT_RE = re.compile(
    r"workdir|(?<![\w$])tools\s*(?:\.\s*[A-Za-z_$][\w$]*\s*\(|\[)")
# The only escapes a plain literal may contain and still be read as a path.
_JS_SIMPLE_ESCAPES = {"\\": "\\", '"': '"', "'": "'", "/": "/"}
# Stand-ins that can never be real paths (they start with a NUL byte).
# `Redactor.resolve_dir` maps UNREADABLE_DIR to None -- "unknown, fail
# closed" -- and INHERITED_DIR to the enclosing directory.
UNREADABLE_DIR = "\x00unreadable"
INHERITED_DIR = "\x00inherited"


def _js_string_value(literal: str) -> Optional[str]:
    """The value of a plain '...' or "..." literal, or None for anything
    else (a template literal, or an escape other than \\\\ \\" \\' \\/)."""
    if len(literal) < 2 or literal[0] not in "\"'":
        return None
    out: List[str] = []
    body = literal[1:-1]
    i = 0
    while i < len(body):
        if body[i] != "\\":
            out.append(body[i])
            i += 1
            continue
        escaped = body[i + 1] if i + 1 < len(body) else ""
        if escaped not in _JS_SIMPLE_ESCAPES:
            return None
        out.append(_JS_SIMPLE_ESCAPES[escaped])
        i += 2
    return "".join(out)


def _js_object_top_level(masked: str, start: int) -> Optional[str]:
    """The inside of the `{...}` that opens at `start`, with everything
    nested deeper blanked out; None if it never closes."""
    depth = 0
    kept: List[str] = []
    for ch in masked[start:]:
        if ch in "{[(":
            depth += 1
            kept.append(ch if depth == 1 else "\x01")
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                return "".join(kept[1:])
            kept.append("\x01")
        else:
            kept.append(ch if depth == 1 else "\x01")
    return None


def _js_mask(source: str) -> Optional[Tuple[str, List[str]]]:
    """(`source` with each string literal replaced by `\\x00<n>\\x00`, the
    literals), or None when the text cannot be read that way.

    Read left to right, the way JavaScript reads it, so a quote inside a
    string does not end it. Comments, regular expressions and division all
    start with a `/`, and a quote inside a comment or a regular expression
    would mislead the masking (a backquote in a comment can hide the code
    up to the next one), so a `/` outside a string literal makes the text
    unreadable -- except in the leading `// @exec:` pragma, which Codex
    reads itself. So do a backslash outside a string, an unterminated or
    multi-line quoted string, and a template literal that interpolates
    (`${...}` is code). A NUL character would clash with the stand-ins.
    """
    if "\x00" in source or "\x01" in source:
        return None
    pragma = _JS_PRAGMA_RE.match(source)
    text = source[pragma.end():] if pragma else source
    literals: List[str] = []
    out: List[str] = []
    position = 0
    while position < len(text):
        ch = text[position]
        if ch in "'\"`":
            end = position + 1
            while end < len(text) and text[end] != ch:
                if text[end] == "\\":
                    end += 1  # an escape: the next character is taken as is
                elif ch == "`" and text.startswith("${", end):
                    return None
                elif ch != "`" and text[end] in "\r\n":
                    return None
                end += 1
            if end >= len(text):
                return None
            literals.append(text[position:end + 1])
            out.append(f"\x00{len(literals) - 1}\x00")
            position = end + 1
        elif ch in "/\\":
            return None
        else:
            out.append(ch)
            position += 1
    return "".join(out), literals


def js_stated_workdirs(source: str) -> List[str]:
    """Where the commands of a Codex exec script run (see above).

    Returns one entry per tool call: a stated directory, INHERITED_DIR for a
    call that states none, or -- for anything the plain shape does not
    cover -- the single entry UNREADABLE_DIR. The hooks in
    `.codex/hooks/_common.py` carry a copy (they may not import this
    package); it masks strings with a regular expression, which only
    matters for the edit records they write.
    """
    read = _js_mask(source)
    if read is None:
        return [UNREADABLE_DIR]
    masked, literals = read
    if _JS_DYNAMIC_RE.search(masked):
        return [UNREADABLE_DIR]
    calls = list(_JS_TOOL_CALL_RE.finditer(masked))
    if len(calls) != len(_JS_TOOLS_WORD_RE.findall(masked)):
        return [UNREADABLE_DIR]  # `tools` is used some other way
    found: List[str] = []
    accounted = 0
    for call in calls:
        if call.group(1) != EXEC_COMMAND_TOOL:
            found.append(INHERITED_DIR)
            continue
        body = (_js_object_top_level(masked, call.end() - 1)
                if call.group(2) else None)
        if body is None:
            return [UNREADABLE_DIR]  # the options are not an inline object
        pairs = [pair for pair in _JS_CLEAN_WORKDIR_RE.finditer(body)
                 if pair.group("key") is None or _js_string_value(
                     literals[int(pair.group("key"))]) == "workdir"]
        if not pairs:
            found.append(INHERITED_DIR)
        for pair in pairs:
            value = _js_string_value(literals[int(pair.group("value"))])
            accounted += 1 + (value.count("workdir") if value else 0)
            found.append(value.strip() if value and value.strip()
                         else UNREADABLE_DIR)
    if source.count("workdir") != accounted:
        return [UNREADABLE_DIR]  # a `workdir` the plain shape does not explain
    return found


# THE TOOLS AN EXEC SCRIPT CALLS, read the same presence-based way as its
# workdirs: each `tools.<name>(...)`, and -- for an inline `{...}` argument
# -- its options. Measured over 7,471 real exec scripts: 6,505 of 6,559
# exec_command calls state `cmd` as one string literal. For any other shape
# -- a variable, a joined string, a template literal with `${...}` -- the
# command is only known when the script runs, so it cannot be checked.
_JS_PROPERTY_RE = re.compile(
    r"\s*(?:(?P<name>[A-Za-z_$][\w$]*)|\x00(?P<key>\d+)\x00)"
    r"\s*(?::\s*(?P<value>.*?))?\s*", re.DOTALL)
_JS_LITERAL_RE = re.compile(r"\x00(\d+)\x00")
_JS_ESCAPED_CHARS = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                     "v": "\v", "0": "\0"}
_JS_LINE_CONTINUATION_RE = re.compile(r"\\(?:\r\n|[\r\n\u2028\u2029])")


class JsToolCall(NamedTuple):
    """One `tools.<tool>(...)` call of an exec script.

    options: for an inline `{...}` argument, each key and its text -- None
             when the value is not a plain string (a number, a variable, an
             array); None altogether for any other argument.
    """
    tool: str
    options: Optional[Dict[str, Optional[str]]]


class JsScript(NamedTuple):
    """What `js_script` read: the calls, and the code with strings masked."""
    calls: List[JsToolCall]
    code: str


def js_script(source: str) -> Optional[JsScript]:
    """The tool calls of a Codex exec script (see above), or None when the
    script cannot be read without running it."""
    read = _js_mask(source)
    if read is None:
        return None
    masked, literals = read
    if _JS_DYNAMIC_RE.search(masked):
        return None
    matches = list(_JS_TOOL_CALL_RE.finditer(masked))
    if len(matches) != len(_JS_TOOLS_WORD_RE.findall(masked)):
        return None  # `tools` is used some other way
    calls: List[JsToolCall] = []
    for match in matches:
        options: Optional[Dict[str, Optional[str]]] = None
        if match.group(2):
            body = _js_object_top_level(masked, match.end() - 1)
            if body is None:
                return None
            options = {}
            for part in body.split(","):
                if not part.strip():
                    continue
                prop = _JS_PROPERTY_RE.fullmatch(part)
                if prop is None:
                    return None  # a property the plain shape does not cover
                key = prop.group("name")
                if prop.group("key") is not None:
                    key = _js_string_value(literals[int(prop.group("key"))])
                if key is None or key in options:
                    return None
                literal = _JS_LITERAL_RE.fullmatch(prop.group("value") or "")
                options[key] = (
                    _js_literal_text(literals[int(literal.group(1))])
                    if literal else None)
        calls.append(JsToolCall(match.group(1), options))
    return JsScript(calls, masked)


def _js_literal_text(literal: str) -> Optional[str]:
    """The text a JavaScript string literal holds, or None for a template
    literal that interpolates (`${...}`)."""
    quote, body = literal[0], literal[1:-1]
    if quote == "`" and "${" in body:
        return None
    body = _JS_LINE_CONTINUATION_RE.sub("", body)

    def decode(match: "re.Match[str]") -> str:
        code = match.group(1)
        if len(code) > 1:
            return _decode_escape(match)  # \xNN, \uNNNN, \u{N}
        return _JS_ESCAPED_CHARS.get(code, code)
    return _ESCAPE_RE.sub(decode, body)


def _is_patch_text(text: str) -> bool:
    """True for an `apply_patch` input: a patch, not a shell command."""
    return text.lstrip().startswith("*** Begin Patch")


def _dict_stated_workdirs(value: Mapping[str, Any],
                          keys: Tuple[str, ...] = ("workdir", "cwd")
                          ) -> List[str]:
    """The directory a dict states under one of `keys` ([] for none).

    Only its own top-level key counts; a `workdir` anywhere else inside it
    -- a nested object, say -- makes the directory unreadable.
    """
    mentions = json.dumps(value, ensure_ascii=False, default=str).count("workdir")
    for key in keys:
        if key in value:
            own = value[key]
            accounted = ((key == "workdir")
                         + (own.count("workdir") if isinstance(own, str) else 0))
            if mentions != accounted:
                return [UNREADABLE_DIR]
            return [_readable_dir(own)]
    return [UNREADABLE_DIR] if mentions else []


# DIRECTORY CHANGES INSIDE A COMMAND -- PRESENCE-BASED, FAIL CLOSED. A patch
# applied after `cd /outside` runs outside, whatever the call's workdir says
# (review probe R2), and a round-2 review found a dozen ways to write such a
# change (`builtin cd`, `if cd`, `! cd`, `env -C`, `git -C`, ...). So any
# directory-changing word anywhere in the command makes a relative patch
# header's directory unknown -- except the one plain shape
# `cd /absolute/path && ...` at the start of the command.
#
# Windows shells count too, and they ignore case: PowerShell's
# `Set-Location`, `Push-Location`, `Pop-Location` and the `sl` alias, its
# `Invoke-Expression` (`iex`, an eval), `Start-Process -WorkingDirectory`,
# .NET's `CurrentDirectory`, and `CD`/`CHDIR`/`PUSHD` in any case.
#
# Words are looked for in the command as the shell would see them: JSON and
# JavaScript escapes decoded (`c\u0064` is `cd`), then quotes and
# backslashes removed (`c""d` is `cd`). A directory change whose word is
# only built at run time (a command name kept in a variable, or printed by
# `printf`) is an accepted residual, like the path-less commands in the
# module docstring.
_DIR_CHANGE_RE = re.compile(
    r"(?i:(?<![\w$./-])(?:cd|pushd|popd|eval|sl|iex)(?![\w$.-]))"
    r"|(?i:(?<![A-Za-z0-9_])chdir)"
    r"|(?i:(?<![\w$-])(?:set|push|pop)-location(?![\w-]))"
    r"|(?i:invoke-expression|-workingdirectory|currentdirectory)"
    r"|CDPATH|GIT_WORK_TREE|GIT_DIR"
    r"|(?<![\w-])-C"
    r"|--(?:chdir|directory|work-tree|git-dir)")
_PLAIN_CD_RE = re.compile(r"cd[ \t]+(/[A-Za-z0-9_./@+,:=%-]*)[ \t]+&&[ \t]")
_ESCAPE_RE = re.compile(
    r"\\(u\{[0-9a-fA-F]{1,6}\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_ESCAPED_CHARS = {"n": "\n", "t": "\t", "r": "\r"}


def _decode_escape(match: "re.Match[str]") -> str:
    code = match.group(1)
    if len(code) > 1 and code[0] in "ux":
        number = int(code.strip("u{}x"), 16)
        return chr(number) if number < 0x110000 else ""
    return _ESCAPED_CHARS.get(code, code)


def _shell_word_view(text: str) -> str:
    """`text` as the shell would see its words (see _DIR_CHANGE_RE)."""
    for _ in range(3):  # a JSON string inside a JS string: escapes nest
        if "\\" not in text:
            break
        text = _ESCAPE_RE.sub(_decode_escape, text)
    return re.sub(r"[\"'`\\]", "", text)


def _patch_dirs(text: str) -> Tuple[Optional[str], ...]:
    """The directories a directory change in `text` may move a patch to.

    () when there is none; (path,) for the one plain `cd /abs && ` at the
    start of the command; (None,) -- unknown -- for anything else.
    """
    view = _shell_word_view(text)
    changes = list(_DIR_CHANGE_RE.finditer(view))
    if not changes:
        return ()
    if len(changes) == 1:
        start = changes[0].start()
        before = view[:start].rstrip(" \t")
        plain = _PLAIN_CD_RE.match(view, start)
        # At the start of the text, or of a string value (`cmd: "cd ...`).
        if plain and (before == "" or before.endswith(":")):
            return (plain.group(1),)
    return (None,)


def _expand_user_and_env(token: str) -> Tuple[str, bool]:
    """Return (expanded, unresolved). `unresolved` means fail closed."""
    out = token
    if out.startswith("~/") or out == "~":
        try:
            out = str(Path.home()) + out[1:]
        except (RuntimeError, OSError):
            return out, True
    elif out.startswith("~"):
        return out, True  # `~bob/x`, `~+`: another home, or a shell variable
    unresolved = False

    def _sub(match: "re.Match[str]") -> str:
        nonlocal unresolved
        name = match.group(1) or match.group(2) or match.group(3) or ""
        if name.upper() in _HOME_VARS:
            try:
                return str(Path.home())
            except (RuntimeError, OSError):
                unresolved = True
                return match.group(0)
        unresolved = True
        return match.group(0)

    out = _ENV_VAR_RE.sub(_sub, out)
    return out, unresolved

# JSON-string-valued keys that must be parsed, redacted, and re-serialized.
# Codex encodes tool arguments this way (see
# docs/superpowers/plans/2026-04-22-codex-rollout-notes.md).
EMBEDDED_JSON_KEYS = frozenset({"arguments", "input", "tool_input", "output"})

# Extensions we mirror as plain text (scrubbed) rather than parsed JSON.
TEXT_SUFFIXES = frozenset({".md", ".txt", ".log", ".history", ".diff", ".patch"})

# Aider writes its transcripts into the repo root rather than .ai-traces/.
AIDER_SOURCES = (".aider.chat.history.md", ".aider.input.history")

# A path can appear ANYWHERE, not only as the value of a name we thought to
# allowlist. Codex's patch_apply_begin/end map edits BY path --
# ``payload.changes = {<abs path>: {...diff...}}`` (see
# docs/superpowers/plans/2026-04-22-codex-rollout-notes.md) -- so a
# key-position path carries the single most sensitive record Codex emits.
# PATH_KEYS is an allowlist over POSITIONS and is therefore a floor, not the
# mechanism: `_looks_like_path` classifies path-shaped strings wherever they
# occur, in key or value position.
_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
# Trailing-extension shape, e.g. "main.py", "notes.md". Used for KEYS only:
# a bare filename in key position is a path, but the same string in value
# position is far more likely to be ordinary prose.
# The leading `*` (not `+`) is deliberate: it matches dotfiles like ".env",
# which is precisely the gitignored-therefore-external case.
_FILENAME_RE = re.compile(r"^[^\s/\\]*\.[A-Za-z0-9]{1,8}$")

# No real filesystem path is longer than this (PATH_MAX is 4096 on Linux and
# 1024 on macOS). Anything longer that still LOOKS like a path is data --
# base64 image bytes match the path pattern, and a real 52 MB rollout held a
# 3.8 MB one. Resolving such a string on disk costs time quadratic in its
# length, so it is classified as external without touching the filesystem.
MAX_PATH_CHARS = 4096


def _looks_like_path(text: str) -> bool:
    """True for a string that IS a path (not prose that mentions one).

    Whitespace disqualifies: ``"/bin/sh -c ls"`` is a command line, and
    treating it as one path would sentinel-ize the whole thing and, worse,
    trigger content withholding on a record that touched nothing external.
    Paths containing spaces fall through to `scrub_text`'s prefix rewrite.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_PATH_CHARS:
        return False
    if stripped.startswith(EXTERNAL_PREFIX) or "://" in stripped:
        return False
    if any(ch.isspace() for ch in stripped):
        return False
    return (stripped.startswith(("/", "./", "../", "~/"))
            or bool(_WINDOWS_ABS_RE.match(stripped)))


# FILE URIs are paths, not web addresses. Codex states every command's
# `cwd` as one (all 2,358 CommandExecution items measured), and an image's
# `path`; messages link files with them too. Passed through as "a URL",
# the absolute path -- the user name -- was committed verbatim, and an
# outside working directory was not recognized as outside. So a `file://`
# URI is decoded and classified like any other path. One that names
# another host, carries a query, or cannot be decoded is not a local file
# we can check: it fails closed. `file:/abs`, with one slash, is a file URI
# too, and so is any other value that starts `file:` -- the Codex hooks
# read it the same way (review round 5); in text, only the `file:/` forms
# are looked for.
_TEXT_FILE_URI_RE = re.compile(r"(?i)file:/[^\s\"'<>()\[\]{}`|]*")


def _is_file_uri(text: Any) -> bool:
    return isinstance(text, str) and text[:5].lower() == "file:"


def _file_uri_path(uri: str) -> Tuple[Optional[str], str]:
    """(the local absolute path a file URI names, or None; its #fragment)."""
    try:
        parts = urllib.parse.urlsplit(uri.strip())
        path = urllib.request.url2pathname(parts.path)
    except (ValueError, TypeError, OSError):
        return None, ""
    if (parts.scheme.lower() != "file" or parts.query
            or parts.netloc.lower() not in ("", "localhost")
            or not os.path.isabs(path)):
        return None, ""
    return path, parts.fragment


# Paths embedded in PROSE (a prompt, a shell command, a patch body):
# absolute ones, and relative ones written `./x` or `../x`, which are read
# from the project root. The lookbehind keeps URLs intact: in "https://x/y"
# the "//x/y" is preceded by ":", so it never matches. A closing tag
# (`</b>`, `</bash-input>`) is not a path either (review round 5).
_TEXT_ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/\\])"
    r"(?!(?<=<)/[A-Za-z][\w.:-]*>)"
    r"(?:[A-Za-z]:[\\/]|\.{1,2}/|/)[A-Za-z0-9_.\-/\\~@+]+")
# One word after a slash, as a `/` command is written (`/review`).
_SLASH_WORD_RE = re.compile(r"/[A-Za-z][\w.:-]*")


def _is_slash_command(text: str) -> bool:
    """True for a `/word` that names nothing on this disk: a `/` command,
    not a path. It carries no user name, so it is left as written."""
    return bool(_SLASH_WORD_RE.fullmatch(text)) and not os.path.lexists(text)

# Edit targets that ride inside a TEXT BLOB rather than a structured field:
# Codex `apply_patch` (`*** Update File: <path>`) and ordinary unified-diff
# headers. `codex_normalize._PATCH_FILE_RE` parses the same shape.
_APPLY_PATCH_TARGET_RE = re.compile(
    r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)
# The same header ANYWHERE on a line. A script can build a patch out of
# several literals (`"*** Begin Patch\n" + `*** Delete File: ${f}\n``, the
# real shape of 2 of 794 real patch calls), so its headers need not start a
# line. Used only where line breaks are real (see `_patch_targets`).
_APPLY_PATCH_ANYWHERE_RE = re.compile(
    r"\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)
_DIFF_HEADER_TARGET_RE = re.compile(
    r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)$", re.MULTILINE)
# A unified diff names its file in a `---` line immediately followed by a
# `+++` line. Only a patch contains that pair; a lone `--- word` line is also
# ordinary prose (a Markdown rule followed by a word).
_DIFF_HEADER_PAIR_RE = re.compile(
    r"^--- (?:[ab]/)?(\S+)\n\+\+\+ (?:[ab]/)?(\S+)$", re.MULTILINE)
# A header is an ADDRESS, and only a plain one can be read without running
# the code that wrote it. Round-3 review: `*** Update File: " + f + "` (a
# JavaScript string joined to a variable) and `${path.join(d, "x.py")}` (a
# template literal) were read as relative file names inside the project, so
# an outside file's patch was committed. A header holding a quote or
# backtick, a `${...}` or `$(...)` expansion, or a JavaScript escape is an
# expression: the file it names is unknown, so the patch is withheld.
#
# Only escapes JavaScript really has count (`\x2e`, `\u002e`, `\u{2e}`), so
# a Windows path such as `C:\Users\2024\a.py` is still read as a path.
_EXPRESSION_HEADER_RE = re.compile(
    r"[\"'`]|\$[({]|\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{)")


class PatchTarget(NamedTuple):
    """One file a patch/diff text blob names as its edit target.

    certain:  True for an apply_patch header and for a `---`/`+++` header
              pair, which only a patch contains; False for a lone `---` or
              `+++` line, which is also ordinary prose.
    cd_dirs:  where a directory change in the same text may have moved the
              patch (see `_patch_dirs`): empty for none, else the plain
              `cd` target, or None for unknown. The patch may run in the
              call's own directory or in any of these.
    """
    path: str
    certain: bool
    cd_dirs: Tuple[Optional[str], ...]


def _patch_targets(text: str) -> List[PatchTarget]:
    """The files a patch/diff text blob edits (see PatchTarget)."""
    if not isinstance(text, str) or (
            "*** " not in text and "\n" not in text and "\\n" not in text):
        return []
    forms = [text]
    # Inside the Codex `exec` tool's JavaScript a patch is a string literal,
    # so its line breaks are the two characters backslash + n. Scan a copy
    # with real line breaks too (the capture hooks read patches the same
    # way). Extra matches from the copy can only withhold more, never less.
    if "\\n" in text:
        forms.append(text.replace("\\n", "\n"))
    found: List[Tuple[str, bool]] = []
    for form in forms:
        # A header in the middle of a line counts too -- but not in the
        # undecoded text, where a whole script is one "line".
        header_re = (_APPLY_PATCH_ANYWHERE_RE if form is forms[-1]
                     else _APPLY_PATCH_TARGET_RE)
        found.extend((m.group(1).strip(), True)
                     for m in header_re.finditer(form))
        paired: Set[str] = set()
        for m in _DIFF_HEADER_PAIR_RE.finditer(form):
            paired.update((m.group(1), m.group(2)))
        for m in _DIFF_HEADER_TARGET_RE.finditer(form):
            target = m.group(1).strip()
            found.append((target, target in paired))
    found = [(path, certain) for path, certain in found
             if path and path != "/dev/null"]
    # Presence-based, so it holds for every header in the text alike.
    cd_dirs = _patch_dirs(text) if found else ()
    return [PatchTarget(path, certain, cd_dirs) for path, certain in found]


def _key_looks_like_path(key: Any) -> bool:
    """Key-position detection, deliberately wider than value position.

    A mapping keyed by ``".env"`` or ``"src/main.py"`` is still an
    edit/result map. Over-detection is cheap here: an in-project relative
    path classifies back to itself unchanged, so a false positive like the
    key ``"3.11"`` is a no-op.
    """
    if not isinstance(key, str):
        return False
    stripped = key.strip()
    if not stripped or any(ch.isspace() for ch in stripped):
        return False
    if _looks_like_path(stripped):
        return True
    return "/" in stripped or bool(_FILENAME_RE.match(stripped))


# --- secret patterns ------------------------------------------------------
# Ordered; first match wins. Each entry is (kind, compiled pattern). The
# replacement keeps the KIND so a reader can see what was removed.
_SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:\\n|.)*?"
        r"-----END [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer_token", re.compile(
        r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*"
        r"(?:bearer|token|basic)\s+\S+")),
    # `NAME_WITH_TOKEN = value`. Written to run in LINEAR time, because it
    # runs over every string of every transcript on every snapshot:
    #   (?<![...])  start only at the beginning of an identifier run, not at
    #               every word boundary inside one (`a-b-c-...`);
    #   (?=...KW)   check once, by lookahead, that the run contains a keyword;
    #   ([...]+)    then take the run itself, with no keyword alternation
    #               inside it to backtrack through.
    # The earlier form, `\b([A-Z0-9_\-]*KW[A-Z0-9_\-]*)`, was quadratic in
    # the length of such runs. Benchmark on real Codex rollouts (this
    # pattern alone):
    #   11 MB: 2.1s -> 0.95s      25 MB: 10.9s -> 1.8s
    # and on a 12 KB `a-a-a-...` string, about 5s -> milliseconds. Output
    # was byte-identical to the old pattern on all three files measured.
    # Whole-file timings are recorded at RedactionCache.
    ("credential_assignment", re.compile(
        r"(?i)(?<![A-Z0-9_\-])"
        r"(?=[A-Z0-9_\-]*?(?:api[_\-]?key|secret|token|password|passwd|"
        r"access[_\-]?key))"
        r"([A-Z0-9_\-]+)\s*[:=]\s*"
        r"(['\"]?)([A-Za-z0-9_\-+/=\.]{12,})\2")),
]


def _replace_credential_assignment(match: "re.Match[str]") -> str:
    return f"{match.group(1)}={match.group(2)}<redacted:credential>{match.group(2)}"


def scrub_secrets(text: str) -> str:
    """Replace credential-shaped substrings. Never shortens surrounding prose."""
    for kind, pattern in _SECRET_PATTERNS:
        if kind == "credential_assignment":
            text = pattern.sub(_replace_credential_assignment, text)
        else:
            text = pattern.sub(f"<redacted:{kind}>", text)
    return text


@dataclass
class RedactionReport:
    """What the pass did. Counts are for logs; on-disk markers are the
    durable record that the capture-health manifest reads."""
    files_written: List[str] = field(default_factory=list)
    unparseable: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # Inputs left alone because they were already redacted and have not
    # changed since (see RedactionCache).
    skipped_unchanged: List[str] = field(default_factory=list)


class Redactor:
    """Per-repo redaction context. Cheap to construct; caches gitignore
    lookups so a whole transcript costs at most one ``git check-ignore``."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        try:
            self._real_repo = os.path.realpath(str(repo))
        except OSError:  # fail closed: nothing can be proven in-project
            self._real_repo = str(repo)
        self._ignore_cache: Dict[str, bool] = {}
        self._path_cache: Dict[Tuple[str, str], Tuple[str, bool]] = {}
        self._rel_cache: Dict[str, Optional[str]] = {}
        self._agents_md_cache: Dict[str, Optional[str]] = {}
        self._tracked: Optional[FrozenSet[str]] = None
        # While not None, ignore lookups are only collected (see
        # `prime_command_words`).
        self._collecting: Optional[Set[str]] = None
        self._student_sessions: Optional[FrozenSet[str]] = None
        self._text_path_re = self._build_text_path_re()

    # -- path classification ------------------------------------------

    def _build_text_path_re(self) -> Optional["re.Pattern[str]"]:
        """Regex matching absolute paths worth rewriting inside free text.

        EVERY absolute path is matched, not just repo/home-prefixed ones.
        An earlier version keyed on those two prefixes to keep system paths
        like ``/usr/bin/python`` readable, but that left any other external
        location (``/opt/...``, ``/srv/...``, a mounted volume, another
        user's home) verbatim -- and the fail-closed requirement outranks
        readability. Classification still decides the rendering: in-project
        paths normalize to repo-relative, everything else becomes a
        sentinel.
        """
        return _TEXT_ABS_PATH_RE

    def is_ignored(self, rel_posix: str) -> bool:
        """True if git ignores this repo-relative path. Fails closed."""
        cached = self._ignore_cache.get(rel_posix)
        if cached is not None:
            return cached
        if self._collecting is not None:
            self._collecting.add(rel_posix)  # asked later, all at once
            return False
        self.prime_ignore([rel_posix])
        return self._ignore_cache.get(rel_posix, True)

    def tracked_files(self) -> FrozenSet[str]:
        """The repo-relative files git tracks now. Fails closed: when git
        cannot say, nothing counts as tracked."""
        if self._tracked is None:
            try:
                proc = subprocess.run(
                    ["git", "ls-files", "-z", "--cached"], cwd=str(self.repo),
                    capture_output=True, text=True, timeout=30)
            except (subprocess.TimeoutExpired, OSError):
                proc = None
            self._tracked = (
                frozenset(path for path in proc.stdout.split("\0") if path)
                if proc is not None and proc.returncode == 0 else frozenset())
        return self._tracked

    def prime_ignore(self, rels: Iterable[str]) -> None:
        """Resolve gitignore status for many paths in ONE git call."""
        unknown = sorted({r for r in rels
                          if r and r not in self._ignore_cache})
        if not unknown:
            return
        try:
            proc = subprocess.run(
                ["git", "check-ignore", "--stdin", "-z", "--no-index"],
                cwd=str(self.repo), input="\0".join(unknown) + "\0",
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            for r in unknown:  # fail closed
                self._ignore_cache[r] = True
            return
        if proc.returncode not in (0, 1):
            for r in unknown:  # not a git repo / git error -> fail closed
                self._ignore_cache[r] = True
            return
        ignored = {s for s in proc.stdout.split("\0") if s}
        for r in unknown:
            self._ignore_cache[r] = r in ignored

    def classify_path(self, raw: str,
                      base: Optional[str] = None) -> Tuple[str, bool]:
        """Return ``(rendered, internal)`` for a path-ish string.

        ``rendered`` is a repo-relative POSIX path when internal, or the
        opaque ``<external:sha8>`` sentinel when not.
        """
        if not isinstance(raw, str) or not raw.strip():
            return raw, True
        cache_key = (raw, base or "")
        cached = self._path_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._classify_uncached(raw.strip(), base)
        if self._collecting is None:  # else it rests on a deferred lookup
            self._path_cache[cache_key] = result
        return result

    def _classify_uncached(self, raw: str,
                           base: Optional[str] = None) -> Tuple[str, bool]:
        if _is_file_uri(raw):
            path, fragment = _file_uri_path(raw)
            if path is None:
                return self._external_token(raw), False
            rendered, internal = self._classify_uncached(path)
            if internal and fragment:
                rendered += "#" + self.scrub_text(fragment)  # a line anchor
            return rendered, internal
        if "://" in raw or raw.startswith(EXTERNAL_PREFIX):
            return raw, True  # URLs and already-redacted sentinels pass through
        if len(raw) > MAX_PATH_CHARS:
            return self._external_token(raw), False  # data, not a path
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                # A relative path is only in-project if the directory it
                # resolves against is -- hence `workdir` is load-bearing.
                candidate = Path(base or self.repo) / candidate
            real = os.path.realpath(str(candidate))
            rel = os.path.relpath(real, self._real_repo)
        except (OSError, ValueError, RuntimeError):
            return self._external_token(raw), False
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            return self._external_token(real), False
        rel_posix = Path(rel).as_posix()
        if rel_posix == ".":
            return "", True
        # Our own evidence directory is gitignored on purpose (raw/ is
        # staging). It is capture output, not student content, so it must
        # stay readable in evidence_refs.
        if rel_posix.startswith(ai_traces.AI_TRACES_DIR + "/"):
            return rel_posix, True
        if self.is_ignored(rel_posix):
            # Rule 5: gitignored means not submitted, so treat as external.
            return self._external_token(real), False
        return rel_posix, True

    @staticmethod
    def _external_token(real: str) -> str:
        digest = hashlib.sha256(real.encode("utf-8", "replace")).hexdigest()[:8]
        return f"{EXTERNAL_PREFIX}{digest}>"

    # -- text ----------------------------------------------------------

    def scrub_text(self, text: str) -> str:
        """Scrub credentials, then normalize/opaque absolute paths."""
        if not isinstance(text, str) or not text:
            return text
        out = scrub_secrets(text)
        if "file:" in out.lower():
            out = _TEXT_FILE_URI_RE.sub(self._render_text_path, out)
        if self._text_path_re is not None:
            out = self._text_path_re.sub(self._render_text_path, out)
        return out

    def _render_text_path(self, match: "re.Match[str]") -> str:
        """A path found in text, rendered.

        A `/` command stays as written (review round 5). The project root
        is written `.`, not "": `cd /project && ls` must stay a `cd` into
        the project when the rendered command is checked again on a later
        pass.
        """
        text = match.group(0)
        if _is_slash_command(text):
            return text
        return self.classify_path(text)[0] or "."

    # -- structured records --------------------------------------------

    def collect_path_candidates(self, obj: Any, into: Set[str]) -> None:
        """Gather every path-ish string so gitignore status can be batched."""
        if isinstance(obj, dict):
            for tracked, backup in _file_history_entries(obj):
                real_file = _tracked_real_file(tracked, backup)
                rel = self._rel_candidate(real_file) if real_file else None
                if rel:
                    into.add(rel)
            for k, v in obj.items():
                # KEY position: `{<path>: {...}}` edit/result maps.
                if _key_looks_like_path(k):
                    rel = self._rel_candidate(k)
                    if rel:
                        into.add(rel)
                if str(k).lower() in PATH_KEYS:
                    for s in _as_str_list(v):
                        rel = self._rel_candidate(s)
                        if rel:
                            into.add(rel)
                self.collect_path_candidates(v, into)
        elif isinstance(obj, list):
            for item in obj:
                self.collect_path_candidates(item, into)
        elif isinstance(obj, str):
            if _looks_like_path(obj):
                rel = self._rel_candidate(obj)
                if rel:
                    into.add(rel)
            for target in _patch_targets(obj):
                rel = self._rel_candidate(target.path)
                if rel:
                    into.add(rel)
            if self._text_path_re is not None:
                for m in self._text_path_re.finditer(obj):
                    rel = self._rel_candidate(m.group(0))
                    if rel:
                        into.add(rel)
            if obj.lstrip().startswith(("{", "[")):
                parsed = _try_json(obj)
                if parsed is not None:
                    self.collect_path_candidates(parsed, into)

    def _rel_candidate(self, raw: str) -> Optional[str]:
        """Repo-relative form of `raw`, or None when it isn't in-project.

        Memoized: a transcript names the same few paths thousands of times,
        and each uncached lookup resolves the path on disk.
        """
        if (not isinstance(raw, str) or not raw.strip() or "://" in raw
                or len(raw) > MAX_PATH_CHARS):
            return None
        if raw not in self._rel_cache:
            self._rel_cache[raw] = self._rel_candidate_uncached(raw)
        return self._rel_cache[raw]

    def _rel_candidate_uncached(self, raw: str) -> Optional[str]:
        try:
            candidate = Path(raw.strip())
            if not candidate.is_absolute():
                candidate = self.repo / candidate
            rel = os.path.relpath(os.path.realpath(str(candidate)),
                                  self._real_repo)
        except (OSError, ValueError, RuntimeError):
            return None
        if rel.startswith(os.pardir) or os.path.isabs(rel) or rel == ".":
            return None
        return Path(rel).as_posix()

    def redact(self, obj: Any, base: Optional[str] = None) -> Any:
        """Recursively redact a parsed record (paths only -- see _redact).

        `base` is the directory relative paths in `obj` resolve against --
        for a Codex rollout, the session's cwd -- or None when that is not
        known. A record that states its own directory overrides it.
        """
        return self._redact(obj, base)

    def _redact(self, obj: Any, base: Optional[str] = None) -> Any:
        """Recursively redact a parsed record.

        This walk does PATH ANONYMIZATION ONLY (rules 1/3/5/6), plus the two
        cases where a record itself says which file its text belongs to:
        edit records and patch blobs. Content withholding for tool OUTPUT is
        NOT decided here -- it is decided by the call/output taint join in
        `apply_taint`, because provenance of content is a structural fact of
        the transcript, not something inferrable from which keys happen to
        sit next to which other keys.
        """
        if isinstance(obj, dict):
            if _is_withheld_marker(obj):
                # Already withheld (by an earlier rule or pass). Left as it
                # is: under a key such as `changes` it would otherwise be
                # read as data and lose its `withheld` flag.
                return obj
            # Checked wherever the row appears: claude_normalize nests the
            # same row under `raw_payload` in the normalized tier.
            if _is_file_history_row(obj):
                return self._redact_file_history(obj)
            schema = _codex_schema_for(obj)
            if schema is not None:
                return self._redact_by_schema(obj, schema, base)
            if _is_schema_raw_copy(obj):
                return self._redact_raw_copy(obj, base)
            schema = _claude_row_schema(obj)
            if schema is not None:
                return self._redact_claude_row(obj, schema, base)
            if _is_claude_raw_copy(obj):
                return {**self._redact_dict(
                    {k: v for k, v in obj.items() if k != "raw_payload"}, base),
                    "raw_payload": self._redact_claude_row(
                        obj["raw_payload"],
                        CLAUDE_ROW_SCHEMAS[obj["raw_type"]], base)}
            if self._is_external_edit(obj):
                return self._redact_external_edit(obj, base)
            return self._redact_dict(obj, base)
        if isinstance(obj, list):
            return [self._redact(item, base) for item in obj]
        if isinstance(obj, str):
            # PATCH BLOBS. `apply_patch` names its target in a
            # `*** Update File:` / `+++ b/...` header INSIDE the string, so
            # the body under that header is that file's content --
            # provenance is knowable from the string itself. Checked for
            # every string, list items included: a shell tool carries the
            # patch as the last element of its argv.
            if self._patch_is_external(obj, base):
                return _withheld("external_path")
            if _looks_like_path(obj) and not _is_slash_command(obj):
                return self.classify_path(obj)[0]
            return self.scrub_text(obj)
        return obj

    def _redact_dict(self, obj: Dict[Any, Any],
                     base: Optional[str] = None) -> Dict[Any, Any]:
        # Paths inside this record resolve against its own directory when it
        # states one (`workdir`, `cwd`, ...), else the enclosing one.
        base = self.record_base(obj, base)
        out: Dict[Any, Any] = {}
        entry_withheld = False
        for key, value in obj.items():
            lkey = str(key).lower()

            # KEY POSITION. `patch_apply_begin/end` map file edits BY path
            # (`changes = {<path>: {...diff...}}`). This is the ONE place
            # where per-file provenance is genuinely knowable, so it keeps
            # per-entry handling: an external entry is withheld while its
            # in-project siblings -- submitted work the grader can already
            # read -- survive intact.
            if lkey not in PATH_KEYS and _key_looks_like_path(key):
                rendered_key, internal = self.classify_path(str(key))
                if not internal:
                    out[rendered_key] = {"withheld": True,
                                         "reason": "external_path"}
                    entry_withheld = True
                    continue
                out[rendered_key] = self._redact(value, base)
                continue

            if lkey in EMAIL_KEYS:
                out[key] = REDACTED_EMAIL
                continue
            if lkey in PATH_KEYS:
                out[key] = self._redact_path_value(value)[0]
                continue
            if (lkey == "changes" and isinstance(value, dict)
                    and not _is_withheld_marker(value)):
                out[key] = self._redact_changes(value, base)
                continue
            if lkey in EMBEDDED_JSON_KEYS and isinstance(value, str):
                parsed = _try_json(value)
                if parsed is not None:
                    out[key] = json.dumps(self._redact(parsed, base),
                                          sort_keys=True, ensure_ascii=False)
                    continue

            out[key] = self._redact(value, base)
            # A patch blob withheld by `_redact`, directly or as a list item,
            # marks this record as having lost something.
            redacted = out[key]
            if isinstance(value, (str, list)) and (
                    _is_withheld_marker(redacted)
                    or (isinstance(redacted, list)
                        and any(_is_withheld_marker(v) for v in redacted))):
                entry_withheld = True

        if entry_withheld:
            out["withheld"] = True
        return out

    def _redact_changes(self, changes: Dict[Any, Any],
                        base: Optional[str]) -> Dict[Any, Any]:
        """A Codex edit map, `{<path>: {...diff or content...}}`.

        Each key names the file its entry edits, relative to the directory
        the edit ran in (`base`). An entry whose file is not provably in the
        project -- outside, gitignored, or relative with no known `base`
        (review probes R3/R3b) -- is withheld; in-project siblings survive
        under their repo-relative name. So is an entry that MOVES its file
        (`move_path`) anywhere not provably in the project: its diff is
        then the text of a file that ends up outside.
        """
        out: Dict[Any, Any] = {}
        withheld = False
        for key, entry in changes.items():
            if key == "withheld" and entry is True:
                continue  # our own flag, from an earlier pass; re-set below
            path = str(key)
            moves = [record["move_path"] for record in _iter_dicts(entry)
                     if isinstance(record.get("move_path"), str)
                     and record["move_path"].strip()]
            if self.path_is_internal(path, base) and all(
                    self.path_is_internal(move, base) for move in moves):
                expanded = _expand_user_and_env(path.strip())[0]
                out[self.classify_path(expanded, base)[0]] = self._redact(
                    entry, base)
                continue
            out[self._outside_rendering(path, base)] = _withheld("external_path")
            withheld = True
        if withheld:
            out["withheld"] = True
        return out

    def _outside_rendering(self, path: str, base: Optional[str]) -> str:
        """The `<external:...>` sentinel for a path that is not in-project."""
        known = base is not None and not base.startswith(EXTERNAL_PREFIX)
        if known or os.path.isabs(path):
            rendered = self.classify_path(path, base if known else None)[0]
            if rendered.startswith(EXTERNAL_PREFIX):
                return rendered
        return self._external_token(path)

    # -- where relative paths resolve (see `_call_workdirs`) --------------

    def resolve_dir(self, directory: str,
                    base: Optional[str]) -> Optional[str]:
        """The absolute form of a working directory, or None if unknowable.

        A relative directory resolves against `base`, the directory it was
        given relative to. An `<external:...>` sentinel (from an earlier
        pass) is kept as it is: it is known to be outside the project.
        """
        text = directory.strip()
        if text == INHERITED_DIR:
            return base  # a call that states no directory runs in `base`
        if not text or text == UNREADABLE_DIR:
            return None
        if text.startswith(EXTERNAL_PREFIX):
            return text
        if _is_file_uri(text):
            return _file_uri_path(text)[0]  # None when it names no local path
        expanded, unresolved = _expand_user_and_env(text)
        if unresolved:
            return None
        if os.path.isabs(expanded):
            return expanded
        if base is None or base.startswith(EXTERNAL_PREFIX):
            return base  # unknown stays unknown; outside stays outside
        return os.path.join(base, expanded)

    def record_base(self, record: Mapping[str, Any],
                    base: Optional[str]) -> Optional[str]:
        """The directory relative paths inside `record` resolve against.

        The record's own stated directory wins over the enclosing `base`.
        Two different stated directories make it unknowable (None).
        """
        stated = _call_workdirs(record)
        if not stated:
            return base
        resolved = {self.resolve_dir(d, base) for d in stated}
        return resolved.pop() if len(resolved) == 1 else None

    def path_is_internal(self, raw: str, base: Optional[str]) -> bool:
        """True only if `raw` provably names an in-project path.

        A relative path resolves against `base`. FAILS CLOSED when that
        cannot be done: no `base`, a `base` known to be outside, or an
        environment variable we cannot expand.
        """
        if _is_file_uri(raw.strip()):
            return self.classify_path(raw.strip())[1]
        expanded, unresolved = _expand_user_and_env(raw.strip())
        if unresolved or expanded.startswith(EXTERNAL_PREFIX):
            return False
        if not os.path.isabs(expanded) and (
                base is None or base.startswith(EXTERNAL_PREFIX)):
            return False
        return self.classify_path(expanded, base)[1]

    # -- edit records (see EDIT_TARGET_KEYS) ------------------------------

    def _is_external_edit(self, obj: Mapping[Any, Any]) -> bool:
        """True for an edit record whose target file is outside the project.

        Needs BOTH a target path and a text-bearing key: a record that only
        names a file carries no text to withhold, and ordinary path
        anonymization already covers it.
        """
        if not any(key in obj for key in EDIT_TEXT_KEYS):
            return False
        # Check EVERY target key, not just the first one found: a record
        # naming an in-project `file_path` and an outside `filePath` still
        # carries the outside file's text.
        return any(self._target_is_external(obj[key])
                   for key in EDIT_TARGET_KEYS if key in obj)

    def _target_is_external(self, target: Any) -> bool:
        """True unless `target` provably names only in-project files.

        A string is classified as ONE whole path -- spaces included, since
        real paths contain them ("My Files/x.py"). A list or dict is
        searched for the strings inside it. Anything else -- None, a
        number, a blank string, a container with no strings in it -- names
        no file we can check, so it FAILS CLOSED: we cannot prove the text
        is ours.

        Prose such as a tool schema's "description" also goes through the
        whole-path check. It is harmless there: prose resolves as a
        relative path inside the project, so the schema is kept.
        """
        if isinstance(target, str):
            if not target.strip():
                return True
            expanded, unresolved = _expand_user_and_env(target.strip())
            return unresolved or not self.classify_path(expanded)[1]
        if isinstance(target, dict):
            target = list(target.values())
        if isinstance(target, list):
            strings = list(_iter_strings(target))
            if not strings:
                return True
            return any(self._target_is_external(s) for s in strings)
        return True

    def _redact_external_edit(self, obj: Dict[Any, Any],
                              base: Optional[str] = None) -> Dict[Any, Any]:
        """Keep the edit record's skeleton; withhold everything else."""
        kept = {}
        for k, v in obj.items():
            if k in EDIT_TARGET_KEYS:
                # A target is kept (as a path sentinel) only when it IS a
                # path or a list of paths; any other shape could be
                # carrying text of its own.
                if _is_path_shape(v):
                    kept[k] = v
            elif str(k).lower() in EDIT_SKELETON_KEYS:
                kept[k] = v
        out = self._redact_dict(kept, base)
        for key in obj:
            if key not in kept:
                out[key] = _withheld("external_path")
        out["withheld"] = True
        return out

    # -- Claude Code file-history rows (declared-safe fields only) --------

    def _redact_file_history(self, row: Dict[Any, Any]) -> Dict[Any, Any]:
        """Keep only the declared metadata of a file-history row.

        Anything not in FILE_HISTORY_ROW_KEYS is replaced by a withheld
        marker, so a future Claude Code version that adds a content-bearing
        field degrades to WITHHELD rather than LEAKED.
        """
        out: Dict[Any, Any] = {}
        withheld = False
        for key, value in row.items():
            if key not in FILE_HISTORY_ROW_KEYS:
                out[key] = _withheld("undeclared_field")
                withheld = True
            elif key == "snapshot":
                out[key], entry_withheld = self._redact_file_history_snapshot(value)
                withheld = withheld or entry_withheld
            elif key == "backup":
                # A delta's single backup belongs to its `trackingPath`.
                out[key], entry_withheld = self._redact_tracked_backup(
                    row.get("trackingPath"), value)
                withheld = withheld or entry_withheld
            elif key == "trackingPath":
                out[key] = self._tracked_file_location(value, row.get("backup"))[0]
            else:
                out[key] = self._declared_scalar(value)
                withheld = withheld or _is_withheld_marker(out[key])
        if withheld:
            out["withheld"] = True
        return out

    def _redact_file_history_snapshot(self, snapshot: Any) -> Tuple[Any, bool]:
        """The `snapshot` object: a path-keyed map of backup entries."""
        if _is_withheld_marker(snapshot):
            return snapshot, True
        if not isinstance(snapshot, dict):
            return _withheld("undeclared_field"), True
        out: Dict[Any, Any] = {}
        withheld = False
        for key, value in snapshot.items():
            if key not in FILE_HISTORY_SNAPSHOT_KEYS:
                out[key] = _withheld("undeclared_field")
                withheld = True
            elif key == "trackedFileBackups":
                if not isinstance(value, dict):
                    out[key] = _withheld("undeclared_field")
                    withheld = True
                    continue
                backups: Dict[Any, Any] = {}
                for tracked, backup in value.items():
                    rendered = self._tracked_file_location(tracked, backup)[0]
                    backups[rendered], entry_withheld = (
                        self._redact_tracked_backup(tracked, backup))
                    withheld = withheld or entry_withheld
                out[key] = backups
            else:
                out[key] = self._declared_scalar(value)
                withheld = withheld or _is_withheld_marker(out[key])
        return out, withheld

    def _redact_tracked_backup(self, tracked: Any,
                               backup: Any) -> Tuple[Any, bool]:
        """One backup entry. Returns (rendered, withheld).

        The whole entry is withheld when its file is outside the project:
        even the backup NAME is a pointer to a copy of that file's content.
        """
        if _is_withheld_marker(backup):
            return backup, True
        if not self._tracked_file_location(tracked, backup)[1]:
            return _withheld("external_path"), True
        if not isinstance(backup, dict):
            return _withheld("undeclared_field"), True
        out: Dict[Any, Any] = {}
        withheld = False
        for key, value in backup.items():
            if key not in FILE_HISTORY_BACKUP_KEYS:
                out[key] = _withheld("undeclared_field")
                withheld = True
            elif key == "realParentDir" and isinstance(value, str):
                out[key] = self.classify_path(value)[0]
            else:
                out[key] = self._declared_scalar(value)
                withheld = withheld or _is_withheld_marker(out[key])
        return out, withheld

    # -- Codex rows with a field schema (see CODEX_ROW_SCHEMAS) -----------

    def _redact_by_schema(self, obj: Dict[Any, Any], schema: Any,
                          base: Optional[str]) -> Dict[Any, Any]:
        """Redact a row (its `payload`) or a response item by its schema.

        The `withheld` flag goes on the payload, or on the item itself, so
        a row keeps only the keys every Codex row has (CODEX_ROW_KEYS).
        """
        if not _is_schema_row(obj):  # a response item: the schema is the item
            out, lost = self._apply_fields(obj, schema, base)
            if lost:
                out["withheld"] = True
            return out
        out = {}
        for key, value in obj.items():
            if key != "payload":
                out[key] = self._redact(value, base)
                continue
            out[key], lost = self._apply_fields(value, schema, base)
            if lost:
                out[key]["withheld"] = True
        return out

    def _redact_raw_copy(self, extras: Dict[Any, Any],
                         base: Optional[str]) -> Dict[Any, Any]:
        """Our normalized event's `extras`: the vendor payload it keeps
        follows the same schema as the row it was copied from."""
        out = {}
        for key, value in extras.items():
            if key != "raw_payload":
                out[key] = self._redact(value, base)
                continue
            schema = CODEX_ROW_SCHEMAS[extras["raw_type"]]
            out[key], lost = self._apply_fields(value, schema, base)
            if lost:
                out[key]["withheld"] = True
        return out

    def _redact_claude_row(self, row: Dict[Any, Any], schema: Any,
                           base: Optional[str]) -> Dict[Any, Any]:
        """A Claude Code row with a field schema (see CLAUDE_ROW_SCHEMAS)."""
        out, lost = self._apply_fields(row, schema, self.record_base(row, base))
        if lost:
            out["withheld"] = True
        return out

    def _apply_fields(self, value: Mapping[Any, Any], schema: Any,
                      base: Optional[str]) -> Tuple[Dict[Any, Any], bool]:
        """(rendered, withheld) for a dict checked against a field schema."""
        out: Dict[Any, Any] = {}
        lost = False
        hidden = 0
        for key, item in value.items():
            if key == "withheld" and item is True:
                continue  # our own flag, from an earlier pass; re-set above
            if isinstance(schema, NamedMap):
                declared, child = key in schema.names, schema.value
            else:
                declared, child = key in schema, schema.get(key)
            if not declared:
                # The key may itself be content, so it goes too.
                hidden += 1
                out[f"<withheld-key-{hidden}>"] = _withheld("undeclared_field")
                lost = True
                continue
            out[key], item_lost = self._apply_schema(item, child, value, base)
            lost = lost or item_lost
        return out, lost

    def _apply_schema(self, value: Any, schema: Any, parent: Mapping[Any, Any],
                      base: Optional[str]) -> Tuple[Any, bool]:
        """(rendered, withheld) for one value checked against its schema."""
        if _is_withheld_marker(value):
            return value, True  # withheld by an earlier rule or pass
        if isinstance(schema, Either):
            schema = schema.fields if isinstance(value, dict) else schema.scalar
        if _carries_no_text(value) and schema != AGENTS_TEXT:
            return value, False
        if isinstance(schema, (dict, NamedMap)):
            if isinstance(value, dict):
                return self._apply_fields(value, schema, base)
            return _withheld("undeclared_field"), True
        if isinstance(schema, ListOf):
            if not isinstance(value, list):
                return _withheld("undeclared_field"), True
            rendered, lost = [], False
            for item in value:
                item_out, item_lost = self._apply_schema(
                    item, schema.item, parent, base)
                rendered.append(item_out)
                lost = lost or item_lost
            return rendered, lost
        if schema == AGENTS_TEXT:
            return self._agents_md_text(parent, value)
        if schema == HIDDEN:
            return _withheld("not_project_content"), True
        if schema == ITEMS and isinstance(value, list):
            return [self._redact(item, base) for item in value], False
        if not isinstance(value, str):
            return _withheld("undeclared_field"), True
        if schema == TEXT:
            rendered = self._redact(value, base)
            return rendered, _is_withheld_marker(rendered)
        if schema == PATH and (os.path.isabs(value) or base is not None
                               or _is_file_uri(value)):
            # A relative directory is relative to `base`: in the normalized
            # tier, redacted again on every pass, that is the project root.
            return self.classify_path(value, base)[0], False
        if (schema == URL and value.startswith(("http://", "https://"))
                and not any(ch.isspace() for ch in value)):
            return self.scrub_text(value), False
        shape = IDENTIFIER_SHAPES.get(schema)
        if shape is not None and shape.fullmatch(value):
            return value, False
        return _withheld("undeclared_field"), True

    def _agents_md_text(self, parent: Mapping[str, Any],
                        text: Any) -> Tuple[Any, bool]:
        """AGENTS.md text (`world_state.agents_md.text`, or a turn's
        `user_instructions`): kept only if it is the project's own file.

        The folder it was read for is the parent's `directory`, or, for a
        turn, its `cwd`.
        """
        if text == "":
            return text, False
        directory = (parent.get("directory") if "directory" in parent
                     else parent.get("cwd"))
        if isinstance(directory, str) and directory and not os.path.isabs(
                directory):
            # Already redacted to repo-relative on an earlier pass.
            directory = os.path.join(str(self.repo), directory)
        if isinstance(text, str) and self._agents_md_is_in_project(
                directory, text):
            return self.scrub_text(text), False
        return _withheld("unverified_source"), True

    def _agents_md_is_in_project(self, directory: Any, text: str) -> bool:
        """True if `text` is exactly what the project's AGENTS.md files say.

        Codex reads the AGENTS.md of the session directory and of each
        folder above it up to the project root. `text` must equal one of
        those files, or all of them joined root first. Each file must itself
        be in the project -- not gitignored, not a symlink to somewhere
        else. Anything else is unproven: the file may have changed since,
        or the text may never have come from the project at all.
        """
        if (not isinstance(directory, str) or not os.path.isabs(directory)
                or not text.strip()):
            return False
        rel, internal = self.classify_path(directory)
        if not internal:
            return False
        parts = [p for p in rel.split("/") if p]
        bodies = []
        for depth in range(len(parts) + 1):  # the project root first
            body = self._project_agents_md(self.repo.joinpath(*parts[:depth]))
            if body is not None:
                bodies.append(body.strip())
        candidates = set(bodies)
        if len(bodies) > 1:
            candidates.add("\n\n".join(bodies))
        # A copy redacted on an earlier pass holds the scrubbed text.
        candidates |= {self.scrub_text(body) for body in candidates}
        return text.strip() in candidates

    def _project_agents_md(self, folder: Path) -> Optional[str]:
        """The AGENTS.md instructions in `folder`, if they are in-project."""
        key = str(folder)
        if key not in self._agents_md_cache:
            body = None
            for name in AGENTS_MD_NAMES:
                path = folder / name
                if not path.is_file():
                    continue
                if self.classify_path(str(path))[1]:
                    try:
                        body = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        body = None
                break  # the first file Codex would use decides
            self._agents_md_cache[key] = body
        return self._agents_md_cache[key]

    def student_codex_sessions(self) -> FrozenSet[str]:
        """The Codex sessions this project's rollouts show to be the
        student's own (see HOOK LOGS NAME NO SESSION SOURCE)."""
        if self._student_sessions is None:
            self._student_sessions = _student_codex_sessions(self.repo)
        return self._student_sessions

    # -- tool-injected context (see INJECTED_CONTEXT) ---------------------

    def withhold_injected_context(self, row: Any, typed: Typing) -> Any:
        """`row` with the text of every message item nobody typed withheld.

        `typed` is what the student typed in this transcript (see
        `typed_texts`). Messages are looked for where Codex writes them: a
        `response_item` row, the saved histories of a `compacted` row, the
        prompt records of another agent's session, and our normalized
        copies of all three; and where Claude Code writes them: attachment,
        `user`, `queue-operation` and `system` rows.
        """
        if not isinstance(row, dict):
            return row
        if "event_type" in row:
            return self._withhold_injected_in_event(row, typed)
        kind = row.get("type")
        if kind == CLAUDE_ATTACHMENT_ROW and "attachment" in row:
            return _withhold_claude_attachment_row(row)
        if kind == CLAUDE_USER_ROW:
            return _withhold_claude_user_row(row)
        if kind == CLAUDE_QUEUE_ROW:
            return _withhold_claude_text_field(
                row, "content", lambda text: text.strip() in typed)
        if kind == CLAUDE_LAST_PROMPT_ROW:
            return _withhold_claude_text_field(
                row, "lastPrompt", lambda text: text.strip() in typed)
        if kind == CLAUDE_SYSTEM_ROW:
            return _withhold_command_args(_withhold_claude_text_field(
                row, "content", lambda text: claude_system_text_is_kept(
                    row.get("subtype"), text)), typed)
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return row
        if kind == "event_msg" and typed.agent_file:
            return {**row, "payload": _withhold_agent_prompt(payload)}
        if typed.is_codex_hook(row):
            if typed.by_agent(row):
                return {**row, "payload": _withhold_agent_prompt(payload)}
            return {**row, "payload": _keep_own_request(payload, "prompt")}
        if kind == "event_msg":
            return {**row, "payload": _keep_own_request_in_event(payload)}
        if row.get("type") == "response_item":
            return {**row, "payload": self._withhold_injected_in_message(
                payload, typed)}
        if row.get("type") == "compacted":
            return {**row, "payload": self._withhold_injected_in_history(
                payload, typed)}
        return row

    def _withhold_injected_in_event(self, event: Dict[Any, Any],
                                    typed: Typing) -> Dict[Any, Any]:
        """One of our normalized events, built from a vendor record.

        A Codex message event's `text` is the message's texts joined, so it
        is rebuilt from the items that survived -- it can hold nothing they
        do not. A Claude event's `text` follows the row it came from.
        """
        extras = event.get("extras")
        if not isinstance(extras, dict):
            return event
        raw_type = extras.get("raw_type")
        raw = extras.get("raw_payload")
        text = event.get("text")
        if (typed.by_agent(event) and event.get("event_type") == "user_prompt"
                and raw_type != "response_item"):  # a message: checked below
            out = {**event, "text": _withheld(INJECTED_CONTEXT)}
            if isinstance(raw, dict):
                out["extras"] = {**extras,
                                 "raw_payload": _withhold_agent_prompt(raw)}
            return out
        if event.get("event_type") == "user_prompt" and (
                raw_type == "event_msg" or typed.is_codex_hook(event)):
            out = _keep_own_request(event, "text")
            if isinstance(raw, dict):
                raw = (_keep_own_request(raw, "prompt")
                       if typed.is_codex_hook(event)
                       else _keep_own_request_in_event(raw))
                out = {**out, "extras": {**extras, "raw_payload": raw}}
            return out
        if raw_type == CLAUDE_ATTACHMENT_ROW and "attachment" in extras:
            return {**event, "extras": {  # claude_normalize's copy
                **extras, "attachment": _claude_attachment(
                    extras["attachment"],
                    sidechain=extras.get(CLAUDE_SIDECHAIN_KEY) is True)}}
        # A tool's output is not text anyone typed: the call join decides it
        # (review round 5), as it does in the raw tier.
        claude_text_row = (raw_type in (CLAUDE_USER_ROW, CLAUDE_SYSTEM_ROW)
                           and event.get("event_type") != NORMALIZED_OUTPUT_TYPE)
        if "text" in event and not _is_withheld_marker(text) and (
                claude_text_row and (
                    extras.get(CLAUDE_META_KEY) is True
                    or not _claude_event_text_is_kept(extras, text))):
            event = {**event, "text": _withheld(INJECTED_CONTEXT)}
        if raw_type == CLAUDE_SYSTEM_ROW and isinstance(raw, dict):
            kept = _claude_event_text_is_kept(extras, raw.get("content"))
            return {**event, "extras": {**extras, "raw_payload": (
                _withhold_command_args(_withhold_claude_text_field(
                    raw, "content", lambda _: kept), typed))}}
        if raw_type == CLAUDE_QUEUE_ROW and isinstance(raw, dict):
            return {**event, "extras": {
                **extras, "raw_payload": self.withhold_injected_context(
                    raw, typed)}}
        if not isinstance(raw, dict):
            return event
        if extras.get("raw_type") == "compacted":
            return {**event, "extras": {
                **extras,
                "raw_payload": self._withhold_injected_in_history(raw, typed)}}
        if extras.get("raw_type") != "response_item" or not _is_injectable(raw):
            return event
        message = self._withhold_injected_in_message(raw, typed)
        out = {**event, "extras": {**extras, "raw_payload": message}}
        if "text" in event:
            out["text"] = _surviving_text(message)
        return out

    def _withhold_injected_in_history(self, payload: Dict[Any, Any],
                                      typed: Typing) -> Dict[Any, Any]:
        """A `compacted` payload: each saved message is checked the same way."""
        out = dict(payload)
        for key in CODEX_HISTORY_KEYS:
            if isinstance(payload.get(key), list):
                out[key] = [self._withhold_injected_in_message(item, typed)
                            for item in payload[key]]
        return out

    def _withhold_injected_in_message(self, message: Any,
                                      typed: Typing) -> Any:
        """A response item; if it is a user/developer/system message, each
        content item keeps its text only if the student typed it."""
        if not _is_injectable(message):
            return message
        role = message["role"]
        content = message.get("content")
        items = content if isinstance(content, list) else [content]
        checked = [self._check_content_item(item, role, typed)
                   for item in items]
        out = dict(message)
        out["content"] = checked if isinstance(content, list) else checked[0]
        if any(_holds_withheld_text(item) for item in checked):
            out["withheld"] = True
        return out

    def _check_content_item(self, item: Any, role: str,
                            typed: Typing) -> Any:
        """One content item: kept, or reduced to its type and a marker."""
        if isinstance(item, dict) and "text" not in item:
            if role == TYPING_ROLE and item.get("type") in ATTACHED_ITEM_TYPES:
                return item  # an image the student attached
            return _skeleton_unrecognized(item, INJECTED_CONTEXT)
        text = item.get("text") if isinstance(item, dict) else item
        if text is None or _is_withheld_marker(text):
            return item  # no text, or withheld on an earlier pass
        if isinstance(text, str) and role == TYPING_ROLE:
            own = own_request(text)
            if own is not None and own.strip() in typed:
                return item if own == text else _keep_own_request(item, "text")
            if self._is_project_agents_block(text):
                return item
        if isinstance(item, dict):
            return {"type": item.get("type"), "text": _withheld(INJECTED_CONTEXT)}
        return _withheld(INJECTED_CONTEXT)

    def _is_project_agents_block(self, text: str) -> bool:
        """True for an injected AGENTS.md block that is exactly the project's
        own AGENTS.md (the rp7 rule). A global file joined in front of it
        (`--- project-doc ---`) makes it not the project's."""
        match = _AGENTS_BLOCK_RE.match(text)
        if match is None:
            return False
        directory = match.group("directory").strip()
        if not os.path.isabs(directory):
            # A copy redacted on an earlier pass: the directory is already
            # repo-relative ("" for the project root). Keeping text that
            # equals an in-project file leaks nothing -- it is committed.
            directory = os.path.join(str(self.repo), directory)
        return self._agents_md_is_in_project(directory, match.group("text"))

    def _tracked_file_location(self, tracked: Any,
                               backup: Any) -> Tuple[str, bool]:
        """Return ``(rendered, internal)`` for a tracked file.

        Internal only if BOTH the tracked path and the real file
        (``realParentDir`` + file name) are in-project. The tracked path is
        relative to wherever the session ran, so on its own it can look
        in-project for a file that is not -- ``realParentDir`` is the
        authority on where the file really is. Fails closed on anything
        that is not a usable string.
        """
        if not isinstance(tracked, str) or not tracked.strip():
            return self._external_token(repr(tracked)), False
        rendered, internal = self.classify_path(tracked)
        if tracked.startswith(EXTERNAL_PREFIX):
            return rendered, True  # already redacted on an earlier pass
        if internal and isinstance(backup, dict) and "realParentDir" in backup:
            real_file = _tracked_real_file(tracked, backup)
            if real_file is None:  # unusable realParentDir -> fail closed
                return self._external_token(tracked), False
            real_rendered, real_internal = self.classify_path(real_file)
            if not real_internal:
                # Render the REAL file's sentinel, so it correlates with
                # other references to the same outside file.
                return real_rendered, False
        return rendered, internal

    def _declared_scalar(self, value: Any) -> Any:
        """A declared field keeps a plain value; structure is not declared."""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self.scrub_text(value)
        if _is_withheld_marker(value):
            return value
        return _withheld("undeclared_field")

    def _patch_is_external(self, text: str, base: Optional[str]) -> bool:
        """True if a patch/diff text blob edits a file not provably ours.

        A header path is relative to the directory the patch ran in, `base`
        (review finding y8j: resolving it against the project root made an
        outside file look like project work). With no known `base`, a
        relative header FAILS CLOSED -- except a lone `--- word` line, which
        is as likely to be prose; that one falls back to the project root,
        as before, so ordinary messages are not withheld.

        A header written as an expression (see _EXPRESSION_HEADER_RE) names
        a file we cannot know, so it fails closed as well.

        A `cd` earlier in the same text may have moved the patch elsewhere.
        Whether it applies (a subshell, a later command) cannot be told from
        the text, so the header must be in-project from EVERY candidate
        directory: `base` and each `cd` target.
        """
        for target in _patch_targets(text):
            if target.certain and _EXPRESSION_HEADER_RE.search(target.path):
                return True  # an expression names no file we can check
            if not target.certain:
                where = base if base is not None else str(self.repo)
                candidates: Iterable[Optional[str]] = (where,)
            else:
                candidates = (base,) + target.cd_dirs
            if not all(self.path_is_internal(target.path, where)
                       for where in candidates):
                return True
        return False

    def _redact_path_value(self, value: Any) -> Tuple[Any, bool]:
        if isinstance(value, str):
            return self.classify_path(value)
        if isinstance(value, list):
            rendered = []
            internal = True
            for item in value:
                if isinstance(item, str):
                    text, ok = self.classify_path(item)
                    internal = internal and ok
                    rendered.append(text)
                else:
                    rendered.append(self._redact(item))
            return rendered, internal
        return self._redact(value), True

    # -- which output is kept (see WHICH OUTPUT IS KEPT) ---------------

    def call_output_is_provable(self, record: Mapping[str, Any],
                                base: Optional[str]) -> bool:
        """True only if a call's output provably holds nothing but
        submitted project content. `base` is the directory the call ran in
        when the record does not say."""
        if "event_type" in record:
            return self._event_output_is_provable(record, base)
        kind = _record_type(record)
        if kind == "custom_tool_call":
            return self._tool_output_is_provable(
                record.get("name"), record.get("input"), base)
        if kind == "function_call":
            arguments = record.get("arguments")
            if isinstance(arguments, str):
                arguments = _try_json(arguments)
            return self._tool_output_is_provable(
                record.get("name"), arguments, base)
        if kind == "exec_command_begin":
            return self._command_is_provable(
                record.get("command"), [self._stated_dir(record, "cwd", base)])
        if kind == CLAUDE_CALL_TYPE:
            return self._tool_output_is_provable(
                record.get("name"), record.get("input"), base)
        if "tool_input" in record:  # a hook payload (PreToolUse)
            return self._tool_output_is_provable(
                record.get("tool_name"), record.get("tool_input"),
                self._stated_dir(record, "cwd", base))
        return False

    def _stated_dir(self, record: Mapping[str, Any], key: str,
                    base: Optional[str]) -> Optional[str]:
        """The directory `record` states under `key`, else `base`; None
        when it states one that cannot be read."""
        if key not in record:
            return base
        return self.resolve_dir(_readable_dir(record[key]), base)

    def _tool_output_is_provable(self, name: Any, value: Any,
                                 base: Optional[str]) -> bool:
        """A named tool's output (see CLAUDE_TOOLS and CODEX_TOOLS)."""
        if name == EXEC_TOOL:
            return self._exec_input_is_provable(value, base)
        if name == EXEC_COMMAND_TOOL:
            return (isinstance(value, dict) and "cmd" in value
                    and set(value) <= EXEC_COMMAND_KEYS
                    and self._command_is_provable(value["cmd"], [
                        self._stated_dir(value, "workdir", base)]))
        if name == APPLY_PATCH_TOOL:
            if isinstance(value, dict) and len(value) == 1 and (
                    set(value) <= APPLY_PATCH_KEYS):
                value = next(iter(value.values()))
            return (isinstance(value, str) and _is_patch_text(value)
                    and self._patch_output_is_provable(value, [base]))
        if name == VIEW_IMAGE_TOOL:
            return (isinstance(value, dict) and set(value) <= VIEW_IMAGE_KEYS
                    and self._file_is_project_content(value.get("path"), base))
        tool = CLAUDE_TOOLS.get(name) if isinstance(name, str) else None
        if (tool is None or not isinstance(value, dict)
                or not set(value) <= tool.keys
                or not tool.required <= set(value)):
            return False
        if tool.kind == SHELL_TOOL:
            return self._command_is_provable(value["command"], [base])
        if tool.kind == FILE_TOOL:
            return self._file_is_project_content(value["file_path"], base)
        if tool.kind == SEARCH_TOOL:
            where = value.get("path", base)
            return (self._file_is_project_content(where, base)
                    and self._project_path(where, base) not in ("", None))
        if tool.kind == WEB_TOOL:
            return "url" not in value or _is_public_web_address(value["url"])
        return False

    def _event_output_is_provable(self, event: Mapping[str, Any],
                                  base: Optional[str]) -> bool:
        """One of our normalized `tool_start` events. Every part that says
        what ran must pass: the vendor record it was built from, the tool
        input it copied, and its own `command` (unless that was copied from
        one of the other two). The decision of an earlier pass, made before
        the paths were rendered, stands (CALL_OUTPUT_KEPT_KEY)."""
        extras = event.get("extras")
        extras = extras if isinstance(extras, dict) else {}
        decided = extras.get(CALL_OUTPUT_KEPT_KEY)
        if isinstance(decided, bool):
            return decided
        cwd = event.get("cwd")
        here = (self.resolve_dir(cwd, base)
                if isinstance(cwd, str) and cwd.strip() else base)
        name = event.get("tool_name")
        raw = extras.get("raw_payload")
        checked: List[Any] = []
        if "raw_payload" in extras:
            if not (isinstance(raw, dict)
                    and self.call_output_is_provable(raw, here)):
                return False
            checked.append(raw)
        if "tool_input" in extras:
            if not self._tool_output_is_provable(
                    name, extras["tool_input"], here):
                return False
            checked.append({"tool_input": extras["tool_input"]})
        command = event.get("command")
        if command is not None:
            if not isinstance(command, str):
                return False
            if (command not in _copied_commands(checked)
                    and not self._exec_input_is_provable(command, here)):
                return False
            checked.append(command)
        return bool(checked)

    def _exec_input_is_provable(self, source: Any,
                                base: Optional[str]) -> bool:
        """A Codex `exec` input: JavaScript calling the nested tools (see
        THE TOOLS AN EXEC SCRIPT CALLS). Text that calls none is read as a
        shell command, as our normalized events write one."""
        if not isinstance(source, str):
            return False
        if not _JS_TOOL_CALL_RE.search(source):
            return self.shell_script_is_provable(source, [base])
        script = js_script(source)
        if (script is None or not script.calls
                or JS_STORED_DATA_RE.search(script.code)
                or UNREADABLE_DIR in js_stated_workdirs(source)):
            return False
        for call in script.calls:
            options = call.options
            if call.tool == APPLY_PATCH_TOOL:
                if not self._patch_output_is_provable(source, [base]):
                    return False
            elif (call.tool != EXEC_COMMAND_TOOL or options is None
                  or not set(options) <= EXEC_COMMAND_KEYS
                  or not isinstance(options.get("cmd"), str)
                  or ("workdir" in options and options["workdir"] is None)
                  or not self.shell_script_is_provable(
                      options["cmd"],
                      [self._stated_dir(options, "workdir", base)])):
                return False
        return True

    def _patch_output_is_provable(self, text: str,
                                  places: List[Optional[str]]) -> bool:
        """apply_patch answers with the files it changed: kept only when
        the patch names its files plainly and each is in the project, from
        every directory it may apply in (see `_patch_is_external`)."""
        targets = _patch_targets(text)
        return (any(target.certain for target in targets)
                and not any(self._patch_is_external(text, place)
                            for place in places))

    def _command_is_provable(self, command: Any,
                             places: List[Optional[str]]) -> bool:
        """A command given as a script, or as argv words."""
        script = (_argv_script(command) if isinstance(command, list)
                  else command)
        return (isinstance(script, str)
                and self.shell_script_is_provable(script, places))

    def _project_path(self, raw: Any, base: Optional[str]) -> Optional[str]:
        """The repo-relative form of a readable project path, else None."""
        if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
            return None
        expanded, unresolved = _expand_user_and_env(raw.strip())
        if unresolved or _WINDOWS_ABS_RE.match(expanded):
            return None
        if not os.path.isabs(expanded) and not _is_file_uri(expanded):
            if base is None or not os.path.isabs(base):
                return None
            expanded = os.path.join(base, expanded)
        rendered, internal = self.classify_path(expanded)
        if not internal or _is_protected_read(rendered):
            return None
        return rendered

    def _file_is_project_content(self, raw: Any, base: Optional[str]) -> bool:
        """True for a path a tool reads or edits that is a readable,
        submitted project path."""
        return self._project_path(raw, base) is not None

    def shell_script_is_provable(self, script: str,
                                 directories: Iterable[Optional[str]],
                                 extra_arguments: Iterable[str] = ()) -> bool:
        """THE allow-list predicate for shell text (see WHICH OUTPUT IS
        KEPT): True only if `script`, run from any of `directories`,
        provably reads nothing but submitted project files -- or runs the
        student's own code. `extra_arguments` are more words to check the
        same way (Codex's own parse of the script)."""
        places = list(directories)
        if not places or not all(self._is_project_dir(d) for d in places):
            return False
        segments = _plain_shell_segments(script)
        if segments is None:
            return False
        # Only a leading `cd /abs` can move the commands: no command on the
        # lists changes directory (git's `-C` is refused with its other
        # global options). Both directories are checked, whether or not the
        # `cd` succeeded.
        if segments and segments[0][0] == "cd":
            words = segments.pop(0)
            if (len(words) != 3 or words[2] != ""
                    or not os.path.isabs(words[1])
                    or not self._is_project_dir(words[1])):
                return False
            places.append(words[1])
        arguments: List[str] = list(extra_arguments)
        for words in segments:
            marker = words.index("")
            named = _read_only_arguments(words[:marker])
            if named is None:
                named = self._student_code_arguments(words[:marker], places)
            if named is None:
                return False
            if "--" in named:  # after `--` every word is a file name
                cut = named.index("--")
                named = named[:cut] + ["./" + word for word in named[cut + 1:]]
            arguments.extend(named + words[marker + 1:])
        return all(self._argument_is_in_project(word, place)
                   for place in places for word in arguments)

    def _is_project_dir(self, directory: Optional[str]) -> bool:
        return (directory is not None and os.path.isabs(directory)
                and self._reads_in_project(directory))

    # -- the student's own code (see STUDENT CODE) ------------------------

    def _student_code_arguments(self, words: List[str],
                                places: List[str]) -> Optional[List[str]]:
        """The words to check as files of a command that runs the
        student's own code, or None when it does not."""
        program, args = words[0], words[1:]
        if self._is_runner(program, places, PYTHON_PROGRAM_RE):
            if args[:1] == ["-m"]:
                return (_pytest_arguments(args[2:])
                        if args[1:2] == ["pytest"] else None)
            if not args or args[0].startswith("-"):
                return None  # a script from standard input, or an option
            scripts = self._student_scripts(args[0], places)
            if not scripts:
                return None
            if RUN_TESTS_SCRIPT in scripts:
                return _run_tests_arguments(args[1:])
            return args[1:]
        if self._is_runner(program, places, PYTEST_PROGRAM_RE):
            return _pytest_arguments(args)
        return None

    def _is_runner(self, program: str, places: List[str],
                   name: "re.Pattern[str]") -> bool:
        """True for `name` itself, or for the project venv's program of
        that name, from every place the command may run in. The venv's
        programs are links to an interpreter outside the project, so the
        path is compared as written, not resolved."""
        if "/" not in program:
            return bool(name.fullmatch(program))
        roots = {os.path.abspath(str(self.repo)), self._real_repo}
        for place in places:
            path = os.path.normpath(os.path.join(place, program))
            rels = {os.path.relpath(path, root).replace(os.sep, "/")
                    for root in roots}
            if not any(_is_venv_program(rel, name) for rel in rels):
                return False
        return True

    def _student_scripts(self, script: str,
                         places: List[str]) -> FrozenSet[str]:
        """The repo-relative files `script` names from each place, or
        nothing unless every one is a tracked, readable project `.py` file
        outside the capture layer."""
        if not script.endswith(STUDENT_SCRIPT_SUFFIX):
            return frozenset()
        found: Set[str] = set()
        for place in places:
            rel = self._project_path(script, place)
            if (not rel or rel not in self.tracked_files()
                    or rel.lower().startswith(STUDENT_CODE_EXCLUDED)):
                return frozenset()
            found.add(rel)
        return frozenset(found)

    # -- priming and items ------------------------------------------------

    def prime_command_words(self, rows: List[Any],
                            base: Optional[str] = None) -> None:
        """Ask git once about every path the calls in `rows` may read.

        The decisions are made once with every ignore lookup deferred --
        each unknown path counts as not ignored and is only collected --
        and the collected paths are then looked up together, so the real
        decisions (`build_taint_index`) need no git call of their own.
        """
        self._collecting = set()
        try:
            _decide_calls(self, rows, base)
            collected = self._collecting
        finally:
            self._collecting = None
        self.prime_ignore(collected)

    def item_output_is_provable(self, item: Mapping[str, Any],
                                base: Optional[str]) -> bool:
        """A Codex turn item's own invocation (see CODEX_ITEM_FIELDS): a
        command must pass the allow-list, and a file change must name only
        project files. No other item type keeps its output."""
        if item.get("type") == VOUCHABLE_ITEM_TYPE:
            return self.command_item_is_vouched(item)
        changes = item.get("changes")
        if item.get("type") != "FileChange" or not isinstance(changes, dict):
            return False
        targets = _change_targets(changes)
        return bool(targets) and all(self.path_is_internal(target, base)
                                     for target in targets)

    def command_item_is_vouched(self, item: Mapping[str, Any]) -> bool:
        """True if a CommandExecution's own invocation proves its output
        holds nothing from outside the project (see VOUCHABLE_ITEM_TYPE):
        `[<shell>, -c|-lc, <script>]`, run in its own `cwd`, with every
        path in Codex's parse a project path too."""
        command = item.get("command")
        parsed = _parsed_paths(item.get("parsed_cmd"))
        if (item.get("type") != VOUCHABLE_ITEM_TYPE or parsed is None
                or not _is_shell_argv(command)):
            return False
        return self.shell_script_is_provable(
            command[2], [self._stated_dir(item, "cwd", None)], parsed)

    def _argument_is_in_project(self, word: str, directory: str) -> bool:
        """True unless a command argument may name a file outside the
        project.

        Every word is classified as a path -- a pattern or a count too --
        whether or not a file by that name exists now: a gitignored file
        may be gone by the time the transcript is redacted. An option is
        not a file (the allow-list has already vetted it), but the value
        of `--opt=value` may be one.
        """
        if word.startswith("-"):
            if "=" not in word:
                return "/" not in word
            word = word.split("=", 1)[1]
        if not word or _is_stream_device(word):
            return True
        if _is_file_uri(word) or os.path.isabs(word):
            return self._reads_in_project(word)
        return self._reads_in_project(os.path.join(directory, word))

    def reads_outside(self, raw: str, base: Optional[str] = None) -> bool:
        """True unless reading `raw` reads only submitted project files.

        That is: the path is in the project, not ignored, and not one of
        the protected paths (PROTECTED_READ_DIRS). The path itself is still
        rendered as usual wherever it is written down.
        """
        rendered, internal = self.classify_path(raw, base)
        return not internal or _is_protected_read(rendered)

    def _reads_in_project(self, path: str) -> bool:
        """True for a read of submitted project files only (see
        `reads_outside`)."""
        return not self.reads_outside(path)


# --- adapter-level vocabulary recognition (DEFAULT DENY) ------------------
#
# The taint join can only fire on record shapes it recognizes. An adapter
# whose vocabulary is unknown therefore got PATH ANONYMIZATION ONLY, and its
# tool-result content passed straight through -- found in review against
# vscode_chat, an adapter that is registered, shipping, and committed.
#
# So recognition itself is now DEFAULT DENY, one level up from the
# already-accepted "unparseable transcript is withheld" rule: if a
# transcript's records do not match a vocabulary that has been EMPIRICALLY
# VERIFIED against real data, its content is not committed at all. A vendor
# that changes format, or a future adapter nobody has measured, degrades to
# WITHHELD rather than LEAKED.
#
# Adding a vocabulary here is a security decision and is pinned by
# `test_recognized_vocabularies_are_pinned` so it cannot happen quietly.

VOCAB_CODEX = "codex_rollout"
VOCAB_CLAUDE = "claude_session"
VOCAB_CAPTURE_HOOK_LOG = "capture_hook_log"

# Each entry names WHAT was measured, on WHAT data. Do not add one without
# a real session file in hand.
RECOGNIZED_VOCABULARIES = frozenset({
    # payload.type taxonomy; call_id join measured over 150 rollouts:
    # 3,530 pairs, 0 orphans either side, 0 duplicate ids within a file.
    # Inside it, row types, response item types, `event_msg` types, turn
    # items and the fields of several row types are default deny too
    # (CODEX_ROW_TYPES, CODEX_RESPONSE_ITEM_TYPES, CODEX_EVENT_MSG_TYPES,
    # CODEX_ITEM_FIELDS, CODEX_ROW_SCHEMAS; 844 rollouts measured).
    VOCAB_CODEX,
    # tool_use.id -> tool_result.tool_use_id; measured 85/85 joined, no
    # orphans. Row types are open-ended (assistant/user/attachment/
    # last-prompt/queue-operation observed), so rows are recognized by their
    # uuid/sessionId spine rather than an enumerated type list -- plus the
    # two spine-less file-history row types (CLAUDE_FILE_HISTORY_TYPES),
    # which every real session contains.
    VOCAB_CLAUDE,
    # Our OWN hook records (.codex/hooks, .claude/hooks, .cursor/hooks) -- we
    # write them, so the shape is verified by construction. They log each
    # hook payload VERBATIM, so Cursor's edit text is in here too; it is
    # withheld for outside files by the edit-record rule, not by a join.
    VOCAB_CAPTURE_HOOK_LOG,
})

# Text transcripts have no record vocabulary to verify. This one is the
# exception because it is USER INPUT ONLY -- every line is something the
# student typed, which decision rule 2 keeps unconditionally.
USER_INPUT_ONLY_SOURCES = frozenset({".aider.input.history"})


# The NORMALIZED tier (`normalized/*.jsonl`, `interaction-stream.jsonl`) is
# OUR OWN schema, so default deny applies here too -- just per RECORD rather
# than per file. Gating the whole file would withhold everything; gating each
# record works because we can enumerate our own event types exactly.
#
# This closes the drift hole: withholding in this tier depends on
# `is_output_record` matching the literal string "tool_result", so a
# normalizer that ever emitted "tool_output" instead -- a typo, a refactor,
# or a new adapter's normalizer (the Cursor one is still unwritten) -- would
# silently pass external file content straight through. An event type we do
# not recognize now FAILS CLOSED instead.
#
# Pinned by `test_normalized_event_types_are_pinned`: adding one is a
# deliberate, visible edit, because it is an assertion that the taint join
# handles that record shape.
NORMALIZED_EVENT_TYPES = frozenset({
    # session/turn structure
    "session_start", "session_end", "turn_start", "turn_end",
    # authored text -- never withheld (instructor decision); a
    # `slash_command` is a typed Claude Code `/` command
    "user_prompt", "slash_command", "assistant_message", "reasoning",
    "system_message", "attachment", "unclassified_text",
    # tool call/result pair -- the taint join's whole purpose
    "tool_start", "tool_result",
    # approvals + hook bookkeeping
    "approval_request", "hook_event",
    # per-edit provenance from our own hooks (no text; see record_edit)
    "file_edit",
})
# claude_normalize emits `raw_<vendor type>` for rows it does not model.
# Recognized as a FAMILY (the prefix is ours), and they are pass-through
# vendor payloads, so the taint join still applies to their contents.
NORMALIZED_RAW_PREFIX = "raw_"


def normalized_event_recognized(event_type: str) -> bool:
    if not event_type:
        return False
    return (event_type in NORMALIZED_EVENT_TYPES
            or event_type.startswith(NORMALIZED_RAW_PREFIX))


def row_vocabulary(row: Any) -> Optional[str]:
    """Which verified vocabulary this row belongs to, or None."""
    if not isinstance(row, dict):
        return None
    # Checked first: our hook records also carry a session_id.
    if "hook_event_name" in row or (
            "adapter_name" in row and "event_type" in row):
        return VOCAB_CAPTURE_HOOK_LOG
    if isinstance(row.get("payload"), dict) and isinstance(row.get("type"), str):
        return VOCAB_CODEX
    if "type" in row and ("uuid" in row or "sessionId" in row
                          or "session_id" in row):
        return VOCAB_CLAUDE
    # Claude Code's undo checkpoints carry neither uuid nor sessionId. Only
    # these two measured types are admitted -- by exact name, not by a
    # "file-history-" prefix -- and `_redact_file_history` keeps only
    # their declared fields.
    if _is_file_history_row(row) and "messageId" in row:
        return VOCAB_CLAUDE
    return None


def recognize_vocabulary(rows: List[Any]) -> Optional[str]:
    """The single vocabulary every row belongs to, else None (fail closed).

    ANY unrecognized row disqualifies the file. That is deliberately strict:
    a partially-understood transcript is exactly the case where the join
    silently covers some records and not others.
    """
    found: Set[str] = set()
    for row in rows:
        vocab = row_vocabulary(row)
        if vocab is None or vocab not in RECOGNIZED_VOCABULARIES:
            return None
        found.add(vocab)
    if len(found) != 1:
        return None
    return found.pop()


# --- call/output taint join -----------------------------------------------

def _iter_dicts(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _join_id(record: Mapping[str, Any]) -> Optional[str]:
    for key in JOIN_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _record_type(record: Mapping[str, Any]) -> str:
    """Vendor records key on `type`; our normalized events on `event_type`."""
    value = record.get("type") or record.get("event_type") or ""
    return str(value).strip()


def is_call_record(record: Mapping[str, Any]) -> bool:
    """A tool INVOCATION, in any of the shapes the adapters produce."""
    rtype = _record_type(record)
    if (rtype in CALL_PAYLOAD_TYPES or rtype in BEGIN_EVENT_TYPES
            or rtype == NORMALIZED_CALL_TYPE):
        return True
    if rtype == CLAUDE_CALL_TYPE and "input" in record:
        return True
    # Hook payloads (PreToolUse) carry the invocation under `tool_input`.
    return "tool_input" in record and "tool_response" not in record


def is_output_record(record: Mapping[str, Any]) -> bool:
    """A tool RESULT -- the channel file contents enter the log through."""
    rtype = _record_type(record)
    if rtype in OUTPUT_PAYLOAD_TYPES or rtype in END_EVENT_TYPES:
        return True
    if rtype == NORMALIZED_OUTPUT_TYPE:
        return True
    if rtype == CLAUDE_OUTPUT_TYPE:
        return True
    return "tool_response" in record


class TaintIndex:
    """Per-transcript-file join between tool calls and their outputs.

    Membership is decided ONCE per file, before redaction, from the
    unredacted rows -- the join keys are opaque ids, so nothing about
    student content is needed to build it. A call is "tainted" when its
    output may not be kept (see WHICH OUTPUT IS KEPT).
    """

    def __init__(self) -> None:
        self.tainted: Set[str] = set()
        self.known: Set[str] = set()
        self.duplicated: Set[str] = set()
        # Turn items: how often each id occurs, and which vouch for their
        # own output (see VOUCHABLE_ITEM_TYPE).
        self.item_counts: Dict[str, int] = {}
        self.vouched: Set[str] = set()
        # Row position -> decision, for our normalized `tool_start` events
        # (see CALL_OUTPUT_KEPT_KEY).
        self.event_decisions: Dict[int, bool] = {}

    def add_call(self, call_id: Optional[str], tainted: bool) -> None:
        if not call_id:
            return
        if call_id in self.known:
            # A duplicate id makes the join ambiguous -> fail closed on it.
            self.duplicated.add(call_id)
        self.known.add(call_id)
        if tainted:
            self.tainted.add(call_id)

    def taint(self, call_id: Optional[str]) -> None:
        """Mark `call_id` tainted without registering a call for it.

        Used when a record that is not itself a call (a Codex turn item)
        shows that whatever ran under this id read outside the project.
        """
        if call_id:
            self.tainted.add(call_id)

    def add_item(self, item_id: Optional[str], vouched: bool) -> None:
        if not item_id:
            return
        self.item_counts[item_id] = self.item_counts.get(item_id, 0) + 1
        if vouched:
            self.vouched.add(item_id)

    def item_output_is_withheld(self, item_id: Optional[str]) -> bool:
        """A turn item's output: joined to a call, it follows that call
        (and the item's own invocation, which taints the id when it fails);
        otherwise it is kept only if the item -- the only one with its id --
        vouched for itself."""
        if not item_id or item_id in self.known:
            return self.output_is_withheld(item_id)
        return not (item_id in self.vouched
                    and self.item_counts.get(item_id) == 1)

    def output_is_withheld(self, call_id: Optional[str]) -> bool:
        """Fail closed: unjoinable outputs are withheld, not trusted.

        Not observed once in 3,530 real pairs -- which is exactly why it
        must be handled rather than assumed away.
        """
        if not call_id:
            return True
        if call_id in self.duplicated or call_id not in self.known:
            return True
        return call_id in self.tainted


def _call_workdirs(record: Mapping[str, Any]) -> List[str]:
    """The directories a record says its relative paths resolve against.

    Load-bearing: an exec whose `workdir` is outside the project makes every
    relative path in that command external, so the call must taint even when
    the command itself names only bare filenames (`cat notes.py`).

    Most specific first, and the first kind found wins:
      1. an explicit `workdir` key;
      2. directories stated INSIDE the invocation -- a dict's `workdir`, or
         `workdir:` in the Codex exec JavaScript. One script can run several
         commands, so every distinct one is returned;
      3. for our own normalized events, the vendor record they were built
         from (`extras.raw_payload`): a `function_call`'s workdir lives only
         in its `arguments`, never in the event's extracted `command`;
      4. the directory the record itself ran in (`cwd`).
    Returns [] when none is stated. A workdir that is stated but cannot be
    read comes back as UNREADABLE_DIR -- the directory is unknown, not
    absent (see `js_stated_workdirs`). When a script states a directory for
    some calls but not others, INHERITED_DIR stands for the others: they
    run in the enclosing directory, which is then a candidate too.
    """
    if "workdir" in record or "working_dir" in record:
        return _dict_stated_workdirs(record, ("workdir", "working_dir"))
    stated: List[str] = []
    for field in INPUT_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value.lstrip().startswith("{"):
            parsed = _try_json(value)  # JSON-encoded function arguments
            value = parsed if isinstance(parsed, dict) else value
        if isinstance(value, dict):
            stated.extend(_dict_stated_workdirs(value))
        elif isinstance(value, str) and _JS_SCRIPT_HINT_RE.search(value):
            stated.extend(js_stated_workdirs(value))
    if all(d == INHERITED_DIR for d in stated):
        stated = []  # nothing stated at all: the enclosing directory applies
    extras = record.get("extras")
    raw = extras.get("raw_payload") if isinstance(extras, dict) else None
    if not stated and isinstance(raw, dict):
        stated = _call_workdirs(raw)
    if stated:
        return list(dict.fromkeys(stated))  # distinct, in order
    for key in ("cwd", "directory"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def _readable_dir(value: Any) -> str:
    """A stated directory, or UNREADABLE_DIR if it is not a usable string."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNREADABLE_DIR


# Codex rows that state the directory the session is working in. A turn
# can change it, so each `turn_context` restates it.
SESSION_DIR_ROW_TYPES = frozenset({"session_meta", "turn_context"})


def _next_session_base(redactor: "Redactor", row: Any,
                       base: Optional[str]) -> Optional[str]:
    """The session's working directory once `row` has been read."""
    if not isinstance(row, dict) or row.get("type") not in SESSION_DIR_ROW_TYPES:
        return base
    payload = row.get("payload")
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not cwd.strip():
        return base
    return redactor.resolve_dir(cwd, base)


def _is_codex_item_event(record: Mapping[str, Any]) -> bool:
    """True for a Codex `item_completed` payload (see CODEX_ITEM_FIELDS)."""
    return record.get("type") == CODEX_ITEM_EVENT_TYPE and "item" in record


def _item_fields(item: Any) -> Optional[ItemFields]:
    """The declared fields of a turn item, or None for an unknown type."""
    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
        return None
    return CODEX_ITEM_FIELDS.get(item["type"])


def _is_shell_argv(command: Any) -> bool:
    """True for `[<shell>, "-c" | "-lc", <script>]` with a known shell."""
    return (isinstance(command, list) and len(command) == 3
            and all(isinstance(part, str) for part in command)
            and command[0] in VOUCHABLE_SHELLS
            and command[1] in SHELL_SCRIPT_FLAGS)


def _argv_script(words: List[Any]) -> Optional[str]:
    """A command given as a list of words, as one script: the script in
    `[<shell>, "-lc", <script>]`, or the words quoted back together (run
    without a shell, they are one command)."""
    if not words or not all(isinstance(word, str) for word in words):
        return None
    return words[2] if _is_shell_argv(words) else shlex.join(words)


def _is_venv_program(rel: str, name: "re.Pattern[str]") -> bool:
    """True for the project venv's program called `name` (see STUDENT CODE)."""
    match = PROJECT_VENV_PROGRAM_RE.fullmatch(rel)
    return bool(match and name.fullmatch(match.group("name")))


def _pytest_arguments(args: List[str]) -> Optional[List[str]]:
    """The words of a pytest run to check as files (its test paths and
    expressions), or None for an option not on the list."""
    named: List[str] = []
    position = 0
    while position < len(args):
        word = args[position]
        if word == "--":
            return named + args[position:]
        if word in PYTEST_VALUE_OPTIONS:
            if position + 1 >= len(args):
                return None
            named.append(args[position + 1])
            position += 2
            continue
        if word.startswith("-") and word != "-":
            if word not in PYTEST_FLAGS and not PYTEST_GLUED_OPTIONS.fullmatch(
                    word):
                return None
        else:
            named.append(word)
        position += 1
    return named


def _run_tests_arguments(args: List[str]) -> Optional[List[str]]:
    """The words of a `run_tests.py` run to check as files, or None for
    anything but its listed options."""
    named: List[str] = []
    position = 0
    while position < len(args):
        word = args[position]
        if RUN_TESTS_FLAGS.fullmatch(word):
            position += 1
            continue
        if word not in RUN_TESTS_VALUE_OPTIONS or position + 1 >= len(args):
            return None
        value = args[position + 1]
        allowed = RUN_TESTS_VALUE_OPTIONS[word]
        if allowed is not None and value not in allowed:
            return None
        named.append(value)
        position += 2
    return named


def _is_public_web_address(url: Any) -> bool:
    """True for an http(s) address of a host that is not this machine or
    its network (a local server can serve a local file)."""
    if not isinstance(url, str):
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or not host:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if _NUMERIC_HOST_LABEL_RE.fullmatch(host.rsplit(".", 1)[-1]):
        return False  # an address written another way: `127.1`, `0x7f.1`
    return ("." in host and host != "localhost"
            and not host.endswith(_LOCAL_HOST_SUFFIXES))


def _copied_commands(parts: List[Any]) -> Set[str]:
    """The command texts our normalizers copy out of these records into an
    event's `command` (see `codex_normalize._extract_command`)."""
    found: Set[str] = set()
    for part in parts:
        if not isinstance(part, dict):
            continue
        sources = [part]
        arguments = part.get("arguments")
        if isinstance(arguments, str):
            arguments = _try_json(arguments)
        sources.extend(nested for nested in (arguments, part.get("tool_input"))
                       if isinstance(nested, dict))
        for source in sources:
            for key in ("command", "cmd", "input", "patch"):
                value = source.get(key)
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    found.add(" ".join(str(word) for word in value))
    return found


def _change_targets(changes: Dict[Any, Any]) -> List[str]:
    """The files a FileChange touches: each key, plus any `move_path`.

    The entry VALUES are not the invocation -- they hold the diff, and a
    `move_path` of null would otherwise read as an unreadable input.
    """
    targets: List[str] = [str(key) for key in changes]
    for record in _iter_dicts(list(changes.values())):
        if isinstance(record.get("move_path"), str):
            targets.append(record["move_path"])
    return targets


def _plain_shell_segments(script: str) -> Optional[List[List[str]]]:
    """The words of each command in a PLAIN script, or None (see WHICH
    OUTPUT IS KEPT). Redirections are split off: the files `<` reads are
    returned as the last words of a segment, after a "" marker."""
    if any(ch in script for ch in _UNPLAIN_ANYWHERE):
        return None
    if _UNPLAIN_UNQUOTED_RE.search(_SHELL_QUOTED_RE.sub(" ", script)):
        return None
    # Quoted text cannot separate commands; blank it out (same length) so
    # the separators found line up with the real script.
    masked = _SHELL_QUOTED_RE.sub(
        lambda m: m.group(0)[0] + "_" * (len(m.group(0)) - 2) + m.group(0)[-1],
        script)
    segments: List[List[str]] = []
    start = 0
    for end, after in [(m.start(), m.end())
                       for m in _SEGMENT_SEP_RE.finditer(masked)] + [
                           (len(script), len(script))]:
        try:
            words = shlex.split(script[start:end], posix=True)
        except ValueError:
            return None
        start = after
        if any(word.startswith("=") for word in words):
            return None  # zsh: `=cat` is the path of the cat program
        split = _split_redirections(words)
        if split is None:
            return None
        if split[0]:
            segments.append(split[0] + [""] + split[1])
        elif split[1]:
            return None  # a redirection with no command
    return segments


def _split_redirections(words: List[str]
                        ) -> Optional[Tuple[List[str], List[str]]]:
    """(command words, files the redirections read); None unless every
    redirection is a plain one (see WHICH OUTPUT IS KEPT)."""
    command: List[str] = []
    reads: List[str] = []
    pending: Optional[Tuple[str, str]] = None  # an operator awaiting its target
    for word in words:
        if pending is not None:
            if not _plain_redirection(*pending, word, reads):
                return None
            pending = None
            continue
        if "<" not in word and ">" not in word:
            command.append(word)
            continue
        match = _REDIRECT_RE.fullmatch(word)
        if match is None:
            return None  # `a>b`, `x<y`: not a plain redirection
        stream, operator, target = match.groups()
        if target == "":
            pending = (stream, operator)
        elif not _plain_redirection(stream, operator, target, reads):
            return None
    if pending is not None:
        return None
    return command, reads


def _plain_redirection(stream: str, operator: str, target: str,
                       reads: List[str]) -> bool:
    """True for a plain redirection; the file it reads goes into `reads`.

    `<file` reads a file; `>` and `>>` may only write to a stream device or
    copy a stream (`2>&1`), never to a file. A target that starts with
    `<`, `>`, `&`, `-` or `=` is something else (zsh's `<->` is a glob).
    """
    if operator != "<" and stream != "&" and _STREAM_COPY_RE.fullmatch(target):
        return True
    if not target or target[0] in "<>&-=":
        return False
    if operator == "<":
        if stream == "&":
            return False
        reads.append(target)
        return True
    return _is_stream_device(target)


def _option_words(args: List[str]) -> List[str]:
    """The options among `args`: words starting with `-`, up to `--`."""
    options: List[str] = []
    for word in args:
        if word == "--":
            break
        if word.startswith("-") and word != "-":  # `-` alone is stdin
            options.append(word)
    return options


def _read_only_arguments(words: List[str]) -> Optional[List[str]]:
    """The arguments of a read-only command that may name files, or None
    when the command is not one (see READ_ONLY_PROGRAMS)."""
    verb, args = words[0], words[1:]
    if verb == "sed":
        return _sed_files(args)
    if verb == "git":
        return _git_arguments(args)
    allowed = READ_ONLY_PROGRAMS.get(verb)
    if allowed is None or not all(
            allowed.fullmatch(word) for word in _option_words(args)):
        return None
    return args


def _sed_files(args: List[str]) -> Optional[List[str]]:
    """The files of `sed -n 'N,Mp' <files>`, or None for any other sed."""
    files: List[str] = []
    printed = False
    for word in args:
        if word in SED_OPTIONS:
            continue
        if word.startswith("-"):
            return None
        if not printed:
            if not _SED_PRINT_RE.fullmatch(word):
                return None
            printed = True
            continue
        files.append(word)
    return files if printed else None


def _git_arguments(args: List[str]) -> Optional[List[str]]:
    """The arguments of a read-only git subcommand, or None."""
    position = 0
    while position < len(args) and args[position].startswith("-"):
        if args[position] not in GIT_GLOBAL_OPTIONS:
            return None
        position += 1
    if position >= len(args):
        return None
    subcommand, rest = args[position], args[position + 1:]
    allowed = READ_ONLY_GIT_COMMANDS.get(subcommand)
    options = _option_words(rest)
    if allowed is None or not all(allowed.fullmatch(word) for word in options):
        return None
    if (subcommand == "branch" and len(options) < len(rest)
            and not GIT_BRANCH_LIST_OPTIONS & set(options)):
        return None  # `git branch <name>` creates a branch
    if subcommand in GIT_REVISION_READERS and _git_operands(subcommand, rest):
        return None  # a revision, or a path not marked as one by `--`
    return rest


def _git_operands(subcommand: str, args: List[str]) -> List[str]:
    """The words before a `--` that are not options: revisions, or paths
    git may read as revisions. `git grep`'s first one is its pattern."""
    before = args[:args.index("--")] if "--" in args else args
    operands = [word for word in before if not word.startswith("-")]
    return operands[1:] if subcommand == "grep" else operands


def _parsed_paths(parsed: Any) -> Optional[List[str]]:
    """The paths in Codex's parse of a command, or None if it is unreadable."""
    if not isinstance(parsed, list):
        return None
    paths: List[str] = []
    for entry in parsed:
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            return None
        path = entry.get("path")
        if path is None:
            continue
        if not isinstance(path, str):
            return None
        paths.append(path)
    return paths


def build_taint_index(redactor: "Redactor", rows: List[Any],
                      base: Optional[str] = None) -> TaintIndex:
    """Decide, per call id, whether its output must be withheld.

    `base` is where relative paths resolve before any row says otherwise;
    Codex rows then update it as the session's cwd is stated.
    """
    redactor.prime_command_words(rows, base)
    return _decide_calls(redactor, rows, base)


def _decide_calls(redactor: "Redactor", rows: List[Any],
                  base: Optional[str]) -> TaintIndex:
    """`build_taint_index`, once git has been asked about the paths."""
    index = TaintIndex()
    for position, row in enumerate(rows):
        base = _next_session_base(redactor, row, base)
        # A call nested inside a row (a Claude tool_use) ran where the row
        # says it ran.
        row_base = (redactor.record_base(row, base)
                    if isinstance(row, dict) else base)
        for record in _call_bearing_records(row):
            parent = base if record is row else row_base
            if is_call_record(record):
                kept = redactor.call_output_is_provable(record, parent)
                index.add_call(_join_id(record), not kept)
                if record is row and "event_type" in row:
                    index.event_decisions[position] = kept
            elif _is_codex_item_event(record):
                # A turn item names its own invocation too. If that is not
                # provable, taint its id -- which also withholds any call
                # output joined to the same id.
                item = record["item"]
                fields = _item_fields(item)
                if fields is None or not fields.invocation:
                    continue
                kept = redactor.item_output_is_provable(item, parent)
                vouches = item.get("type") == VOUCHABLE_ITEM_TYPE
                index.add_item(_join_id(item), kept and vouches)
                if not kept:
                    index.taint(_join_id(item))
    return index


def _call_bearing_records(obj: Any) -> Iterator[dict]:
    """`_iter_dicts`, except that one of our normalized call events is not
    searched: the vendor record and tool input it copies are part of its
    own decision, and registering them again would duplicate its id."""
    if isinstance(obj, dict):
        yield obj
        if "event_type" in obj and is_call_record(obj):
            return
        for value in obj.values():
            yield from _call_bearing_records(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _call_bearing_records(item)


def _codex_event_recognized(payload: Mapping[str, Any]) -> bool:
    """True if a Codex `event_msg` payload has a declared shape."""
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type:
        return event_type in CODEX_EVENT_MSG_TYPES
    return (payload.get("role") in LEGACY_MESSAGE_ROLES
            and set(payload) <= LEGACY_MESSAGE_KEYS)


def _codex_schema_for(record: Mapping[str, Any]) -> Any:
    """The field schema for a Codex row or response item, or None."""
    kind = record.get("type")
    if not isinstance(kind, str):
        return None
    if _is_schema_row(record):
        return CODEX_ROW_SCHEMAS[kind]
    # A response item. One that also has a `payload` key still keeps to its
    # item schema, where `payload` is undeclared and so withheld -- fail
    # closed rather than open.
    return CODEX_RESPONSE_ITEM_SCHEMAS.get(kind)


def _is_schema_row(record: Mapping[str, Any]) -> bool:
    """True for a Codex ROW whose type has a schema (its payload is a dict)."""
    kind = record.get("type")
    return (isinstance(kind, str) and kind in CODEX_ROW_SCHEMAS
            and isinstance(record.get("payload"), dict))


def _is_schema_raw_copy(record: Mapping[str, Any]) -> bool:
    """True for our normalized events' `extras`, holding the payload of a
    Codex row whose type has a schema (codex_normalize copies it there)."""
    kind = record.get("raw_type")
    return (isinstance(kind, str) and kind in CODEX_ROW_SCHEMAS
            and isinstance(record.get("raw_payload"), dict))


def _carries_no_text(value: Any) -> bool:
    """A boolean, a number, null, or an empty string or container."""
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, (str, list, dict)) and not value


def _response_item_rules(item: Any) -> Any:
    """Default deny for one Codex response item (see CODEX_RESPONSE_ITEM_TYPES)."""
    if isinstance(item, dict) and item.get("type") in CODEX_RESPONSE_ITEM_TYPES:
        return item
    return _skeleton_unrecognized(item, "unrecognized_item_type")


def _row_metadata(metadata: Mapping[Any, Any]) -> Dict[Any, Any]:
    """A row's `metadata`: its declared flags, if they carry no text."""
    out: Dict[Any, Any] = {}
    hidden = 0
    for key, value in metadata.items():
        if key in CODEX_ROW_METADATA_KEYS and _carries_no_text(value):
            out[key] = value
        elif key in CODEX_ROW_METADATA_KEYS:
            out[key] = _withheld("undeclared_field")
        else:
            hidden += 1
            out[f"<withheld-key-{hidden}>"] = _withheld("undeclared_field")
    return out


def apply_codex_row_rules(row: Dict[Any, Any]) -> Dict[Any, Any]:
    """Default deny for one ALREADY-REDACTED Codex rollout row.

    Only the measured row types, response item types and `event_msg` types
    keep their content; any other keeps only its skeleton, so a Codex
    release that adds a new row degrades to WITHHELD rather than LEAKED.
    A top-level key no measured row has is withheld too.
    """
    out: Dict[Any, Any] = {}
    for key, value in row.items():
        if key in CODEX_ROW_KEYS:
            out[key] = value
        elif key == "metadata" and isinstance(value, dict):
            out[key] = _row_metadata(value)
        else:
            out[key] = _withheld("undeclared_field")
    payload = row.get("payload")
    row_type = row.get("type")
    if row_type not in CODEX_ROW_TYPES:
        out["payload"] = _skeleton_unrecognized(payload, "unrecognized_row_type")
    elif row_type == "event_msg" and not _codex_event_recognized(payload):
        out["payload"] = _skeleton_unrecognized(payload)
    elif row_type == "response_item":
        out["payload"] = _response_item_rules(payload)
    elif row_type == "compacted":
        # The saved history is a list of response items, held to the same
        # rule one by one.
        out["payload"] = {
            key: ([_response_item_rules(item) for item in value]
                  if key in CODEX_HISTORY_KEYS and isinstance(value, list)
                  else value)
            for key, value in payload.items()}
    return out


def _apply_item_rules(item: Any, index: TaintIndex) -> Any:
    """Default deny for one Codex turn item (see CODEX_ITEM_FIELDS).

    Declared keys are kept, OUTPUT keys are kept only when the item's
    output is not withheld by the join (or, for an unjoinable id, when the
    item vouched for itself), and every other key is withheld.

    An item type with no invocation to check (WebSearch, Extension) never
    keeps its output: an id that happens to match an in-project call says
    nothing about what THIS item read (review probe R10). An empty output
    value carries no bytes, so it is left as it is.
    """
    fields = _item_fields(item)
    if fields is None:
        return _skeleton_unrecognized(item, "unrecognized_item_type")
    if fields.kinds and item.get("kind") not in fields.kinds:
        return _skeleton_unrecognized(item, "unrecognized_item_kind")
    output_withheld = (not fields.invocation
                       or index.item_output_is_withheld(_join_id(item)))
    out: Dict[Any, Any] = {}
    withheld = False
    for key, value in item.items():
        if key in CODEX_ITEM_COMMON_KEYS or key in fields.kept:
            out[key] = apply_taint(value, index)
        elif key in fields.output and (
                not output_withheld or value in (None, "", [], {})):
            out[key] = apply_taint(value, index)
        elif key == "withheld" and value is True:
            out[key] = True  # our own flag, from an earlier pass
        else:
            reason = ("external_read" if key in fields.output
                      else "undeclared_field")
            out[key] = _withheld(reason)
            withheld = True
    if withheld:
        out["withheld"] = True
    return out


def apply_taint(row: Any, index: TaintIndex) -> Any:
    """Withhold the output of every tainted (or unjoinable) call.

    Operates on the ALREADY-REDACTED row: join ids are opaque and survive
    redaction unchanged, so the index built from the originals still
    applies. Call records are deliberately left alone -- keeping the
    sentinel-ized invocation is how "the student read something outside the
    project" stays visible.

    A Codex turn item keeps only its declared fields, and its output
    follows the same join (see CODEX_ITEM_FIELDS). Row, response item and
    event types are checked afterwards, by `apply_codex_row_rules`.
    """
    if isinstance(row, dict):
        if is_output_record(row) and index.output_is_withheld(_join_id(row)):
            return _skeleton(row)
        if _is_codex_item_event(row):
            out = {key: apply_taint(value, index)
                   for key, value in row.items() if key != "item"}
            out["item"] = _apply_item_rules(row["item"], index)
            return out
        out = {}
        for key, value in row.items():
            # Claude keeps the structured result beside the tool_result
            # block, in the same row; it is governed by that block's join.
            if key == CLAUDE_ROW_RESULT_KEY and _row_result_withheld(row, index):
                out[key] = {"withheld": True, "reason": "external_read"}
                continue
            out[key] = apply_taint(value, index)
        return out
    if isinstance(row, list):
        return [apply_taint(item, index) for item in row]
    return row


def _row_result_withheld(row: Mapping[str, Any], index: TaintIndex) -> bool:
    for record in _iter_dicts(row.get("message")):
        if _record_type(record) == CLAUDE_OUTPUT_TYPE:
            return index.output_is_withheld(_join_id(record))
    return False


def _skeleton_unrecognized(record: Any,
                           reason: str = "unrecognized_event_type") -> Any:
    """Withhold a record whose type we do not recognize.

    Used for normalized events, Codex `event_msg` payloads and Codex turn
    items. Same shape as a taint-withheld output, different reason, so an
    instructor can tell "we withheld this because the agent read outside the
    project" from "we withheld this because the schema drifted and we will
    not guess what the record means".
    """
    if not isinstance(record, dict):
        return {"withheld": True, "reason": reason}
    out = {k: v for k, v in record.items()
           if str(k).lower() in UNRECOGNIZED_SKELETON_KEYS}
    out["withheld"] = True
    out["reason"] = reason
    return out


def _skeleton(record: Mapping[str, Any]) -> dict:
    """Keep the record and its join, drop everything that can carry bytes.

    The multi-file `exec` shape is why this is wholesale: one call can read
    an in-project and an out-of-project file in a single shell string and
    the outputs come back concatenated, so there is no in-data boundary to
    cut along. Withholding the blob is the only honest option.
    """
    out = {k: v for k, v in record.items()
           if str(k).lower() in OUTPUT_SKELETON_KEYS}
    out["withheld"] = True
    out["reason"] = "external_read"
    return out


# --- file-level pass ------------------------------------------------------

def _as_str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _iter_strings(obj: Any) -> Iterator[str]:
    """Every string nested anywhere inside lists and dict values."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_strings(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_strings(item)


def _is_path_shape(value: Any) -> bool:
    """True for a string, or a list made only of strings."""
    if isinstance(value, str):
        return True
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _withheld(reason: str) -> Dict[str, Any]:
    """The marker that replaces a withheld value, so the gap stays visible."""
    return {"withheld": True, "reason": reason}


def _is_withheld_marker(value: Any) -> bool:
    """True for a marker produced by `_withheld` (on this or an earlier pass)."""
    return (isinstance(value, dict) and value.get("withheld") is True
            and set(value) <= {"withheld", "reason"})


def _is_file_history_row(record: Mapping[str, Any]) -> bool:
    """True for a Claude Code file-history row.

    Checks the type is a STRING first: in real transcripts `type` is also a
    JSON-schema key whose value can be a list (``["string", "null"]``), and
    a list cannot be looked up in a set.
    """
    rtype = record.get("type")
    return isinstance(rtype, str) and rtype in CLAUDE_FILE_HISTORY_TYPES


def _file_history_entries(record: Mapping[str, Any]) -> List[Tuple[Any, Any]]:
    """(tracked path, backup) pairs named by a Claude file-history row."""
    rtype = record.get("type")
    if rtype == "file-history-delta":
        return [(record.get("trackingPath"), record.get("backup"))]
    if rtype == "file-history-snapshot":
        snapshot = record.get("snapshot")
        backups = (snapshot.get("trackedFileBackups")
                   if isinstance(snapshot, dict) else None)
        if isinstance(backups, dict):
            return list(backups.items())
    return []


def _tracked_real_file(tracked: Any, backup: Any) -> Optional[str]:
    """Where a tracked file really lives: ``realParentDir/<file name>``.

    None when that cannot be worked out (missing or non-string fields).
    """
    if not isinstance(tracked, str) or not isinstance(backup, dict):
        return None
    parent = backup.get("realParentDir")
    if not isinstance(parent, str) or not parent.strip():
        return None
    name = tracked.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not name:
        return None
    return os.path.join(parent, name)


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _withheld_marker(source_rel: str, reason: str, byte_count: int) -> str:
    return json.dumps(
        {
            "withheld": True,
            "reason": reason,
            "bytes": byte_count,
            "source": source_rel,
        },
        sort_keys=True, indent=2,
    ) + "\n"


def _write_if_changed(path: Path, payload: str) -> None:
    """Write `payload` to `path` only if it differs, and ATOMICALLY.

    Skipping identical writes keeps bytes (and so the snapshot tree SHA)
    stable across runs.

    Atomic, because an agent hook kills the snapshot process after 25
    seconds: the payload goes to a temp file in the same directory, which is
    then renamed over the target. A rename is all-or-nothing, so the target
    is always either the complete old file or the complete new one. A kill
    between the two steps leaves only the temp file behind, and
    `_sweep_temp_files` removes it on the next pass.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            return
    except (OSError, UnicodeDecodeError):
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}{TEMP_SUFFIX}")
    try:
        tmp.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        _remove_stale(tmp)  # a no-op once the rename has happened


def _sweep_temp_files(root: Path) -> None:
    """Delete temp files that a killed pass left behind under `root`.

    Only safe while no other redaction pass is running; the orchestrator
    lock guarantees that. It matters because `redacted/` is force-staged:
    a leftover temp file there would otherwise be committed.
    """
    try:
        leftovers = [p for p in root.rglob("*" + TEMP_SUFFIX) if p.is_file()]
    except OSError:
        return
    for path in leftovers:
        _remove_stale(path)


# --- skip unchanged inputs ------------------------------------------------
#
# Redaction runs on every snapshot trigger, and a single large rollout can
# take seconds. Nearly every input is unchanged since the previous pass, so
# the cache remembers what each input looked like when it was redacted and
# what that produced, and an input is only redone when something changed.
#
# Benchmark (2026-09, real Codex rollouts, one file per `redact_traces`
# pass, macOS laptop). Before: 11 MB 3.5s, 25 MB 20s, 87 MB over 120s --
# superlinear, from the credential pattern and from resolving multi-MB
# base64 blobs as if they were paths (see MAX_PATH_CHARS). After:
#
#   size      first pass   second pass (unchanged input)
#    1.0 MB      0.3s          0.03s
#    5.0 MB      1.5s          0.03s
#   11.3 MB      2.8s          0.02s
#   25.6 MB      7.8s          0.04s
#   52.1 MB      9.4s          0.01s
#   87.5 MB     17.5s          0.02s
#
# A first pass over a very large rollout can still exceed a hook's 25s
# budget on a slow machine. That is safe -- writes are atomic and the cache
# keeps finished files -- and the next trigger without that budget (pytest,
# post-commit) completes it.

CACHE_FILENAME = "auto-track-redaction-cache.json"
CACHE_VERSION = 1
# A file modified within this long before we looked at it could be modified
# again within the same mtime tick without its mtime changing (the "racily
# clean" problem git also handles), so its mtime is not trusted on its own.
RACY_WINDOW_NS = 2_000_000_000


def _file_state(path: Path) -> Optional[Dict[str, int]]:
    """Size and mtime of `path`, or None if it does not exist."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _sha256_file(path: Path) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _policy_fingerprint(repo: Path) -> str:
    """Hash of everything besides the input that decides the output.

    If any of it changes, every cached entry is stale: the redaction rules
    (this file's source), where the project is (in-project vs external is
    relative to it), the home directory (`~` expansion) and the ignore
    rules (gitignored files count as external).

    Known gap: nested `.gitignore` files and a global excludes file are not
    included. A change to those is picked up the next time the input itself
    changes.
    """
    digest = hashlib.sha256()
    digest.update(str(CACHE_VERSION).encode())
    for label, value in (("rules", _read_bytes(Path(__file__))),
                         ("repo", os.path.realpath(str(repo)).encode()),
                         ("home", str(Path.home()).encode()),
                         ("gitignore", _read_bytes(repo / ".gitignore")),
                         ("exclude", _read_bytes(repo / ".git" / "info" / "exclude"))):
        digest.update(label.encode() + b"\0" + value + b"\0")
    return digest.hexdigest()


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b"<missing>"


def _output_stat(path: Path, repo: Path) -> Optional[os.stat_result]:
    """Inspect staged output without following an alias or opening a device.

    Git stages symlinks themselves, not the regular files their targets name.
    Every existing component below the repository root must therefore have
    the same ordinary-file/directory meaning for Git and for the sanitizer.
    The root itself may have a platform alias (for example macOS /tmp).
    """
    relative = path.absolute().relative_to(repo.absolute())
    current = repo
    parts = relative.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            return None
        reparse = getattr(state, "st_file_attributes", 0) & 0x400
        final = index == len(parts) - 1
        if (reparse or stat.S_ISLNK(state.st_mode)
                or not (stat.S_ISREG(state.st_mode) if final
                        else stat.S_ISDIR(state.st_mode))):
            raise ValueError("staged_output_invalid")
    return state


def _output_state(path: Path, repo: Path) -> Optional[Dict[str, Any]]:
    """Hash regular staged bytes; missing files are an explicit cache state."""
    before = _output_stat(path, repo)
    if before is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if (not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError("staged_output_invalid")
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    after = _output_stat(path, repo)
    if (after is None or (before.st_dev, before.st_ino, before.st_size,
                         before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError("staged_output_changed")
    return {"size": after.st_size, "mtime_ns": after.st_mtime_ns,
            "sha256": digest.hexdigest()}


def _write_staged_output(path: Path, payload: str, repo: Path) -> None:
    _output_stat(path, repo)
    _write_if_changed(path, payload)
    _output_stat(path, repo)


def _remove_required(path: Path, repo: Path) -> None:
    """Required removal must fail the pass if unsafe bytes remain staged."""
    _output_stat(path, repo)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class RedactionCache:
    """Which inputs were already redacted, and what they looked like then.

    One entry per input file (keyed by its repo-relative path) holds the
    input's size, mtime and sha256 as of that redaction, plus the size,
    mtime and sha256 of each output it produced (None = "must not exist"). An input
    is skipped only if the input AND all its outputs still match.

    Checking an input is cheap: size and mtime first, and only if the mtime
    moved (or is too recent to trust) the content hash. A file that was
    merely touched or re-copied is therefore still skipped.

    Stored in `.git/`, so it is never committed and cannot disturb snapshot
    dedupe. Without a `.git` directory there is no cache and every input is
    redacted every time, which is slower but still correct.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        git_dir = repo / ".git"
        self.path = git_dir / CACHE_FILENAME if git_dir.is_dir() else None
        self.fingerprint = _policy_fingerprint(repo)
        self.entries: Dict[str, Any] = {}
        self._dirty = False
        self._last_save = time.monotonic()
        if self.path is None:
            return
        # The cache is written atomically too; drop temps a kill left behind.
        for leftover in git_dir.glob(f".{CACHE_FILENAME}.*{TEMP_SUFFIX}"):
            _remove_stale(leftover)
        data = _try_json(_read_bytes(self.path).decode("utf-8", "replace"))
        if (isinstance(data, dict) and data.get("fingerprint") == self.fingerprint
                and isinstance(data.get("entries"), dict)):
            self.entries = data["entries"]

    def is_fresh(self, key: str, src: Path, outputs: List[Path], *,
                 verify_content: bool = False) -> bool:
        """True if `src` and every output are exactly as last recorded.

        Generated tiers are staged in place, so their bytes must be verified
        even when a copy preserved old metadata. Raw sources have a separate
        sanitized derivative and can retain the metadata fast path.
        """
        entry = self.entries.get(key)
        state = _file_state(src)
        if not isinstance(entry, dict) or state is None:
            return False
        if state["size"] != entry.get("size"):
            return False
        mtime_trusted = (state["mtime_ns"] == entry.get("mtime_ns")
                         and state["mtime_ns"] < entry.get("checked_ns", 0) - RACY_WINDOW_NS)
        if verify_content or not mtime_trusted:
            digest = _sha256_file(src)
            if digest is None or digest != entry.get("sha256"):
                return False
            # Same bytes: remember the new mtime so the next check is cheap.
            entry["mtime_ns"] = state["mtime_ns"]
            entry["checked_ns"] = time.time_ns()
            self._dirty = True
        recorded = entry.get("outputs", {})
        return all(_output_state(path, self.repo) == recorded.get(self._key(path), "missing")
                   for path in outputs)

    def input_state(self, src: Path) -> Optional[Dict[str, Any]]:
        """Capture `src` BEFORE redacting it.

        Taken first on purpose: if the file changes while it is being
        redacted, the recorded state is the older one, so the next pass sees
        a mismatch and redoes it. The unsafe order would record the newer
        state and skip content that was never redacted.
        """
        state = _file_state(src)
        digest = _sha256_file(src)
        if state is None or digest is None:
            return None
        return {**state, "sha256": digest, "checked_ns": time.time_ns()}

    def record(self, key: str, before: Optional[Dict[str, Any]],
               outputs: List[Path]) -> None:
        """Remember a completed redaction of `key` (see `input_state`)."""
        if before is None:
            self.entries.pop(key, None)
        else:
            self.entries[key] = {
                **before,
                "outputs": {self._key(p): _output_state(p, self.repo) for p in outputs},
            }
        self._dirty = True
        # Save as we go, so a pass killed part-way still keeps its progress.
        if time.monotonic() - self._last_save > 2.0:
            self.save()

    def record_written(self, key: str, path: Path, payload: str) -> None:
        """Remember a file we rewrote in place with exactly `payload`."""
        data = payload.encode("utf-8")
        state = _file_state(path)
        if state is None or state["size"] != len(data):
            self.entries.pop(key, None)  # changed under us: do not trust it
            self._dirty = True
            return
        self.record(key, {**state, "sha256": hashlib.sha256(data).hexdigest(),
                          "checked_ns": time.time_ns()}, [])

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        payload = json.dumps({"fingerprint": self.fingerprint,
                              "entries": self.entries}, sort_keys=True)
        try:
            _write_if_changed(self.path, payload)
        except OSError:
            return
        self._dirty = False
        self._last_save = time.monotonic()

    def _key(self, path: Path) -> str:
        return ai_traces.repo_rel(self.repo, path)


# The prompt of a hook payload (UserPromptSubmit).
HOOK_PROMPT_KEYS = ("prompt", "user_prompt")

# TOOL CONTEXT INSIDE A TYPED PROMPT (review round 5). Codex Desktop and the
# VS Code extension send the student's request together with context of
# their own, in the same message: the page open in the app's browser
# (`# In app browser:`, after an `<in-app-browser-context>` block), the
# active file, its selected text and the open tabs (`# Context from my IDE
# setup:`), and files the student mentioned (`# Files mentioned by the
# user:`, their text included). The selection or a mentioned file may be
# ignored or outside the project. Measured over the student's 108 real
# rollouts: 22 typed messages hold such context, and in every one the
# student's own words follow the last `## My request:` or `## My request
# for Codex:` header. So a typed text with a context or request header
# keeps only what follows its last request header -- the rest, whatever it
# is, is the tool's -- and one with no request header after its last
# context header is withheld whole. Where something was cut, the record
# says so under TOOL_CONTEXT_KEY.
_TOOL_CONTEXT_HEADER_RE = re.compile(
    r"^# (?:In app browser|Context from my IDE setup"
    r"|Files mentioned by the user):[ \t]*$", re.MULTILINE)
_REQUEST_HEADER_RE = re.compile(r"^## My request(?: for Codex)?:[ \t]*$",
                                re.MULTILINE)
TOOL_CONTEXT_KEY = "tool_context"


def own_request(text: str) -> Optional[str]:
    """The part of a typed Codex text the student wrote (see above), or
    None when it cannot be told apart from the tool's context."""
    contexts = list(_TOOL_CONTEXT_HEADER_RE.finditer(text))
    requests = list(_REQUEST_HEADER_RE.finditer(text))
    if not contexts and not requests:
        return text
    if not requests or (contexts and contexts[-1].start() > requests[-1].start()):
        return None
    return text[requests[-1].end():].strip()


def _keep_own_request(record: Any, key: str) -> Any:
    """`record` with the tool's context cut from its typed text at `key`."""
    text = record.get(key) if isinstance(record, dict) else None
    if not isinstance(text, str):
        return record
    own = own_request(text)
    if own == text:
        return record
    out = dict(record)
    out[key] = _withheld(INJECTED_CONTEXT) if own is None else own
    out[TOOL_CONTEXT_KEY] = _withheld(INJECTED_CONTEXT)
    if out.get("text_elements"):  # places in the text before the cut
        out["text_elements"] = _withheld(INJECTED_CONTEXT)
    return out


def _keep_own_request_in_event(payload: Any) -> Any:
    """A Codex `event_msg` payload of the student's prompt, its tool context
    cut (a `user_message`, a legacy `{role, text}`, a UserMessage item)."""
    if not isinstance(payload, dict):
        return payload
    kind = payload.get("type")
    item = payload.get("item")
    if kind == "user_message":
        return _keep_own_request(payload, "message")
    if kind is None and payload.get("role") == TYPING_ROLE:
        return _keep_own_request(payload, "text")
    if (kind == CODEX_ITEM_EVENT_TYPE and isinstance(item, dict)
            and item.get("type") == USER_MESSAGE_ITEM
            and isinstance(item.get("content"), list)):
        return {**payload, "item": {**item, "content": [
            _keep_own_request(part, "text") for part in item["content"]]}}
    return payload
CODEX_ADAPTER = "codex"
CODEX_ROLLOUTS_DIR = ("codex", "raw", "rollouts")


@dataclass(frozen=True)
class Typing:
    """What the student typed in one transcript file (see `typed_texts`).

    `text in typing` asks whether the student typed `text`. A record from
    a session another agent started (SESSIONS ANOTHER AGENT STARTED) holds
    nothing typed: in a raw rollout -- one session per file -- that is the
    whole file; in our normalized tier, the events of those sessions and of
    the rollout files that hold one (a fork child). A Codex hook record
    holds something typed only in a session known to be the student's
    (HOOK LOGS NAME NO SESSION SOURCE); `codex_hooks` says the file's hook
    records are Codex's even where a record does not say so.
    """
    texts: FrozenSet[str] = frozenset()
    agent_file: bool = False
    agent_sessions: FrozenSet[str] = frozenset()
    agent_files: FrozenSet[str] = frozenset()
    student_sessions: FrozenSet[str] = frozenset()
    codex_hooks: bool = False

    def __contains__(self, text: object) -> bool:
        return text in self.texts

    def by_agent(self, record: Mapping[str, Any]) -> bool:
        """True for a record that holds nothing the student typed because
        another agent's session -- or, for a hook, maybe one -- wrote it."""
        if self.is_codex_hook(record):
            return _hook_session(record) not in self.student_sessions
        return (self.agent_file
                or record.get("session_id") in self.agent_sessions
                or _evidence_file(record) in self.agent_files)

    def is_codex_hook(self, record: Mapping[str, Any]) -> bool:
        """True for a raw Codex hook record, or our event built from one."""
        extras = record.get("extras")
        if isinstance(extras, dict) and "hook_event_name" in extras:
            return record.get("adapter_name") == CODEX_ADAPTER
        return ("hook_event_name" in record and "event_type" not in record
                and (record.get("adapter_name") == CODEX_ADAPTER
                     or (self.codex_hooks and "adapter_name" not in record)))


def _hook_session(record: Mapping[str, Any]) -> Any:
    """The session a hook record, or our event built from one, belongs to."""
    if "event_type" in record:
        return record.get("session_id")
    payload = record.get("payload")
    return payload.get("session_id") if isinstance(payload, dict) else None


def _evidence_file(record: Mapping[str, Any]) -> Optional[str]:
    """The raw file one of our events was built from."""
    refs = record.get("evidence_refs")
    if not isinstance(refs, list) or not refs or not isinstance(refs[0], str):
        return None
    path, _, line = refs[0].rpartition(":")
    return path if path and line.isdigit() else refs[0]


def _student_codex_sessions(repo: Path) -> FrozenSet[str]:
    """See HOOK LOGS NAME NO SESSION SOURCE."""
    students: Set[str] = set()
    agents: Set[str] = set()
    try:
        rollouts = sorted(repo.joinpath(ai_traces.AI_TRACES_DIR,
                                        *CODEX_ROLLOUTS_DIR).rglob("*.jsonl"))
    except OSError:
        return frozenset()
    for path in rollouts:
        metas = _leading_session_metas(path)
        own = (metas[0].get("id") or metas[0].get("session_id")) if metas else None
        if not isinstance(own, str):
            continue
        if all(session_is_students(meta) for meta in metas):
            students.add(own)
        else:
            agents.add(own)
    return frozenset(students - agents)


def _leading_session_metas(path: Path) -> List[Dict[str, Any]]:
    """The session_meta payloads a rollout starts with."""
    metas: List[Dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                row = _try_json(line)
                if not (isinstance(row, dict)
                        and row.get("type") == "session_meta"
                        and isinstance(row.get("payload"), dict)):
                    break
                metas.append(row["payload"])
    except OSError:
        return []
    return metas


def session_is_students(meta: Mapping[str, Any]) -> bool:
    """True for a Codex `session_meta` payload of a session the student
    drives (see SESSIONS ANOTHER AGENT STARTED). A null value states
    nothing."""
    def states(value: Any, allowed: FrozenSet[str]) -> bool:
        return value is None or (isinstance(value, str) and value in allowed)

    return (states(meta.get("source"), STUDENT_SESSION_SOURCES)
            and states(meta.get("thread_source"), STUDENT_THREAD_SOURCES)
            and all(meta.get(key) is None for key in AGENT_SESSION_KEYS))


def typed_texts(rows: Iterable[Any],
                student_sessions: Any = frozenset,
                codex_hooks: bool = False) -> Typing:
    """What the student typed in this transcript, each text stripped.

    `student_sessions` gives the Codex sessions known to be the student's
    (asked only when a Codex hook record is there); `codex_hooks` says the
    file is a Codex hook log (see Typing).

    Read from fixed positions only -- never from inside tool output, where
    anything could be spelled:
      * a Codex `event_msg` row: a `user_message`, an `item_completed`
        UserMessage, or a legacy `{role: user, text}` payload -- unless the
        session is another agent's;
      * a Claude Code `user` row, or queued prompt, the student typed (see
        CLAUDE CODE TEXT NOBODY TYPED);
      * one of our normalized events copied from such a record, or a
        `user_prompt` from the UserPromptSubmit hook.
    """
    rows = [row for row in rows if isinstance(row, dict)]
    agent_starts = [row for row in rows
                    if row.get("event_type") == "session_start"
                    and _normalized_session_is_agents(row)]
    typing = Typing(
        agent_file=any(
            row.get("type") == "session_meta"
            and isinstance(row.get("payload"), dict)
            and not session_is_students(row["payload"]) for row in rows),
        agent_sessions=frozenset(str(row.get("session_id"))
                                 for row in agent_starts),
        agent_files=frozenset(_evidence_file(row) for row in agent_starts),
        codex_hooks=codex_hooks)
    if any(typing.is_codex_hook(row) for row in rows):
        typing = replace(typing, student_sessions=student_sessions())
    typed: Set[str] = set()
    for row in rows:
        payload = row.get("payload")
        extras = row.get("extras")
        extras = extras if isinstance(extras, dict) else {}
        if row.get("type") == "event_msg" and isinstance(payload, dict):
            if not typing.agent_file:
                typed.update(_typed_in_event(payload))
        elif row.get("type") in (CLAUDE_USER_ROW, CLAUDE_ATTACHMENT_ROW):
            typed.update(_claude_typed_in_row(row))
        elif typing.by_agent(row):
            continue
        elif row.get("event_type") == "user_prompt" and (
                extras.get("raw_type") == "event_msg"
                or "hook_event_name" in extras):
            own = (own_request(row["text"])
                   if isinstance(row.get("text"), str) else None)
            if own is not None:
                typed.add(own)
            if isinstance(extras.get("raw_payload"), dict):
                typed.update(_typed_in_event(extras["raw_payload"]))
        elif row.get("event_type") in CLAUDE_TYPED_EVENT_TYPES and (
                extras.get("raw_type") == CLAUDE_USER_ROW
                and isinstance(row.get("text"), str)
                and _claude_event_text_is_kept(extras, row["text"])):
            typed.add(row["text"])
            if "<command-name>" in row["text"]:
                typed.add(_command_line(row["text"]))
        elif (extras.get("raw_type") == CLAUDE_ATTACHMENT_ROW
              and extras.get(CLAUDE_SIDECHAIN_KEY) is not True
              and isinstance(extras.get("attachment"), dict)
              and _claude_prompt_is_typed(extras["attachment"])):
            typed.add(extras["attachment"]["prompt"])
    return replace(typing,
                   texts=frozenset(text.strip() for text in typed) - {""})


def _normalized_session_is_agents(event: Mapping[str, Any]) -> bool:
    """True for our `session_start` event of another agent's Codex session."""
    extras = event.get("extras")
    if not isinstance(extras, dict) or extras.get("raw_type") != "session_meta":
        return False
    meta = extras.get("raw_payload")
    return not isinstance(meta, dict) or not session_is_students(meta)


def _typed_in_event(payload: Mapping[str, Any]) -> List[str]:
    """The typed text a Codex `event_msg` payload records, if any, without
    the tool's context (see TOOL CONTEXT INSIDE A TYPED PROMPT)."""
    return [own for own in map(own_request, _texts_in_event(payload))
            if own is not None]


def _texts_in_event(payload: Mapping[str, Any]) -> List[str]:
    """The texts a Codex `event_msg` prompt payload holds."""
    kind = payload.get("type")
    if kind == "user_message" and isinstance(payload.get("message"), str):
        return [payload["message"]]
    if (kind is None and payload.get("role") == "user"
            and set(payload) <= LEGACY_MESSAGE_KEYS
            and isinstance(payload.get("text"), str)):
        return [payload["text"]]
    item = payload.get("item") if kind == CODEX_ITEM_EVENT_TYPE else None
    if (isinstance(item, dict) and item.get("type") == USER_MESSAGE_ITEM
            and isinstance(item.get("content"), list)):
        return [part["text"] for part in item["content"]
                if isinstance(part, dict) and isinstance(part.get("text"), str)]
    return []


def _is_injectable(record: Any) -> bool:
    """A Codex message whose role a tool may write for (INJECTING_ROLES)."""
    return (isinstance(record, dict) and record.get("type") == "message"
            and record.get("role") in INJECTING_ROLES)


def _holds_withheld_text(item: Any) -> bool:
    if isinstance(item, dict) and (item.get("withheld") is True
                                   or _is_withheld_marker(item.get("text"))):
        return True
    return _is_withheld_marker(item)


def _withhold_claude_attachment_row(row: Dict[Any, Any]) -> Dict[Any, Any]:
    """A Claude Code attachment row (see CLAUDE_ATTACHMENT_ROW)."""
    out = dict(row)
    out["attachment"] = _claude_attachment(
        row["attachment"], sidechain=row.get(CLAUDE_SIDECHAIN_KEY) is True)
    if CLAUDE_RENDERED_KEY in row:
        out[CLAUDE_RENDERED_KEY] = _withheld(INJECTED_CONTEXT)
    return out


def _claude_attachment(attachment: Any, sidechain: bool = False) -> Any:
    """What of one attachment object is kept (see CLAUDE_ATTACHMENT_ROW).
    Nothing in a sub-agent's conversation (`sidechain`) was typed."""
    if not isinstance(attachment, dict):
        return attachment if _carries_no_text(attachment) else _withheld(
            INJECTED_CONTEXT)
    kind = attachment.get("type")
    kind = kind if isinstance(kind, str) else None
    if kind in CLAUDE_EDIT_ATTACHMENTS:
        return attachment  # the edit-record rule decides its snippet
    keep: FrozenSet[str] = frozenset({"type"}) if kind else frozenset()
    if (kind == CLAUDE_QUEUED_PROMPT and not sidechain
            and _claude_prompt_is_typed(attachment)):
        keep = CLAUDE_QUEUED_PROMPT_KEYS
    out: Dict[Any, Any] = {}
    for key, value in attachment.items():
        if (key in keep or _carries_no_text(value)
                or (str(key).lower() in PATH_KEYS and _is_path_shape(value))):
            out[key] = value
        else:
            out[key] = _withheld(INJECTED_CONTEXT)
    return out


def _claude_prompt_is_typed(attachment: Mapping[str, Any]) -> bool:
    """A queued prompt the student typed, not one another agent -- or a
    background task -- sent."""
    origin = attachment.get("origin")
    prompt = attachment.get("prompt")
    return (attachment.get(CLAUDE_META_KEY) is not True
            and (origin is None or origin == CLAUDE_TYPED_ORIGIN)
            and attachment.get("commandMode") in CLAUDE_TYPED_COMMAND_MODES
            and isinstance(prompt, str) and claude_text_is_typed(prompt))


def claude_row_is_typed(row: Mapping[str, Any]) -> bool:
    """False for a Claude Code user row the tool marks as its own (see
    CLAUDE CODE TEXT NOBODY TYPED); True says nothing about its text."""
    if row.get(CLAUDE_META_KEY) is True or any(
            row.get(flag) is True for flag in CLAUDE_TOOL_ROW_FLAGS):
        return False
    origin = row.get("origin")
    source = row.get("promptSource")
    return ((origin is None or origin == CLAUDE_TYPED_ORIGIN)
            and (source is None or source in CLAUDE_TYPED_PROMPT_SOURCES))


def claude_text_is_typed(text: str) -> bool:
    """False for text that starts with markup, unless it is exactly the
    wrapper around a `!` or `/` command the student typed."""
    return (not _MARKUP_START_RE.match(text)
            or _is_typed_command(text))


def _is_typed_command(text: str) -> bool:
    """True for exactly a `!` command, or a `/` command with its name."""
    return bool(_TYPED_BASH_RE.fullmatch(text) or (
        _TYPED_COMMAND_RE.fullmatch(text)
        and "<command-name>" in text and "<command-message>" in text))


def claude_system_text_is_kept(subtype: Any, text: str) -> bool:
    """A `system` row's text: never a local command's output, and nothing
    Claude Code wrote itself (see CLAUDE_MODEL_TEXT_SUBTYPES)."""
    if subtype == CLAUDE_LOCAL_COMMAND:
        return not text.strip() or _is_typed_command(text)
    return subtype in CLAUDE_MODEL_TEXT_SUBTYPES and claude_text_is_typed(text)


def _withhold_command_args(row: Dict[Any, Any], typed: "Typing") -> Dict[Any, Any]:
    """A local_command row's `commandRun.args`, kept only when the student
    typed that command line (`/name args`)."""
    run = row.get("commandRun")
    if not isinstance(run, dict) or _carries_no_text(run.get("args")):
        return row
    name, args = run.get("command"), run.get("args")
    if (isinstance(name, str) and isinstance(args, str)
            and f"/{name.lstrip('/')} {args}".strip() in typed):
        return row
    return {**row, "commandRun": {**run, "args": _withheld(INJECTED_CONTEXT)}}


def _claude_row_schema(record: Mapping[str, Any]) -> Any:
    """The field schema of a Claude Code row beside the conversation (see
    CLAUDE_ROW_SCHEMAS), or None. Such a row names its session."""
    kind = record.get("type")
    if not isinstance(kind, str) or "sessionId" not in record:
        return None
    return CLAUDE_ROW_SCHEMAS.get(kind)


def _is_claude_raw_copy(record: Mapping[str, Any]) -> bool:
    """True for claude_normalize's `extras`, holding a copy of a row that
    has a schema."""
    kind = record.get("raw_type")
    return (isinstance(kind, str) and kind in CLAUDE_ROW_SCHEMAS
            and isinstance(record.get("raw_payload"), dict))


def apply_claude_row_rules(row: Any) -> Any:
    """Default deny for one ALREADY-REDACTED Claude Code row: a row type
    nobody measured keeps only its identity."""
    if not isinstance(row, dict):
        return row
    kind = row.get("type")
    if (kind in CLAUDE_CONVERSATION_ROWS or kind in CLAUDE_ROW_SCHEMAS
            or _is_file_history_row(row)):
        return row
    out: Dict[Any, Any] = {}
    for key in ("type", "uuid", "sessionId", "timestamp"):
        value = row.get(key)
        shape = IDENTIFIER_SHAPES[_CLAUDE_ROW_IDENTITY[key]]
        if isinstance(value, str) and shape.fullmatch(value):
            out[key] = value
    out.update(_withheld("unrecognized_row_type"))
    return out


def _apply_claude_event_rules(event: Dict[Any, Any]) -> Dict[Any, Any]:
    """The same default deny for the row claude_normalize copied into one
    of our events."""
    extras = event.get("extras")
    if (event.get("adapter_name") != "claude" or not isinstance(extras, dict)
            or not isinstance(extras.get("raw_payload"), dict)):
        return event
    return {**event, "extras": {
        **extras, "raw_payload": apply_claude_row_rules(extras["raw_payload"])}}


def _claude_event_text_is_kept(extras: Mapping[str, Any], text: Any) -> bool:
    """For our normalized event of a Claude `user` or `system` row: may its
    text be kept? The normalizer's decision (CLAUDE_TEXT_KEPT_KEY) wins; an
    event written before it recorded one is judged by its text."""
    decided = extras.get(CLAUDE_TEXT_KEPT_KEY)
    if extras.get(CLAUDE_META_KEY) is True:
        return False
    if isinstance(decided, bool):
        return decided
    if not isinstance(text, str):
        return False
    if extras.get("raw_type") == CLAUDE_SYSTEM_ROW:
        return claude_system_text_is_kept(extras.get("subtype"), text)
    return claude_text_is_typed(text)


def _claude_typed_in_row(row: Mapping[str, Any]) -> List[str]:
    """What the student typed in a raw Claude `user` or attachment row."""
    attachment = row.get("attachment")
    if row.get("type") == CLAUDE_ATTACHMENT_ROW:
        if (isinstance(attachment, dict)
                and row.get(CLAUDE_SIDECHAIN_KEY) is not True
                and attachment.get("type") == CLAUDE_QUEUED_PROMPT
                and _claude_prompt_is_typed(attachment)):
            return [attachment["prompt"]]
        return []
    message = row.get("message")
    if not claude_row_is_typed(row) or not isinstance(message, dict):
        return []
    content = message.get("content")
    items = content if isinstance(content, list) else [content]
    texts = [item.get("text") if isinstance(item, dict) else item
             for item in items]
    typed = [text for text in texts
             if isinstance(text, str) and claude_text_is_typed(text)]
    return typed + [_command_line(text) for text in typed
                    if _MARKUP_START_RE.match(text)
                    and "<command-name>" in text]


def _command_line(command: str) -> str:
    """The line the student typed for a `/` command: `/name args`."""
    parts = dict(_COMMAND_PART_RE.findall(command))
    return f"{parts.get('command-name', '')} {parts.get('command-args', '')}"


def _claude_origin(origin: Any) -> Any:
    """A row's `origin`: its kind is kept; another agent's message in it
    (`body`) and its other text are not."""
    if origin is None or origin == CLAUDE_TYPED_ORIGIN:
        return origin
    if not isinstance(origin, dict):
        return origin if _carries_no_text(origin) else _withheld(
            INJECTED_CONTEXT)
    return {key: value if (_carries_no_text(value) or (
                key == "kind" and isinstance(value, str)
                and _ORIGIN_KIND_RE.fullmatch(value)))
            else _withheld(INJECTED_CONTEXT)
            for key, value in origin.items()}


def _withhold_claude_user_row(row: Dict[Any, Any]) -> Dict[Any, Any]:
    """A Claude `user` row keeps only the text the student typed."""
    out = dict(row)
    if "origin" in row:
        out["origin"] = _claude_origin(row["origin"])
    typed = claude_row_is_typed(row)
    out["message"] = _withhold_message_text(
        row.get("message"), lambda text: typed and claude_text_is_typed(text))
    return out


def _withhold_claude_text_field(row: Dict[Any, Any], key: str,
                                keep: Any) -> Dict[Any, Any]:
    """`row` with its text under `key` withheld unless `keep(text)`."""
    text = row.get(key)
    if not isinstance(text, str) or keep(text):
        return row
    return {**row, key: _withheld(INJECTED_CONTEXT)}


def _withhold_message_text(message: Any, keep: Any = lambda text: False) -> Any:
    """A Claude message with each text withheld unless `keep(text)`."""
    if not isinstance(message, dict):
        return message
    content = message.get("content")
    if isinstance(content, str):
        if keep(content):
            return message
        return {**message, "content": _withheld(INJECTED_CONTEXT)}
    if not isinstance(content, list):
        return message
    return {**message, "content": [
        {"type": item.get("type"), "text": _withheld(INJECTED_CONTEXT)}
        if isinstance(item, dict) and isinstance(item.get("text"), str)
        and not keep(item["text"])
        else item
        for item in content]}


def _withhold_agent_prompt(payload: Any) -> Any:
    """A Codex prompt record from another agent's session, text withheld:
    a `user_message` (or a legacy `{role, text}`) event, an item_completed
    UserMessage, or a UserPromptSubmit hook payload."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    kind = payload.get("type")
    item = payload.get("item")
    if (kind == CODEX_ITEM_EVENT_TYPE and isinstance(item, dict)
            and item.get("type") == USER_MESSAGE_ITEM):
        content = item.get("content")
        out["item"] = {**item, "withheld": True, "content": [
            {"type": part.get("type") if isinstance(part, dict) else None,
             "text": _withheld(INJECTED_CONTEXT)}
            for part in (content if isinstance(content, list) else [content])]}
    elif kind == "user_message":
        # Its message, and the images and text spans that go with it.
        for key, value in payload.items():
            if key != "type" and not _carries_no_text(value):
                out[key] = _withheld(INJECTED_CONTEXT)
    elif kind is None and payload.get("role") == "user":
        if isinstance(payload.get("text"), str):  # a legacy event
            out["text"] = _withheld(INJECTED_CONTEXT)
    elif kind is None:  # a hook payload
        for key in HOOK_PROMPT_KEYS:
            if isinstance(payload.get(key), str):
                out[key] = _withheld(INJECTED_CONTEXT)
    return out


def _surviving_text(message: Mapping[str, Any]) -> Any:
    """A message's texts joined the way codex_normalize joins them, from
    the items that survived; a marker when none did."""
    content = message.get("content")
    items = content if isinstance(content, list) else [content]
    texts = [item.get("text") if isinstance(item, dict) else item
             for item in items]
    kept = [text for text in texts if isinstance(text, str)]
    return "\n".join(kept) if kept else _withheld(INJECTED_CONTEXT)


def redact_jsonl_rows(redactor: Redactor, rows: List[Any],
                      base: Optional[str] = None,
                      adapter: Optional[str] = None) -> List[Any]:
    """Batch the gitignore lookups for a whole file, then redact.

    Two passes, per file:
      1. build the call/output taint index, and the set of texts the
         student typed, from the UNREDACTED rows
      2. withhold injected message text, anonymize paths, then withhold
         the outputs of tainted calls
    The index is per FILE because `call_id` uniqueness is only guaranteed
    within a transcript.

    `base` is where relative paths resolve until a row states otherwise.
    For a vendor transcript it is None (unknown): a Codex rollout states its
    cwd in its first row, and a relative patch header with nothing to
    resolve against is withheld. `adapter` names the tool whose raw folder
    the file came from, if any.
    """
    candidates: Set[str] = set()
    for row in rows:
        redactor.collect_path_candidates(row, candidates)
    redactor.prime_ignore(candidates)
    index = build_taint_index(redactor, rows, base)
    typed = typed_texts(rows, redactor.student_codex_sessions,
                        codex_hooks=adapter == CODEX_ADAPTER)
    out = []
    for position, row in enumerate(rows):
        base = _next_session_base(redactor, row, base)
        row = redactor.withhold_injected_context(row, typed)
        redacted = apply_taint(redactor.redact(row, base), index)
        vocabulary = row_vocabulary(row)
        if vocabulary == VOCAB_CODEX:
            redacted = apply_codex_row_rules(redacted)
        elif vocabulary == VOCAB_CLAUDE:
            redacted = apply_claude_row_rules(redacted)
        else:
            redacted = _apply_claude_event_rules(redacted)
        kept = index.event_decisions.get(position)
        extras = redacted.get("extras", {})
        if kept is not None and isinstance(extras, dict):
            redacted = {**redacted,
                        "extras": {**extras, CALL_OUTPUT_KEPT_KEY: kept}}
        out.append(redacted)
    return out


def redact_file(redactor: Redactor, src: Path, dst: Path,
                report: RedactionReport, *,
                require_vocabulary: bool = False) -> None:
    """Emit the committed derivative of one raw artifact. Fails closed.

    `require_vocabulary` is set for RAW TRANSCRIPTS: their content is only
    committed if their records match a verified call/output vocabulary, so
    an adapter nobody has measured withholds instead of leaking.
    """
    repo = redactor.repo
    src_rel = ai_traces.repo_rel(repo, src)
    try:
        size = src.stat().st_size
    except OSError:
        size = 0
    marker = dst.with_name(dst.name + WITHHELD_SUFFIX)

    def withhold(reason: str) -> None:
        _write_staged_output(marker, _withheld_marker(src_rel, reason, size), repo)
        report.unparseable.append(src_rel)
        report.files_written.append(ai_traces.repo_rel(repo, marker))

    try:
        raw_text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        withhold("unreadable_or_binary")
        _remove_required(dst, repo)
        return

    suffix = src.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        # A prose transcript has no record vocabulary to verify, so it
        # cannot be joined and its agent-read content cannot be bounded.
        # The one exception is a USER-INPUT-ONLY file: every line is
        # something the student typed, which rule 2 keeps unconditionally.
        if require_vocabulary and src.name not in USER_INPUT_ONLY_SOURCES:
            withhold("unrecognized_record_vocabulary")
            _remove_required(dst, repo)
            return
        _write_staged_output(dst, redactor.scrub_text(raw_text), repo)
        report.files_written.append(ai_traces.repo_rel(repo, dst))
        _remove_required(marker, repo)
        return

    if suffix == ".jsonl" or "\n" in raw_text.strip():
        rows: List[Any] = []
        bad_bytes = 0
        for line in raw_text.splitlines():
            if not line.strip():
                continue
            parsed = _try_json(line)
            if parsed is None:
                bad_bytes += len(line.encode("utf-8", "replace"))
                continue
            rows.append(parsed)
        if bad_bytes:
            # Any unparseable line is withheld wholesale -- we cannot know
            # what it contains, so it never reaches the derivative.
            withhold("unparseable")
        else:
            _remove_required(marker, repo)
        if not rows:
            _remove_required(dst, repo)
            return
        if require_vocabulary and recognize_vocabulary(rows) is None:
            withhold("unrecognized_record_vocabulary")
            _remove_required(dst, repo)
            return
        redacted = redact_jsonl_rows(redactor, rows,
                                     adapter=_adapter_of(src_rel))
        payload = "".join(
            json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
            for r in redacted)
        _write_staged_output(dst, payload, repo)
        report.files_written.append(ai_traces.repo_rel(repo, dst))
        return

    parsed = _try_json(raw_text)
    if parsed is None:
        withhold("unparseable")
        _remove_required(dst, repo)
        return
    if require_vocabulary and recognize_vocabulary(
            parsed if isinstance(parsed, list) else [parsed]) is None:
        withhold("unrecognized_record_vocabulary")
        _remove_required(dst, repo)
        return
    _remove_required(marker, repo)
    redacted = redact_jsonl_rows(redactor, [parsed])[0]
    _write_staged_output(
        dst, json.dumps(redacted, sort_keys=True, indent=2,
                        ensure_ascii=False) + "\n", repo)
    report.files_written.append(ai_traces.repo_rel(repo, dst))


def _adapter_of(rel: str) -> Optional[str]:
    """The tool whose trace folder a repo-relative path is in, if any."""
    parts = rel.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == ai_traces.AI_TRACES_DIR else None


def _remove_stale(path: Path) -> None:
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


def redact_in_place(redactor: Redactor, path: Path,
                    report: RedactionReport) -> str:
    """Redact a JSONL file we generated ourselves (normalized/, stream).

    DEFAULT DENY applies here too, per RECORD: an event type outside our own
    enumerated schema is withheld rather than passed through, so a normalizer
    that drifts (or a new adapter's normalizer) cannot silently reopen the
    content leak the taint join exists to close.

    Returns the text the file now holds. Malformed generated files raise a
    fixed-code error so the orchestrator withholds the conversation tier.
    The permissive shared loader cannot distinguish empty files from parse
    failures, and leaving failed input untouched would stage its raw bytes.
    """
    rows = []
    try:
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("normalized_record_invalid")
                rows.append(row)
    except (OSError, UnicodeError, ValueError):
        # Do not expose parse errors containing transcript bytes. The caller
        # records this failure and never caches it as a successful rewrite.
        raise ValueError("normalized_record_invalid") from None
    # Relative paths in this tier resolve against the PROJECT ROOT. Every
    # pass after the first reads paths this module already rewrote, and it
    # rewrites them repo-relative -- a session cwd of the project root
    # becomes "". (On the first pass each event still carries the absolute
    # session cwd, which overrides this. An event with no cwd at all -- a
    # rollout with no session_meta -- is resolved against the root too;
    # the raw tier withholds that case instead.)
    redacted = [
        row if normalized_event_recognized(str(row.get("event_type") or ""))
        else _skeleton_unrecognized(row)
        for row in redact_jsonl_rows(redactor, rows,
                                     base=str(redactor.repo))
    ]
    payload = "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
        for r in redacted)
    _write_if_changed(path, payload)
    report.files_written.append(ai_traces.repo_rel(redactor.repo, path))
    return payload


_HOOK_LOG_NAME_RE = re.compile(r"hooks-(.+)\.jsonl")


def _redact_file_cached(redactor: Redactor, cache: RedactionCache,
                        src: Path, dst: Path, report: RedactionReport) -> None:
    """`redact_file`, skipped when neither `src` nor its outputs changed."""
    marker = dst.with_name(dst.name + WITHHELD_SUFFIX)
    outputs = [dst, marker]
    key = ai_traces.repo_rel(redactor.repo, src)
    session = _HOOK_LOG_NAME_RE.fullmatch(src.name)
    if _adapter_of(key) == CODEX_ADAPTER and session:
        # A hook log's copy also depends on whether its session is known to
        # be the student's (HOOK LOGS NAME NO SESSION SOURCE).
        known = session.group(1) in redactor.student_codex_sessions()
        key += "#student-session" if known else "#other-session"
    for output in outputs:
        _output_stat(output, redactor.repo)
    if cache.is_fresh(key, src, outputs):
        report.skipped_unchanged.append(key)
        return
    before = cache.input_state(src)
    redact_file(redactor, src, dst, report, require_vocabulary=True)
    cache.record(key, before, outputs)


def _redact_in_place_cached(redactor: Redactor, cache: RedactionCache,
                            path: Path, report: RedactionReport) -> None:
    """`redact_in_place`, skipped when the file is still as we left it."""
    key = ai_traces.repo_rel(redactor.repo, path)
    _output_stat(path, redactor.repo)
    if cache.is_fresh(key, path, [], verify_content=True):
        report.skipped_unchanged.append(key)
        return
    payload = redact_in_place(redactor, path, report)
    if payload is not None:
        cache.record_written(key, path, payload)


def _audit_derivative_outputs(root: Path, cache: RedactionCache) -> None:
    """Attest orphan and unexpected derivatives too, not just current inputs.

    A pruned raw source does not erase its previously sanitized derivative's
    provenance. Its recorded byte hash can still attest it. Unknown files or
    old metadata-only cache records cannot establish that provenance.
    """
    recorded: Dict[str, List[Any]] = {}
    for entry in cache.entries.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("outputs"), dict):
            continue
        for key, value in entry["outputs"].items():
            if isinstance(value, dict) and isinstance(value.get("sha256"), str):
                recorded.setdefault(key, []).append(value)

    def visit(directory: Path) -> None:
        # Check directories without following a symlink/reparse alias, even
        # when the directory is empty and there is no leaf to inspect.
        _output_stat(directory / ".capture-output-probe", cache.repo)
        for path in directory.iterdir():
            state = path.lstat()
            if stat.S_ISDIR(state.st_mode) and not (getattr(state, "st_file_attributes", 0) & 0x400):
                visit(path)
            else:
                current = _output_state(path, cache.repo)
                if current is None or not any(
                        current["sha256"] == previous["sha256"]
                        and current["size"] == previous.get("size")
                        for previous in recorded.get(cache._key(path), [])):
                    raise ValueError("staged_output_unattested")

    visit(root)


def redact_traces(repo: Path) -> RedactionReport:
    """Produce the committed, redacted derivative of every captured trace.

    Called by the orchestrator BEFORE the health manifest and before any
    staging, so nothing unredacted can reach a snapshot. Never raises: a
    redaction failure must not block a snapshot, and anything it could not
    process is withheld rather than committed.

    Must run under the orchestrator lock: it deletes leftover temp files,
    which is only safe when no other pass is writing them.
    """
    report = RedactionReport()
    redactor = Redactor(repo)
    cache = RedactionCache(repo)
    root = repo / ai_traces.AI_TRACES_DIR
    try:
        _output_stat(root / ".capture-output-probe", repo)
    except (OSError, ValueError):
        report.errors.append("derivative_output_invalid")
        return report
    _sweep_temp_files(root)
    try:
        adapter_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except FileNotFoundError:
        adapter_dirs = []
    except OSError:
        adapter_dirs = []
        report.errors.append("derivative_output_invalid")

    for adapter_dir in adapter_dirs:
        raw_root = adapter_dir / RAW_SUBDIR
        out_root = adapter_dir / REDACTED_SUBDIR
        if raw_root.is_dir():
            try:
                sources = sorted(p for p in raw_root.rglob("*") if p.is_file())
            except OSError:
                sources = []
            for src in sources:
                try:
                    rel = src.relative_to(raw_root)
                    _redact_file_cached(redactor, cache, src, out_root / rel,
                                        report)
                except Exception as e:  # one bad file cannot stop the pass
                    report.errors.append(f"{src.name}: {type(e).__name__}: {e}")
        normalized = adapter_dir / "normalized"
        if normalized.is_dir():
            for path in sorted(normalized.glob("*.jsonl")):
                try:
                    _redact_in_place_cached(redactor, cache, path, report)
                except Exception as e:
                    report.errors.append(
                        f"{path.name}: {type(e).__name__}: {e}")

    stream = repo / ai_traces.INTERACTION_STREAM
    if stream.is_file():
        try:
            _redact_in_place_cached(redactor, cache, stream, report)
        except Exception as e:
            report.errors.append(f"interaction-stream: {type(e).__name__}: {e}")

    # Aider writes its transcripts to the repo root, not into .ai-traces/.
    for name in AIDER_SOURCES:
        src = repo / name
        if src.is_file():
            try:
                _redact_file_cached(redactor, cache, src,
                                    root / "aider" / REDACTED_SUBDIR / name,
                                    report)
            except Exception as e:
                report.errors.append(f"{name}: {type(e).__name__}: {e}")
    # All redacted files are force-staged, including leftovers whose raw
    # source disappeared and files no current adapter input produced.
    for adapter_dir in adapter_dirs:
        output_root = adapter_dir / REDACTED_SUBDIR
        try:
            if output_root.exists() or output_root.is_symlink():
                _audit_derivative_outputs(output_root, cache)
        except Exception:
            report.errors.append("derivative_output_invalid")
    cache.save()
    return report


def raw_unstage_paths(repo: Path) -> List[str]:
    """Repo-relative ``raw/`` dirs to drop from the index before committing.

    Derived from DISK, not from the adapter registry, on purpose: hook-only
    tools (Cursor has no pull-side adapter class) also write raw traces, and
    a registry-driven list would silently commit theirs unredacted.

    This does NOT weaken the force-add anti-evasion control: the trace
    directories are still force-staged, so a student cannot hide the
    existence of their traces; only the unredacted raw bytes are removed,
    and the redacted derivative next to them is committed.
    """
    root = repo / ai_traces.AI_TRACES_DIR
    out: List[str] = []
    try:
        for adapter_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            raw_root = adapter_dir / RAW_SUBDIR
            if raw_root.is_dir():
                out.append(
                    f"{ai_traces.AI_TRACES_DIR}/{adapter_dir.name}/{RAW_SUBDIR}")
    except OSError:
        return []
    return out


def unredacted_paths(repo: Path) -> List[str]:
    """Static exclusions for snapshots that bypass sanitizer and its lock.

    No current AI-trace bytes are attested on the watchdog path: even a
    previously redacted file may have changed since its last safe snapshot.
    Do not discover paths from disk here; the hung parent may be writing or
    replacing those namespaces. Previously safe evidence remains in parents.
    """
    return [ai_traces.AI_TRACES_DIR, ".codex-transcripts", ".claude-transcripts",
            ".codex/auth.json", *AIDER_SOURCES]


def has_withheld_markers(repo: Path, adapter_name: str,
                         reason: Optional[str] = None) -> bool:
    """True iff this adapter withheld an artifact (optionally: for `reason`).

    Derived from on-disk markers, so the value is stable run-to-run and safe
    for the capture-health manifest.
    """
    root = ai_traces.adapter_dir(repo, adapter_name)
    try:
        if not root.is_dir():
            return False
        for path in root.rglob("*"):
            if not (path.is_file() and path.name.endswith(WITHHELD_SUFFIX)):
                continue
            if reason is None:
                return True
            data = _try_json(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("reason") == reason:
                return True
        return False
    except (OSError, UnicodeDecodeError):
        return False
