"""Tests for the repo-local Codex hooks (hooks v2).

These are INFRASTRUCTURE tests -- deliberately unmarked, so the
student-facing runner (run_tests.py, which collects only
@pytest.mark.bundle tests) never grades them.

The hooks are exercised the way Codex exercises them: as a subprocess
launched through ``.codex/hooks/_run.py <hook_name>`` with the event
payload on stdin. Every test runs against a COPY of the hook tree inside
tmp_path, never the real repo, so a hook that writes evidence cannot
pollute the working tree -- and copying also proves the hooks are
location-independent (the same tree has to work in a student repo and in
a non-Python assignment repo, per the epic's standing constraint).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = REPO_ROOT / ".codex" / "hooks"
HOOKS_JSON = REPO_ROOT / ".codex" / "hooks.json"


@pytest.fixture()
def hook_repo(tmp_path: Path) -> Path:
    """A throwaway project root carrying a copy of the hook tree."""
    import shutil

    repo = tmp_path / "assignment"
    (repo / ".codex").mkdir(parents=True)
    if HOOK_DIR.is_dir():
        shutil.copytree(HOOK_DIR, repo / ".codex" / "hooks")
    else:  # hooks not shipped yet -- let the assertions report that, not the fixture
        (repo / ".codex" / "hooks").mkdir()
    if HOOKS_JSON.is_file():
        shutil.copy2(HOOKS_JSON, repo / ".codex" / "hooks.json")
    return repo


def run_hook(repo: Path, hook_name: str, payload: dict, cwd: Path | None = None,
             env: dict | None = None):
    """Invoke one hook exactly as Codex would; return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(repo / ".codex" / "hooks" / "_run.py"), hook_name],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd or repo),
        env=env,
        timeout=60,
    )


def make_transcript(tmp_path: Path, name: str = "rollout-2026-08-13T00-00-00-abc.jsonl") -> Path:
    path = tmp_path / "codex-home" / "sessions" / "2026" / "08" / "13" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/x"}}) + "\n",
        encoding="utf-8",
    )
    return path


def test_stop_hook_copies_transcript_into_ai_traces(hook_repo: Path, tmp_path: Path):
    """The Stop payload carries transcript_path; the hook copies it in-repo.

    This is the whole point of push-based capture: the transcript lands in
    the repo at the moment of use, without re-scanning $CODEX_HOME by
    cwd+mtime.
    """
    transcript = make_transcript(tmp_path)
    payload = {
        "hook_event_name": "Stop",
        "session_id": "019ffc18-eafe-7bb3-838e-9d24c64d92a3",
        "cwd": str(hook_repo),
        "transcript_path": str(transcript),
    }

    result = run_hook(hook_repo, "stop", payload)

    assert result.returncode == 0, result.stderr
    copies = sorted((hook_repo / ".ai-traces" / "codex").rglob(transcript.name))
    assert copies, "Stop hook did not copy the transcript into .ai-traces/codex/"
    assert copies[0].read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8")


def test_session_end_hook_also_copies_transcript(hook_repo: Path, tmp_path: Path):
    """SessionEnd is the backstop for a session that ends without a Stop."""
    transcript = make_transcript(tmp_path, "rollout-2026-08-13T01-00-00-def.jsonl")
    result = run_hook(hook_repo, "session_end", {
        "hook_event_name": "SessionEnd",
        "session_id": "sess-end",
        "reason": "exit",
        "transcript_path": str(transcript),
    })

    assert result.returncode == 0, result.stderr
    assert list((hook_repo / ".ai-traces" / "codex").rglob(transcript.name))


def test_transcript_recopied_when_rollout_grows(hook_repo: Path, tmp_path: Path):
    """Codex appends to one rollout all session long; a one-shot copy freezes it."""
    transcript = make_transcript(tmp_path)
    payload = {"session_id": "grow", "transcript_path": str(transcript)}
    run_hook(hook_repo, "stop", payload)

    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "response_item", "payload": {}}) + "\n")
    run_hook(hook_repo, "stop", payload)

    dest = hook_repo / ".ai-traces" / "codex" / "raw" / "rollouts" / transcript.name
    assert dest.read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8")


def test_transcript_path_with_tilde_is_expanded(hook_repo: Path, tmp_path: Path):
    """A ``~/...`` transcript_path must resolve against HOME, as in the
    Claude and Cursor hooks; otherwise the copy silently never happens."""
    home = tmp_path / "home"
    transcript = home / ".codex" / "sessions" / "rollout-2026-08-13T02-00-00-tld.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "session_meta"}\n', encoding="utf-8")
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))

    result = run_hook(hook_repo, "stop", {
        "session_id": "tilde",
        "transcript_path": "~/.codex/sessions/" + transcript.name,
    }, env=env)

    assert result.returncode == 0, result.stderr
    dest = hook_repo / ".ai-traces" / "codex" / "raw" / "rollouts" / transcript.name
    assert dest.is_file(), "transcript_path with ~ was not expanded"


# --- heartbeat: the untrusted-hooks signal ------------------------------


def test_session_start_writes_heartbeat_under_ai_traces_codex_heartbeat(hook_repo: Path):
    """AGENTS.md tells the assistant to look exactly here; a hook must write it."""
    result = run_hook(hook_repo, "session_start", {
        "hook_event_name": "SessionStart", "session_id": "abc-123", "source": "startup",
    })

    assert result.returncode == 0, result.stderr
    beats = sorted((hook_repo / ".ai-traces" / "codex" / "heartbeat").glob("*"))
    assert beats, "no heartbeat written; the AGENTS.md un-authorized check would misfire forever"
    body = json.loads(beats[0].read_text(encoding="utf-8"))
    assert body["adapter_name"] == "codex"
    assert body["session_id"] == "abc-123"


def test_heartbeat_content_is_stable_across_refires(hook_repo: Path):
    """Snapshots dedupe by tree SHA -- a varying heartbeat would kill dedupe."""
    payload = {"session_id": "abc-123", "hook_event_name": "SessionStart"}
    run_hook(hook_repo, "session_start", payload)
    beat = hook_repo / ".ai-traces" / "codex" / "heartbeat" / "abc-123.json"
    first = beat.read_bytes()

    run_hook(hook_repo, "user_prompt_submit", dict(payload, prompt="hi"))
    run_hook(hook_repo, "stop", payload)

    assert beat.read_bytes() == first
    # No timestamp-ish field may creep in later.
    assert set(json.loads(first)) == {"adapter_name", "schema_version", "session_id"}


def test_every_hook_writes_a_heartbeat(hook_repo: Path):
    """Codex trust is granted per hook entry, so no single hook may own the signal."""
    for name in sorted(p.stem for p in HOOK_DIR.glob("*.py") if not p.stem.startswith("_")):
        beat_dir = hook_repo / ".ai-traces" / "codex" / "heartbeat"
        for stale in beat_dir.glob("*"):
            stale.unlink()
        run_hook(hook_repo, name, {"session_id": f"s-{name}"})
        assert list(beat_dir.glob("*")), f"{name} hook wrote no heartbeat"


def test_heartbeat_is_readable_by_capture_health(hook_repo: Path, tmp_path: Path):
    """The manifest's conjunction (evidence AND heartbeat) must see both halves."""
    from tests._capture import capture_health

    transcript = make_transcript(tmp_path)
    run_hook(hook_repo, "stop", {"session_id": "joint", "transcript_path": str(transcript)})

    assert capture_health.has_heartbeat(hook_repo, "codex") is True
    assert capture_health.has_evidence(hook_repo, "codex") is True
    health = {"adapters": {"codex": {
        "heartbeat": capture_health.has_heartbeat(hook_repo, "codex"),
        "evidence": capture_health.has_evidence(hook_repo, "codex"),
    }}}
    assert capture_health.hooks_look_unauthorized(health, "codex") is False


# --- per-edit provenance ------------------------------------------------


def test_post_tool_use_records_edit_provenance_without_content(hook_repo: Path):
    run_hook(hook_repo, "post_tool_use", {
        "hook_event_name": "PostToolUse",
        "session_id": "edits",
        "turn_id": "t1",
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/server.py", "new_string": "SECRET-TOKEN-VALUE"},
        "tool_response": {"ok": True},
    })

    edits = hook_repo / ".ai-traces" / "codex" / "raw" / "hooks" / "edits-edits.jsonl"
    rows = [json.loads(line) for line in edits.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "file_edit"
    assert row["file"] == "src/server.py"
    assert row["extent_chars"] == len("SECRET-TOKEN-VALUE")
    assert len(row["content_sha256"]) == 64
    # Proportionality: the edit's text lives in the rollout, not in this record.
    assert "SECRET-TOKEN-VALUE" not in json.dumps(row)


# Real Codex edits do not arrive as {"file_path": ...}. The model calls a
# code-mode ``exec`` tool whose input is JavaScript source; apply_patch is a
# string literal inside it, so its newlines are ESCAPED (backslash-n). Older
# and shell-style tools carry the patch in a heredoc with real newlines.
# Everything below is synthetic -- only the shapes mirror real rollouts.
_PATCH_LINES = [
    "*** Begin Patch",
    "*** Update File: src/server.py",
    "@@ def handle():",
    "-    return None",
    "+    return reply",
    "*** Add File: src/protocol.py",
    "+HEADER = 1",
    "+FOOTER = 2",
    "*** End Patch",
]
_JS_EXEC_SOURCE = (
    'const patch = "' + "\\n".join(_PATCH_LINES) + '";\n'
    "text(await tools.apply_patch(patch));\n"
)
_SHELL_HEREDOC = "apply_patch <<'EOF'\n" + "\n".join(_PATCH_LINES) + "\nEOF\n"


@pytest.mark.parametrize("tool_name, tool_input", [
    pytest.param("exec", _JS_EXEC_SOURCE, id="exec-js-source-string"),
    pytest.param("exec", {"input": _JS_EXEC_SOURCE}, id="exec-js-source-in-input"),
    pytest.param("exec_command", {"cmd": _SHELL_HEREDOC}, id="exec_command-heredoc"),
    pytest.param("Bash", {"command": ["bash", "-lc", _SHELL_HEREDOC]}, id="shell-argv"),
])
def test_post_tool_use_records_apply_patch_inside_exec_commands(
        hook_repo: Path, tool_name, tool_input):
    """Each file named by a patch header gets its own provenance record."""
    run_hook(hook_repo, "post_tool_use", {
        "hook_event_name": "PostToolUse",
        "session_id": "patch",
        "turn_id": "t2",
        "cwd": str(hook_repo),
        "tool_name": tool_name,
        "tool_input": tool_input,
    })

    edits = hook_repo / ".ai-traces" / "codex" / "raw" / "hooks" / "edits-patch.jsonl"
    assert edits.is_file(), "apply_patch inside an exec command was not recorded"
    rows = [json.loads(line) for line in edits.read_text(encoding="utf-8").splitlines()]
    by_file = {row["file"]: row for row in rows}
    assert set(by_file) == {"src/server.py", "src/protocol.py"}
    # Extent counts only the ADDED text for that file (each '+' line + newline).
    assert by_file["src/server.py"]["extent_chars"] == len("    return reply\n")
    assert by_file["src/protocol.py"]["extent_chars"] == len("HEADER = 1\nFOOTER = 2\n")
    assert all(row["tool_name"] == tool_name for row in rows)
    # Proportionality: no patch text in the record.
    assert "return reply" not in edits.read_text(encoding="utf-8")


def test_lines_after_a_js_quoted_end_patch_are_not_attributed(hook_repo: Path):
    """In JS source the envelope ends as ``*** End Patch";``, not a bare line.

    Later '+'-prefixed text in the same exec source must not be counted as
    part of the last patched file.
    """
    source = _JS_EXEC_SOURCE + 'const note = "\\n+not part of any patch";\n'
    run_hook(hook_repo, "post_tool_use", {
        "session_id": "trailing", "cwd": str(hook_repo),
        "tool_name": "exec", "tool_input": source,
    })
    edits = hook_repo / ".ai-traces" / "codex" / "raw" / "hooks" / "edits-trailing.jsonl"
    rows = [json.loads(line) for line in edits.read_text(encoding="utf-8").splitlines()]
    by_file = {row["file"]: row for row in rows}
    assert by_file["src/protocol.py"]["extent_chars"] == len("HEADER = 1\nFOOTER = 2\n")


# A patch header is relative to the directory the patch ran in: the
# command's `workdir`, else the session `cwd`. The record must name the file
# relative to the REPO -- the redaction and attribution passes resolve it
# there -- and a file outside the repo is not the student's project at all.

def _patched_files(hook_repo: Path, session: str, payload: dict) -> set:
    """Run PostToolUse and return the `file` of every edit row it wrote."""
    run_hook(hook_repo, "post_tool_use", dict(payload, session_id=session))
    edits = hook_repo / ".ai-traces" / "codex" / "raw" / "hooks" / f"edits-{session}.jsonl"
    if not edits.is_file():
        return set()
    return {json.loads(line)["file"]
            for line in edits.read_text(encoding="utf-8").splitlines()}


def _heredoc(header: str) -> str:
    return ("apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: " + header
            + "\n+x = 1\n*** End Patch\nEOF\n")


@pytest.mark.parametrize("workdir, header, expected", [
    pytest.param(None, "src/server.py", {"src/server.py"}, id="relative-to-session-cwd"),
    pytest.param("src", "server.py", {"src/server.py"}, id="relative-to-relative-workdir"),
    pytest.param("<outside>", "server.py", set(), id="outside-workdir-skipped"),
    pytest.param(None, "<repo>/src/server.py", {"src/server.py"}, id="absolute-made-repo-relative"),
    pytest.param(None, "../elsewhere/server.py", set(), id="escape-skipped"),
])
def test_patch_header_paths_are_resolved_and_recorded_repo_relative(
        hook_repo: Path, tmp_path: Path, workdir, header, expected):
    places = {"<outside>": str(tmp_path / "elsewhere"), "<repo>": str(hook_repo)}
    tool_input = {"cmd": _heredoc(header.replace("<repo>", places["<repo>"]))}
    if workdir:
        tool_input["workdir"] = places.get(workdir, workdir)
    files = _patched_files(hook_repo, "rel", {
        "cwd": str(hook_repo), "tool_name": "exec_command", "tool_input": tool_input,
    })
    assert files == expected


@pytest.mark.parametrize("workdir, cwd, expected", [
    pytest.param("{repo_uri}/src", "{repo}", {"src/server.py"}, id="inside"),
    pytest.param("{outside_uri}", "{repo}", set(), id="outside"),
    pytest.param(None, "{repo_uri}", {"server.py"}, id="session-cwd"),
    pytest.param("src", "{repo_uri}", {"src/server.py"}, id="under-uri-cwd"),
    pytest.param("{spaced_uri}", "{repo}", {"my dir/server.py"}, id="encoded"),
    # Not a local directory we can check: nothing is recorded.
    pytest.param("file://otherhost{repo}/src", "{repo}", set(), id="other-host"),
    pytest.param("{repo_uri}/src?x=1", "{repo}", set(), id="query"),
    pytest.param("file:src", "{repo}", set(), id="relative-uri"),
])
def test_file_uri_directories_are_decoded_or_fail_closed(
        hook_repo: Path, tmp_path: Path, workdir, cwd, expected):
    """Codex states directories as file URIs too (every CommandExecution
    `cwd`). Read as a relative path, `file:///outside` became a folder
    inside the project, and the outside patch was recorded as project work."""
    (hook_repo / "my dir").mkdir()
    fill = lambda s: s.format(  # noqa: E731
        repo=hook_repo, repo_uri=hook_repo.as_uri(),
        outside_uri=(tmp_path / "elsewhere").as_uri(),
        spaced_uri=(hook_repo / "my dir").as_uri())
    tool_input = {"cmd": _heredoc("server.py")}
    if workdir:
        tool_input["workdir"] = fill(workdir)
    files = _patched_files(hook_repo, "uri", {
        "cwd": fill(cwd), "tool_name": "exec_command", "tool_input": tool_input,
    })
    assert files == expected


def test_patch_in_exec_source_with_an_outside_workdir_is_not_recorded(
        hook_repo: Path, tmp_path: Path):
    """The review reproduction's shape: the workdir is a key of the object
    literal inside the exec tool's JavaScript source."""
    source = ('const r = await tools.exec_command({cmd:"'
              + _heredoc("notes.py").replace("\n", "\\n")
              + '", workdir:"' + str(tmp_path / "elsewhere") + '"});')
    files = _patched_files(hook_repo, "jsout", {
        "cwd": str(hook_repo), "tool_name": "exec", "tool_input": source,
    })
    assert files == set()


def _exec_source(cmd: str, options: str = "", prelude: str = "") -> str:
    """Codex exec JavaScript running ``cmd``, with extra options verbatim."""
    return (prelude + 'const r = await tools.exec_command({cmd:"'
            + cmd.replace("\n", "\\n") + '"' + options + "});")


@pytest.mark.parametrize("options, prelude", [
    pytest.param(", workdir: wd", 'const wd = "{repo}/src";\n', id="identifier"),
    pytest.param(", workdir: `{repo}/src`", "", id="template-literal"),
    pytest.param(", workdir", 'const workdir = "{repo}/src";\n', id="shorthand"),
])
def test_patch_under_a_workdir_that_is_not_a_plain_literal_is_not_recorded(
        hook_repo: Path, options, prelude):
    """Only a plain string literal can be read without running the code.
    The directory here is even inside the repo -- it still cannot be known."""
    fill = lambda s: s.replace("{repo}", str(hook_repo))  # noqa: E731
    files = _patched_files(hook_repo, "nonlit", {
        "cwd": str(hook_repo), "tool_name": "exec",
        "tool_input": _exec_source(_heredoc("server.py"), fill(options), fill(prelude)),
    })
    assert files == set()


def test_patch_under_two_different_workdirs_is_not_recorded(hook_repo: Path):
    """One script, two commands, two directories: which one the patch ran
    in cannot be told, even though both are inside the repo."""
    source = (_exec_source(_heredoc("server.py"), f', workdir:"{hook_repo}"')
              + f'\nawait tools.exec_command({{cmd:"ls", workdir:"{hook_repo / "src"}"}});')
    files = _patched_files(hook_repo, "twodirs", {
        "cwd": str(hook_repo), "tool_name": "exec", "tool_input": source,
    })
    assert files == set()


@pytest.mark.parametrize("cd, expected", [
    pytest.param("cd <repo>/src && ", {"src/server.py"}, id="cd-into-repo"),
    pytest.param("cd <outside> && ", set(), id="cd-outside"),
    pytest.param("cd $DIR && ", set(), id="cd-variable"),
    pytest.param("cd src && ", set(), id="cd-relative"),
])
def test_cd_before_a_patch_sets_where_its_headers_resolve(
        hook_repo: Path, tmp_path: Path, cd, expected):
    cmd = (cd.replace("<repo>", str(hook_repo))
           .replace("<outside>", str(tmp_path / "elsewhere"))
           + _heredoc("server.py"))
    files = _patched_files(hook_repo, "cd", {
        "cwd": str(hook_repo), "tool_name": "exec", "tool_input": _exec_source(cmd),
    })
    assert files == expected


@pytest.mark.parametrize("source, cwd", [
    # Round-2 review: the session runs in the repo, the patch really runs
    # outside, so recording `server.py` would be misattribution.
    pytest.param(_exec_source("builtin cd <outside> && " + _heredoc("server.py")),
                 "<repo>", id="builtin-cd"),
    pytest.param(_exec_source("if cd <outside>; then " + _heredoc("server.py") + " fi"),
                 "<repo>", id="if-cd"),
    pytest.param(_exec_source("if true; then cd <outside>; fi; " + _heredoc("server.py")),
                 "<repo>", id="then-cd"),
    pytest.param(_exec_source("git -C <outside> status && " + _heredoc("server.py")),
                 "<repo>", id="git-C"),
    pytest.param('const wd = "<outside>"; const opts = {cmd:"'
                 + _heredoc("server.py").replace("\n", "\\n")
                 + '"}; opts.workdir = wd; await tools.exec_command(opts);',
                 "<repo>", id="property-assignment"),
    pytest.param(_exec_source(_heredoc("server.py"), ', ["workdir"]: wd',
                              'const wd = "<outside>";\n'),
                 "<repo>", id="computed-key"),
    pytest.param(_exec_source(_heredoc("server.py"), ", ...opts",
                              'const opts = {}; opts["w" + "orkdir"] = "<outside>";\n'),
                 "<repo>", id="spread"),
    pytest.param('const patch = "' + _heredoc("server.py").replace("\n", "\\n")
                 + '"; const run = tools["exec_" + "command"];\n'
                 'await run(JSON.parse(atob(blob)));',
                 "<repo>", id="tools-reached-indirectly"),
    # A dict input: its own workdir is the repo, a nested one is not.
    pytest.param({"cmd": _heredoc("server.py").replace("\\n", "\n"),
                  "workdir": "<repo>", "env": {"workdir": "<outside>"}},
                 "<repo>", id="dict-nested-workdir"),
    # Another call states the repo; the patch call states nothing, so it
    # runs in the session directory, which is outside.
    pytest.param('await tools.exec_command({cmd:"ls", workdir:"<repo>"});\n'
                 + _exec_source(_heredoc("server.py")),
                 "<outside>", id="inherits-session-cwd"),
])
def test_patch_whose_directory_is_hidden_is_not_recorded(
        hook_repo: Path, tmp_path: Path, source, cwd):
    places = {"<repo>": str(hook_repo), "<outside>": str(tmp_path / "elsewhere")}

    def fill(value):
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        for key, place in places.items():
            value = value.replace(key, place)
        return value
    source, cwd = fill(source), fill(cwd)
    files = _patched_files(hook_repo, "hidden", {
        "cwd": cwd, "tool_name": "exec", "tool_input": source,
    })
    assert files == set()


_EXPR_PATCH_TAIL = "\\n+x = 1\\n*** End Patch\\nEOF"


@pytest.mark.parametrize("source", [
    # Round-3 review: the header is a JavaScript or shell expression, so the
    # file it names is known only to the running script. Recording the
    # expression text as a project file is misattribution.
    pytest.param('const f = "<outside>/server.py";\n'
                 'await tools.exec_command({cmd:"apply_patch <<\'EOF\'\\n*** Begin Patch'
                 '\\n*** Update File: " + f + "' + _EXPR_PATCH_TAIL + '"});',
                 id="concatenation"),
    pytest.param('const d = "<outside>";\n'
                 'await tools.exec_command({cmd:`apply_patch <<\'EOF\'\n*** Begin Patch\n'
                 '*** Update File: ${path.join(d, "server.py")}\n+x = 1\n'
                 '*** End Patch\nEOF`});', id="template-expression"),
    pytest.param('const d = "<outside>";\n'
                 'await tools.exec_command({cmd:`apply_patch <<\'EOF\'\n*** Begin Patch\n'
                 '*** Update File: ${d}/server.py\n+x = 1\n*** End Patch\nEOF`});',
                 id="template-variable"),
    pytest.param(_exec_source("apply_patch <<EOF\n*** Begin Patch\n"
                              "*** Update File: $(echo <outside>)/server.py\n+x = 1\n"
                              "*** End Patch\nEOF"), id="command-substitution"),
    # PowerShell directory changes, and a key spelled with an escape.
    pytest.param(_exec_source("Set-Location <outside>; " + _heredoc("server.py")),
                 id="set-location"),
    pytest.param(_exec_source("Push-Location <outside>; " + _heredoc("server.py")),
                 id="push-location"),
    pytest.param(_exec_source("sl <outside>; " + _heredoc("server.py")), id="sl"),
    pytest.param(_exec_source(_heredoc("server.py"), ', w\\u006frkdir: "<outside>"'),
                 id="escaped-key"),
])
def test_patch_whose_target_is_an_expression_is_not_recorded(
        hook_repo: Path, tmp_path: Path, source):
    source = source.replace("<outside>", str(tmp_path / "elsewhere"))
    files = _patched_files(hook_repo, "expr", {
        "cwd": str(hook_repo), "tool_name": "exec", "tool_input": source,
    })
    assert files == set()


def test_lines_under_an_expression_header_are_not_added_to_the_file_before_it(
        hook_repo: Path):
    """A header that is its own JavaScript literal does not start a line, so
    it is not read as a header -- but it still ends the previous file's
    section. Otherwise that file's record would count another file's lines."""
    source = ('const f = "/elsewhere/notes.py";\n'
              'await tools.exec_command({cmd:"apply_patch <<\'EOF\'\\n*** Begin Patch'
              '\\n*** Update File: src/a.py\\n+A = 1\\n" + "*** Update File: " + f'
              ' + "\\n+OTHER = 2\\n*** End Patch\\nEOF"});')
    run_hook(hook_repo, "post_tool_use", {
        "session_id": "sections", "cwd": str(hook_repo), "tool_name": "exec",
        "tool_input": source,
    })
    edits = hook_repo / ".ai-traces" / "codex" / "raw" / "hooks" / "edits-sections.jsonl"
    rows = [json.loads(line) for line in edits.read_text(encoding="utf-8").splitlines()]
    assert [(row["file"], row["extent_chars"]) for row in rows] == [
        ("src/a.py", len("A = 1\n"))]


def test_patch_in_a_call_with_a_plain_repo_workdir_is_recorded(hook_repo: Path):
    """Guard against over-withholding: both calls state the same directory."""
    source = (f'await tools.exec_command({{cmd:"ls", workdir:"{hook_repo}"}});\n'
              + _exec_source(_heredoc("server.py"), f', "workdir": "{hook_repo}/src"'))
    files = _patched_files(hook_repo, "plain", {
        "cwd": str(hook_repo), "tool_name": "exec", "tool_input": source,
    })
    assert files == set()  # two different directories: unknown
    source = _exec_source(_heredoc("server.py"), f', "workdir": "{hook_repo}/src"')
    files = _patched_files(hook_repo, "plain2", {
        "cwd": str(hook_repo), "tool_name": "exec", "tool_input": source,
    })
    assert files == {"src/server.py"}


def test_relative_patch_header_with_no_known_directory_is_not_recorded(hook_repo: Path):
    """No workdir and no session cwd: the file cannot be known, so nothing
    is recorded rather than a guess."""
    files = _patched_files(hook_repo, "nocwd", {
        "tool_name": "exec_command", "tool_input": {"cmd": _heredoc("notes.py")},
    })
    assert files == set()


def test_post_tool_use_ignores_exec_commands_without_a_patch(hook_repo: Path):
    run_hook(hook_repo, "post_tool_use", {
        "session_id": "plain", "tool_name": "exec_command",
        "tool_input": {"cmd": "cat src/server.py"},
    })
    assert not list((hook_repo / ".ai-traces" / "codex" / "raw" / "hooks").glob("edits-*.jsonl"))


def test_post_tool_use_ignores_non_edit_tools(hook_repo: Path):
    run_hook(hook_repo, "post_tool_use", {
        "session_id": "reads", "tool_name": "Read",
        "tool_input": {"file_path": "README.md"},
    })
    assert not list((hook_repo / ".ai-traces" / "codex" / "raw" / "hooks").glob("edits-*.jsonl"))


def test_hook_event_payload_is_logged_verbatim(hook_repo: Path):
    run_hook(hook_repo, "user_prompt_submit", {
        "session_id": "log", "hook_event_name": "UserPromptSubmit", "prompt": "how do sockets work",
    })
    log = hook_repo / ".ai-traces" / "codex" / "raw" / "hooks" / "hooks-log.jsonl"
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["hook_event_name"] == "UserPromptSubmit"
    assert row["payload"]["prompt"] == "how do sockets work"


# --- fail-open behavior -------------------------------------------------


@pytest.mark.parametrize("stdin_text", ["", "not json at all", "[]", "null"])
def test_hooks_exit_zero_on_garbage_stdin(hook_repo: Path, stdin_text: str):
    """A hook must never fail a student's turn, whatever Codex hands it."""
    for name in sorted(p.stem for p in HOOK_DIR.glob("*.py") if not p.stem.startswith("_")):
        result = subprocess.run(
            [sys.executable, str(hook_repo / ".codex" / "hooks" / "_run.py"), name],
            input=stdin_text, capture_output=True, text=True, cwd=str(hook_repo), timeout=60,
        )
        assert result.returncode == 0, f"{name}: rc={result.returncode} {result.stderr}"


def test_unknown_hook_name_is_a_no_op(hook_repo: Path):
    result = run_hook(hook_repo, "does_not_exist", {"session_id": "x"})
    assert result.returncode == 0


def test_hooks_work_when_cwd_is_a_subdirectory(hook_repo: Path, tmp_path: Path):
    """Codex discovers hooks by walking UP from cwd; cwd is not the repo root."""
    sub = hook_repo / "src" / "deep"
    sub.mkdir(parents=True)
    transcript = make_transcript(tmp_path, "rollout-2026-08-13T02-00-00-sub.jsonl")

    result = run_hook(hook_repo, "stop", {
        "session_id": "subdir", "cwd": str(sub), "transcript_path": str(transcript),
    }, cwd=sub)

    assert result.returncode == 0, result.stderr
    assert (hook_repo / ".ai-traces" / "codex" / "raw" / "rollouts" / transcript.name).is_file()


def test_hooks_import_nothing_from_the_python_capture_layer(hook_repo: Path):
    """Standing constraint: the hook tree must run in a non-Python repo too.

    Hooks reach the capture layer only through its CLI seam (a subprocess),
    never by importing it.
    """
    import re

    bad = re.compile(r"^\s*(import|from)\s+tests\b", re.MULTILINE)
    for path in HOOK_DIR.glob("*.py"):
        assert not bad.search(path.read_text(encoding="utf-8")), f"{path.name} imports the capture layer"


def test_snapshot_seam_absent_is_survivable(hook_repo: Path, tmp_path: Path):
    """A non-Python assignment repo has no tests/_capture; Stop must still work."""
    assert not (hook_repo / "tests").exists()
    transcript = make_transcript(tmp_path, "rollout-2026-08-13T03-00-00-seam.jsonl")
    result = run_hook(hook_repo, "stop", {"session_id": "seam", "transcript_path": str(transcript)})
    assert result.returncode == 0
    assert (hook_repo / ".ai-traces" / "codex" / "raw" / "rollouts" / transcript.name).is_file()


# --- hooks.json configuration -------------------------------------------

KNOWN_CODEX_EVENTS = {
    "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
    "PreCompact", "PostCompact", "SubagentStart", "SubagentStop",
    "Stop", "SessionStart", "SessionEnd",
}


def load_hooks_config() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))


def iter_hook_entries():
    for event, groups in load_hooks_config()["hooks"].items():
        for group in groups:
            for entry in group["hooks"]:
                yield event, group, entry


def test_hooks_json_registers_only_known_events_and_the_required_ones():
    events = set(load_hooks_config()["hooks"])
    unknown = events - KNOWN_CODEX_EVENTS
    assert not unknown, f"unknown event names would risk the whole file failing to load: {unknown}"
    # These three carry the acceptance behaviors: heartbeat, per-edit
    # provenance, transcript copy.
    assert {"SessionStart", "PostToolUse", "Stop", "SessionEnd"} <= events


def test_every_registered_hook_points_at_a_shipped_script():
    for event, _group, entry in iter_hook_entries():
        assert entry["type"] == "command"
        command = entry["command"]
        names = [p.stem for p in HOOK_DIR.glob("*.py") if not p.stem.startswith("_")]
        assert any(f"_run.py\" {name}" in command for name in names), f"{event}: {command}"


def test_every_hook_sets_an_explicit_timeout():
    """The default is 1 SECOND on SessionEnd -- it kills the transcript copy."""
    for event, _group, entry in iter_hook_entries():
        assert isinstance(entry.get("timeout"), int), f"{event} has no explicit timeout"
        assert entry["timeout"] >= 5, f"{event} timeout is too tight for a file copy"


def test_every_hook_has_a_windows_command():
    for event, _group, entry in iter_hook_entries():
        assert entry.get("commandWindows"), f"{event} would be dead on Windows"


def test_hook_commands_contain_no_machine_specific_paths():
    """hooks.json is hash-pinned and shipped verbatim, so it cannot be templated."""
    text = HOOKS_JSON.read_text(encoding="utf-8")
    assert "/Users/" not in text and "C:\\" not in text
    assert "$r/.codex/hooks/_run.py" in text  # resolved at run time instead


def test_hook_commands_force_exit_zero():
    """Belt and braces: even a missing interpreter must not fail a turn."""
    for event, _group, entry in iter_hook_entries():
        assert "exit 0" in entry["command"], event
        assert "exit 0" in entry["commandWindows"], event


# --- shipping -----------------------------------------------------------


def test_hook_files_are_integrity_tracked():
    """Un-pinned hooks would let a student silently disable their own capture."""
    from tools import verify_integrity

    tracked = set(verify_integrity.TRACKED)
    expected = {".codex/hooks.json"} | {
        f".codex/hooks/{p.name}" for p in HOOK_DIR.glob("*.py")
    }
    assert expected <= tracked, f"not integrity-tracked: {sorted(expected - tracked)}"


# create-assignment.sh is instructor-only; a student repo has no generator to inspect.
@pytest.mark.skipif(
    not (REPO_ROOT / "create-assignment.sh").is_file(),
    reason="create-assignment.sh is instructor-only; not present in a student repo",
)
def test_create_assignment_ships_the_hook_tree():
    text = (REPO_ROOT / "create-assignment.sh").read_text(encoding="utf-8")
    assert "cp -r .codex" in text
