"""Shared helpers for the repo-local Codex hooks.

Design rules, all load-bearing:

1. **Stdlib only, no project imports.** These hooks must work unchanged in
   an assignment repo that has no Python test harness at all, so nothing
   here imports ``tests._capture``. When a snapshot is wanted the hook
   *shells out* to the capture CLI seam (``python -m tests._capture
   snapshot``) and simply skips when that seam is absent.
2. **Never block a turn.** Every entry point swallows exceptions and
   returns 0. Evidence capture is best-effort; a broken hook must degrade
   to a missing record, never to a student who cannot use their tools.
3. **Never assume cwd is the repo root.** Codex discovers
   ``.codex/hooks.json`` by walking *up* from the session cwd, so a session
   opened in a subdirectory still fires these hooks. Paths are resolved from
   this file's own location.
4. **Heartbeat content is stable.** Snapshots dedupe by tree SHA, so a
   heartbeat that changed on every fire would defeat dedupe. The file is
   keyed by session id and contains no timestamps -- re-firing within a
   session rewrites identical bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

AI_TRACES_DIR = ".ai-traces"
ADAPTER_NAME = "codex"
SCHEMA_VERSION = "1"

# Tool names whose PostToolUse payload describes a file edit. Codex's tool
# vocabulary has drifted before, so the path-shaped fallback in
# ``_edited_file`` matters more than this list.
EDIT_TOOL_NAMES = {
    "edit", "write", "multiedit", "notebookedit", "apply_patch", "patch",
    "str_replace_editor", "create_file", "update_file",
}

# apply_patch envelope markers. Real Codex edits rarely arrive as a
# {"file_path": ...} tool input: the model runs a shell/``exec`` tool whose
# command string CONTAINS the patch, so the target files have to be read out
# of these header lines instead.
PATCH_BEGIN = "*** Begin Patch"
_PATCH_FILE_HEADER = re.compile(r"^\*\*\* (?:Add File|Update File): (.+?)\s*$")
_PATCH_MOVE_TO = re.compile(r"^\*\*\* Move to: (.+?)\s*$")
# A header marker that does not start its line (a script joining literals:
# ``" + "*** Update File: " + f``) is not read as a header -- its file is
# unknown -- but it still ends the section before it.
_PATCH_ANY_HEADER = re.compile(r"\*\*\* (?:Add|Update|Delete) File: ")
# "End Patch" may be followed by the closing quote of a JS string literal
# (``*** End Patch";``), so anything after the marker still ends the section.
_PATCH_SECTION_END = re.compile(r"^\*\*\* (?:Delete File: .*|End Patch\b.*)$")
# Keys under which a tool input may carry its command text.
_COMMAND_KEYS = ("command", "cmd", "input", "arguments", "script", "code", "patch")
# Keys under which a tool input may state the directory its command runs in.
_WORKDIR_KEYS = ("workdir", "working_dir", "cwd")
# Stands in for "a directory is stated here but cannot be known". Relative
# patch headers under it are not recorded. A NUL cannot occur in a real path.
UNKNOWN_DIR = "\0unknown"

# Where an exec script's commands run. This mirrors ``js_stated_workdirs``
# and ``_patch_dirs`` in ``tests/_capture/redaction.py`` -- read the long
# comments there; the hooks may not import that package, so keep the two
# copies in step. In short, both are PRESENCE-BASED: the reader accepts one
# plain shape (``tools.exec_command({..., workdir: "<plain literal>"})``)
# and anything else it sees -- another ``workdir``, a spread, a computed
# key, ``tools`` used indirectly, a backslash outside a string (an escape can
# spell ``workdir``), any directory-changing shell word other than a leading
# ``cd /abs &&`` (PowerShell's ``Set-Location`` and ``sl`` included) --
# leaves the directory unknown.
_JS_STRING = re.compile(
    r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', re.DOTALL)
_JS_DYNAMIC = re.compile(
    r"\.\.\.|[{,]\s*\[|\]\s*=(?![=>])"
    r"|__proto__|(?<![\w$])(?:Object|Reflect|globalThis)\s*[.\[]"
    r"|(?<![\w$.])(?:eval|Function|with)\s*\(")
_JS_TOOLS_WORD = re.compile(r"(?<![\w$])tools(?![\w$])")
_JS_TOOL_CALL = re.compile(r"(?<![\w$])tools\s*\.\s*([A-Za-z_$][\w$]*)\s*\(\s*(\{)?")
_JS_CLEAN_WORKDIR = re.compile(
    r"(?:(?<![\w$.])workdir|\x00(?P<key>\d+)\x00)"
    r"\s*:\s*\x00(?P<value>\d+)\x00\s*(?=,|$)")
_JS_SCRIPT_HINT = re.compile(
    r"workdir|(?<![\w$])tools\s*(?:\.\s*[A-Za-z_$][\w$]*\s*\(|\[)")
_JS_SIMPLE_ESCAPES = {"\\": "\\", '"': '"', "'": "'", "/": "/"}

_DIR_CHANGE = re.compile(
    r"(?i:(?<![\w$./-])(?:cd|pushd|popd|eval|sl|iex)(?![\w$.-]))"
    r"|(?i:(?<![A-Za-z0-9_])chdir)"
    r"|(?i:(?<![\w$-])(?:set|push|pop)-location(?![\w-]))"
    r"|(?i:invoke-expression|-workingdirectory|currentdirectory)"
    r"|CDPATH|GIT_WORK_TREE|GIT_DIR"
    r"|(?<![\w-])-C"
    r"|--(?:chdir|directory|work-tree|git-dir)")
_PLAIN_CD = re.compile(r"cd[ \t]+(/[A-Za-z0-9_./@+,:=%-]*)[ \t]+&&[ \t]")
_ESCAPE = re.compile(
    r"\\(u\{[0-9a-fA-F]{1,6}\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_ESCAPED_CHARS = {"n": "\n", "t": "\t", "r": "\r"}
# A patch header written as an expression -- a quote or backtick (a string
# joined to a variable), a ``${...}``/``$(...)``/``$VAR`` expansion, a
# ``%VAR%``, or a JavaScript escape -- names a file only the running script
# knows. Such a header is skipped, never recorded as its own text.
_EXPRESSION_HEADER = re.compile(
    r"[\"'`$]|%\w+%|\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{)")


def project_repo_root() -> Path:
    """The repo root: <root>/.codex/hooks/_common.py -> <root>."""
    return Path(__file__).resolve().parents[2]


def adapter_dir(repo: Path) -> Path:
    return repo / AI_TRACES_DIR / ADAPTER_NAME


def read_payload() -> Dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def session_id_of(payload: Dict[str, Any]) -> str:
    for key in ("session_id", "conversation_id", "agent_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_component(value.strip())
    return "unknown"


def _safe_component(value: str) -> str:
    """Filesystem-safe filename component (session ids are UUID-ish)."""
    keep = [c if (c.isalnum() or c in "-_.") else "-" for c in value]
    out = "".join(keep).strip("-.") or "unknown"
    return out[:80]


def append_hook_event(repo: Path, event_name: str, payload: Dict[str, Any]) -> None:
    """Append the raw hook event, verbatim, to this session's JSONL.

    Deliberately unfiltered by cwd: Codex only runs these hooks because it
    found THIS repo's hooks.json by walking up from the session cwd, so the
    session belongs to this repo even when its cwd is a subdirectory. A cwd
    filter here is how v1 could silently record nothing.
    """
    out = adapter_dir(repo) / "raw" / "hooks" / f"hooks-{session_id_of(payload)}.jsonl"
    event = {
        "adapter_name": ADAPTER_NAME,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hook_event_name": event_name,
        "payload": payload,
    }
    _append_jsonl(out, event)


def write_heartbeat(repo: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Prove a hook actually ran on this machine.

    ``capture_health`` reports the presence of anything under
    ``.ai-traces/<adapter>/heartbeat/`` and AGENTS.md tells the student's
    assistant to check the same directory. Its absence *together with* Codex
    evidence is the untrusted-hooks signal; absence alone only means the
    tool was never used here.

    Every hook writes it, not just SessionStart: Codex trust is granted per
    hook entry, so a student can approve some entries and not others.
    """
    path = adapter_dir(repo) / "heartbeat" / f"{session_id_of(payload)}.json"
    body = json.dumps(
        {
            "adapter_name": ADAPTER_NAME,
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id_of(payload),
        },
        sort_keys=True, indent=2,
    ) + "\n"
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == body:
            return path  # identical bytes; do not disturb mtime
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    except OSError:
        return None
    return path


def copy_transcript(repo: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Copy the rollout named by ``transcript_path`` into the repo.

    Push-based capture's whole advantage: the transcript path arrives in the
    payload, so no cwd+mtime rescan of $CODEX_HOME is needed. The
    destination matches ``codex_ingest``'s so the normalizer picks it up.

    Re-copies when the source has grown or been rewritten -- Codex appends
    to one rollout file across a whole session.
    """
    source = payload.get("transcript_path")
    if not isinstance(source, str) or not source:
        return None
    try:
        # expanduser: a "~/..." path would otherwise be looked up relative
        # to the cwd, fail is_file(), and the copy would silently not happen.
        src = Path(source).expanduser()
        if not src.is_file():
            return None
        dest = adapter_dir(repo) / "raw" / "rollouts" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            s, d = src.stat(), dest.stat()
            if d.st_size == s.st_size and int(d.st_mtime) >= int(s.st_mtime):
                return dest
        shutil.copy2(src, dest)
        return dest
    except OSError:
        return None


def _edited_file(payload: Dict[str, Any]) -> Optional[str]:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "filename", "notebook_path", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _command_strings(
        value: Any,
        workdirs: Tuple[Optional[str], ...] = (None,),
) -> Iterator[Tuple[str, Tuple[Optional[str], ...]]]:
    """Every string a tool input could carry its command in, paired with the
    directories that command may run in.

    The shape varies by Codex version and tool: a bare string (the ``exec``
    tool's JavaScript source), a dict with ``cmd``/``command``/``input``, an
    argv list such as ``["bash", "-lc", "..."]``, or a JSON-encoded
    ``arguments`` string. Rather than guess one, walk them all.

    In the directories, None means "the session directory" (nothing was
    stated) and ``UNKNOWN_DIR`` means a directory is stated but unreadable.
    A dict's own ``workdir`` applies to the strings inside it; an exec script
    states its own (see ``_js_stated_workdirs``).
    """
    if isinstance(value, str):
        here = workdirs
        if _JS_SCRIPT_HINT.search(value):
            stated = _js_stated_workdirs(value)
            if any(entry is not None for entry in stated):
                candidates: List[Optional[str]] = []
                for entry in stated:  # None: that call inherits `workdirs`
                    candidates.extend(workdirs if entry is None else (entry,))
                here = tuple(dict.fromkeys(candidates))
        yield value, here
        stripped = value.strip()
        if stripped.startswith("{"):  # JSON-encoded function arguments
            try:
                yield from _command_strings(json.loads(stripped), workdirs)
            except ValueError:
                pass
    elif isinstance(value, list):
        for item in value:
            yield from _command_strings(item, workdirs)
    elif isinstance(value, dict):
        own = _dict_stated_workdir(value)
        if own is not None:
            workdirs = (own,)
        for key in _COMMAND_KEYS:
            if key in value:
                yield from _command_strings(value[key], workdirs)


def _dict_stated_workdir(value: Dict[str, Any]) -> Optional[str]:
    """The directory a dict tool input states: None for none, UNKNOWN_DIR
    when it is unreadable or a ``workdir`` appears anywhere else inside."""
    mentions = json.dumps(value, ensure_ascii=False).count("workdir")
    for key in _WORKDIR_KEYS:
        if key in value:
            own = value[key]
            accounted = ((key == "workdir")
                         + (own.count("workdir") if isinstance(own, str) else 0))
            if mentions != accounted or not (isinstance(own, str) and own.strip()):
                return UNKNOWN_DIR
            return own.strip()
    return UNKNOWN_DIR if mentions else None


def _js_string_value(literal: str) -> Optional[str]:
    """The value of a plain '...' or "..." literal; None for anything else."""
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
    """The inside of the ``{...}`` opening at ``start``, nested parts blanked."""
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


def _js_stated_workdirs(source: str) -> List[Optional[str]]:
    """One entry per tool call in an exec script: its stated directory, or
    None if it states none. Anything outside the plain shape gives
    ``[UNKNOWN_DIR]``."""
    literals: List[str] = []

    def hide(match: "re.Match[str]") -> str:
        literals.append(match.group(0))
        return "\x00%d\x00" % (len(literals) - 1)

    masked = _JS_STRING.sub(hide, source)
    if any(ch in masked for ch in "\"'`\\") or _JS_DYNAMIC.search(masked):
        return [UNKNOWN_DIR]
    calls = list(_JS_TOOL_CALL.finditer(masked))
    if len(calls) != len(_JS_TOOLS_WORD.findall(masked)):
        return [UNKNOWN_DIR]
    found: List[Optional[str]] = []
    accounted = 0
    for call in calls:
        if call.group(1) != "exec_command":
            found.append(None)
            continue
        body = _js_object_top_level(masked, call.end() - 1) if call.group(2) else None
        if body is None:
            return [UNKNOWN_DIR]
        pairs = [pair for pair in _JS_CLEAN_WORKDIR.finditer(body)
                 if pair.group("key") is None
                 or _js_string_value(literals[int(pair.group("key"))]) == "workdir"]
        if not pairs:
            found.append(None)
        for pair in pairs:
            value = _js_string_value(literals[int(pair.group("value"))])
            accounted += 1 + (value.count("workdir") if value else 0)
            found.append(value.strip() if value and value.strip() else UNKNOWN_DIR)
    if source.count("workdir") != accounted:
        return [UNKNOWN_DIR]
    return found


def _decode_escape(match: "re.Match[str]") -> str:
    code = match.group(1)
    if len(code) > 1 and code[0] in "ux":
        number = int(code.strip("u{}x"), 16)
        return chr(number) if number < 0x110000 else ""
    return _ESCAPED_CHARS.get(code, code)


def _patch_dirs(text: str) -> Tuple[Optional[str], ...]:
    """Where a directory change in ``text`` may move a patch: () for none,
    (path,) for one plain leading ``cd /abs &&``, (None,) otherwise."""
    view = text
    for _ in range(3):  # escapes can nest (JSON inside JavaScript)
        if "\\" not in view:
            break
        view = _ESCAPE.sub(_decode_escape, view)
    view = re.sub(r"[\"'`\\]", "", view)  # quote removal, as the shell does
    changes = list(_DIR_CHANGE.finditer(view))
    if not changes:
        return ()
    if len(changes) == 1:
        start = changes[0].start()
        before = view[:start].rstrip(" \t")
        plain = _PLAIN_CD.match(view, start)
        if plain and (before == "" or before.endswith(":")):
            return (plain.group(1),)
    return (None,)


def _patch_edits(text: str) -> List[Tuple[str, str, Tuple[Optional[str], ...]]]:
    """(file, added_text, cd_dirs) for each file an apply_patch envelope edits.

    ``added_text`` joins the patch's ``+`` lines for that file: the same
    thing ``new_string`` measures for a Claude-style edit. Deleted files are
    skipped -- they leave no lines behind to attribute.

    ``cd_dirs`` is where a directory change in the same text may have moved
    the patch (see ``_patch_dirs`` and ``_repo_relative``).
    """
    if PATCH_BEGIN not in text or "apply_patch" not in text:
        return []
    cd_dirs = _patch_dirs(text)
    # Inside the exec tool's JavaScript the patch is a string literal, so its
    # line breaks are the two characters backslash + n. Turn them back into
    # real newlines for scanning only; nothing here is written out.
    after_begin = text.split(PATCH_BEGIN, 1)[1]
    if not after_begin.startswith(("\n", "\r\n")):
        text = text.replace("\\n", "\n")
    edits: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        header = _PATCH_FILE_HEADER.match(line)
        move = _PATCH_MOVE_TO.match(line)
        if header:
            current = header.group(1).strip()
            edits.setdefault(current, [])
        elif move and current is not None:
            # A rename: the lines end up at the NEW path, so re-key them.
            added = edits.pop(current)
            current = move.group(1).strip()
            edits.setdefault(current, []).extend(added)
        elif _PATCH_SECTION_END.match(line) or _PATCH_ANY_HEADER.search(line):
            current = None
        elif current is not None and line.startswith("+"):
            edits[current].append(line[1:] + "\n")
    return [(path, "".join(added), cd_dirs) for path, added in edits.items()]


def _append_edit_record(repo: Path, payload: Dict[str, Any], tool_name: str,
                        path: str, new_text: str) -> Path:
    """Write one payload-free provenance row and return the log it went to."""
    record = {
        "adapter_name": ADAPTER_NAME,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type": "file_edit",
        "session_id": session_id_of(payload),
        "turn_id": str(payload.get("turn_id") or ""),
        "tool_name": tool_name,
        "file": path,
        "extent_chars": len(new_text),
        "content_sha256": hashlib.sha256(new_text.encode("utf-8", "replace")).hexdigest(),
    }
    out = adapter_dir(repo) / "raw" / "hooks" / f"edits-{session_id_of(payload)}.jsonl"
    _append_jsonl(out, record)
    return out


def record_edit(repo: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Record per-edit provenance from a PostToolUse payload.

    Two payload shapes are recognized:

    * an edit tool naming its file directly (``tool_input.file_path``), and
    * an apply_patch envelope inside a shell/``exec`` command string -- the
      way real Codex sessions actually edit files. One row per patched file
      inside the repo, named relative to the repo.

    Proportionality: file path, tool, change extent and a content hash --
    never the edited text. The full text is already in the rollout that
    ``copy_transcript`` captures; duplicating it here would widen the
    disclosure surface for no extra signal (see AI_POLICY.md).
    """
    tool_name = payload.get("tool_name")
    tool_name = tool_name if isinstance(tool_name, str) else ""
    path = _edited_file(payload)
    if path is None:
        return _record_patch_edits(repo, payload, tool_name)
    if tool_name.replace("-", "_").lower() not in EDIT_TOOL_NAMES and not _looks_like_write(payload):
        return None

    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    new_text = ""
    for key in ("new_string", "content", "new_str", "text"):
        value = tool_input.get(key)
        if isinstance(value, str):
            new_text = value
            break
    return _append_edit_record(repo, payload, tool_name, path, new_text)


def _record_patch_edits(repo: Path, payload: Dict[str, Any],
                        tool_name: str) -> Optional[Path]:
    """Record every file an apply_patch inside a command string edits.

    The tool name is NOT checked here (it is ``exec``, ``exec_command`` or a
    shell tool, never an edit tool); the patch envelope itself is the signal.

    Each header path is recorded RELATIVE TO THE REPO, because that is where
    the redaction and attribution passes resolve it. A header is relative to
    the directory the patch ran in, not to the repo (see ``_patch_dir``).
    """
    out: Optional[Path] = None
    seen = set()
    for text, workdirs in _command_strings(payload.get("tool_input")):
        directory = _patch_dir(workdirs, payload.get("cwd"))
        for header, added, cd_dirs in _patch_edits(text):
            path = _repo_relative(repo, header, directory, cd_dirs)
            if path is None or path in seen:  # outside, unknowable, or seen
                continue                      # (one command can be reached twice)
            seen.add(path)
            out = _append_edit_record(repo, payload, tool_name, path, added)
    return out


def _patch_dir(workdirs: Tuple[Optional[str], ...],
               session_cwd: Any) -> Optional[Path]:
    """The one directory a patch's relative header paths resolve against.

    Codex applies a patch in the command's ``workdir``, which may itself be
    relative to the session ``cwd``; with no workdir (None), in the session
    ``cwd``. None when that cannot be known -- an unreadable directory, or
    candidates that are not all the same directory.
    """
    cwd = _absolute_dir(session_cwd, None)
    resolved = set()
    for workdir in workdirs:
        if workdir is None:
            resolved.add(cwd)
        elif workdir == UNKNOWN_DIR:
            return None
        else:
            resolved.add(_absolute_dir(workdir, cwd))
    if len(resolved) != 1 or None in resolved:
        return None
    return resolved.pop()


def _file_uri_path(uri: str) -> Optional[str]:
    """The local absolute path a ``file://`` URI names, or None.

    Codex states directories as file URIs too. One that names another
    host, carries a query, or cannot be decoded is not a local directory we
    can check. Mirrors ``_file_uri_path`` in ``tests/_capture/redaction.py``.
    """
    import urllib.request  # slow to import; only needed for a file URI

    try:
        parts = urllib.parse.urlsplit(uri)
        path = urllib.request.url2pathname(parts.path)
    except (ValueError, TypeError, OSError):
        return None
    if (parts.scheme.lower() != "file" or parts.query
            or parts.netloc.lower() not in ("", "localhost")
            or not os.path.isabs(path)):
        return None
    return path


def _absolute_dir(text: Any, base: Optional[Path]) -> Optional[Path]:
    """``text`` as an absolute directory (a relative one joins ``base``).

    None when that is impossible, including a ``$VAR`` we would have to
    guess the value of, and a file URI that names no local directory.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    if text[:5].lower() == "file:":
        text = _file_uri_path(text)
        if text is None:
            return None
    if "$" in text or "%" in text:
        return None
    path = Path(os.path.expanduser(text))
    if path.is_absolute():
        return path
    return base / path if base is not None else None


def _repo_relative(repo: Path, header: str, directory: Optional[Path],
                   cd_dirs: Tuple[Optional[str], ...] = ()) -> Optional[str]:
    """The repo-relative path a patch header names, or None to skip it.

    Skipped: a file outside the repo, which is not the student's project,
    a relative header whose directory is unknown, and a header written as an
    expression (``_EXPRESSION_HEADER``). Recording those against a guessed
    base is how an outside file came to look like project work (review
    finding y8j).

    A ``cd`` before the header may have moved the patch -- or not (a
    subshell). So a relative header is recorded only if it lands inside the
    repo from ``directory`` AND from every ``cd`` target, and the path
    recorded is the one under the last ``cd``, where it most likely ran.
    """
    if _EXPRESSION_HEADER.search(header):
        return None
    target = Path(os.path.expanduser(header))
    if target.is_absolute():
        candidates = [target]
    else:
        bases = [directory] + [Path(d) if d else None for d in cd_dirs]
        if any(base is None for base in bases):
            return None
        candidates = [base / target for base in bases]
    inside = [_inside_repo(repo, path) for path in candidates]
    if any(rel is None for rel in inside):
        return None
    return inside[-1]


def _inside_repo(repo: Path, path: Path) -> Optional[str]:
    """``path`` relative to the repo (POSIX form), or None if outside it."""
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(repo))
    except (OSError, ValueError):  # ValueError: a different Windows drive
        return None
    if (rel in (os.curdir, os.pardir) or rel.startswith(os.pardir + os.sep)
            or os.path.isabs(rel)):
        return None
    return Path(rel).as_posix()


def _looks_like_write(payload: Dict[str, Any]) -> bool:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    return any(k in tool_input for k in ("new_string", "content", "new_str", "patch"))


def request_snapshot(repo: Path, reason: str) -> bool:
    """Ask the capture layer for a snapshot through its CLI seam.

    Shelling out (rather than importing) is the epic's standing constraint:
    the hook tree ships unchanged into assignment repos that may have no
    Python capture layer at all, where this simply no-ops.
    """
    seam = repo / "tests" / "_capture" / "__main__.py"
    if not seam.is_file():
        return False
    env = dict(os.environ)
    # The child IS the snapshot; stop the venv sitecustomize from taking a
    # second one when the child exits.
    env["_CAPTURE_SUBPROCESS"] = "1"
    try:
        subprocess.run(
            [sys.executable, "-m", "tests._capture", "snapshot", "--reason", reason],
            cwd=str(repo), env=env, timeout=25,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    except OSError:
        return


def safe_main(
    event_name: str,
    callback: Optional[Callable[[Path, Dict[str, Any]], None]] = None,
) -> int:
    """Common hook body. Always returns 0."""
    try:
        payload = read_payload()
        repo = project_repo_root()
        write_heartbeat(repo, payload)
        append_hook_event(repo, event_name, payload)
        if callback is not None:
            callback(repo, payload)
    except Exception:
        pass
    return 0
