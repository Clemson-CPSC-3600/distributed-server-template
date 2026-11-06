"""Tests for the transcript redaction pass (instructor decision, 2026-08-14).

The decision: SCRUB, scoped by the project boundary. The privacy boundary IS
the submission boundary -- in-project content is already visible to the
grader, so it is kept verbatim; anything outside the project is pure exposure
with no forensic value and is replaced by a stable opaque sentinel.

Layers covered here:
    1. secret-pattern scrub before anything is staged
    2. path handling: in-project -> repo-relative, out-of-project ->
       ``<external:sha8>`` (stable, so repeated references still correlate)
    3. gitignored in-project files count as EXTERNAL (a gitignored ``.env``
       is not submitted, so its contents must not be committed)
    4. external content is dropped but the EVENT is retained, carrying
       ``withheld: true`` -- gaps stay visible as gaps
    5. fail closed: unparseable input is never committed verbatim; a marker
       records the withheld byte count and the capture-health manifest
       surfaces it
    6. the force-add anti-evasion control (Attack K) is pinned: raw traces
       stop being committed, but the trace directories are still FORCE
       staged, so a student cannot hide traces by gitignoring them.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests._capture import capture_health, redaction
from tests._capture.agent_adapters.claude import ClaudeAdapter
from tests._capture.agent_adapters.codex import CodexAdapter
from tests._capture.agent_adapters.vscode_chat import VSCodeChatAdapter

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    """Throwaway git repo with a .gitignore that hides .env and venv/."""
    return make_repo(tmp_path)


def make_repo(root: Path) -> Path:
    """Turn `root` into the throwaway git repo the `repo` fixture returns."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / ".gitignore").write_text(
        ".env\nvenv/\n.ai-traces/*/raw/\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def write_rollout(repo: Path, rows, name="rollout-1.jsonl") -> Path:
    path = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def redacted_text(repo: Path, name="rollout-1.jsonl") -> str:
    out = (repo / ".ai-traces" / "codex" / redaction.REDACTED_SUBDIR
           / "rollouts" / name)
    assert out.exists(), f"missing redacted derivative: {out}"
    return out.read_text(encoding="utf-8")


# --- Layer 1: secret-pattern scrub ---------------------------------------

SECRET = "sk-proj-ABC123abc456DEF789ghiJKL012mno345"
SECRET_LINE = "TOP_SECRET = 'do not leak'"


def test_secret_token_scrubbed_before_staging(repo):
    """A known token pattern never reaches the committed derivative."""
    write_rollout(repo, [
        {"type": "session_meta", "payload": {"id": "s1", "cwd": str(repo)}},
        # The student typed it: the typed record, then the message copy.
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": f"export OPENAI_API_KEY={SECRET}"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text",
                         "text": f"export OPENAI_API_KEY={SECRET}"}]}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert SECRET not in text
    assert "<redacted:" in text
    # Prompt text itself is KEPT -- only the credential is removed.
    assert "export OPENAI_API_KEY" in text


@pytest.mark.parametrize("secret", [
    "ghp_0123456789abcdefghijklmnopqrstuvwxyz",
    "AKIAIOSFODNN7EXAMPLE",
    "".join(("xoxb-", "123456789012", "-abcdefghijklmnop")),
    "-----BEGIN RSA PRIVATE KEY-----\\nMIIEowIBAAKCAQEA\\n-----END RSA PRIVATE KEY-----",
])
def test_common_credential_shapes_are_scrubbed(repo, secret):
    write_rollout(repo, [
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": f"here it is: {secret}"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert secret.split("\\n")[0] not in text or "PRIVATE KEY" not in text
    assert "<redacted:" in text


def test_user_email_key_is_stripped(repo):
    write_rollout(repo, [
        {"type": "session_meta", "payload": {
            "id": "s1", "cwd": str(repo), "user_email": "student@school.edu"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "student@school.edu" not in text


# --- Layer 2: path handling ----------------------------------------------

def test_in_project_absolute_path_becomes_repo_relative(repo):
    write_rollout(repo, [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "shell",
            "arguments": json.dumps({"file_path": str(repo / "src" / "main.py")})}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "src/main.py" in text
    assert str(repo) not in text


def test_external_path_becomes_stable_opaque_sentinel(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "secret_notes.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    write_rollout(repo, [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "read",
            "arguments": json.dumps({"file_path": str(outside)})}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "read",
            "arguments": json.dumps({"file_path": str(outside)})}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "secret_notes.py" not in text
    assert str(outside) not in text
    tokens = [tok for tok in text.split('"') if tok.startswith("<external:")]
    assert tokens, f"no external sentinel in: {text}"
    # Stable: the same external file yields the same token both times, so an
    # attribution pass can still correlate repeated references.
    assert len(set(tokens)) == 1


def test_home_directory_paths_in_free_text_lose_the_username(repo, monkeypatch):
    home = Path.home()
    leaky = str(home / "Documents" / "other-course" / "hw.py")
    write_rollout(repo, [
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": f"I read {leaky} for you"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert leaky not in text
    assert home.name not in text


# --- Layer 3: gitignored in-project files count as external --------------

def test_gitignored_in_project_file_is_treated_as_external(repo):
    """A gitignored `.env` is not submitted, so reading it taints the call
    exactly like a path outside the project would."""
    (repo / ".env").write_text("API_KEY=whatever\n", encoding="utf-8")
    write_rollout(repo, [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "c1",
            "input": 'const r = await tools.exec_command({cmd:"cat '
                     + str(repo / ".env") + '"})'}},
        {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "c1",
            "output": [{"type": "input_text", "text": "API_KEY=whatever"}]}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "API_KEY=whatever" not in text
    assert "<external:" in text  # the call keeps the sentinel


def test_capture_layer_traces_are_never_treated_as_external(repo):
    """`.ai-traces/*/raw/` is gitignored, but it is OUR evidence, not the
    student's private content -- evidence_refs must stay readable."""
    r = redaction.Redactor(repo)
    rendered, internal = r.classify_path(
        str(repo / ".ai-traces" / "codex" / "raw" / "rollouts" / "r.jsonl"))
    assert internal is True
    assert rendered == ".ai-traces/codex/raw/rollouts/r.jsonl"


def test_unresolvable_path_fails_closed(repo):
    r = redaction.Redactor(repo)
    rendered, internal = r.classify_path("/definitely/not/under/the/repo/x.py")
    assert internal is False
    assert rendered.startswith("<external:")


# --- Layer 4: withheld content stays a visible gap -----------------------

def test_patch_changes_mapping_keyed_by_external_path_is_redacted(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "private.py"
    outside.write_text(SECRET_LINE + "\n", encoding="utf-8")
    inside = repo / "src" / "main.py"
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "patch_apply_end", "call_id": "c9", "success": True,
            "changes": {
                str(outside): {"unified_diff": f"+{SECRET_LINE}\n"},
                str(inside): {"unified_diff": "+print('hi')\n"},
            }}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)

    assert str(outside) not in text
    assert "private.py" not in text
    assert SECRET_LINE not in text
    assert "<external:" in text

    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(rows) == 1, "the edit event itself must be RETAINED"
    changes = rows[0]["payload"]["changes"]
    external_keys = [k for k in changes if k.startswith("<external:")]
    assert len(external_keys) == 1
    assert changes[external_keys[0]] == {"withheld": True,
                                         "reason": "external_path"}
    # The in-project sibling edit survives intact -- it is submitted work.
    assert changes["src/main.py"]["unified_diff"] == "+print('hi')\n"
    assert rows[0]["payload"]["call_id"] == "c9"


def test_gitignored_path_in_key_position_is_external(repo):
    (repo / ".env").write_text("API_KEY=whatever\n", encoding="utf-8")
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "patch_apply_end",
            "changes": {".env": {"unified_diff": "+API_KEY=whatever\n"}}}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "API_KEY=whatever" not in text
    assert "<external:" in text


def test_function_call_arguments_json_string_with_keyed_paths(repo,
                                                             tmp_path_factory):
    """``function_call.payload.arguments`` is a JSON STRING that must be
    parsed before the mapping inside it can be scanned."""
    outside = tmp_path_factory.mktemp("other") / "leak.py"
    outside.write_text("x\n", encoding="utf-8")
    write_rollout(repo, [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "apply_patch", "call_id": "f1",
            "arguments": json.dumps(
                {"changes": {str(outside): {"content": SECRET_LINE}}})}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert SECRET_LINE not in text
    assert str(outside) not in text
    assert "<external:" in text


def test_prompt_text_survives_a_prompt_that_mentions_an_outside_path(repo):
    """Rule 2 guard: a broader path sweep must NOT start blanking prompts.

    The path is redacted; the student's words are not."""
    leaky = str(Path.home() / "Documents" / "other" / "thing.py")
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "user_message",
            "message": f"why does {leaky} fail when I import it?"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert leaky not in text
    assert "why does" in text
    assert "fail when I import it?" in text
    assert "withheld" not in text


def test_session_cwd_above_the_repo_does_not_blank_the_prompt(repo):
    """`cwd` is rewritten but must never trigger content withholding --
    otherwise a session started one directory up loses its prompts.

    The session's instructions are a different matter: the tool injects
    them, and injected text is not captured (instructor decision 35e)."""
    write_rollout(repo, [
        {"type": "session_meta", "payload": {
            "id": "s1", "cwd": str(repo.parent),
            "instructions": "be a tutor"}},
        {"type": "event_msg", "payload": {
            "type": "user_message", "cwd": str(repo.parent),
            "message": "help me with the parser"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "help me with the parser" in text
    assert "be a tutor" not in text
    meta = json.loads(text.splitlines()[0])["payload"]
    assert meta["instructions"] == {"withheld": True,
                                    "reason": "not_project_content"}


def test_apply_patch_text_targeting_an_external_file_is_withheld(
        repo, tmp_path_factory):
    """Codex `apply_patch` ships the edit as a PATCH TEXT BLOB whose target
    is a `*** Update File:` header, not a structured field."""
    outside = tmp_path_factory.mktemp("other") / "secret.py"
    outside.write_text("x\n", encoding="utf-8")
    write_rollout(repo, [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "apply_patch", "call_id": "p1",
            "arguments": json.dumps(
                {"patch": f"*** Update File: {outside}\n+{SECRET_LINE}\n"})}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert str(outside) not in text
    assert SECRET_LINE not in text
    assert "withheld" in text
    assert "p1" in text, "the event itself must be RETAINED"


def test_in_project_patch_text_is_kept_verbatim(repo):
    """The session cwd is what a relative header resolves against. Without
    one, the header's file is unknowable and the patch is withheld."""
    write_rollout(repo, [
        {"type": "session_meta", "payload": {"id": "s1", "cwd": str(repo)}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "apply_patch",
            "arguments": json.dumps(
                {"patch": "*** Update File: src/main.py\n+print('hi')\n"})}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "print('hi')" in text
    assert "withheld" not in text


def test_unified_diff_header_naming_an_external_file_is_withheld(
        repo, tmp_path_factory):
    """Absolute diff headers are decidable and are withheld.

    KNOWN RESIDUAL: a git-style header from ANOTHER repo (`--- a/x.py`)
    carries no absolute path, so nothing can tell it from an in-project
    edit. That is the same path-less residual documented in AI_POLICY.md,
    not a separate defect."""
    outside = tmp_path_factory.mktemp("other") / "hidden.py"
    outside.write_text("x\n", encoding="utf-8")
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "tool_result",
            "output": f"--- {outside}\n+++ {outside}\n+{SECRET_LINE}\n"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert SECRET_LINE not in text
    assert str(outside) not in text


def test_absolute_path_outside_home_in_prose_is_not_left_verbatim(repo):
    """The home-prefix rule is not enough: an external path can live
    anywhere on disk (/opt, /srv, a mounted volume)."""
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "agent_message",
            "message": "I looked at /opt/scratch/other_project/notes.py first"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "/opt/scratch/other_project/notes.py" not in text
    assert "<external:" in text
    assert "I looked at" in text  # prose survives


def test_urls_are_not_mangled_into_path_sentinels(repo):
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "agent_message",
            "message": "see https://docs.python.org/3/library/json.html"}},
    ])
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    assert "https://docs.python.org/3/library/json.html" in text


# Not every slash in prose starts a path (review round 5): a closing tag
# and a one-word `/command` that names nothing on disk carry no user name,
# so they are left as written. A `./` or `../` path is read from the
# project root: inside the project it is written repo-relative, outside it
# becomes a sentinel.
@pytest.mark.parametrize("text, expected", [
    ("<b>bold</b> <bash-input>ls</bash-input>", None),
    ("<command-name>/review</command-name> then /compact", None),
    ("run /review-pr 12, or /plugin:cmd", None),
    ("edit ./src/main.py now", "edit src/main.py now"),
    ("cd ./ first", None),
    ("cat </etc/hosts", "cat <<external:"),
    ("see /tmp and {repo}/src", "see <external:"),
])
def test_prose_slashes_that_name_no_outside_path_are_kept(repo, text, expected):
    text = text.replace("{repo}", str(repo))
    out = redaction.Redactor(repo).scrub_text(text)
    if expected is None:
        assert out == text
    else:
        assert out.startswith(expected)
    assert str(repo) not in out


def test_a_relative_prose_path_that_leaves_the_project_is_hidden(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "notes.md"
    relative = os.path.relpath(outside, repo)
    assert relative.startswith("../")
    out = redaction.Redactor(repo).scrub_text(f"see {relative} and ./.env")
    assert outside.parent.name not in out
    assert out.startswith("see <external:")
    assert out.endswith(" and <external:" + out.rsplit("<external:", 1)[1])


# --- Layer 4b: the call/output taint join --------------------------------
#
# Content withholding is a JOIN, not an inference. Shapes below are the ones
# measured on real transcripts: Codex `exec` (a JavaScript source string
# wrapping ONE shell string, joined to its output by call_id) and Claude
# Code (`tool_use.id` -> `tool_result.tool_use_id`).

READ_SECRET = "SECRET_FROM_OUTSIDE_THE_PROJECT"


def js_exec(cmd, call_id="c1", workdir=None):
    """The real Codex `custom_tool_call` input shape."""
    inner = {"cmd": cmd}
    if workdir:
        inner["workdir"] = workdir
    body = ", ".join(f'{k}:"{v}"' for k, v in inner.items())
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": call_id,
        "input": "const r = await tools.exec_command({" + body + "})"}}


def js_output(text=READ_SECRET, call_id="c1"):
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call_output", "call_id": call_id,
        "output": [{"type": "input_text", "text": text}]}}


def run_rows(repo, rows):
    write_rollout(repo, rows)
    redaction.redact_traces(repo)
    text = redacted_text(repo)
    return text, [json.loads(l) for l in text.splitlines() if l.strip()]


def test_exec_reading_external_file_withholds_the_joined_output(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "notes.py"
    outside.write_text("x\n", encoding="utf-8")
    text, rows = run_rows(repo, [js_exec(f"cat {outside}"), js_output()])
    assert READ_SECRET not in text
    assert str(outside) not in text
    call, out = rows[0]["payload"], rows[1]["payload"]
    # The CALL is kept -- evidence that something outside was read.
    assert call["call_id"] == "c1"
    assert "<external:" in call["input"]
    # The OUTPUT is withheld but the record and its join survive.
    assert out["withheld"] is True
    assert out["reason"] == "external_read"
    assert out["call_id"] == "c1"


def test_multi_file_command_mixing_in_and_out_of_project_withholds_all(
        repo, tmp_path_factory):
    """One shell string can read several files and the outputs come back as
    ONE undifferentiated blob -- there is no boundary in the data to cut
    along, so the whole blob is withheld."""
    outside = tmp_path_factory.mktemp("other") / "private.py"
    outside.write_text("x\n", encoding="utf-8")
    text, rows = run_rows(repo, [
        js_exec(f"cat src/main.py && cat {outside}"), js_output()])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["withheld"] is True


def test_in_project_command_output_is_kept_verbatim(repo):
    text, rows = run_rows(repo, [
        session_meta(repo), js_exec("cat src/main.py"),
        js_output("print('hi')")])
    assert "print('hi')" in text
    assert "withheld" not in text


@pytest.mark.parametrize("cmd_template, tainted", [
    # --- addendum shapes: path-like but not plainly absolute -----------
    ("cat ~/.ssh/config", True),                     # tilde
    ("cat $HOME/.aws/credentials", True),            # env expansion
    ("cat ${HOME}/.aws/credentials", True),          # braced env
    ("cd .. && cat ../other/file.py", True),         # relative escape
    ("cat ../../etc/passwd", True),                  # multi-level escape
    ("cat $UNKNOWABLE_VAR/secrets.txt", True),       # unresolvable -> closed
    # --- in-project controls: must NOT taint ---------------------------
    ("cat src/main.py", False),
    # Allow-list (d5w): a relative `cd` may follow CDPATH, and this
    # project has no tracked run_tests.py to run. Both were kept before.
    ("cd tests && cat conftest.py", True),
    ("python run_tests.py", True),
    # --- gitignored in-project: rule 5 says external -------------------
    ("cat venv/lib/thing.py", True),
    # A bare word names a file, whether or not it still exists.
    ("cat .env", True),
    ("cd venv && cat lib/thing.py", True),
    ("cat old.env.bak", True),
    ("ls src && echo venvish", False),
    # Another user's home; git's own files (names ignore case).
    ("cat ~bob/.ssh/id_rsa", True),
    ("cat .git/config", True),
    ("cat .GIT/config", True),
    ("git status && cat .gitignore", False),
])
def test_taint_recognizes_path_shapes_beyond_plain_absolute(
        repo, cmd_template, tainted):
    (repo / "venv").mkdir(exist_ok=True)
    (repo / "venv" / "lib").mkdir(exist_ok=True)
    (repo / "venv" / "lib" / "thing.py").write_text("x\n", encoding="utf-8")
    (repo / ".env").write_text("KEY=1\n", encoding="utf-8")
    with (repo / ".gitignore").open("a", encoding="utf-8") as f:
        f.write("*.bak\n")  # and no such file exists
    text, rows = run_rows(repo, [session_meta(repo), js_exec(cmd_template),
                                 js_output()])
    withheld = rows[2]["payload"].get("withheld") is True
    assert withheld is tainted, f"{cmd_template!r} -> withheld={withheld}"
    if not tainted:
        assert READ_SECRET in text


# Our own capture files are unredacted until the pass rewrites them: raw
# transcripts, normalized events, the interaction stream, Aider's history
# (review round 4). Their paths stay readable in the record, but READING
# them reads every transcript, so the output is withheld -- for the whole
# `.ai-traces` directory, the rule vouching already uses.
TRACE_READS = [
    "cat .ai-traces/codex/raw/rollouts/other.jsonl",
    "cat ./.ai-traces/codex/raw/rollouts/other.jsonl",
    "cat {repo}/.ai-traces/codex/raw/rollouts/other.jsonl",
    "cat src/../.ai-traces/codex/raw/rollouts/other.jsonl",
    "head .ai-traces/codex/normalized/s1.jsonl",
    "tail {repo}/.ai-traces/interaction-stream.jsonl",
    "cat {repo}/.ai-traces/codex/redacted/rollouts/other.jsonl",
    "rg KEY .ai-traces",
    "grep -rn KEY .ai-traces/codex",
    "cd .ai-traces && cat codex/raw/rollouts/other.jsonl",
    "cat .aider.chat.history.md",
]


@pytest.mark.parametrize("cmd", TRACE_READS)
def test_reading_the_capture_files_taints_the_call(repo, cmd):
    for rel in (".ai-traces/codex/raw/rollouts/other.jsonl",
                ".ai-traces/codex/normalized/s1.jsonl",
                ".ai-traces/interaction-stream.jsonl",
                ".aider.chat.history.md"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("{}\n", encoding="utf-8")
    text, rows = run_rows(repo, [js_exec(cmd.format(repo=repo)), js_output()])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["withheld"] is True
    call = rows[0]["payload"]["input"]
    assert str(repo) not in call
    if cmd.startswith("cat {repo}/"):  # still rendered repo-relative
        assert ".ai-traces/" in call and "<external:" not in call


# The files an adapter drops from every snapshot (`unstage_after`, e.g.
# Codex's login token) are protected reads whether or not the project's
# .gitignore lists them -- and so is a directory search that reaches them.
@pytest.mark.parametrize("cmd, tainted", [
    ("cat .codex/auth.json", True),
    ("head -c 100 ./.codex/auth.json", True),
    ("cat {repo}/.CODEX/Auth.json", True),
    ("rg token .codex", True),
    ("cat .codex/config.toml", False),
])
def test_reading_an_unstaged_secret_taints_the_call(repo, cmd, tainted):
    (repo / ".codex").mkdir()
    (repo / ".codex" / "auth.json").write_text('{"token": 1}\n', encoding="utf-8")
    (repo / ".codex" / "config.toml").write_text("x = 1\n", encoding="utf-8")
    assert ".codex" not in (repo / ".gitignore").read_text(encoding="utf-8")
    text, rows = run_rows(repo, [
        session_meta(repo), js_exec(cmd.format(repo=repo)), js_output()])
    assert (READ_SECRET not in text) is tainted


def test_every_unstaged_path_is_a_protected_read():
    from tests._capture.agent_adapters import registry
    unstaged = {path for adapter in registry._REGISTERED
                for path in adapter().unstage_after()}
    assert ".codex/auth.json" in unstaged
    for path in unstaged:
        assert redaction._is_protected_read(path), path


@pytest.mark.parametrize("cmd, tainted", [
    # Stream devices hold no file content of their own.
    ("cat src/main.py 2>/dev/null", False),
    ("cat src/main.py >/dev/stdout 2>&1 </dev/stdin", False),
    ("echo done > /dev/stderr && cat src/main.py", False),
    # Allow-list (d5w): `tee` is not a recognized read-only program.
    ("cat src/main.py | tee /dev/tty", True),
    ("cat /dev/fd/0 < src/main.py", False),
    # Any other device, or a path that only starts like one, still taints.
    ("cat /dev/random", True),
    ("cat /dev/null/../../etc/hosts", True),
    ("cat /dev/nullfile", True),
    ("cat /dev/fd/0/../../../etc/hosts", True),
    ("cat src/main.py 2>/dev/null; cat ~/.ssh/config", True),
])
def test_stream_devices_do_not_taint_a_call(repo, cmd, tainted):
    text, rows = run_rows(repo, [session_meta(repo), js_exec(cmd),
                                 js_output()])
    withheld = rows[2]["payload"].get("withheld") is True
    assert withheld is tainted, f"{cmd!r} -> withheld={withheld}"


# A JOINED call's command is judged like vouching judges an unjoined one
# (review round 5): a command can reach an ignored file without naming it
# -- by searching a whole directory, through a word the shell expands, or
# from the directory a `cd` moved to. `sub/secret.txt` is ignored and
# `sub/other.txt` is not; {repo} is the project.
HIDDEN_READS = [
    # Recursive searches read every file below their directory.
    "grep -rn KEY .", "grep -R KEY src", "grep --recursive KEY",
    "grep --rec KEY .", "grep -d recurse KEY .",
    "grep --directories=recurse KEY .", "egrep -ri KEY src", "rgrep KEY",
    "g''rep -r KEY .", "diff -r src sub", "ack KEY", "ag KEY",
    "find . -type f -exec cat {} +", "find sub -execdir cat {} \\;",
    "find . -type f | xargs cat",
    # rg keeps to the ignore rules only with its plain options.
    "rg -uu KEY", "rg --hidden KEY", "rg -. KEY", "rg --no-ignore-vcs KEY",
    "rg -g '*.txt' KEY", "rg --glob=!x KEY", "rg -L KEY", "rg --pre cat KEY",
    "rg -z KEY", "rg -tpy KEY", "rg -niuu KEY",
    "RIPGREP_CONFIG_PATH=cfg rg KEY",
    "git grep --untracked KEY", "git grep --no-index KEY",
    "git grep --untr KEY", "git grep -O KEY",
    "git --no-pager grep --no-exclude-standard --untracked KEY",
    # Words the shell expands name files nobody can know from the text.
    "cat *", "cat sub/*.txt", "cat sub/secret.tx?", "cat sub/secret.t[x]t",
    "cat sub/secret.{txt,md}", "head -n 3 **/*.txt",
    "cat $(ls sub | grep sec)", "cat `ls sub`", "cat <(ls)",
    "cat $'sub/secret.t\\x78t'", "cat sub/secret.t\\xt", "cat =ls",
    "cat ${X:-sub/secret.txt}", "cat $1",
    # A relative word is read from wherever a `cd` moved to.
    "cd sub && cat secret.txt", "cd sub; cat secret.txt",
    "cd {repo}/sub && cat secret.txt", "cd && cat .ssh/id_rsa",
    "cd - && cat x", "pushd sub && cat secret.txt",
    'cd "$(git rev-parse --show-toplevel)" && cat x', "cd $D && cat x",
    "(cd sub && cat secret.txt)",
    # A revision other than a branch or tag name can hold an ignored file:
    # `git stash push -a` keeps them in `stash^3`.
    "git show stash^3:sub/secret.txt", "git show stash@{0}",
    "git grep KEY stash^3", "git log -p --all", "git log -p -g",
    "git diff HEAD~1 -- src", "git show ORIG_HEAD", "git show 0123abcd",
    "git log -p refs/stash", "git -C sub show main^3",
    "git cat-file -p 0123abcd", "git archive HEAD", "git stash show -p",
]
PLAIN_READS = [
    "cat src/main.py", "grep -n print src/main.py", "grep -c print src/main.py",
    "rg print", "rg -n -i print src", "rg -C 2 print src/main.py",
    "rg --files", "git grep print", "cd {repo}/sub && cat other.txt",
    "echo 'a*b' \"c?d\"", "grep -n 'pr.*nt' src/main.py",
    "cat src/main.py 2>&1 | head -5",
    "git diff", "git show --stat", "git log --oneline -3",
    "git diff -- src/main.py",
]
# Kept by the block-list this module used before, and not provable by the
# allow-list (instructor decision d5w): options and programs nobody put on
# the list, a relative `cd`, git's `-C`, inline code, and git revisions.
NO_LONGER_KEPT_READS = [
    "rg --files -uu", "ls -R", "ls -R src", "find . -name '*.py'",
    "find src -type f", "cd sub && cat other.txt", "git -C sub status",
    "python -c 'print(1)'", "git diff main", "git grep print HEAD",
    "git stash list",
]


def joined_call_rows(repo, shape, cmd):
    """A call running `cmd` and its output, as each tool writes them."""
    if shape == "claude":
        return "claude", [
            {"type": "assistant", "uuid": "a1", "sessionId": "s1",
             "cwd": str(repo), "message": {"content": [
                 {"type": "tool_use", "id": "t1", "name": "Bash",
                  "input": {"command": cmd}}]}},
            {"type": "user", "uuid": "u1", "sessionId": "s1", "cwd": str(repo),
             "message": {"content": [{"type": "tool_result",
                                      "tool_use_id": "t1",
                                      "content": READ_SECRET}]}}]
    if shape == "exec_script":  # a JavaScript string literal
        call = js_exec("x")
        call["payload"]["input"] = (
            "const r = await tools.exec_command({cmd: " + json.dumps(cmd) + "});")
    else:  # a function call's JSON arguments
        call = {"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": "c1",
            "arguments": json.dumps({"cmd": cmd})}}
    return "codex", [session_meta(repo), call, js_output()]


def joined_output_kept(repo, shape, cmd):
    (repo / "sub").mkdir()
    (repo / "sub" / "secret.txt").write_text("KEY=1\n", encoding="utf-8")
    (repo / "sub" / "other.txt").write_text("x\n", encoding="utf-8")
    with (repo / ".gitignore").open("a", encoding="utf-8") as f:
        f.write("sub/secret.txt\n")
    adapter, rows = joined_call_rows(repo, shape, cmd.replace("{repo}", str(repo)))
    write_raw(repo, adapter, "sessions/s.jsonl", rows)
    redaction.redact_traces(repo)
    return READ_SECRET in read_all_committed(repo, adapter)


SHAPES = ["exec_script", "function_call", "claude"]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("cmd", HIDDEN_READS)
def test_joined_call_that_may_read_an_ignored_file_is_withheld(
        repo, shape, cmd):
    assert not joined_output_kept(repo, shape, cmd)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("cmd", PLAIN_READS)
def test_joined_call_that_reads_only_project_files_is_kept(repo, shape, cmd):
    assert joined_output_kept(repo, shape, cmd)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("cmd", NO_LONGER_KEPT_READS)
def test_joined_call_the_allow_list_cannot_prove_is_withheld(repo, shape, cmd):
    assert not joined_output_kept(repo, shape, cmd)


# A name PATTERN is not a file the command opens: `find -path ./.git
# -prune` and `--exclude-dir=.git` only keep `.git` out (review round 5).
# The block-list kept these; `find` and `du` are not on the allow-list
# (instructor decision d5w), so they are withheld like any other read.
EXCLUDING_READS = [
    "find . -path ./.git -prune -o -type f -print",
    "find . -not -path './.git/*' -name '*.py'",
    "find . -path '*/.git/*' -prune -o -print | head",
    "find . -name .git -prune -o -name .env -prune -o -print",
    "du -sh --exclude=.git .", "du -sh --exclude .git src",
]
STILL_READS = [
    "find . -name .git -prune -o -print; cat .git/config",
    "find . -name .git -prune -o -print && ls .git",
    "find . -name .env -prune -o -print; cat .env",
    "find .git -name config", "du --exclude=x .git",
    "find . -path ./.git -prune -o -exec cat {} +",
]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("cmd", EXCLUDING_READS)
def test_programs_off_the_allow_list_are_withheld_even_when_they_exclude_git(
        repo, shape, cmd):
    assert not joined_output_kept(repo, shape, cmd)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("cmd", STILL_READS)
def test_the_same_name_outside_a_pattern_is_still_a_read(repo, shape, cmd):
    assert not joined_output_kept(repo, shape, cmd)


def test_a_path_in_a_json_argument_key_is_still_a_read(repo):
    call = {"type": "response_item", "payload": {
        "type": "function_call", "name": "read_files", "call_id": "c1",
        "arguments": json.dumps({"files": {".env": {"lines": 3}}})}}
    text, _ = run_rows(repo, [session_meta(repo), call, js_output()])
    assert READ_SECRET not in text


def test_an_escaped_line_break_does_not_hide_a_read(repo):
    """Inside the JavaScript string, `\\n` is two characters; the shell
    sees a line break, and `.env` as a word of its own."""
    call = js_exec("x")
    call["payload"]["input"] = (
        'const r = await tools.exec_command({cmd: "cat .env\\necho done"});')
    text, rows = run_rows(repo, [session_meta(repo), call, js_output()])
    assert READ_SECRET not in text


def test_a_cd_into_the_project_root_is_kept_on_every_pass(repo):
    """The normalized tier is redacted again on every snapshot, with its
    paths already rendered: `cd <root>` must not turn into a bare `cd`."""
    path = write_normalized(repo, [
        normalized_event("tool_start", tool_use_id="c2", tool_name="exec",
                         cwd=str(repo), command=f"cd {repo} && cat src/main.py"),
        normalized_event("tool_result", tool_use_id="c2", text="KEEP_OUTPUT"),
    ])
    redactor = redaction.Redactor(repo)
    for _ in range(2):
        redaction.redact_in_place(redactor, path, redaction.RedactionReport())
        assert "KEEP_OUTPUT" in path.read_text(encoding="utf-8")


def test_workdir_outside_the_project_taints_every_relative_read(
        repo, tmp_path_factory):
    """`workdir` is load-bearing: a relative path is only in-project if the
    directory it resolves against is."""
    outside_dir = tmp_path_factory.mktemp("elsewhere")
    text, rows = run_rows(repo, [
        js_exec("cat notes.py", workdir=str(outside_dir)), js_output()])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["withheld"] is True


def test_orphan_output_with_no_matching_call_fails_closed(repo):
    text, rows = run_rows(repo, [js_output(call_id="never-called")])
    assert READ_SECRET not in text
    assert rows[0]["payload"]["withheld"] is True


def test_output_without_a_call_id_fails_closed(repo):
    text, rows = run_rows(repo, [
        {"type": "response_item",
         "payload": {"type": "custom_tool_call_output",
                     "output": [{"type": "input_text", "text": READ_SECRET}]}},
    ])
    assert READ_SECRET not in text
    assert rows[0]["payload"]["withheld"] is True


def test_duplicate_call_id_fails_closed(repo):
    """Never seen in 3,530 real pairs -- which is why it is handled, not
    assumed away."""
    text, rows = run_rows(repo, [
        js_exec("cat src/main.py", call_id="dup"),
        js_exec("cat src/main.py", call_id="dup"),
        js_output(call_id="dup"),
    ])
    assert READ_SECRET not in text
    assert rows[2]["payload"]["withheld"] is True


def test_call_with_no_readable_invocation_fails_closed(repo):
    text, rows = run_rows(repo, [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "c1"}},
        js_output(),
    ])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["withheld"] is True


# --- Layer 4b-i: command output is an ALLOW-LIST -------------------------
#
# Instructor decision (d5w, Option A): command output is kept only when the
# invocation provably reads nothing but submitted project files -- a
# recognized read-only command on project files, or the student's own
# tracked project code -- and everything else is withheld, joined or not.
# The call itself is always kept. Every probe runs in every shape a command
# reaches the transcript in.

ALLOW_SHAPES = ["exec_script", "function_call", "claude", "item", "hook",
                "normalized"]
CLAUDE_TOOL_SHAPES = ["claude", "hook", "normalized"]


def allow_list_repo(repo):
    """Tracked student code and runners, an ignored secret, and a script
    an agent wrote but nobody committed."""
    (repo / ".env").write_text("KEY=1\n", encoding="utf-8")
    (repo / "run_tests.py").write_text("print('runner')\n", encoding="utf-8")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_example.py").write_text(
        "def test_x():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "_capture").mkdir(exist_ok=True)
    (repo / "tests" / "_capture" / "dump.py").write_text(
        "print(1)\n", encoding="utf-8")
    (repo / "run.sh").write_text("cat .env\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "runners"], cwd=repo, check=True)
    (repo / "peek.py").write_text("print(open('.env').read())\n",
                                  encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "x").write_text("x\n", encoding="utf-8")


def claude_tool_rows(repo, shape, name, tool_input):
    """A Claude Code tool call and its output, as each tier writes them."""
    if shape == "claude":
        return "claude", "sessions/s.jsonl", [
            {"type": "assistant", "uuid": "a1", "sessionId": "s1",
             "cwd": str(repo), "message": {"content": [
                 {"type": "tool_use", "id": "t1", "name": name,
                  "input": tool_input}]}},
            {"type": "user", "uuid": "u1", "sessionId": "s1", "cwd": str(repo),
             "message": {"content": [{"type": "tool_result",
                                      "tool_use_id": "t1",
                                      "content": READ_SECRET}]}}]
    if shape == "hook":
        return "claude", "hooks/hooks-s1.jsonl", [
            {"adapter_name": "claude", "captured_at": "2026-09-17T00:00:00Z",
             "hook_event_name": event, "payload": {
                 "session_id": "s1", "hook_event_name": event,
                 "cwd": str(repo), "tool_name": name, "tool_use_id": "t1",
                 "tool_input": tool_input,
                 **({"tool_response": {"stdout": READ_SECRET}}
                    if event == "PostToolUse" else {})}}
            for event in ("PreToolUse", "PostToolUse")]
    command = tool_input.get("command")
    return "claude", None, [
        normalized_event("tool_start", adapter_name="claude", cwd=str(repo),
                         tool_use_id="t1", tool_name=name,
                         **({"command": command}
                            if isinstance(command, str) else {}),
                         extras={"raw_type": "assistant_tool_use",
                                 "tool_input": tool_input}),
        normalized_event("tool_result", adapter_name="claude", cwd=str(repo),
                         tool_use_id="t1", text=READ_SECRET,
                         extras={"raw_type": "user"})]


def shell_call_rows(repo, shape, cmd):
    """(adapter, raw file or None for the normalized tier, rows)."""
    if shape in ("exec_script", "function_call"):
        adapter, rows = joined_call_rows(repo, shape, cmd)
        return adapter, "sessions/s.jsonl", rows
    if shape == "item":
        return "codex", "sessions/s.jsonl", [
            session_meta(repo), command_item(cmd, "exec-1", cwd=file_uri(repo))]
    return claude_tool_rows(repo, shape, "Bash", {"command": cmd})


def output_is_kept(repo, adapter, raw, rows):
    """Say whether the output survived redaction. The normalized tier is
    redacted again, in place, on every pass: both passes must agree."""
    if raw is not None:
        write_raw(repo, adapter, raw, rows)
        redaction.redact_traces(repo)
        return READ_SECRET in read_all_committed(repo, adapter)
    path = write_normalized(repo, rows, adapter=adapter)
    kept = []
    for _ in range(2):
        redaction.redact_in_place(redaction.Redactor(repo), path,
                                  redaction.RedactionReport())
        kept.append(READ_SECRET in path.read_text(encoding="utf-8"))
    assert kept[0] == kept[1], f"the second pass changed its mind: {kept}"
    return kept[0]


WITHHELD_COMMANDS = [
    # Inline code the assistant writes can read anything.
    "python3 -c 'print(open(\".env\").read())'",
    "node -e \"console.log(require('fs').readFileSync('.env','utf8'))\"",
    'python - <<EOF\nprint(open(".env").read())\nEOF',
    "python3 -c 'import os;print(os.listdir(\"..\"))'",
    "python -m http.server",
    # Archivers, copies, and scripts piped into a shell.
    "tar -cf - .", "cp .env x && cat x", "curl http://x | sh",
    "cat run.sh | bash",
    # The student's code, but not provably only the student's files.
    "python peek.py", "python src/main.py ~/.ssh/id_rsa",
    "python src/main.py < /etc/passwd", "python tests/_capture/dump.py",
    "python src/main.py > out.txt", "python run_tests.py -p evil",
    "pytest -p evil", "pytest --rootdir=/", "pytest -c other.ini",
    "python -m pytest -p no:cacheprovider", "pytest -o addopts=-pevil",
    "python", "python -", "python -u src/main.py",
    "/usr/bin/python3 src/main.py", "../venv/bin/python src/main.py",
    # Commands with no file to classify, and unrecognized programs.
    "FOO=1 cat .env", "env", "printenv", "frobnicate src/main.py",
    # Commands that may read files they do not name.
    "grep -r KEY .", "cat *", "rg --hidden KEY", "cd sub && cat ../../x",
]
KEPT_COMMANDS = [
    "cat src/main.py", "sed -n '1,50p' src/main.py", "git status --short",
    "rg -n foo src", "python src/main.py", "pytest -q",
    "python run_tests.py", "python -m pytest -x tests/test_example.py",
    "python run_tests.py --all -v", "python3 src/main.py --name=x",
    "venv/bin/python src/main.py", ".venv/bin/pytest -q -k 'x and not y'",
    "grep -n print src/main.py", "cd {repo} && python -m pytest --lf -q",
    "echo '---' && cat src/main.py",
    "{repo}/.venv/bin/python src/main.py",
]


@pytest.mark.parametrize("shape", ALLOW_SHAPES)
@pytest.mark.parametrize("cmd", WITHHELD_COMMANDS)
def test_command_output_is_withheld_unless_the_allow_list_proves_it(
        repo, shape, cmd):
    allow_list_repo(repo)
    assert not output_is_kept(
        repo, *shell_call_rows(repo, shape, cmd.replace("{repo}", str(repo))))


@pytest.mark.parametrize("shape", ALLOW_SHAPES)
@pytest.mark.parametrize("cmd", KEPT_COMMANDS)
def test_command_output_of_project_reads_and_student_code_is_kept(
        repo, shape, cmd):
    allow_list_repo(repo)
    assert output_is_kept(
        repo, *shell_call_rows(repo, shape, cmd.replace("{repo}", str(repo))))


WITHHELD_CLAUDE_TOOLS = [
    # Grep searches hidden files; a glob or type overrides ignore rules.
    ("Grep", {"pattern": "KEY", "path": "{repo}/src", "glob": "*"}),
    ("Grep", {"pattern": "KEY", "path": "{repo}/src", "type": "py"}),
    ("Grep", {"pattern": "KEY", "path": "{repo}"}),
    ("Grep", {"pattern": "KEY"}),
    ("Read", {"file_path": "{repo}/.env"}),
    ("Read", {"file_path": "{repo}/src/main.py", "follow": True}),
    ("WebFetch", {"url": "http://localhost:8000/.env", "prompt": "show"}),
    ("WebFetch", {"url": "file:///etc/passwd", "prompt": "show"}),
    ("WebFetch", {"url": "http://127.1:8000/.env", "prompt": "show"}),
    # Tools nobody measured, and tools whose output is not a project file.
    ("FancyReader", {"file_path": "{repo}/src/main.py"}),
    ("Agent", {"description": "d", "prompt": "read .env",
               "subagent_type": "general-purpose"}),
    ("mcp__files__read", {"path": "{repo}/src/main.py"}),
    ("Glob", {"pattern": "**/*.py"}),
]
KEPT_CLAUDE_TOOLS = [
    ("Read", {"file_path": "{repo}/src/main.py"}),
    ("Read", {"file_path": "{repo}/src/main.py", "offset": 1, "limit": 5}),
    ("Edit", {"file_path": "{repo}/src/main.py", "old_string": "a",
              "new_string": "b", "replace_all": False}),
    ("Write", {"file_path": "{repo}/src/new.py", "content": "x = 1\n"}),
    ("Grep", {"pattern": "print", "path": "{repo}/src", "-n": True,
              "output_mode": "content"}),
    ("WebSearch", {"query": "python sockets"}),
    ("WebFetch", {"url": "https://docs.python.org/3/", "prompt": "sum"}),
]


def fill_repo(value, repo):
    return json.loads(json.dumps(value).replace("{repo}", str(repo)))


@pytest.mark.parametrize("shape", CLAUDE_TOOL_SHAPES)
@pytest.mark.parametrize("name, tool_input", WITHHELD_CLAUDE_TOOLS)
def test_claude_tool_output_is_withheld_unless_the_tool_table_proves_it(
        repo, shape, name, tool_input):
    allow_list_repo(repo)
    assert not output_is_kept(repo, *claude_tool_rows(
        repo, shape, name, fill_repo(tool_input, repo)))


@pytest.mark.parametrize("shape", CLAUDE_TOOL_SHAPES)
@pytest.mark.parametrize("name, tool_input", KEPT_CLAUDE_TOOLS)
def test_claude_tool_output_on_project_files_is_kept(
        repo, shape, name, tool_input):
    allow_list_repo(repo)
    assert output_is_kept(repo, *claude_tool_rows(
        repo, shape, name, fill_repo(tool_input, repo)))


def test_claude_tool_table_is_pinned():
    """Adding a tool, or an input key, is a decision that its output can
    only be project content. Measured over 2,212 real Claude Code sessions
    (Grep: its input schema in Claude Code 2.1.274)."""
    assert {name: set(tool.keys)
            for name, tool in redaction.CLAUDE_TOOLS.items()} == {
        "Bash": {"command", "description", "timeout", "run_in_background",
                 "dangerouslyDisableSandbox"},
        "Read": {"file_path", "offset", "limit", "pages"},
        "Edit": {"file_path", "old_string", "new_string", "replace_all"},
        "Write": {"file_path", "content"},
        "Grep": {"pattern", "path", "output_mode", "-B", "-A", "-C",
                 "context", "-n", "-i", "-o", "head_limit", "offset",
                 "multiline"},
        "WebFetch": {"url", "prompt"},
        "WebSearch": {"query", "allowed_domains"},
    }
    # Codex: measured over 13,794 tool calls in 816 real rollouts.
    assert redaction.CODEX_TOOLS == {"exec", "exec_command", "apply_patch",
                                     "view_image"}
    assert redaction.EXEC_COMMAND_KEYS == {
        "cmd", "workdir", "yield_time_ms", "max_output_tokens", "login",
        "tty", "justification", "sandbox_permissions", "prefix_rule"}


# Codex `exec` scripts are JavaScript in a fresh V8 isolate: nothing but the
# nested tools reaches a file. Only `exec_command` (with a literal command
# and known options) and `apply_patch` are allowed, and the script must be
# readable without running it.
@pytest.mark.parametrize("source", [
    # A command from another call, stored earlier or still running.
    'const r = await tools.exec_command({cmd: "cat src/main.py"});\n'
    'text(r.output); text(load("earlier"));',
    'text(await tools.write_stdin({session_id: 1, chars: ""}));',
    # Tools other than the shell.
    'const r = await tools.mcp__node_repl__js({code: "1"}); text(r);',
    'text(await tools.web__run({q: "x"}));',
    'text(JSON.stringify(ALL_TOOLS));\n'
    'text(await tools.exec_command({cmd: "cat src/main.py"}));',
    'text([].constructor.constructor("return globalThis")()["lo" + "ad"]("k"));'
    '\ntext(await tools.exec_command({cmd: "cat src/main.py"}));',
    # Options that change what runs.
    'text(await tools.exec_command({cmd: "cat src/main.py", shell: "python3"}));',
    'text(await tools.exec_command("cat .env"));',
    # A comment or template literal hides a second call from a reader that
    # only masks strings.
    '// `\ntext(await tools.exec_command({cmd: "cat .env"}));\n// `\n'
    'text(await tools.exec_command({cmd: "cat src/main.py"}));',
    'text(`${await tools.exec_command({cmd: "cat .env"})}`);\n'
    'text(await tools.exec_command({cmd: "cat src/main.py"}));',
    'const x = 1 / 2; text(await tools.exec_command({cmd: "cat src/main.py"}));',
    # No call at all.
    'text("tools.exec_command(")',
])
def test_exec_scripts_are_read_as_an_allow_list_too(repo, source):
    allow_list_repo(repo)
    call = js_exec("x")
    call["payload"]["input"] = source
    assert not output_is_kept(repo, "codex", "sessions/s.jsonl",
                              [session_meta(repo), call, js_output()])


@pytest.mark.parametrize("source", [
    'const r = await tools.exec_command({cmd: "cat src/main.py", '
    'workdir: "{repo}", yield_time_ms: 1000, max_output_tokens: 100});\n'
    'text(r.output); if (r.exit_code !== 0) text("failed");',
    '// @exec: {"yield_time_ms": 10000}\n'
    'text(await tools.exec_command({"cmd": "git status --short"}));',
    'const patch = "*** Begin Patch\\n*** Update File: src/main.py\\n@@\\n'
    '-print(\'hi\')\\n+print(\'ho\')\\n*** End Patch";\n'
    'text(await tools.apply_patch(patch));',
])
def test_plain_exec_scripts_keep_their_output(repo, source):
    allow_list_repo(repo)
    call = js_exec("x")
    call["payload"]["input"] = source.replace("{repo}", str(repo))
    assert output_is_kept(repo, "codex", "sessions/s.jsonl",
                          [session_meta(repo), call, js_output()])


def test_a_student_script_must_be_tracked_when_the_transcript_is_redacted(repo):
    """An agent-written `peek.py` does not vouch -- until it is committed,
    when the file (and whatever it reads) is in the submission itself."""
    allow_list_repo(repo)
    adapter, _, rows = shell_call_rows(repo, "claude", "python peek.py")
    assert not output_is_kept(repo, adapter, "sessions/s.jsonl", rows)
    subprocess.run(["git", "add", "peek.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "peek"], cwd=repo, check=True)
    # A new transcript file: the cache keeps an unchanged file's decision.
    write_raw(repo, "claude", "sessions/s2.jsonl", rows)
    redaction.redact_traces(repo)
    assert READ_SECRET in (repo / ".ai-traces" / "claude" / "redacted"
                           / "sessions" / "s2.jsonl").read_text(encoding="utf-8")


# --- Layer 4b-ii: Codex `item_completed` items ---------------------------
#
# Current Codex CLIs (0.140 and later) also log every finished turn item as
# `event_msg` / `item_completed`. A tool item holds its call AND its output in
# one object, under names the call/output join did not know, so its output
# used to be committed (review finding sx3). Measured over 843 real rollouts:
# 12 item types, and 3,605 of 3,636 CommandExecution ids join no call record.

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "codex"
ITEM_FIXTURE_SECRET = "FIXTURE_OUTSIDE_BODY"


def item_row(item, event="item_completed"):
    """An `event_msg` row wrapping one turn item, as Codex 0.140+ writes it."""
    return {"type": "event_msg", "payload": {
        "type": event, "thread_id": "t1", "turn_id": "u1",
        "completed_at_ms": 1, "item": item}}


def command_item(cmd, item_id, cwd, output=READ_SECRET, parsed_cmd=None):
    """A CommandExecution item: the command, where it ran, and its output."""
    if parsed_cmd is None:
        parsed_cmd = [{"type": "unknown", "cmd": cmd}]
    return item_row({
        "type": "CommandExecution", "id": item_id,
        "command": ["/bin/zsh", "-lc", cmd], "cwd": cwd,
        "parsed_cmd": parsed_cmd,
        "aggregated_output": output, "stdout": output,
        "formatted_output": output, "stderr": "", "exit_code": 0,
        "status": "completed", "process_id": "1", "source": "agent",
        "duration": {"secs": 0, "nanos": 1}})


def test_command_item_output_is_withheld_when_its_call_reads_outside(
        repo, tmp_path_factory):
    """The review reproduction: every output field of the item leaked."""
    outside = tmp_path_factory.mktemp("other") / "secret.txt"
    outside.write_text("x\n", encoding="utf-8")
    cmd = f"cat {outside}"
    text, rows = run_rows(repo, [
        js_exec(cmd, call_id="call_1"),
        command_item(cmd, "call_1", cwd=str(repo)),
        js_output(call_id="call_1"),
    ])
    assert READ_SECRET not in text
    item = rows[1]["payload"]["item"]
    for key in ("aggregated_output", "stdout", "formatted_output"):
        assert item[key] == {"withheld": True, "reason": "external_read"}
    assert item["stderr"] == ""  # an empty value carries no bytes
    # The record, its join and its metadata survive, so the gap is visible.
    assert (item["type"], item["id"], item["exit_code"]) == (
        "CommandExecution", "call_1", 0)
    assert item["withheld"] is True


def test_mcp_item_whose_own_arguments_name_an_outside_file_is_withheld(
        repo, tmp_path_factory):
    """An item carries its own invocation. Here the joined call record says
    nothing, but the MCP `arguments` name an outside file."""
    outside = tmp_path_factory.mktemp("other") / "notes.txt"
    text, rows = run_rows(repo, [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "read", "call_id": "m1",
            "arguments": "{}"}},
        item_row({
            "type": "McpToolCall", "id": "m1", "server": "files",
            "tool": "read", "arguments": {"path": str(outside)},
            "result": {"content": [{"type": "text", "text": READ_SECRET}],
                       "isError": False},
            "status": "completed", "duration": {"secs": 0, "nanos": 1}}),
    ])
    assert READ_SECRET not in text
    item = rows[1]["payload"]["item"]
    assert item["result"] == {"withheld": True, "reason": "external_read"}
    assert item["tool"] == "read"


def test_command_item_run_in_an_outside_cwd_taints_its_joined_output(
        repo, tmp_path_factory):
    """The item's `cwd` is where the command really ran. A bare filename
    read there is outside the project, and so is the joined output."""
    outside_dir = tmp_path_factory.mktemp("elsewhere")
    text, rows = run_rows(repo, [
        js_exec("cat notes.txt", call_id="c2"),
        command_item("cat notes.txt", "c2", cwd=str(outside_dir)),
        js_output(call_id="c2"),
    ])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["item"]["stdout"]["withheld"] is True
    assert rows[2]["payload"]["withheld"] is True


def test_item_output_with_no_joinable_call_fails_closed(repo):
    """The common case in real data: the `exec` tool's JavaScript mints an
    `exec-...` id that matches no call record. Unjoinable output is withheld,
    like an orphan `*_call_output` -- unless the item vouches for itself
    (see the vouching tests below); running a program never does, unless
    it is the student's own tracked code."""
    text, rows = run_rows(repo, [
        command_item("node src/main.js", "exec-1", cwd=str(repo))])
    assert READ_SECRET not in text
    assert rows[0]["payload"]["item"]["aggregated_output"]["withheld"] is True


# VOUCHING (decision dxp): an item whose `exec-...` id joins no call keeps
# its output when its own invocation -- script, cwd, Codex's parse --
# provably reads only in-project, non-ignored files. {repo} and {outside}
# are filled in; the cwd is a file URI, as in real items.
VOUCHED_SCRIPTS = [
    "cat src/main.py",
    "sed -n '1,200p' src/main.py",
    "nl -ba src/main.py | head -n 40",
    "rg -n 'print' src",
    "rg -C 2 print src/main.py",   # -C is context lines here, not a directory
    "rg -A3 -i print src && head -20 src/main.py",
    "rg --files | sort",
    "grep print src/main.py 2>/dev/null",
    "git status --short && git diff -- src/main.py",
    # The student's own tracked code (instructor decision d5w).
    "python src/main.py", "pytest -q",
    "git --no-pager log --oneline -5",
    "git log --format='%h %s' -3 && git branch --list",
    "ls -la src; pwd; echo done",
    "cd {repo}/src && cat main.py",
    "wc -l src/main.py > /dev/null 2>&1",
    "cat src/main.py > /dev/stdout 2>/dev/stderr",
    "cat < src/main.py",
    "git grep print", "git grep print -- src", "git show --stat",
]
NOT_VOUCHED_SCRIPTS = [
    # Running a program can read anything -- the student's own tracked
    # code aside (see VOUCHED_SCRIPTS).
    "python -c 1", "cat src/main.py; python -c 1", "node src/main.js",
    "cat src/main.py | sh", "printenv", "ps aux",
    # Ignored or outside files.
    "cat .env", "cat venv/lib/site.py", "cat ../elsewhere/notes.txt",
    "cat {outside}/notes.txt", "cat link.txt", "wc -l < {outside}/notes.txt",
    "ls {outside}", "tail -n 5 {outside_uri}",
    # Anything the checker cannot read plainly.
    "cat $HOME/notes.txt", "cat ~/notes.txt", "cat `echo x`",
    "cat src/*.py", "cat src/{{main,other}}.py", "cat <(cat notes.txt)",
    "cat <<EOF\nx\nEOF", "FOO=1 cat src/main.py", "cat src\\/main.py",
    "cat 'src/main.py", "# cat src/main.py",
    # Directory changes, except one plain `cd /abs &&` into the project.
    "cd {outside} && cat notes.txt", "cd src && cat main.py",
    "pushd {outside}; cat notes.txt", "cd {repo}/src && cd {outside} && ls",
    "cd && cat notes.txt", "cd - && cat notes.txt", "cd {outside}/.. && ls",
    "git -C {outside} status",
    # Options that read past .gitignore, follow links out, or run helpers.
    "grep -rn KEY .", "grep -R KEY src", "rg -uu KEY", "rg --no-ignore KEY",
    "rg --hidden KEY", "rg -L KEY", "rg --pre cat KEY", "rg -z KEY",
    "ls -R", "sed 'e cat notes.txt' src/main.py",
    # rg globs override every ignore rule; file lists name unseen files.
    "rg -g '.env' KEY", "rg --glob=.env KEY", "rg -ng '*' KEY",
    "rg --iglob .env KEY", "rg --type-add 'x:.env' -tx KEY",
    "sort --files0-from=list.txt", "wc --files0-from list.txt",
    # A file name that looks like an option, or a path glued to one.
    "cat -- -secret", "grep -o -f../elsewhere/notes.txt src/main.py",
    # Our own raw transcripts are unredacted.
    "cat .ai-traces/codex/raw/r.jsonl", "rg -n KEY .ai-traces",
    "sed -n '1,5p;w out' src/main.py", "sed -i 's/a/b/' src/main.py",
    "git -c core.pager=cat log", "git -C {outside} log",
    "git diff --no-index {outside}/notes.txt src/main.py",
    "git config --global user.email", "git status --ignored",
    "git grep -O KEY", "git grep --no-exclude-standard KEY",
    "git push", "find . -name x",
    # Options are an allow-list: abbreviations and glued values are unknown
    # (review round 4), and so is any option no measured command used.
    "file -f.env", "grep -o -f.env src/main.py", "grep --rec KEY .",
    "grep --recur -n KEY", "grep --dir=recurse KEY .", "rg --no-ig KEY",
    "rg --hid KEY", "git grep --untracked --no-exclude KEY",
    "git grep --no-inde KEY", "git status --ignor",
    "git ls-files -o -i --exclude-standard", "sort --files0=list.txt",
    "sort --files0-fr list.txt", "wc --files0=list.txt", "ls --recur",
    "head -c1000 src/main.py", "tail -f src/main.py", "cat -n src/main.py",
    # Nothing that writes a file.
    "uniq src/main.py out.txt", "git log --outp=x", "git diff --output=x",
    "sort -o out.txt src/main.py", "cat src/main.py > out.txt",
    "cat src/main.py >> out.txt", "cat src/main.py &> out.txt",
    "git branch feature",
    # Any revision: a stash (or its untracked-files commit) can hold an
    # ignored file (review round 5). Paths go after `--`.
    "git show stash^3", "git show stash^3:.env", "git grep ENV stash^3",
    "git log -1 --stat stash^3", "git diff main", "git blame src/main.py",
    "git show HEAD:src/main.py", "git log --oneline 0123abcd",
    # Options that make git run a configured program (see the residual).
    "git diff --ext-diff", "git diff --textconv", "git show --textconv",
    "git log --patch --textconv",
    # zsh expansions, and anything else with `<`, `>` or parentheses.
    "cat =ls", "cat <->", "cat =(cat src/main.py)", "cat src/main.py(.)",
    "cat <> src/main.py", "cat src/main.py>/dev/null", "(cat src/main.py)",
    "cat src/main.py <&3",
    # An ignored name is ignored whether or not the file still exists.
    "cat gone.key", "cat .env.gone",
    # Git's own files can hold credentials; names ignore case. So can the
    # files an adapter never commits, listed in .gitignore or not.
    "cat .git/config", "cat .GIT/config", "ls .git", "cat .codex/auth.json",
    "cat .AI-TRACES/codex/raw/r.jsonl", "cat .Ai-traces/interaction-stream.jsonl",
]


def vouch_rows(repo, outside, script, item_id="exec-1", cwd=None,
               parsed_cmd=None):
    (outside / "notes.txt").write_text("x\n", encoding="utf-8")
    (repo / "link.txt").symlink_to(outside / "notes.txt")
    (repo / ".env").write_text("KEY=1\n", encoding="utf-8")
    (repo / "-secret").write_text("KEY=1\n", encoding="utf-8")
    with (repo / ".gitignore").open("a", encoding="utf-8") as f:
        f.write("-secret\n*.key\n.env.*\n")
    script = script.format(repo=repo, outside=outside,
                           outside_uri=file_uri(outside / "notes.txt"))
    return [session_meta(repo), command_item(
        script, item_id, cwd=file_uri(repo) if cwd is None else cwd,
        parsed_cmd=parsed_cmd)]


@pytest.mark.parametrize("script", VOUCHED_SCRIPTS)
def test_unjoined_output_is_kept_when_its_invocation_is_provably_in_project(
        repo, tmp_path_factory, script):
    rows = vouch_rows(repo, tmp_path_factory.mktemp("elsewhere"), script)
    text, out = run_rows(repo, rows)
    item = out[1]["payload"]["item"]
    for key in ("aggregated_output", "stdout", "formatted_output"):
        assert item[key] == READ_SECRET, key
    assert "withheld" not in item


@pytest.mark.parametrize("script", NOT_VOUCHED_SCRIPTS)
def test_unjoined_output_stays_withheld_unless_provably_in_project(
        repo, tmp_path_factory, script):
    rows = vouch_rows(repo, tmp_path_factory.mktemp("elsewhere"), script)
    text, out = run_rows(repo, rows)
    assert READ_SECRET not in text
    assert out[1]["payload"]["item"]["stdout"] == {
        "withheld": True, "reason": "external_read"}


@pytest.mark.parametrize("cwd, parsed_cmd", [
    ("outside", None),                               # ran outside
    ("repo-path", None),                             # a plain path is fine...
    (None, None),                                    # ...no cwd is not
    ("file://otherhost/{repo}", None),               # not a local directory
    ("repo-ignored", None),                          # an ignored directory
    ("repo", [{"type": "read", "cmd": "cat x", "name": "x",
               "path": "{outside}/notes.txt"}]),    # Codex's parse disagrees
    ("repo", [{"type": "read", "cmd": "cat x", "name": "x", "path": ".env"}]),
    ("repo", "not a list"),
])
def test_vouching_needs_a_readable_in_project_cwd_and_parse(
        repo, tmp_path_factory, cwd, parsed_cmd):
    outside = tmp_path_factory.mktemp("elsewhere")
    (repo / "venv").mkdir()
    places = {"outside": file_uri(outside), "repo": file_uri(repo),
              "repo-path": str(repo), "repo-ignored": file_uri(repo / "venv")}
    fill = lambda v: json.loads(json.dumps(v).replace(  # noqa: E731
        "{repo}", str(repo)).replace("{outside}", str(outside)))
    rows = vouch_rows(repo, outside, "cat src/main.py",
                      cwd=places.get(cwd, fill(cwd)) if cwd else "",
                      parsed_cmd=fill(parsed_cmd))
    if cwd is None:
        del rows[1]["payload"]["item"]["cwd"]
    text, _ = run_rows(repo, rows)
    assert (READ_SECRET in text) is (cwd == "repo-path")


@pytest.mark.parametrize("shell", [
    "python3", "fish", "pwsh", "/opt/homebrew/bin/zsh", "/bin/zsh -x"])
def test_vouching_needs_a_known_shell(repo, tmp_path_factory, shell):
    """Every measured item runs `/bin/zsh -lc`; fish and PowerShell read
    `(...)` as a command, and Python is not a shell at all."""
    rows = vouch_rows(repo, tmp_path_factory.mktemp("elsewhere"),
                      "cat src/main.py")
    rows[1]["payload"]["item"]["command"][0] = shell
    text, _ = run_rows(repo, rows)
    assert READ_SECRET not in text


def test_duplicate_unjoined_ids_are_not_vouched_for(repo, tmp_path_factory):
    rows = vouch_rows(repo, tmp_path_factory.mktemp("elsewhere"),
                      "cat src/main.py")
    rows.append(command_item("cat .env", "exec-1", cwd=file_uri(repo)))
    text, _ = run_rows(repo, rows)
    assert READ_SECRET not in text


def test_unjoined_mcp_output_is_never_vouched_for(repo):
    """Every measured unjoined McpToolCall runs JavaScript in a REPL."""
    text, _ = run_rows(repo, [session_meta(repo), item_row({
        "type": "McpToolCall", "id": "exec-9", "server": "node_repl",
        "tool": "js", "arguments": {"code": "1 + 1", "title": "add"},
        "result": {"content": [{"type": "text", "text": READ_SECRET}]},
        "status": "completed", "duration": {"secs": 0, "nanos": 1}})])
    assert READ_SECRET not in text


def test_in_project_item_output_joined_to_its_call_is_kept(repo):
    """Guard against over-withholding. The shell that runs the command
    (`/bin/zsh`) is not a file the command reads."""
    text, rows = run_rows(repo, [
        session_meta(repo),
        js_exec("cat src/main.py", call_id="c1"),
        command_item("cat src/main.py", "c1", cwd=str(repo),
                     output="print('hi')"),
        js_output("print('hi')", call_id="c1"),
    ])
    assert rows[2]["payload"]["item"]["stdout"] == "print('hi')"
    assert "withheld" not in text


# Real CommandExecution items state `cwd` as a file URI (all 2,358 measured),
# and ImageView items their `path`. A URI was passed through as "a URL":
# the absolute path (the user name) was committed verbatim, and an outside
# directory was not recognized as one.
def file_uri(path):
    return Path(path).as_uri()


def test_command_item_with_an_outside_file_uri_cwd_is_withheld(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("else where")  # a space: %20 in the URI
    text, rows = run_rows(repo, [
        js_exec("cat notes.txt", call_id="c1"),
        command_item("cat notes.txt", "c1", cwd=file_uri(outside)),
        js_output(call_id="c1"),
    ])
    assert READ_SECRET not in text
    assert str(outside) not in text and outside.name not in text
    item = rows[1]["payload"]["item"]
    assert item["cwd"].startswith("<external:")
    assert item["stdout"] == {"withheld": True, "reason": "external_read"}


@pytest.mark.parametrize("uri, rendered", [
    ("{repo_uri}/src", "src"),
    ("{repo_uri}", ""),
    ("file://otherhost{repo}/src", "<external:"),    # not a local file
    ("file:///{outside}/../..", "<external:"),
])
def test_file_uris_are_classified_as_paths(repo, tmp_path_factory, uri,
                                           rendered):
    outside = tmp_path_factory.mktemp("other")
    uri = (uri.replace("{repo_uri}", file_uri(repo)).replace("{repo}", str(repo))
           .replace("/{outside}", str(outside)))
    _, rows = run_rows(repo, [session_meta(repo), item_row({
        "type": "ImageView", "id": "v1", "path": uri})])
    path = rows[1]["payload"]["item"]["path"]
    assert path.startswith(rendered) if rendered else path == ""
    assert str(repo) not in json.dumps(rows[1])


@pytest.mark.parametrize("workdir", ["file:{outside}", "FILE:{outside}",
                                     "file:elsewhere", "file:"])
def test_a_single_slash_file_uri_workdir_is_read_like_the_hook_reads_it(
        repo, tmp_path_factory, workdir):
    """`file:/abs` is a file URI too (review round 5): the Codex hook
    decodes it, or records nothing when it names no absolute path."""
    outside = tmp_path_factory.mktemp("elsewhere")
    workdir = workdir.replace("{outside}", str(outside))
    text, rows = run_rows(repo, [session_meta(repo), {
        "type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": "c1",
            "arguments": json.dumps({"cmd": heredoc_patch("notes.py"),
                                     "workdir": workdir})}},
        js_output()])
    assert OUTSIDE_CONTEXT not in text
    assert READ_SECRET not in text
    assert str(outside) not in text


def test_a_single_slash_file_uri_in_text_loses_the_user_name(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "notes.md"
    text, _ = run_rows(repo, [{"type": "event_msg", "payload": {
        "type": "agent_message",
        "message": f"KEEP see file:{repo}/src/main.py and file:{outside}."}}])
    assert "KEEP see src/main.py and <external:" in text
    assert str(outside) not in text and str(repo) not in text


def test_file_uris_in_text_lose_the_user_name(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "notes.md"
    text, _ = run_rows(repo, [{"type": "event_msg", "payload": {
        "type": "agent_message",
        "message": (f"KEEP see [main]({file_uri(repo / 'src' / 'main.py')}) "
                    f"and {file_uri(outside)}.")}}])
    assert "KEEP see [main](src/main.py) and <external:" in text
    assert str(outside) not in text and str(repo) not in text


def test_a_command_reading_an_outside_file_uri_taints_its_output(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "secret.txt"
    outside.write_text("x\n", encoding="utf-8")
    text, rows = run_rows(repo, [
        js_exec(f"curl -s {file_uri(outside)}"), js_output()])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["withheld"] is True


@pytest.mark.parametrize("item, undeclared_key", [
    # An item type nobody has measured is withheld whole.
    ({"type": "FileRead", "id": "c1", "path": "src/main.py",
      "contents": READ_SECRET}, None),
    # A measured item type with a field nobody has measured.
    ({"type": "AgentMessage", "id": "c1", "phase": "final_answer",
      "content": [], "file_text": READ_SECRET}, "file_text"),
])
def test_unmeasured_item_shapes_fail_closed(repo, item, undeclared_key):
    text, rows = run_rows(repo, [item_row(item)])
    assert READ_SECRET not in text
    out = rows[0]["payload"]["item"]
    assert out["withheld"] is True
    if undeclared_key is None:
        assert out["reason"] == "unrecognized_item_type"
        assert out["type"] == "FileRead"  # the gap stays visible
    else:
        assert out[undeclared_key] == {"withheld": True,
                                       "reason": "undeclared_field"}
        assert out["phase"] == "final_answer"


def test_unmeasured_event_msg_types_fail_closed(repo):
    """`item_completed` leaked because an event type nobody had measured
    was passed through with path anonymization only."""
    text, rows = run_rows(repo, [
        item_row({"type": "CommandExecution", "id": "c1",
                  "aggregated_output": READ_SECRET}, event="item_started"),
        {"type": "event_msg", "payload": {
            "type": "exec_command_output_delta", "call_id": "c1",
            "chunk": READ_SECRET}},
    ])
    assert READ_SECRET not in text
    for row in rows:
        assert row["payload"]["withheld"] is True
        assert row["payload"]["reason"] == "unrecognized_event_type"


def test_codex_item_and_event_types_are_pinned():
    """Declaring a type is a security decision: it says the rules above are
    verified to handle that shape. Measured over 843 real rollouts."""
    assert set(redaction.CODEX_ITEM_FIELDS) == {
        "UserMessage", "AgentMessage", "Reasoning", "ContextCompaction",
        "CommandExecution", "McpToolCall", "FileChange", "WebSearch",
        "Extension", "ImageView", "CollabAgentToolCall", "SubAgentActivity",
    }
    assert redaction.CODEX_EVENT_MSG_TYPES == frozenset({
        "user_message", "agent_message", "agent_reasoning",
        "task_started", "task_complete", "token_count",
        "thread_settings_applied", "turn_aborted", "context_compacted",
        "item_completed",
    }) | redaction.BEGIN_EVENT_TYPES | redaction.END_EVENT_TYPES


def load_item_fixture(repo, outside):
    """The sanitized real-shaped fixture, with its path placeholders filled."""
    def fill(value):
        if isinstance(value, str):
            return (value.replace("__REPO__", str(repo))
                    .replace("__OUTSIDE__", str(outside)))
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, dict):
            return {fill(k): fill(v) for k, v in value.items()}
        return value
    text = (FIXTURES / "rollout-item-completed.jsonl").read_text(
        encoding="utf-8")
    return [fill(json.loads(line)) for line in text.splitlines()
            if line.strip()]


def test_real_shaped_item_rollout_keeps_work_and_withholds_outside_reads(
        repo, tmp_path_factory):
    """Every item type and field seen in real rollouts, with made-up values."""
    outside = tmp_path_factory.mktemp("outside")
    (outside / "notes.txt").write_text("x\n", encoding="utf-8")
    rows_in = load_item_fixture(repo, outside)
    text, rows = run_rows(repo, rows_in)

    assert ITEM_FIXTURE_SECRET not in text
    assert "FIXTURE_SEARCH_SNIPPET" not in text  # web results, unjoined
    assert str(outside) not in text
    for kept in ("KEEP_PROMPT", "KEEP_THOUGHT", "KEEP_REPLY",
                 "KEEP_IN_PROJECT_OUTPUT", "KEEP_IN_PROJECT_DIFF"):
        assert kept in text, kept
    # Every measured shape is recognized: nothing is withheld as unknown.
    assert "unrecognized_" not in text
    assert "undeclared_field" not in text

    def items(rows_):
        return [(r["payload"]["item"]["type"], r["payload"]["item"]["id"])
                for r in rows_ if r["payload"].get("type") == "item_completed"]
    assert items(rows) == items(rows_in)
    assert {t for t, _ in items(rows)} == set(redaction.CODEX_ITEM_FIELDS)


# --- Layer 4b-iii: relative patch headers --------------------------------
#
# `*** Update File: notes.py` names a file relative to the directory the
# patch ran in: the call's `workdir`, else the session's cwd. Resolving it
# against the project root instead made an outside file's patch look like
# project work, so its body was committed (review finding y8j).

OUTSIDE_CONTEXT = "OUTSIDE_CONTEXT_LINE"


def js_apply_patch(target, workdir=None, call_id="p1"):
    """A Codex `exec` input running an apply_patch heredoc.

    Inside the JavaScript source the patch's line breaks are the two
    characters backslash + n, as in real rollouts. The ` ` line is context
    copied from the target file -- its content, not the agent's edit.
    """
    patch = "\\n".join([
        "*** Begin Patch", f"*** Update File: {target}", "@@",
        " " + OUTSIDE_CONTEXT, "-old", "+new", "*** End Patch"])
    options = f', workdir:"{workdir}"' if workdir else ""
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": call_id,
        "input": ("const r = await tools.exec_command({cmd:\"apply_patch "
                  "<<'EOF'\\n" + patch + "\\nEOF\"" + options + "})")}}


def session_meta(cwd):
    return {"type": "session_meta", "payload": {"id": "s1", "cwd": str(cwd)}}


def test_relative_patch_header_under_an_outside_workdir_is_withheld(
        repo, tmp_path_factory):
    """The review reproduction."""
    outside = tmp_path_factory.mktemp("elsewhere")
    text, rows = run_rows(repo, [
        session_meta(repo),
        js_apply_patch("notes.py", workdir=outside),
        js_output("ok", call_id="p1"),
    ])
    assert OUTSIDE_CONTEXT not in text
    call = rows[1]["payload"]
    assert call["input"] == {"withheld": True, "reason": "external_path"}
    assert call["call_id"] == "p1"  # the call itself is kept


@pytest.mark.parametrize("session_cwd, workdir, withheld", [
    ("repo", None, False),        # the session ran in the project
    ("repo", "src", False),       # a relative workdir inside the project
    ("repo", "..", True),         # a relative workdir that leaves it
    ("outside", None, True),      # the session ran outside the project
    ("outside", "repo", False),   # an absolute workdir back inside it
    (None, None, True),           # nothing to resolve against: fail closed
])
def test_relative_patch_header_resolves_against_workdir_then_session_cwd(
        repo, tmp_path_factory, session_cwd, workdir, withheld):
    places = {"repo": str(repo),
              "outside": str(tmp_path_factory.mktemp("elsewhere"))}
    rows = [] if session_cwd is None else [session_meta(places[session_cwd])]
    rows.append(js_apply_patch("notes.py", workdir=places.get(workdir, workdir)))
    text, _ = run_rows(repo, rows)
    assert (OUTSIDE_CONTEXT not in text) is withheld


def test_patch_inside_a_command_argv_is_withheld_for_an_outside_cwd(
        repo, tmp_path_factory):
    """Shell tools carry the heredoc as the last argv element, with real
    newlines, so strings inside a list are checked too."""
    outside = tmp_path_factory.mktemp("elsewhere")
    heredoc = ("apply_patch <<'EOF'\n*** Begin Patch\n"
               "*** Update File: notes.py\n@@\n " + OUTSIDE_CONTEXT
               + "\n-old\n+new\n*** End Patch\nEOF")
    text, rows = run_rows(repo, [
        session_meta(repo),
        {"type": "event_msg", "payload": {
            "type": "exec_command_begin", "call_id": "e1",
            "cwd": str(outside), "command": ["bash", "-lc", heredoc]}},
    ])
    assert OUTSIDE_CONTEXT not in text
    assert rows[1]["payload"]["withheld"] is True


def test_in_project_relative_patch_survives_repeated_normalized_redaction(
        repo):
    """The normalized tier is redacted in place on every snapshot, and the
    first pass rewrites the session cwd to "" (the project root). A patch
    kept on the first pass must still be kept on the second."""
    patch = ("*** Begin Patch\n*** Update File: src/main.py\n@@\n"
             " print('hi')\n+KEEP_PATCH_LINE\n*** End Patch")
    path = write_normalized(repo, [normalized_event(
        "tool_start", cwd=str(repo), tool_use_id="p1", command=patch)])
    redactor = redaction.Redactor(repo)
    for _ in range(2):
        redaction.redact_in_place(redactor, path, redaction.RedactionReport())
        row = json.loads(path.read_text(encoding="utf-8"))
        assert row["command"] == patch
        assert row["cwd"] == ""


def exec_source(cmd, options=""):
    """A Codex `exec` input running `cmd` (JavaScript-escaped) with extra
    exec_command options written verbatim."""
    body = cmd.replace("\n", "\\n")
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": "p1",
        "input": ('const r = await tools.exec_command({cmd:"' + body + '"'
                  + options + "})")}}


def heredoc_patch(target):
    return ("apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: "
            + target + "\n@@\n " + OUTSIDE_CONTEXT
            + "\n-old\n+new\n*** End Patch\nEOF")


@pytest.mark.parametrize("options, prelude", [
    # A variable holds the directory (the real shape: 89 calls).
    (", workdir: wd", 'const wd = "{outside}";\n'),
    # A template literal (11 calls).
    (", workdir: `{outside}`", ""),
    # Shorthand for `workdir: workdir`.
    (", workdir", 'const workdir = "{outside}";\n'),
    # An expression whose first literal is the project itself.
    (', workdir: "{repo}" + sub', 'const sub = "/../elsewhere";\n'),
])
def test_workdir_that_is_not_a_plain_string_literal_fails_closed(
        repo, tmp_path_factory, options, prelude):
    """Only a plain '...' or "..." literal can be read without running the
    JavaScript. Anything else leaves the directory unknown."""
    outside = str(tmp_path_factory.mktemp("elsewhere"))

    def fill(text):
        return text.replace("{outside}", outside).replace("{repo}", str(repo))
    row = exec_source(heredoc_patch("notes.py"), fill(options))
    row["payload"]["input"] = fill(prelude) + row["payload"]["input"]
    text, _ = run_rows(repo, [session_meta(repo), row, js_output(call_id="p1")])
    assert OUTSIDE_CONTEXT not in text
    assert READ_SECRET not in text  # the output of that call too


# Whole exec sources: {patch} is the JavaScript-escaped heredoc patch,
# {outside} an outside directory, {repo} the project. The session starts in
# the project; the turn (when `turn_cwd` is "outside") moves outside.
PATCH_JS = heredoc_patch("notes.py").replace("\n", "\\n")


@pytest.mark.parametrize("source, turn_cwd", [
    # Round-2 review: the key is not a clean `workdir: "<literal>"` pair.
    ('const wd = "{outside}";\n'
     'await tools.exec_command({cmd:"{patch}", /* " */ workdir: wd /* " */});',
     None),
    ('const wd = "{outside}";\n'
     'await tools.exec_command({cmd:"{patch}", ["workdir"]: wd});', None),
    ('const wd = "{outside}";\n'
     'await tools.exec_command({cmd:"{patch}", workdir/*c*/: wd});', None),
    ('const wd = "{outside}"; const opts = {cmd:"{patch}"};\n'
     'opts.workdir = wd; await tools.exec_command(opts);', None),
    ('const opts = {cmd:"{patch}"}; opts.workdir = "{outside}";\n'
     'await tools.exec_command(opts);', None),
    # The word never appears: the key is built at run time.
    ('const opts = {}; opts["w" + "orkdir"] = "{outside}";\n'
     'await tools.exec_command({cmd:"{patch}", ...opts});', None),
    ('const o = {cmd:"{patch}"}; Object.assign(o, JSON.parse(extra));\n'
     'await tools.exec_command(o);', None),
    # The tool itself is reached indirectly; its options are unreadable.
    ('const patch = "{patch}"; const run = tools["exec_" + "command"];\n'
     'await run(JSON.parse(atob(blob)));', None),
    # A workdir inside a nested object is not the call's workdir.
    ('await tools.exec_command({cmd:"{patch}", env: {workdir: "{repo}"}});',
     "outside"),
    # Another call states the project; the patch call states nothing and
    # so runs in the turn's directory, which is outside.
    ('await tools.exec_command({cmd:"ls", workdir:"{repo}"});\n'
     'await tools.exec_command({cmd:"{patch}"});', "outside"),
    # Round-3 review: an escape outside any string spells the key.
    ('await tools.exec_command({cmd:"{patch}", w\\u006frkdir: "{outside}"});',
     None),
])
def test_workdir_hidden_from_a_plain_reading_fails_closed(
        repo, tmp_path_factory, source, turn_cwd):
    outside = str(tmp_path_factory.mktemp("elsewhere"))
    code = (source.replace("{patch}", PATCH_JS).replace("{outside}", outside)
            .replace("{repo}", str(repo)))
    rows = [session_meta(repo)]
    if turn_cwd:
        rows.append({"type": "turn_context",
                     "payload": {"turn_id": "t", "cwd": outside}})
    rows.append({"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": "p1",
        "input": code}})
    text, _ = run_rows(repo, rows)
    assert OUTSIDE_CONTEXT not in text


@pytest.mark.parametrize("source", [
    'await tools.exec_command({cmd:"{patch}", workdir: "{repo}"});',
    'await tools.exec_command({"cmd": "{patch}", "workdir": "{repo}/src"});',
    'const r = await tools.exec_command({cmd:"{patch}"}); text(r.output);',
    'await tools.exec_command({cmd:"ls", workdir:"{repo}"});\n'
    'await tools.exec_command({cmd:"{patch}", workdir:"{repo}"});',
])
def test_plainly_stated_workdir_inside_the_project_keeps_the_patch(
        repo, source):
    code = source.replace("{patch}", PATCH_JS).replace("{repo}", str(repo))
    text, _ = run_rows(repo, [session_meta(repo), {
        "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "p1",
            "input": code}}])
    assert OUTSIDE_CONTEXT in text


def test_two_different_stated_workdirs_leave_the_directory_unknown(repo):
    """One exec script can run several commands. With two different
    workdirs -- both in the project here -- a relative header cannot be
    placed, so the patch is withheld."""
    row = exec_source(heredoc_patch("notes.py"), f', workdir:"{repo}"')
    row["payload"]["input"] += (
        f'\nawait tools.exec_command({{cmd:"ls", workdir:"{repo / "src"}"}})')
    text, _ = run_rows(repo, [session_meta(repo), row])
    assert OUTSIDE_CONTEXT not in text


@pytest.mark.parametrize("cd, withheld", [
    ("cd {outside} && ", True),         # review probe R2
    ("cd {outside}; ", True),
    ("cd $WORK && ", True),             # not a literal
    ("cd src && ", True),               # not an absolute path
    ("(cd {outside} && ls); ", True),   # a subshell may or may not apply
    # Round-2 review: a directory change that is not the one simple
    # `cd /abs && ...` shape. Any such word fails closed.
    ("builtin cd {outside} && ", True),
    ("command cd {outside} && ", True),
    ("if cd {outside}; then ", True),
    ("if true; then cd {outside}; fi; ", True),
    ("for d in 1; do cd {outside}; done; ", True),
    ("! cd {outside}; ", True),
    ("time cd {outside}; ", True),
    ("CDPATH={outside} cd . && ", True),
    ("env -C {outside} ", True),
    ("git -C {outside} status && ", True),
    ("pushd {outside} >/dev/null; ", True),
    ("cd {repo}/src && popd; ", True),
    ("c\\u0064 {outside} && ", True),   # an escape inside the JS string
    ('c\\"\\"d {outside} && ', True),   # shell quote removal
    ("cd {repo}/src && cd {outside} && ", True),
    # Round-3 review: PowerShell's directory changes and their aliases.
    ("Set-Location {outside}; ", True),
    ("set-location -Path {outside}; ", True),
    ("Push-Location {outside}; ", True),
    ("sl {outside}; ", True),
    ("Pop-Location; ", True),
    ("cd {repo}/src && ", False),       # into the project: kept
    ("", False),
])
def test_cd_before_a_patch_changes_where_its_headers_resolve(
        repo, tmp_path_factory, cd, withheld):
    outside = str(tmp_path_factory.mktemp("elsewhere"))
    cmd = (cd.replace("{outside}", outside).replace("{repo}", str(repo))
           + heredoc_patch("notes.py"))
    text, _ = run_rows(repo, [session_meta(repo), exec_source(cmd)])
    assert (OUTSIDE_CONTEXT not in text) is withheld


# Round-3 review: a header written as a JavaScript (or shell) expression
# names a file only the running script knows. Read as a plain relative path,
# `" + f + "` resolved inside the project, so an outside file's patch body was
# committed. {nl} is a line break as the source spells it.
PATCH_BODY = "{nl}@@{nl} " + OUTSIDE_CONTEXT + "{nl}-old{nl}+new{nl}*** End Patch{nl}EOF"


@pytest.mark.parametrize("source", [
    # String concatenation with a variable (both quote styles).
    'const f = "{outside}/notes.py";\n'
    'await tools.exec_command({cmd:"apply_patch <<\'EOF\'{nl}*** Begin Patch'
    '{nl}*** Update File: " + f + "' + PATCH_BODY + '"});',
    "const f = '{outside}/notes.py';\n"
    "await tools.exec_command({cmd:'apply_patch <<EOF{nl}*** Begin Patch"
    "{nl}*** Update File: ' + f + '" + PATCH_BODY + "'});",
    # Template interpolation: an expression, and a bare variable (the real
    # shape of 2 of 794 real patch calls).
    'const d = "{outside}";\n'
    'await tools.exec_command({cmd:`apply_patch <<\'EOF\'\n*** Begin Patch\n'
    '*** Update File: ${path.join(d, "notes.py")}'
    + PATCH_BODY.replace("{nl}", "\n") + '`});',
    'const d = "{outside}";\n'
    'await tools.exec_command({cmd:`apply_patch <<\'EOF\'\n*** Begin Patch\n'
    '*** Update File: ${d}/notes.py' + PATCH_BODY.replace("{nl}", "\n") + '`});',
    # Shell command substitution inside an unquoted heredoc.
    'await tools.exec_command({cmd:"apply_patch <<EOF{nl}*** Begin Patch'
    '{nl}*** Update File: $(echo {outside})/notes.py' + PATCH_BODY + '"});',
    # A JavaScript escape: the header really is ../<outside>/notes.py.
    'await tools.exec_command({cmd:"apply_patch <<\'EOF\'{nl}*** Begin Patch'
    '{nl}*** Update File: \\x2e\\x2e/{outside_name}/notes.py' + PATCH_BODY
    + '"});',
    # The header is its own literal, so it does not start a line (the real
    # shape of 2 of 794 real patch calls, there with `Delete File`).
    'const f = "{outside}/notes.py";\n'
    'await tools.exec_command({cmd:"apply_patch <<\'EOF\'{nl}*** Begin Patch'
    '{nl}" + "*** Update File: " + f + "' + PATCH_BODY + '"});',
    'const f = "{outside}/notes.py";\n'
    'const patch = "*** Begin Patch{nl}" + `*** Update File: ${f}{nl}`'
    ' + "' + PATCH_BODY.replace("{nl}EOF", "") + '";\n'
    'await tools.exec_command({cmd: "apply_patch <<\'EOF\'{nl}" + patch'
    ' + "{nl}EOF"});',
])
def test_patch_header_written_as_an_expression_fails_closed(
        repo, tmp_path_factory, source):
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "notes.py").write_text("x\n", encoding="utf-8")
    code = (source.replace("{nl}", "\\n").replace("{outside_name}", outside.name)
            .replace("{outside}", str(outside)))
    assert (repo.parent / outside.name) == outside  # the escape case needs it
    text, _ = run_rows(repo, [session_meta(repo), {
        "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "p1",
            "input": code}}])
    assert OUTSIDE_CONTEXT not in text


def test_expression_syntax_in_a_patch_body_does_not_withhold_it(repo):
    """Guard against over-withholding: only the HEADER is an address. A
    JavaScript file's own quotes and template literals are just its text."""
    body = ('{nl}@@{nl}+const KEEP_BODY = `${a}` + \\"b\\" + \'c\';'
            '{nl}*** End Patch{nl}EOF')
    code = ('await tools.exec_command({cmd:"apply_patch <<\'EOF\'{nl}'
            '*** Begin Patch{nl}*** Update File: src/app.js' + body + '"});'
            ).replace("{nl}", "\\n")
    text, _ = run_rows(repo, [session_meta(repo), {
        "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "p1",
            "input": code}}])
    assert "KEEP_BODY" in text


def test_nested_workdir_in_json_arguments_fails_closed(repo, tmp_path_factory):
    """Only the arguments' own top-level `workdir` is readable."""
    outside = tmp_path_factory.mktemp("elsewhere")
    text, _ = run_rows(repo, [session_meta(repo), {
        "type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": "f1",
            "arguments": json.dumps({
                "cmd": heredoc_patch("notes.py"), "workdir": str(repo),
                "env": {"workdir": str(outside)}})}}])
    assert OUTSIDE_CONTEXT not in text


@pytest.mark.parametrize("make_row", [
    # A function call's JSON arguments (real newlines in the command).
    lambda outside: {"type": "response_item", "payload": {
        "type": "function_call", "name": "exec_command", "call_id": "f1",
        "arguments": json.dumps({
            "cmd": f"builtin cd {outside} && " + heredoc_patch("notes.py")})}},
    # A shell tool's argv.
    lambda outside: {"type": "response_item", "payload": {
        "type": "function_call", "name": "shell", "call_id": "s1",
        "arguments": json.dumps({"command": [
            "bash", "-lc",
            f"if cd {outside}; then " + heredoc_patch("notes.py") + "; fi"]})}},
])
def test_directory_change_in_other_call_shapes_fails_closed(
        repo, tmp_path_factory, make_row):
    outside = tmp_path_factory.mktemp("elsewhere")
    text, _ = run_rows(repo, [session_meta(repo), make_row(outside)])
    assert OUTSIDE_CONTEXT not in text


def test_cd_mentioned_in_an_agent_message_does_not_withhold_it(repo):
    """Prose is never withheld for a `cd` it mentions (decision rule 2)."""
    text, _ = run_rows(repo, [session_meta(repo), {
        "type": "event_msg", "payload": {
            "type": "agent_message",
            "message": "KEEPME run cd /tmp first\n--- notes\nthen continue"}}])
    assert "KEEPME" in text


@pytest.mark.parametrize("rows_for", [
    # Review probe R3: a FileChange item, the turn's cwd is outside.
    lambda repo, out: [session_meta(repo), {
        "type": "turn_context", "payload": {"turn_id": "t", "cwd": out}},
        item_row({"type": "FileChange", "id": "x1", "status": "completed",
                  "changes": {"notes.py": {
                      "type": "update", "move_path": None,
                      "unified_diff": f"@@\n {OUTSIDE_CONTEXT}\n-a\n+b\n"}},
                  "stdout": "", "stderr": ""})],
    # Review probe R3b: a patch_apply_begin event, session cwd outside.
    lambda repo, out: [session_meta(out), {
        "type": "event_msg", "payload": {
            "type": "patch_apply_begin", "call_id": "x2",
            "auto_approved": True, "changes": {"notes.py": {"update": {
                "unified_diff": f"@@\n {OUTSIDE_CONTEXT}\n",
                "move_path": None}}}}}],
    # No cwd known at all.
    lambda repo, out: [{
        "type": "event_msg", "payload": {
            "type": "patch_apply_end", "call_id": "x3",
            "changes": {"notes.py": {
                "unified_diff": f"@@\n {OUTSIDE_CONTEXT}\n"}}}}],
])
def test_relative_changes_keys_resolve_against_the_records_directory(
        repo, tmp_path_factory, rows_for):
    outside = str(tmp_path_factory.mktemp("elsewhere"))
    text, rows = run_rows(repo, rows_for(repo, outside))
    assert OUTSIDE_CONTEXT not in text
    assert "<external:" in text


def test_relative_changes_key_inside_the_project_is_kept(repo):
    text, rows = run_rows(repo, [
        session_meta(repo / "src"),
        item_row({"type": "FileChange", "id": "x1", "status": "completed",
                  "changes": {"main.py": {
                      "type": "update", "move_path": None,
                      "unified_diff": "@@\n-a\n+KEEP_DIFF\n"}},
                  "stdout": "", "stderr": ""}),
    ])
    changes = rows[1]["payload"]["item"]["changes"]
    assert changes == {"src/main.py": {
        "type": "update", "move_path": None,
        "unified_diff": "@@\n-a\n+KEEP_DIFF\n"}}


def test_extension_item_of_an_unmeasured_kind_is_withheld(repo):
    """Review probe R10: an unknown extension kind kept its `result`
    because its id happened to join an in-project call."""
    text, rows = run_rows(repo, [
        session_meta(repo), js_exec("ls", call_id="c10"),
        item_row({"type": "Extension", "id": "c10", "kind": "fs.read",
                  "result": READ_SECRET, "query": READ_SECRET}),
    ])
    assert READ_SECRET not in text
    item = rows[2]["payload"]["item"]
    assert item["reason"] == "unrecognized_item_kind"
    assert (item["type"], item["id"]) == ("Extension", "c10")


def test_item_with_no_invocation_never_keeps_its_output(repo):
    """Review probe R10: a WebSearch item names nothing we can check, so an
    id that happens to match an in-project call does not vouch for it."""
    text, rows = run_rows(repo, [
        session_meta(repo), js_exec("ls", call_id="c10"),
        item_row({"type": "WebSearch", "id": "c10", "query": "q",
                  "results": [READ_SECRET]}),
    ])
    assert READ_SECRET not in text
    assert rows[2]["payload"]["item"]["results"]["withheld"] is True


# --- Layer 4b-iv: every Codex row type, and `world_state` ------------------
#
# Review finding rp7: any `{type, payload}` row counted as Codex, so a row
# type nobody had measured got path anonymization only. `world_state` (741
# real rows) was one: it carries the text of the user's own skills and the
# AGENTS.md instructions, up to ~23k characters, verbatim.

AGENTS_TEXT = "# Project rules\n\nKEEP_AGENTS_RULE explain before you edit.\n"


def load_world_fixture(repo, outside, agents_text=AGENTS_TEXT):
    """The sanitized real-shaped world_state fixture, placeholders filled."""
    def fill(value):
        if isinstance(value, str):
            return (value.replace("__AGENTS_MD__", agents_text)
                    .replace("__REPO__", str(repo))
                    .replace("__OUTSIDE__", str(outside)))
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, dict):
            return {fill(k): fill(v) for k, v in value.items()}
        return value
    text = (FIXTURES / "rollout-world-state.jsonl").read_text(encoding="utf-8")
    return [fill(json.loads(line)) for line in text.splitlines() if line.strip()]


def test_real_shaped_world_state_keeps_settings_and_withholds_outside_text(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (repo / "AGENTS.md").write_text(AGENTS_TEXT, encoding="utf-8")
    text, rows = run_rows(repo, load_world_fixture(repo, outside))

    for leaked in ("FIXTURE_HOST_SKILL_BODY", "FIXTURE_FILESYSTEM_NOTE",
                   "FIXTURE_SUBAGENTS_NOTE", "FIXTURE_APPROVED_PREFIX",
                   "FIXTURE_UNVERIFIED_AGENTS_TEXT", "FIXTURE_OUTSIDE_BODY",
                   str(outside)):
        assert leaked not in text, leaked
    for kept in ("KEEP_AGENTS_RULE", "KEEP_PROMPT", "KEEP_AGENT_NOTE",
                 "KEEP_IN_PROJECT_OUTPUT"):
        assert kept in text, kept
    assert "unrecognized_" not in text  # every row type here is measured
    assert "withheld-key" not in text    # and every key
    assert "undeclared_field" not in text

    state = rows[1]["payload"]["state"]
    assert rows[1]["payload"]["withheld"] is True
    assert state["agents_md"] == {"directory": "", "text": AGENTS_TEXT}
    assert state["host_skills"] == {
        "body": {"withheld": True, "reason": "not_project_content"},
        "includeInstructions": True}
    assert state["environments"]["environments"]["local"] == {
        "cwd": "", "shell": "zsh", "status": "ready"}
    assert state["collaboration_mode"]["mode"] == "default"
    assert state["realtime"] == {"active": False}
    assert state["context_window_guidance"] == ""
    # The second snapshot's AGENTS.md text is not what the project has.
    assert rows[3]["payload"]["state"]["agents_md"]["text"] == {
        "withheld": True, "reason": "unverified_source"}
    assert rows[4]["payload"] == {"full": False, "state": {
        "agents_md": {}, "collaboration_mode": None}}


def test_review_probe_r4_world_state_skill_body_is_withheld(repo):
    text, _ = run_rows(repo, [session_meta(repo), {
        "type": "world_state", "payload": {"full": True, "state": {
            "host_skills": {"body": READ_SECRET + " from ~/.codex/skills",
                            "includeInstructions": True},
            "agents_md": {"directory": str(repo),
                          "text": READ_SECRET + "AGENTS"}}}}])
    assert READ_SECRET not in text


@pytest.mark.parametrize("layout, kept", [
    ("root", True),                  # the project's own AGENTS.md
    ("root+sub", True),              # root and subdirectory files, joined
    ("sub-only", True),              # the subdirectory's file alone
    ("different", False),            # the file says something else now
    ("outside-dir", False),          # the session ran outside the project
    ("ignored", False),              # a gitignored AGENTS.md is not submitted
    ("symlink-out", False),          # the file really lives outside
    ("missing", False),              # nothing to compare with
])
def test_agents_md_text_is_kept_only_when_it_is_the_projects_own_file(
        repo, tmp_path_factory, layout, kept):
    outside = tmp_path_factory.mktemp("outside")
    sub = repo / "src"
    root_text, sub_text = "ROOT_RULES\n", "SUB_RULES\n"
    directory, text = sub, "ROOT_RULES\n\nSUB_RULES"
    if layout in ("root", "different", "ignored", "symlink-out", "missing"):
        directory, text = repo, root_text
    if layout == "sub-only":
        text = sub_text
    if layout == "outside-dir":
        directory, text = outside, root_text
        (outside / "AGENTS.md").write_text(root_text, encoding="utf-8")
    if layout in ("root", "root+sub", "sub-only", "outside-dir"):
        (repo / "AGENTS.md").write_text(root_text, encoding="utf-8")
    if layout in ("root+sub", "sub-only"):
        (sub / "AGENTS.md").write_text(sub_text, encoding="utf-8")
    if layout == "different":
        (repo / "AGENTS.md").write_text("OTHER_RULES\n", encoding="utf-8")
    if layout == "ignored":
        (repo / "AGENTS.md").write_text(root_text, encoding="utf-8")
        with (repo / ".gitignore").open("a", encoding="utf-8") as f:
            f.write("AGENTS.md\n")
    if layout == "symlink-out":
        (outside / "rules.md").write_text(root_text, encoding="utf-8")
        (repo / "AGENTS.md").symlink_to(outside / "rules.md")
    _, rows = run_rows(repo, [session_meta(directory), {
        "type": "world_state", "payload": {"full": True, "state": {
            "agents_md": {"directory": str(directory), "text": text}}}}])
    out = rows[1]["payload"]["state"]["agents_md"]["text"]
    assert (out == text) is kept, out


@pytest.mark.parametrize("row, reason", [
    ({"type": "window_snapshot", "payload": {"text": READ_SECRET}},
     "unrecognized_row_type"),
    ({"type": "response_item", "payload": {
        "type": "file_search_result", "call_id": "c1", "text": READ_SECRET}},
     "unrecognized_item_type"),
])
def test_unmeasured_codex_row_and_response_item_types_fail_closed(
        repo, row, reason):
    text, rows = run_rows(repo, [session_meta(repo), row])
    assert READ_SECRET not in text
    assert rows[1]["payload"]["withheld"] is True
    assert rows[1]["payload"]["reason"] == reason
    assert rows[1]["type"] == row["type"]  # the gap stays visible


def test_unmeasured_item_inside_a_compacted_history_fails_closed(repo):
    text, rows = run_rows(repo, [session_meta(repo), typed_row("KEEP_PROMPT"), {
        "type": "compacted", "payload": {"message": "", "replacement_history": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "KEEP_PROMPT"}]},
            {"type": "file_search_result", "text": READ_SECRET}]}}])
    assert READ_SECRET not in text
    assert "KEEP_PROMPT" in text
    history = rows[2]["payload"]["replacement_history"]
    assert history[1]["reason"] == "unrecognized_item_type"


def test_undeclared_top_level_row_field_is_withheld(repo):
    text, rows = run_rows(repo, [session_meta(repo), {
        "type": "token_usage_record", "payload": {"turn_id": "t"},
        "context": READ_SECRET}])
    assert READ_SECRET not in text
    assert rows[1]["context"] == {"withheld": True, "reason": "undeclared_field"}


def test_unrecognized_record_skeleton_keeps_no_path_keyed_bodies(repo):
    """A skeleton keeps identity, not content: an unknown event's `changes`
    map would carry in-project file bodies under an unverified shape."""
    text, rows = run_rows(repo, [session_meta(repo), {
        "type": "event_msg", "payload": {
            "type": "patch_apply_updated", "call_id": "c1",
            "changes": {"src/main.py": {"content": "UNVERIFIED_BODY"}}}}])
    assert "UNVERIFIED_BODY" not in text
    assert rows[1]["payload"] == {
        "type": "patch_apply_updated", "call_id": "c1",
        "withheld": True, "reason": "unrecognized_event_type"}


OUTSIDE_FILE = "/opt/elsewhere/notes.txt"


@pytest.mark.parametrize("row", [
    # Round-2 review: dict KEYS carry text too.
    {"type": "world_state", "payload": {"full": True, "state": {
        "permissions": {"approved": {READ_SECRET + " cat notes": True}}}}},
    {"type": "world_state", "payload": {"full": True, "state": {
        "x": {READ_SECRET: 1}}}},
    {"type": "world_state", "payload": {"full": True, "state": {
        "environments": {"environments": {
            READ_SECRET + " name": {"cwd": "/tmp"}}}}}},
    # A declared identifier field that does not hold an identifier.
    {"type": "world_state", "payload": {"full": True, "state": {
        "model": READ_SECRET}}},
    # Undeclared fields of other measured row types.
    {"type": "turn_context", "payload": {
        "turn_id": "t", "user_instructions": READ_SECRET}},
    {"type": "token_usage_record", "payload": {"note": READ_SECRET}},
    {"type": "inter_agent_communication_metadata", "payload": {
        "note": READ_SECRET}},
    {"type": "compacted", "payload": {"message": "", "other_history": [
        {"type": "file_read", "text": READ_SECRET}]}},
    # Response items whose declared fields are not project content.
    {"type": "response_item", "payload": {
        "type": "tool_search_output", "call_id": "t",
        "tools": [{"type": "function", "name": "read",
                   "description": READ_SECRET}]}},
    {"type": "response_item", "payload": {
        "type": "web_search_call", "id": "w", "result": READ_SECRET,
        "action": {"type": "open_page", "url": "file://" + OUTSIDE_FILE}}},
    {"type": "response_item", "payload": {
        "type": "image_generation_call", "id": "i", "result": READ_SECRET}},
    # An item with a `payload` that is not a row payload keeps to its schema.
    {"type": "response_item", "payload": {
        "type": "web_search_call", "id": "w", "payload": READ_SECRET,
        "result": READ_SECRET}},
    {"type": "response_item", "payload": {
        "type": "web_search_call", "id": "w", "payload": {"x": 1},
        "result": READ_SECRET}},
    # An in-project file moved OUT of the project: its diff goes with it.
    {"type": "event_msg", "payload": {
        "type": "patch_apply_end", "call_id": "z", "changes": {
            "src/main.py": {"unified_diff": READ_SECRET,
                            "move_path": OUTSIDE_FILE}}}},
])
def test_undeclared_or_non_project_text_in_measured_rows_is_withheld(
        repo, row):
    text, rows = run_rows(repo, [session_meta(repo), row])
    assert READ_SECRET not in text
    assert OUTSIDE_FILE not in text


def test_undeclared_row_metadata_is_withheld(repo):
    text, rows = run_rows(repo, [session_meta(repo), {
        "type": "response_item",
        "metadata": {"client_authored": True, "note": READ_SECRET},
        "payload": {"type": "message", "role": "user", "content": []}}])
    assert READ_SECRET not in text
    assert rows[1]["metadata"] == {
        "client_authored": True,
        "<withheld-key-1>": {"withheld": True, "reason": "undeclared_field"}}


def test_undeclared_key_is_replaced_by_a_placeholder(repo):
    """The key itself is the content, so it is not kept either."""
    _, rows = run_rows(repo, [session_meta(repo), {
        "type": "world_state", "payload": {"full": True, "state": {
            "permissions": {"approved": {
                READ_SECRET + " cat notes": True, "git status": True}}}}}])
    payload = rows[1]["payload"]
    assert payload["state"]["permissions"] == {"<withheld-key-1>": {
        "withheld": True, "reason": "undeclared_field"}}
    assert payload["withheld"] is True


def test_measured_turn_context_and_response_items_keep_their_settings(repo):
    _, rows = run_rows(repo, [session_meta(repo), {
        "type": "turn_context", "payload": {
            "turn_id": "turn-1", "cwd": str(repo), "model": "gpt-5.3-codex",
            "approval_policy": "on-request", "timezone": "America/New_York",
            "active_permission_profile": {"id": ":workspace"},
            "workspace_roots": [str(repo)],
            "collaboration_mode": {"mode": "default", "settings": {
                "developer_instructions": "PLAN MODE PROMPT",
                "reasoning_effort": "medium"}}}}, {
        "type": "response_item", "payload": {
            "type": "web_search_call", "id": "ws_1", "status": "completed",
            "action": {"type": "search", "query": "KEEP_QUERY sockets",
                       "url": "https://docs.python.org/3/"}}}, {
        "type": "event_msg", "payload": {
            "type": "patch_apply_end", "call_id": "z", "changes": {
                "src/main.py": {"unified_diff": "KEEP_DIFF",
                                "move_path": str(repo / "src" / "app.py")}}}}, {
        # 98 real rows carry this row-level metadata.
        "type": "response_item", "ordinal": 7,
        "metadata": {"client_authored": True,
                     "fallback_token_limit_override": 4096},
        "payload": {"type": "function_call_output", "call_id": "none",
                    "output": ""}}])
    turn = rows[1]["payload"]
    assert (turn["cwd"], turn["model"], turn["workspace_roots"]) == (
        "", "gpt-5.3-codex", [""])
    assert turn["active_permission_profile"] == {"id": ":workspace"}
    assert turn["collaboration_mode"]["settings"] == {
        "developer_instructions": {"withheld": True,
                                   "reason": "not_project_content"},
        "reasoning_effort": "medium"}
    search = rows[2]["payload"]
    assert search["action"]["query"] == "KEEP_QUERY sockets"
    assert search["action"]["url"] == "https://docs.python.org/3/"
    assert "withheld" not in search
    change = rows[3]["payload"]["changes"]["src/main.py"]
    assert change["unified_diff"] == "KEEP_DIFF"
    assert rows[4]["metadata"] == {"client_authored": True,
                                   "fallback_token_limit_override": 4096}


def test_normalized_copy_of_a_measured_row_follows_the_same_schema(repo):
    """codex_normalize keeps the vendor payload under extras.raw_payload."""
    path = write_normalized(repo, [normalized_event(
        "turn_start", extras={"raw_type": "turn_context", "raw_payload": {
            "turn_id": "turn-1", "user_instructions": READ_SECRET}})])
    redaction.redact_traces(repo)
    assert READ_SECRET not in path.read_text(encoding="utf-8")


def test_codex_row_and_response_item_types_are_pinned():
    """Measured over 844 real rollouts (Codex CLI 0.129 through 0.154)."""
    assert redaction.CODEX_ROW_TYPES == frozenset({
        "session_meta", "turn_context", "event_msg", "response_item",
        "compacted", "world_state", "inter_agent_communication_metadata",
        "token_usage_record",
    })
    assert redaction.CODEX_RESPONSE_ITEM_TYPES == frozenset({
        "message", "reasoning", "agent_message", "compaction",
        "image_generation_call", "web_search_call", "tool_search_call",
        "tool_search_output",
    }) | redaction.CALL_PAYLOAD_TYPES | redaction.OUTPUT_PAYLOAD_TYPES
    assert set(redaction.CODEX_ROW_SCHEMAS) == {
        "session_meta", "world_state", "turn_context", "token_usage_record",
        "inter_agent_communication_metadata", "compacted"}
    # What session_meta keeps: its settings. The system prompt, tool
    # documentation and agent nicknames are withheld (decision 35e).
    assert redaction.schema_leaves(
        redaction.CODEX_ROW_SCHEMAS["session_meta"]) == {
        "id": "uuid", "session_id": "uuid", "forked_from_id": "uuid",
        "parent_thread_id": "uuid", "timestamp": "timestamp", "cwd": "path",
        "originator": "label", "cli_version": "version",
        "model_provider": "name", "history_mode": "name",
        "thread_source": "snake", "multi_agent_version": "name",
        "context_window.window_id": "uuid", "git.commit_hash": "hex",
        "git.branch": "branch", "git.repository_url": "git_remote",
        "source": "name", "source.subagent": "name",
        "source.subagent.other": "name",
        "source.subagent.thread_spawn.parent_thread_id": "uuid",
        "source.subagent.thread_spawn.agent_role": "name",
        "source.subagent.thread_spawn.agent_path": "path",
        "agent_role": "name", "agent_path": "path",
        "base_instructions.provenance.type": "name",
        "base_instructions.provenance.model": "name",
        # A tool's sub-tools follow the same schema, listed once.
        "dynamic_tools.type": "snake", "dynamic_tools.name": "snake",
    }
    assert set(redaction.CODEX_RESPONSE_ITEM_SCHEMAS) == {
        "web_search_call", "tool_search_call", "tool_search_output",
        "image_generation_call"}
    # What world_state keeps: every other string in it is withheld.
    assert redaction.schema_leaves(
        redaction.CODEX_ROW_SCHEMAS["world_state"]) == {
        "state.agents_md.directory": "path",
        "state.agents_md.text": "agents_text",
        "state.collaboration_mode": "name",
        "state.collaboration_mode.mode": "name",
        "state.collaboration_mode.model": "name",
        "state.environments.current_date": "date",
        "state.environments.environments.local.cwd": "path",
        "state.environments.environments.local.shell": "name",
        "state.environments.environments.local.status": "name",
        "state.environments.timezone": "timezone",
        "state.model": "name",
        "state.multi_agent_mode.mode": "camel",
        "state.personality.model": "name",
        "state.personality.personality": "name",
    }


# --- Layer 4b-v: tool-injected context (instructor decision 35e) --------
#
# "Tool-injected context is not captured; never commit a student's global
# ~/.codex/AGENTS.md." Codex puts text nobody typed into user- and
# developer-role messages: AGENTS.md bodies, environment blocks, skill
# bodies, an approval reviewer's copy of the whole conversation (tool
# output included). What the student typed is recorded separately, so a
# message item is kept only when it JOINS such a record -- or when it is
# provably the project's own AGENTS.md.

TYPED = "KEEP_TYPED why does my parser drop the last token?"
INJECTED = "INJECTED_CONTEXT_BODY"
WITHHELD_INJECTED = {"withheld": True, "reason": "injected_context"}


def typed_row(text):
    """The record current Codex CLIs write for a prompt the student typed."""
    return item_row({"type": "UserMessage", "id": "u1", "client_id": "k1",
                     "content": [{"type": "text", "text": text,
                                  "text_elements": []}]})


def message_row(role, *texts):
    return {"type": "response_item", "payload": {
        "type": "message", "role": role,
        "content": [{"type": "input_text", "text": t} for t in texts]}}


def agents_block(directory, body):
    return (f"# AGENTS.md instructions for {directory}\n\n<INSTRUCTIONS>\n"
            f"{body}\n</INSTRUCTIONS>")


@pytest.mark.parametrize("role, injected", [
    ("user", "<environment_context>\n  <cwd>/home/s/p</cwd>\n  "
             + INJECTED + "\n</environment_context>"),
    ("user", agents_block("/home/s", INJECTED)),     # a global AGENTS.md
    ("user", "<skill>\n<name>notes</name>\n" + INJECTED + "\n</skill>"),
    ("user", "<subagent_notification>" + INJECTED + "</subagent_notification>"),
    # An approval reviewer's copy of the conversation, tool output included.
    ("user", "[3] tool exec_command result:\n" + INJECTED),
    ("user", '<image name=[Image #1] path="/home/s/' + INJECTED + '.png">'),
    ("user", INJECTED),                                 # nobody typed it
    ("developer", "<permissions instructions>" + INJECTED
                  + "</permissions instructions>"),
    ("developer", INJECTED),
    ("system", INJECTED),
])
def test_injected_context_is_withheld_and_typed_text_kept(repo, role, injected):
    text, rows = run_rows(repo, [
        session_meta(repo), typed_row(TYPED),
        message_row(role, injected), message_row("user", injected, TYPED)])
    assert INJECTED not in text
    assert TYPED in text
    assert rows[1]["payload"]["item"]["content"][0]["text"] == TYPED
    injected_only = rows[2]["payload"]
    assert injected_only["content"] == [
        {"type": "input_text", "text": WITHHELD_INJECTED}]
    assert injected_only["withheld"] is True
    assert injected_only["role"] == role  # the gap stays visible
    mixed = rows[3]["payload"]["content"]
    assert mixed == [{"type": "input_text", "text": WITHHELD_INJECTED},
                     {"type": "input_text", "text": TYPED}]


def test_typed_text_is_joined_through_the_older_user_message_event(repo):
    """Before item_completed, Codex recorded a prompt as `user_message`."""
    text, rows = run_rows(repo, [
        session_meta(repo),
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": TYPED + "\n"}},
        message_row("user", TYPED)])
    assert rows[2]["payload"]["content"][0]["text"] == TYPED
    assert "withheld" not in text


def test_an_image_the_student_attached_is_not_injected_context(repo):
    image = {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}
    _, rows = run_rows(repo, [session_meta(repo), {
        "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [image]}}])
    assert rows[1]["payload"]["content"] == [image]


@pytest.mark.parametrize("layout, kept", [
    ("project", True),            # exactly the project's AGENTS.md
    ("global+project", False),    # a global file joined in front of it
    ("outside-dir", False),       # the session ran outside the project
    ("changed", False),           # the file says something else now
])
def test_agents_md_block_is_kept_only_when_it_is_the_projects_own_file(
        repo, tmp_path_factory, layout, kept):
    """The rp7 provenance rule, reused for the injected message block."""
    outside = tmp_path_factory.mktemp("home")
    (repo / "AGENTS.md").write_text(AGENTS_TEXT, encoding="utf-8")
    body, directory = AGENTS_TEXT.strip(), repo
    if layout == "global+project":
        body = ("GLOBAL_RULE from ~/.codex/AGENTS.md"
                "\n\n--- project-doc ---\n\n" + body)
    if layout == "outside-dir":
        directory = outside
        (outside / "AGENTS.md").write_text(AGENTS_TEXT, encoding="utf-8")
    if layout == "changed":
        body = "KEEP_AGENTS_RULE but an older version"
    text, rows = run_rows(repo, [
        session_meta(directory), message_row("user", agents_block(directory, body))])
    assert "GLOBAL_RULE" not in text
    item = rows[1]["payload"]["content"][0]["text"]
    assert (item != WITHHELD_INJECTED) is kept
    if kept:
        assert "KEEP_AGENTS_RULE" in item


def test_injected_messages_inside_a_compacted_history_are_withheld(repo):
    text, rows = run_rows(repo, [session_meta(repo), typed_row(TYPED), {
        "type": "compacted", "payload": {"message": "", "replacement_history": [
            message_row("user", TYPED)["payload"],
            message_row("developer", INJECTED)["payload"],
            message_row("user", "<environment_context>" + INJECTED)["payload"],
        ], "guardian_history": [
            message_row("user", ">>> TRANSCRIPT START",
                        "[1] tool exec_command result:\n" + INJECTED)["payload"],
        ]}}])
    assert INJECTED not in text
    history = rows[2]["payload"]["replacement_history"]
    assert history[0]["content"][0]["text"] == TYPED
    assert history[1]["content"][0]["text"] == WITHHELD_INJECTED
    assert history[2]["content"][0]["text"] == WITHHELD_INJECTED


SYSTEM_PROMPT = "INJECTED_SYSTEM_PROMPT You are Codex, a coding agent."


def test_session_meta_keeps_its_settings_and_withholds_injected_text(repo):
    _, rows = run_rows(repo, [{"type": "session_meta", "payload": {
        "id": "019fecf1-e55b-7371-af00-b8ccc6e19996",
        "session_id": "019fecf1-e55b-7371-af00-b8ccc6e19996",
        "timestamp": "2026-08-10T18:31:43.740Z", "cwd": str(repo),
        "originator": "Codex Desktop", "cli_version": "0.146.0-alpha.9.2",
        "source": {"subagent": {"thread_spawn": {
            "parent_thread_id": "019fecf1-e55b-7371-af00-b8ccc6e19990",
            "depth": 1, "agent_role": "explorer", "agent_path": None,
            "agent_nickname": "Ada the 2nd"}}},
        "thread_source": "user", "model_provider": "openai",
        "history_mode": "legacy", "multi_agent_version": "v2",
        "context_window": {"window_id": "019fecf1-e55b-7371-af00-b8d13d9cec89"},
        "git": {"commit_hash": "0123456789abcdef0123456789abcdef01234567",
                "branch": "feat/parser",
                "repository_url": "https://github.com/course/proj.git"},
        "base_instructions": {"text": SYSTEM_PROMPT,
                              "provenance": {"type": "custom"}},
        "dynamic_tools": [{"type": "namespace", "name": "codex_apps",
                           "description": INJECTED, "tools": [{
                               "type": "function", "name": "read_doc",
                               "description": INJECTED, "deferLoading": True,
                               "inputSchema": {"type": "object"}}]}],
    }}])
    meta = rows[0]["payload"]
    hidden = {"withheld": True, "reason": "not_project_content"}
    assert "INJECTED" not in json.dumps(meta)
    assert meta["base_instructions"] == {"text": hidden,
                                         "provenance": {"type": "custom"}}
    assert meta["dynamic_tools"] == [{
        "type": "namespace", "name": "codex_apps", "description": hidden,
        "tools": [{"type": "function", "name": "read_doc",
                   "description": hidden, "deferLoading": True,
                   "inputSchema": hidden}]}]
    assert meta["source"]["subagent"]["thread_spawn"]["agent_nickname"] == hidden
    assert (meta["cwd"], meta["originator"], meta["cli_version"]) == (
        "", "Codex Desktop", "0.146.0-alpha.9.2")
    assert meta["git"] == {
        "commit_hash": "0123456789abcdef0123456789abcdef01234567",
        "branch": "feat/parser",
        "repository_url": "https://github.com/course/proj.git"}
    assert "withheld-key" not in json.dumps(meta)
    assert "undeclared_field" not in json.dumps(meta)


@pytest.mark.parametrize("instructions, kept", [
    (AGENTS_TEXT.strip(), True),
    ("GLOBAL_RULE\n\n--- project-doc ---\n\n" + AGENTS_TEXT.strip(), False),
])
def test_turn_context_instructions_follow_the_same_rule(repo, instructions, kept):
    (repo / "AGENTS.md").write_text(AGENTS_TEXT, encoding="utf-8")
    text, rows = run_rows(repo, [session_meta(repo), {
        "type": "turn_context", "payload": {
            "turn_id": "t1", "cwd": str(repo),
            "user_instructions": instructions,
            "developer_instructions": INJECTED}}])
    assert INJECTED not in text and "GLOBAL_RULE" not in text
    turn = rows[1]["payload"]
    assert turn["developer_instructions"] == {
        "withheld": True, "reason": "not_project_content"}
    assert (turn["user_instructions"] == instructions) is kept


def test_normalized_tier_follows_the_injected_context_rule(repo):
    """codex_normalize copies messages into the normalized tier and the
    interaction stream; the same join decides what survives there, pass
    after pass (both tiers are redacted in place on every snapshot)."""
    from tests._capture.agent_adapters import codex_normalize
    (repo / "AGENTS.md").write_text(AGENTS_TEXT, encoding="utf-8")
    write_rollout(repo, [
        {"type": "session_meta", "payload": {
            "id": "sess-35e", "cwd": str(repo),
            "base_instructions": {"text": SYSTEM_PROMPT}}},
        message_row("developer", "<permissions instructions>" + INJECTED),
        message_row("user", agents_block(repo, AGENTS_TEXT.strip())),
        message_row("user", "<environment_context>" + INJECTED),
        message_row("user", TYPED),
        typed_row(TYPED),
    ], name="rollout-35e.jsonl")
    codex_normalize.normalize_all(repo)
    normalized = repo / ".ai-traces" / "codex" / "normalized" / "sess-35e.jsonl"
    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    for _ in range(2):
        redaction.redact_traces(repo)
        for path in (normalized, stream):
            body = path.read_text(encoding="utf-8")
            assert INJECTED not in body
            assert "INJECTED_SYSTEM_PROMPT" not in body
            assert "KEEP_AGENTS_RULE" in body
            events = [json.loads(line) for line in body.splitlines()]
            prompts = [e["text"] for e in events
                       if e["event_type"] == "user_prompt"]
            assert prompts == [TYPED]


# Codex runs sub-agents and approval reviewers as sessions of their own, and
# records the prompt it gives them where a typed prompt goes. A reviewer's
# prompt is a copy of the conversation, command output included.
REVIEWER_COPY = (">>> TRANSCRIPT START\n[1] user: fix it\n"
                 "[2] tool exec_command call: cat /home/s/notes.txt\n"
                 "[3] tool exec_command result:\n" + INJECTED
                 + "\n>>> TRANSCRIPT END")
AGENT_SESSIONS = [
    {"source": {"subagent": {"other": "guardian"}},
     "thread_source": "guardian_review", "parent_thread_id": "p1"},
    {"source": {"subagent": {"other": "guardian"}}, "thread_source": "subagent"},
    {"source": {"subagent": {"thread_spawn": {
        "parent_thread_id": "p1", "depth": 1, "agent_role": "worker",
        "agent_nickname": "Gauss", "agent_path": None}}},
     "thread_source": "subagent", "agent_role": "worker"},
    {"source": "a_source_nobody_measured"},
    {"source": "cli", "thread_source": "subagent"},
    {"parent_thread_id": "p1"},
]


def agent_session_rows(meta, session_id="g1"):
    return [
        {"type": "session_meta", "payload": {
            "id": session_id, "cwd": "/home/s/p", **meta}},
        typed_row(REVIEWER_COPY),
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": REVIEWER_COPY, "images": [],
            "text_elements": [{"placeholder": INJECTED}]}},
        {"type": "event_msg", "payload": {"role": "user",
                                          "text": REVIEWER_COPY}},
        message_row("user", REVIEWER_COPY),
        {"type": "event_msg", "payload": {
            "type": "agent_reasoning", "text": "KEEP the reviewer's reasoning"}},
    ]


@pytest.mark.parametrize("meta", AGENT_SESSIONS)
def test_nothing_is_typed_in_another_agents_session(repo, meta):
    text, rows = run_rows(repo, agent_session_rows(meta))
    assert INJECTED not in text
    assert "KEEP the reviewer's reasoning" in text  # its own text is kept
    item = rows[1]["payload"]["item"]
    assert item["content"] == [{"type": "text", "text": WITHHELD_INJECTED}]
    assert rows[2]["payload"]["type"] == "user_message"
    assert rows[2]["payload"]["message"] == WITHHELD_INJECTED
    assert rows[3]["payload"] == {"role": "user", "text": WITHHELD_INJECTED}


@pytest.mark.parametrize("meta", [
    {}, {"source": "cli", "thread_source": "user"},
    {"source": "vscode", "thread_source": None}, {"source": "exec"}])
def test_the_students_own_sessions_keep_their_prompts(repo, meta):
    text, out = run_rows(repo, agent_session_rows(meta))
    assert INJECTED in out[1]["payload"]["item"]["content"][0]["text"]
    assert INJECTED in out[2]["payload"]["message"]
    assert INJECTED in out[3]["payload"]["text"]


def test_agent_session_prompts_are_withheld_in_the_normalized_tier(repo):
    from tests._capture.agent_adapters import codex_normalize
    write_rollout(repo, agent_session_rows(AGENT_SESSIONS[0]),
                  name="rollout-g.jsonl")
    # With no prompt record, the message itself becomes the prompt event.
    write_rollout(repo, [
        {"type": "session_meta", "payload": {"id": "g2", **AGENT_SESSIONS[2]}},
        message_row("user", REVIEWER_COPY),
    ], name="rollout-g2.jsonl")
    write_rollout(repo, [
        {"type": "session_meta", "payload": {
            "id": "s1", "cwd": str(repo), "source": "cli"}},
        typed_row(TYPED), message_row("user", TYPED),
    ], name="rollout-s.jsonl")
    codex_normalize.normalize_all(repo)
    normalized = repo / ".ai-traces" / "codex" / "normalized"
    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    for _ in range(2):
        redaction.redact_traces(repo)
        for path in [*normalized.glob("*.jsonl"), stream]:
            assert INJECTED not in path.read_text(encoding="utf-8"), path.name
        events = [json.loads(line) for line in
                  stream.read_text(encoding="utf-8").splitlines()]
        prompts = [e["text"] for e in events
                   if e["event_type"] == "user_prompt"]
        assert TYPED in prompts
        assert all(p in (TYPED, WITHHELD_INJECTED) for p in prompts)


# Codex Desktop and the VS Code extension write their own context into the
# typed message, ahead of the student's request (review round 5): the page
# open in the app's browser, the active file and selection, files the
# student mentioned. INJECTED marks that context.
TOOL_CONTEXTS = [
    "# In app browser:\n<in-app-browser-context url=\"https://x.test/"
    + INJECTED + "\">\nTitle: " + INJECTED
    + "\n</in-app-browser-context>\n\n## My request for Codex:\n" + TYPED,
    "# Context from my IDE setup:\n\n## Active file: .env\n\n"
    "## Active selection of the file:\n" + INJECTED + "\n\n## Open tabs:\n"
    "- .env: .env\n\n## My request for Codex:\n" + TYPED + "\n",
    "# Files mentioned by the user:\n\n## notes.md: /home/s/notes.md\n"
    + INJECTED + "\n## My request: (a line the file happened to hold)\n\n"
    "## My request:\n" + TYPED,
]


@pytest.mark.parametrize("typed", TOOL_CONTEXTS + [
    "# In app browser:\n" + INJECTED])  # no request to keep
def test_tool_context_in_a_typed_codex_prompt_is_withheld(repo, typed):
    from tests._capture.agent_adapters import codex_normalize
    write_rollout(repo, [
        {"type": "session_meta", "payload": {
            "id": "s1", "cwd": str(repo), "source": "vscode",
            "originator": "Codex Desktop"}},
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": typed, "images": [],
            "text_elements": [{"byte_range": {"start": 0, "end": 3}}]}},
        typed_row(typed), message_row("user", typed),
    ], name="rollout-s1.jsonl")
    write_raw(repo, "codex", "hooks/hooks-s1.jsonl",
              [hook_prompt_row("s1", typed)])
    codex_normalize.normalize_all(repo)
    for _ in range(2):
        redaction.redact_traces(repo)
        texts = committed_codex_texts(repo)
        for name, text in texts.items():
            assert INJECTED not in text, name
            if typed.endswith(("\n", TYPED)):  # a request to keep
                assert TYPED in text, name
        rows = [json.loads(line) for line in
                texts["codex/redacted/rollouts/rollout-s1.jsonl"].splitlines()]
        kept = rows[1]["payload"]["message"]
        assert kept == (TYPED if TYPED in typed else WITHHELD_INJECTED)


def hook_prompt_row(session_id, prompt):
    """What .codex/hooks logs for a UserPromptSubmit."""
    return {"adapter_name": "codex", "captured_at": "2026-09-01T10:00:00Z",
            "hook_event_name": "UserPromptSubmit",
            "payload": {"session_id": session_id, "turn_id": "t1",
                        "cwd": "/home/s/p", "prompt": prompt,
                        "hook_event_name": "UserPromptSubmit"}}


def committed_codex_texts(repo):
    """Every committed Codex file and the stream, by name."""
    root = repo / ".ai-traces"
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in root.rglob("*.jsonl") if "/raw/" not in str(p)}


def test_a_hook_prompt_is_kept_only_for_a_student_session(repo):
    """A hook log names no session source (review round 5). Its prompt is
    typed only when the session's own rollout, in this project's traces,
    is the student's; another agent's session, or one whose rollout is not
    here, keeps nothing."""
    from tests._capture.agent_adapters import codex_normalize
    other = "an unknown session's prompt " + INJECTED
    write_rollout(repo, [{"type": "session_meta", "payload": {
        "id": "s1", "cwd": str(repo), "source": "cli"}},
        typed_row(TYPED)], name="rollout-s1.jsonl")
    write_rollout(repo, agent_session_rows(AGENT_SESSIONS[2], "g1"),
                  name="rollout-g1.jsonl")
    for session_id, prompt in (("s1", TYPED), ("g1", REVIEWER_COPY),
                               ("u1", other)):
        write_raw(repo, "codex", f"hooks/hooks-{session_id}.jsonl",
                  [hook_prompt_row(session_id, prompt)])
    codex_normalize.normalize_all(repo)
    for _ in range(2):
        redaction.redact_traces(repo)
        texts = committed_codex_texts(repo)
        for name, text in texts.items():
            assert INJECTED not in text, name
        assert TYPED in texts["codex/redacted/hooks/hooks-s1.jsonl"]
        events = [json.loads(line) for line in
                  texts["codex/normalized/hooks.jsonl"].splitlines()]
        assert [e["text"] for e in events] == [
            WITHHELD_INJECTED, TYPED, WITHHELD_INJECTED]


def test_a_hook_prompt_is_kept_once_its_rollout_arrives(repo):
    write_raw(repo, "codex", "hooks/hooks-s1.jsonl",
              [hook_prompt_row("s1", TYPED)])
    redaction.redact_traces(repo)
    hooks = repo / ".ai-traces/codex/redacted/hooks/hooks-s1.jsonl"
    assert TYPED not in hooks.read_text(encoding="utf-8")
    write_rollout(repo, [{"type": "session_meta", "payload": {
        "id": "s1", "cwd": str(repo), "source": "cli"}}],
        name="rollout-s1.jsonl")
    redaction.redact_traces(repo)
    assert TYPED in hooks.read_text(encoding="utf-8")


def test_a_fork_child_rollout_is_another_agents_session(repo):
    """A sub-agent forked from the student's session copies the parent's
    session_meta after its own (6 real rollouts): everything in that file is
    still the sub-agent's (review round 5). The parent's own file is not."""
    from tests._capture.agent_adapters import codex_normalize
    agent = {"id": "g1", "cwd": str(repo), **AGENT_SESSIONS[2]}
    student = {"id": "s1", "cwd": str(repo), "source": "vscode",
               "thread_source": "user"}
    write_rollout(repo, [
        {"type": "session_meta", "payload": agent},
        {"type": "session_meta", "payload": student},
        typed_row(REVIEWER_COPY), message_row("user", REVIEWER_COPY),
    ], name="rollout-g1.jsonl")
    write_rollout(repo, [{"type": "session_meta", "payload": student},
                         typed_row(TYPED), message_row("user", TYPED)],
                  name="rollout-s1.jsonl")
    write_raw(repo, "codex", "hooks/hooks-s1.jsonl",
              [hook_prompt_row("s1", TYPED)])
    codex_normalize.normalize_all(repo)
    for _ in range(2):
        redaction.redact_traces(repo)
        texts = committed_codex_texts(repo)
        for name, text in texts.items():
            assert INJECTED not in text, name
        stream = [json.loads(line)
                  for line in texts["interaction-stream.jsonl"].splitlines()]
        assert TYPED in [e.get("text") for e in stream
                         if e["event_type"] == "user_prompt"
                         and "hook_event_name" not in e["extras"]]
        assert TYPED in texts["codex/redacted/hooks/hooks-s1.jsonl"]


def test_claude_tool_use_join_withholds_the_external_read(repo,
                                                          tmp_path_factory):
    """Measured on this machine: tool_use.id -> tool_result.tool_use_id,
    85/85 joined, zero orphans either way."""
    outside = tmp_path_factory.mktemp("other") / "elsewhere.py"
    outside.write_text("x\n", encoding="utf-8")
    path = repo / ".ai-traces" / "claude" / "raw" / "sessions" / "s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Every real row states its cwd; a relative path needs one.
    rows_in = [
        {"type": "assistant", "uuid": "u1", "sessionId": "s1",
         "cwd": str(repo), "message": {"content": [
             {"type": "tool_use", "id": "toolu_1", "name": "Read",
              "input": {"file_path": str(outside)}}]}},
        {"type": "user", "uuid": "u2", "sessionId": "s1", "cwd": str(repo),
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "toolu_1",
              "content": READ_SECRET}]},
         "toolUseResult": {"file": {"content": READ_SECRET}}},
        {"type": "assistant", "uuid": "u3", "sessionId": "s1",
         "cwd": str(repo), "message": {"content": [
             {"type": "tool_use", "id": "toolu_2", "name": "Read",
              "input": {"file_path": "src/main.py"}}]}},
        {"type": "user", "uuid": "u4", "sessionId": "s1", "cwd": str(repo),
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "toolu_2",
              "content": "print('hi')"}]}},
    ]
    with path.open("w", encoding="utf-8") as f:
        for row in rows_in:
            f.write(json.dumps(row) + "\n")
    redaction.redact_traces(repo)
    out = (repo / ".ai-traces" / "claude" / redaction.REDACTED_SUBDIR
           / "sessions" / "s.jsonl").read_text(encoding="utf-8")
    assert READ_SECRET not in out
    assert str(outside) not in out
    # The in-project read is untouched.
    assert "print('hi')" in out


# --- Layer 4b': Claude Code file-history rows ----------------------------
#
# Real Claude Code sessions interleave `file-history-snapshot` and
# `file-history-delta` rows (its undo checkpoints). They carry no uuid or
# sessionId, and before this was handled ONE such row withheld the whole
# transcript -- every real session was withheld. The fixture keeps the real
# key layout with every value replaced by a placeholder.

CLAUDE_FIXTURE = (Path(__file__).parent / "fixtures" / "claude"
                  / "session-file-history.jsonl")


def write_claude_fixture(repo, outside_dir, extra_rows=()):
    text = (CLAUDE_FIXTURE.read_text(encoding="utf-8")
            .replace("{REPO}", str(repo))
            .replace("{OUTSIDE}", str(outside_dir)))
    text += "".join(json.dumps(row) + "\n" for row in extra_rows)
    path = repo / ".ai-traces" / "claude" / "raw" / "sessions" / "real.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return (repo / ".ai-traces" / "claude" / redaction.REDACTED_SUBDIR
            / "sessions" / "real.jsonl")


def test_real_shaped_claude_transcript_with_file_history_is_committed(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    out = write_claude_fixture(repo, outside)

    redaction.redact_traces(repo)

    assert not out.with_name(out.name + redaction.WITHHELD_SUFFIX).exists()
    rows = [json.loads(line)
            for line in out.read_text(encoding="utf-8").splitlines()]
    assert "KEEP_PROMPT" in out.read_text(encoding="utf-8")
    snapshot = next(r for r in rows if r["type"] == "file-history-snapshot"
                    and r["snapshot"]["trackedFileBackups"])
    backups = snapshot["snapshot"]["trackedFileBackups"]
    # The in-project checkpoint keeps its declared-safe metadata.
    assert backups["src/main.py"] == {
        "backupFileName": "0123456789abcdef@v1", "version": 1,
        "backupTime": "2026-09-01T12:00:01.500Z", "realParentDir": "src"}
    deltas = [r for r in rows if r["type"] == "file-history-delta"]
    assert deltas[0]["trackingPath"] == "src/main.py"
    assert deltas[0]["backup"]["backupFileName"] == "0123456789abcdef@v2"


def test_file_history_entries_for_external_files_are_withheld(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    out = write_claude_fixture(repo, outside)

    redaction.redact_traces(repo)

    text = out.read_text(encoding="utf-8")
    assert str(outside) not in text
    assert "notes.py" not in text
    # A RELATIVE key is external when its real parent directory is.
    assert "relative-but-outside" not in text
    # External backup references are withheld, not just re-labelled.
    assert "fedcba9876543210" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    backups = next(r for r in rows if r["type"] == "file-history-snapshot"
                   and r["snapshot"]["trackedFileBackups"]
                   )["snapshot"]["trackedFileBackups"]
    withheld = [v for k, v in backups.items() if k != "src/main.py"]
    assert len(withheld) == 2
    assert all(v == {"withheld": True, "reason": "external_path"}
               for v in withheld)
    external_delta = [r for r in rows if r["type"] == "file-history-delta"][1]
    assert external_delta["backup"] == {"withheld": True,
                                        "reason": "external_path"}
    assert external_delta["trackingPath"].startswith(redaction.EXTERNAL_PREFIX)


def test_undeclared_fields_in_file_history_rows_are_withheld(
        repo, tmp_path_factory):
    """Default deny inside the row: only declared-safe metadata survives."""
    outside = tmp_path_factory.mktemp("outside")
    out = write_claude_fixture(repo, outside, extra_rows=[
        {"type": "file-history-delta", "messageId": "m9",
         "snapshotMessageId": "m1", "trackingPath": "src/main.py",
         "backup": {"backupFileName": None, "version": 3,
                    "backupTime": "t", "realParentDir": str(repo / "src"),
                    "futureField": READ_SECRET},
         "futureContent": READ_SECRET, "timestamp": "t"},
    ])

    redaction.redact_traces(repo)

    text = out.read_text(encoding="utf-8")
    assert READ_SECRET not in text
    last = json.loads(text.splitlines()[-1])
    assert last["futureContent"] == {"withheld": True,
                                     "reason": "undeclared_field"}


def test_non_string_type_values_do_not_break_redaction(repo):
    """`type` is also a JSON-schema key, so real rollouts carry values like
    ``["string", "null"]`` -- which must not crash the file-history check."""
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": "KEEP_SCHEMA",
            "schema": {"type": ["string", "null"],
                       "items": {"type": {"nested": True}}}}},
    ])
    report = redaction.redact_traces(repo)
    assert report.errors == []
    assert "KEEP_SCHEMA" in redacted_text(repo)
    # A list-valued `type` at row level is simply not a recognized row.
    assert redaction.row_vocabulary({"type": ["x"], "messageId": "m"}) is None


def test_unknown_claude_row_type_still_withholds_the_whole_file(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    out = write_claude_fixture(repo, outside, extra_rows=[
        {"type": "file-history-something-new", "messageId": "m9",
         "text": READ_SECRET},
    ])

    redaction.redact_traces(repo)

    assert out.with_name(out.name + redaction.WITHHELD_SUFFIX).exists()
    assert not out.exists()


# --- Layer 4b'': EDIT records carry the target file's text ---------------
#
# An edit invocation names exactly one target and carries that target's old
# and new text, so -- like an apply_patch blob -- its provenance is readable
# from the record itself. When the target is outside the project the text
# is withheld; the record, its path sentinel and its ids survive.

EDIT_OLD = "EXTERNAL_OLD_TEXT_7f3a"
EDIT_NEW = "EXTERNAL_NEW_TEXT_9c1d"


def read_all_committed(repo, adapter):
    out_dir = repo / ".ai-traces" / adapter / redaction.REDACTED_SUBDIR
    return "".join(p.read_text(encoding="utf-8")
                   for p in out_dir.rglob("*") if p.is_file())


def write_raw(repo, adapter, rel, rows):
    path = repo / ".ai-traces" / adapter / "raw" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")


def cursor_edit_row(file_path):
    """What .cursor/hooks/_common.py::append_hook_event logs verbatim."""
    return {"adapter_name": "cursor", "captured_at": "2026-09-01T00:00:00Z",
            "hook_event_name": "afterFileEdit",
            "payload": {"conversation_id": "c1", "generation_id": "g1",
                        "hook_event_name": "afterFileEdit",
                        "file_path": file_path,
                        "edits": [{"old_string": EDIT_OLD,
                                   "new_string": EDIT_NEW}]}}


def test_cursor_edit_log_withholds_text_of_an_external_file(
        repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "private.py"
    write_raw(repo, "cursor", "hooks/hooks-c1.jsonl",
              [cursor_edit_row(str(outside))])

    redaction.redact_traces(repo)

    text = read_all_committed(repo, "cursor")
    assert EDIT_OLD not in text and EDIT_NEW not in text
    assert str(outside) not in text
    payload = json.loads(text)["payload"]
    assert payload["edits"] == {"withheld": True, "reason": "external_path"}
    assert payload["conversation_id"] == "c1"


def test_cursor_edit_log_keeps_text_of_an_in_project_file(repo):
    write_raw(repo, "cursor", "hooks/hooks-c1.jsonl",
              [cursor_edit_row(str(repo / "src" / "main.py"))])

    redaction.redact_traces(repo)

    text = read_all_committed(repo, "cursor")
    assert EDIT_OLD in text and EDIT_NEW in text


@pytest.mark.parametrize("name, tool_input", [
    ("Edit", {"file_path": "{T}", "old_string": EDIT_OLD,
              "new_string": EDIT_NEW}),
    ("Write", {"file_path": "{T}", "content": EDIT_NEW}),
    ("MultiEdit", {"file_path": "{T}", "edits": [
        {"old_string": EDIT_OLD, "new_string": EDIT_NEW}]}),
    ("NotebookEdit", {"notebook_path": "{T}", "cell_id": "x",
                      "new_source": EDIT_NEW}),
])
def test_claude_edit_calls_on_external_files_withhold_their_text(
        repo, tmp_path_factory, name, tool_input):
    target = str(tmp_path_factory.mktemp("outside") / "f.ipynb")
    tool_input = json.loads(json.dumps(tool_input).replace("{T}", target))
    write_raw(repo, "claude", "sessions/s.jsonl", [
        {"type": "assistant", "uuid": "u1", "sessionId": "s1",
         "message": {"content": [{"type": "tool_use", "id": "toolu_1",
                                  "name": name, "input": tool_input}]}},
    ])
    write_raw(repo, "claude", "hooks/hooks-s1.jsonl", [
        {"adapter_name": "claude", "hook_event_name": event,
         "payload": {"session_id": "s1", "hook_event_name": event,
                     "tool_name": name, "tool_use_id": "toolu_1",
                     "tool_input": tool_input,
                     **({"tool_response": {"filePath": target,
                                           "content": EDIT_NEW}}
                        if event == "PostToolUse" else {})}}
        for event in ("PreToolUse", "PostToolUse")
    ])

    redaction.redact_traces(repo)

    text = read_all_committed(repo, "claude")
    assert "tool_use" in text  # the call itself is kept
    assert EDIT_OLD not in text and EDIT_NEW not in text
    assert target not in text


def test_claude_edit_calls_on_in_project_files_keep_their_text(repo):
    write_raw(repo, "claude", "sessions/s.jsonl", [
        {"type": "assistant", "uuid": "u1", "sessionId": "s1",
         "message": {"content": [{"type": "tool_use", "id": "toolu_1",
                                  "name": "Edit", "input": {
                                      "file_path": str(repo / "src/main.py"),
                                      "old_string": EDIT_OLD,
                                      "new_string": EDIT_NEW}}]}},
    ])

    redaction.redact_traces(repo)

    text = read_all_committed(repo, "claude")
    assert EDIT_OLD in text and EDIT_NEW in text


def edited_text_file_row(filename):
    """Claude Code's note that a file changed: {filename, snippet} of it."""
    return {"type": "attachment", "uuid": "u2", "sessionId": "s1",
            "attachment": {"type": "edited_text_file",
                           "filename": filename, "snippet": EDIT_NEW}}


def test_edited_text_file_attachment_withholds_snippet_of_external_file(
        repo, tmp_path_factory):
    outside = str(tmp_path_factory.mktemp("outside") / "private.py")
    write_raw(repo, "claude", "sessions/s.jsonl",
              [edited_text_file_row(outside)])

    redaction.redact_traces(repo)

    text = read_all_committed(repo, "claude")
    assert EDIT_NEW not in text
    assert outside not in text
    attachment = json.loads(text)["attachment"]
    assert attachment["snippet"] == {"withheld": True,
                                     "reason": "external_path"}
    assert attachment["type"] == "edited_text_file"


def test_edited_text_file_attachment_keeps_snippet_of_in_project_file(repo):
    write_raw(repo, "claude", "sessions/s.jsonl",
              [edited_text_file_row(str(repo / "src" / "main.py"))])

    redaction.redact_traces(repo)

    assert EDIT_NEW in read_all_committed(repo, "claude")


# Claude Code attaches context on its own too (decision 35e): the CLAUDE.md
# files it loaded -- the user's global ~/.claude/CLAUDE.md included -- the
# system prompt, skill listings, hook output, IDE selections, and a
# `rendered` copy of each. None of it was typed.
def claude_row(row_type, **fields):
    return {"type": row_type, "uuid": "u-" + row_type, "sessionId": "s1",
            **fields}


@pytest.mark.parametrize("attachment", [
    {"type": "instructions", "files": [
        {"type": "User", "path": "/home/s/.claude/CLAUDE.md",
         "content": INJECTED},
        {"type": "Project", "path": "{repo}/CLAUDE.md", "content": INJECTED}]},
    {"type": "nested_memory", "path": "{repo}/src/CLAUDE.md",
     "displayPath": "src/CLAUDE.md", "content": {
         "type": "Project", "path": "{repo}/src/CLAUDE.md",
         "content": INJECTED, "rawContent": INJECTED,
         "contentDiffersFromDisk": False}},
    {"type": "prompt_snapshot", "systemPrompt": [INJECTED],
     "cliPrefix": INJECTED},
    {"type": "skill_listing", "content": INJECTED, "names": ["notes"],
     "skillCount": 1, "isInitial": True},
    {"type": "session_context",
     "context": {"userEmail": "student@school.edu", "gitStatus": INJECTED}},
    {"type": "selected_lines_in_ide", "content": INJECTED,
     "filename": "/home/s/other.py", "displayPath": "other.py",
     "ideName": "VS Code", "lineStart": 1, "lineEnd": 2},
    {"type": "hook_success", "stdout": INJECTED, "stderr": "",
     "content": INJECTED, "command": "python hook.py", "exitCode": 0,
     "durationMs": 3, "hookEvent": "Stop", "hookName": "Stop",
     "toolUseID": "t1"},
    # A prompt another agent queued is not the student's.
    {"type": "queued_command", "prompt": INJECTED, "isMeta": True,
     "commandMode": "prompt", "origin": {"kind": "peer", "body": INJECTED}},
    # Nor is a queued task notification (774 real rows, no origin).
    {"type": "queued_command", "prompt": "<task-notification>" + INJECTED,
     "commandMode": "task-notification",
     "timestamp": "2026-09-01T10:00:00.000Z"},
    {"type": "queued_command", "prompt": "<task-notification>" + INJECTED,
     "commandMode": "prompt"},
    {"type": "queued_command", "prompt": INJECTED},
    {"type": "brand_new_kind", "text": INJECTED},
    # `changes` is not an edit map here; its marker must stay a marker.
    {"type": "environment", "changes": [{"field": "cwd", "value": INJECTED}],
     "snapshot": {"workingDirectory": "/home/s", "isGitRepo": True}},
])
def test_claude_attachments_are_injected_context(repo, attachment):
    attachment = json.loads(json.dumps(attachment).replace("{repo}", str(repo)))
    write_raw(repo, "claude", "sessions/s.jsonl", [claude_row(
        "attachment", attachment=attachment,
        rendered=[{"content": INJECTED}])])
    redaction.redact_traces(repo)
    text = read_all_committed(repo, "claude")
    assert INJECTED not in text
    assert "student@school.edu" not in text
    row = json.loads(text)
    assert row["attachment"]["type"] == attachment["type"]
    assert row["rendered"] == WITHHELD_INJECTED
    for key, value in attachment.items():
        if isinstance(value, (list, dict)):  # containers hold text
            assert row["attachment"][key] == WITHHELD_INJECTED, key
    # Numbers and flags carry no text, so this rule keeps them (the
    # edit-record rule may still withhold them for an outside file).
    for key, value in attachment.items():
        if isinstance(value, (bool, int)):
            kept = row["attachment"][key]
            assert kept == value or kept.get("reason") == "external_path"


def test_claude_tool_output_that_starts_with_markup_is_judged_by_its_call(repo):
    """The markup rule is for what a student may have typed. A tool's
    output is kept or withheld by its call (review round 5): an in-project
    HTML file stays readable in every tier."""
    from tests._capture.agent_adapters import claude_normalize
    html = "<html>KEEP_HTML</html>"
    (repo / "index.html").write_text(html, encoding="utf-8")
    write_raw(repo, "claude", "sessions/s1.jsonl", [
        claude_row("user", cwd=str(repo), message={
            "role": "user", "content": TYPED}),
        claude_row("assistant", cwd=str(repo), message={
            "role": "assistant", "content": [{
                "type": "tool_use", "id": "tu1", "name": "Bash",
                "input": {"command": "cat index.html"}}]}),
        dict(claude_row("user", cwd=str(repo), message={
            "role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "tu1",
                "content": html}]}), uuid="u-result"),
    ])
    claude_normalize.normalize_all(repo)
    normalized = repo / ".ai-traces" / "claude" / "normalized" / "s1.jsonl"
    for _ in range(2):
        redaction.redact_traces(repo)
        events = [json.loads(line) for line in
                  normalized.read_text(encoding="utf-8").splitlines()]
        assert [e["text"] for e in events
                if e["event_type"] == "tool_result"] == [html]
        assert html in read_all_committed(repo, "claude")


def test_claude_sidechain_prompts_are_never_typed(repo):
    """A sub-agent's conversation (`isSidechain`) holds the prompts another
    agent wrote, in the student's own shape (review round 5)."""
    from tests._capture.agent_adapters import claude_normalize
    queued = {"type": "queued_command", "prompt": INJECTED,
              "commandMode": "prompt", "origin": {"kind": "human"}}
    write_raw(repo, "claude", "sessions/s1.jsonl", [
        claude_row("user", cwd=str(repo), isSidechain=True, message={
            "role": "user", "content": INJECTED}),
        claude_row("attachment", cwd=str(repo), isSidechain=True,
                   attachment=queued),
        dict(queue_row("enqueue", INJECTED), cwd=str(repo)),
    ])
    claude_normalize.normalize_all(repo)
    for _ in range(2):
        redaction.redact_traces(repo)
        for path in (repo / ".ai-traces").rglob("*.jsonl"):
            if "/raw/" not in str(path):
                assert INJECTED not in path.read_text(encoding="utf-8"), path


def test_claude_queued_prompt_the_student_typed_is_kept(repo):
    attachment = {"type": "queued_command", "prompt": TYPED,
                  "commandMode": "prompt", "origin": {"kind": "human"},
                  "timestamp": "2026-09-01T10:00:00.000Z"}
    write_raw(repo, "claude", "sessions/s.jsonl", [claude_row(
        "attachment", attachment=attachment, rendered=[{"content": TYPED}])])
    redaction.redact_traces(repo)
    row = json.loads(read_all_committed(repo, "claude"))
    assert row["attachment"] == attachment
    assert row["rendered"] == WITHHELD_INJECTED


def test_claude_meta_user_rows_are_injected_context(repo):
    """`isMeta` user rows are text Claude Code adds: skill bodies, command
    caveats, system reminders. The student's own prompt stays."""
    write_raw(repo, "claude", "sessions/s.jsonl", [
        claude_row("user", isMeta=True, message={"role": "user", "content": (
            "Base directory for this skill: /home/s/.claude/skills/x\n\n"
            + INJECTED)}),
        claude_row("user", isMeta=True, message={"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>" + INJECTED}]}),
        claude_row("user", message={"role": "user", "content": TYPED}),
    ])
    redaction.redact_traces(repo)
    text = read_all_committed(repo, "claude")
    rows = [json.loads(line) for line in text.splitlines()]
    assert INJECTED not in text
    assert rows[0]["message"]["content"] == WITHHELD_INJECTED
    assert rows[1]["message"]["content"] == [
        {"type": "text", "text": WITHHELD_INJECTED}]
    assert rows[2]["message"]["content"] == TYPED


def test_claude_normalized_tier_follows_the_injected_context_rule(repo):
    from tests._capture.agent_adapters import claude_normalize
    write_raw(repo, "claude", "sessions/s1.jsonl", [
        claude_row("user", cwd=str(repo), isMeta=True,
                   message={"role": "user", "content": INJECTED}),
        claude_row("attachment", cwd=str(repo), attachment={
            "type": "instructions", "files": [{
                "type": "User", "path": "/home/s/.claude/CLAUDE.md",
                "content": INJECTED}]}),
        claude_row("user", cwd=str(repo),
                   message={"role": "user", "content": TYPED}),
    ])
    claude_normalize.normalize_all(repo)
    normalized = repo / ".ai-traces" / "claude" / "normalized" / "s1.jsonl"
    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    for _ in range(2):
        redaction.redact_traces(repo)
        for path in (normalized, stream):
            body = path.read_text(encoding="utf-8")
            assert INJECTED not in body
            events = [json.loads(line) for line in body.splitlines()]
            prompts = [e["text"] for e in events
                       if e["event_type"] == "user_prompt"]
            assert prompts == [TYPED]


# Claude Code also writes text of its own into USER rows that are not
# `isMeta` (review round 4): background-task notifications, the output of a
# `/` command or of a `!` shell command, and sub-agent boilerplate, each in
# a markup wrapper; and messages from other agents, marked by `origin`. The
# student's own rows carry no origin (older versions) or a human one, and
# the only wrappers a student produces are the command they typed.
TASK_NOTE = ("<task-notification>\n<task-id>t1</task-id>\n<result>"
             + INJECTED + "</result>\n</task-notification>")
TYPED_COMMAND = ("<command-name>/review</command-name>\n"
                 "<command-message>review</command-message>\n"
                 "<command-args>PR 12 " + TYPED + "</command-args>")


def user_row(content, **fields):
    return claude_row("user", message={"role": "user", "content": content},
                      **fields)


@pytest.mark.parametrize("row", [
    user_row(TASK_NOTE, origin={"kind": "task-notification"},
             promptSource="system"),
    user_row(TASK_NOTE),
    user_row("<local-command-stdout>" + INJECTED + "</local-command-stdout>"),
    user_row("<bash-stdout>" + INJECTED + "</bash-stdout>"
             "<bash-stderr></bash-stderr>"),
    user_row([{"type": "text", "text": "<fork-boilerplate>" + INJECTED
               + "</fork-boilerplate>"}]),
    user_row("<brand-new-wrapper>" + INJECTED + "</brand-new-wrapper>"),
    # Unwrapped text another agent, or the tool, sent.
    user_row(INJECTED, origin={"kind": "task-notification"}),
    user_row(INJECTED, promptSource="system"),
    user_row(INJECTED, origin={"kind": "human", "note": INJECTED}),
    user_row(INJECTED, isMeta=True, origin={
        "kind": "peer", "from": "lead", "body": INJECTED}),
    # A compaction summary is the tool's text too.
    user_row(INJECTED, isCompactSummary=True, isVisibleInTranscriptOnly=True),
    # So is every prompt in a sub-agent's conversation (review round 5).
    user_row(INJECTED, isSidechain=True),
    user_row(INJECTED, isSidechain=True, origin={"kind": "human"},
             promptSource="typed"),
    # A typed wrapper with anything else in it is not the student's.
    user_row("<bash-input>ls</bash-input><bash-stdout>" + INJECTED
             + "</bash-stdout>"),
    user_row("<bash-input>ls</bash-input>" + INJECTED),
    user_row(TYPED_COMMAND + "\n<local-command-stdout>" + INJECTED
             + "</local-command-stdout>"),
    # A command name and message are short words; a command has both.
    user_row("<command-name>/x</command-name><command-message>" + INJECTED
             + " " + "y" * 80 + "</command-message>"),
    user_row("<command-name>/x</command-name><command-args>" + INJECTED
             + "</command-args>"),
])
def test_claude_user_rows_nobody_typed_are_withheld(repo, row):
    write_raw(repo, "claude", "sessions/s.jsonl", [row])
    redaction.redact_traces(repo)
    text = read_all_committed(repo, "claude")
    assert INJECTED not in text
    kept = json.loads(text)
    assert kept["type"] == "user" and kept["uuid"] == row["uuid"]
    if isinstance(row.get("origin"), dict):
        assert kept["origin"]["kind"] == row["origin"]["kind"]


@pytest.mark.parametrize("content, fields", [
    (TYPED, {}),
    (TYPED, {"origin": {"kind": "human"}, "promptSource": "typed"}),
    (TYPED, {"origin": {"kind": "human"}, "promptSource": "queued"}),
    (TYPED, {"origin": {"kind": "human"}, "promptSource": "sdk"}),
    (TYPED, {"promptSource": "suggestion_accepted"}),
    ([{"type": "text", "text": TYPED}], {}),
    # What a student produces with `!` and `/`: the command, not its output.
    ("<bash-input>ls src # " + TYPED + "</bash-input>", {}),
    (TYPED_COMMAND, {}),
    ("<command-message>review</command-message>\n"
     "<command-name>/review " + "x" * 3 + "</command-name>",
     {"origin": {"kind": "human"}}),
])
def test_claude_user_rows_the_student_typed_are_kept(repo, content, fields):
    write_raw(repo, "claude", "sessions/s.jsonl", [user_row(content, **fields)])
    redaction.redact_traces(repo)
    kept = json.loads(read_all_committed(repo, "claude"))
    assert "withheld" not in json.dumps(kept)
    # Kept, and path-anonymized like any text.
    assert isinstance(kept["message"]["content"], type(content))
    assert (TYPED in json.dumps(kept)) is (TYPED in json.dumps(content))


def test_claude_system_rows_keep_no_command_output(repo):
    """Newer Claude Code versions log `/` command output as a system row."""
    rows = [
        claude_row("system", subtype="local_command", content=(
            "<local-command-stdout>" + INJECTED + "</local-command-stdout>")),
        claude_row("system", subtype="local_command", content=INJECTED),
        claude_row("system", subtype="local_command", content=TYPED_COMMAND),
        claude_row("system", subtype="informational",
                   content="<new-wrapper>" + INJECTED + "</new-wrapper>"),
        claude_row("system", subtype="away_summary", content=TYPED),
    ]
    write_raw(repo, "claude", "sessions/s.jsonl", rows)
    redaction.redact_traces(repo)
    text = read_all_committed(repo, "claude")
    assert INJECTED not in text
    contents = [json.loads(line)["content"] for line in text.splitlines()]
    assert contents[:2] == [WITHHELD_INJECTED, WITHHELD_INJECTED]
    assert TYPED in contents[2]  # the command the student typed
    assert contents[3:] == [WITHHELD_INJECTED, TYPED]


# Claude Code's rows beside the conversation get a field schema each (review
# round 5): hook output, API errors, scheduled prompts, titles the tool
# made up and account ids are withheld, and so is any field, subtype or
# row type nobody measured. ACCOUNT stands for an account id.
ACCOUNT = "0b1c2d3e-aaaa-bbbb-cccc-0123456789ab"
CLAUDE_SIDE_ROWS = [
    claude_row("system", subtype="stop_hook_summary", hookCount=1,
               hookInfos=[{"command": INJECTED}], hookErrors=[INJECTED],
               hookAdditionalContext=[INJECTED], stopReason=INJECTED,
               hasOutput=True, preventedContinuation=False,
               level="suggestion", toolUseID="toolu_1"),
    claude_row("system", subtype="api_error", retryAttempt=1, maxRetries=10,
               retryInMs=500, source="stream", error={
                   "formatted": INJECTED, "message": INJECTED,
                   "isNetworkDown": False, "status": 529, "connection": {
                       "code": "ECONNRESET", "isSSLError": False,
                       "message": INJECTED}}),
    claude_row("system", subtype="scheduled_task_fire", content=INJECTED,
               prompt=INJECTED, cron="*/5 * * * *", cronKind="recurring",
               taskId="abc123", taskKind="prompt"),
    claude_row("system", subtype="model_refusal_fallback", content=INJECTED,
               apiRefusalExplanation=INJECTED, apiRefusalCategory="cyber",
               fallbackModel="claude-x", originalModel="claude-y",
               retractedMessageUuids=["u-1"]),
    claude_row("system", subtype="informational", content=INJECTED),
    claude_row("system", subtype="compact_boundary", content=INJECTED,
               compactMetadata={"trigger": "auto", "preTokens": 5,
                                "preservedSegment": {"headUuid": "u-1",
                                                     "note": INJECTED}}),
    claude_row("system", subtype="local_command", content=TYPED_COMMAND,
               commandRun={"command": "review", "args": INJECTED}),
    claude_row("system", subtype="brand_new_subtype", content=INJECTED),
    claude_row("system", subtype="turn_duration", durationMs=1,
               detail=INJECTED),
    claude_row("ai-title", aiTitle=INJECTED),
    claude_row("custom-title", customTitle=INJECTED),
    claude_row("agent-name", agentName=INJECTED),
    claude_row("last-prompt", lastPrompt=INJECTED, leafUuid="u-2"),
    claude_row("bridge-session", bridgeSessionId="session_1",
               lastSequenceNum=3, ownerAccountUuid=ACCOUNT,
               ownerOrganizationUuid=ACCOUNT),
    claude_row("pr-link", prNumber=12, prRepository="someone/" + INJECTED,
               prUrl="https://github.com/someone/" + INJECTED + "/pull/12"),
    claude_row("frame-link", artifactCount=1, title=INJECTED,
               frameUrl="https://claude.ai/frame/" + INJECTED),
    claude_row("cost-state", totalCostUSD=1.5, modelUsage={
        "claude-x": {"inputTokens": 3}, INJECTED: {}}),
    claude_row("artifact-autoreact-ledger", v=1, accountUuid=ACCOUNT,
               artifacts={INJECTED: 1}),
    claude_row("atis-latch", atis=INJECTED),
    claude_row("mode", mode="normal", note=INJECTED),
    claude_row("brand-new-row", text=INJECTED),
]


@pytest.mark.parametrize("row", CLAUDE_SIDE_ROWS)
def test_claude_rows_beside_the_conversation_keep_only_declared_fields(
        repo, row):
    write_raw(repo, "claude", "sessions/s.jsonl", [user_row(TYPED_COMMAND), row])
    redaction.redact_traces(repo)
    text = read_all_committed(repo, "claude")
    assert INJECTED not in text and ACCOUNT not in text
    kept = json.loads(text.splitlines()[1])
    assert (kept["type"], kept["uuid"]) == (row["type"], row["uuid"])
    assert kept["withheld"] is True


def test_claude_rows_beside_the_conversation_keep_their_settings(repo):
    rows = [
        claude_row("system", subtype="turn_duration", durationMs=5,
                   messageCount=2, cwd="{repo}/src", gitBranch="feature/x",
                   entrypoint="sdk-cli", version="2.1.3", isMeta=False,
                   isSidechain=False, userType="external", parentUuid="u-0",
                   timestamp="2026-09-01T10:00:00.000Z", slug="happy-otter",
                   level="info"),
        claude_row("system", subtype="away_summary", content=TYPED),
        claude_row("system", subtype="local_command", content=TYPED_COMMAND,
                   commandRun={"command": "review",
                               "args": "PR 12 " + TYPED}),
        claude_row("permission-mode", permissionMode="acceptEdits"),
        claude_row("mode", mode="normal"),
        claude_row("last-prompt", lastPrompt=TYPED, leafUuid="u-2"),
        claude_row("last-prompt", lastPrompt="/review PR 12 " + TYPED,
                   leafUuid="u-3"),
        claude_row("bridge-session", bridgeSessionId="session_01Ab",
                   lastSequenceNum=3),
        claude_row("pr-link", prNumber=12,
                   timestamp="2026-09-01T10:00:00.000Z"),
        claude_row("continued-in", continuedInSessionId="s2"),
        dict(queue_row("enqueue", TYPED), reason="interrupt"),
    ]
    rows = json.loads(json.dumps(rows).replace("{repo}", str(repo)))
    write_raw(repo, "claude", "sessions/s.jsonl",
              [user_row(TYPED), user_row(TYPED_COMMAND)] + rows)
    redaction.redact_traces(repo)
    kept = [json.loads(line) for line in
            read_all_committed(repo, "claude").splitlines()][2:]
    rows[0]["cwd"] = "src"
    assert kept == rows


def queue_row(operation, content=None):
    row = {"type": "queue-operation", "operation": operation,
           "sessionId": "s1", "timestamp": "2026-09-01T10:00:00.000Z"}
    if content is not None:
        row["content"] = content
    return row


def test_claude_queue_operations_keep_only_prompts_the_student_typed(repo):
    """A queue row has no origin, so -- like a Codex message item -- its
    text is kept only when it joins a prompt the student typed."""
    other_agent = "a message from another agent " + INJECTED
    rows = [
        user_row(TYPED),
        user_row(TYPED_COMMAND),
        user_row(other_agent, isMeta=True, origin={"kind": "coordinator"}),
        queue_row("enqueue", TYPED),
        queue_row("enqueue", "/review PR 12 " + TYPED),  # the line typed
        queue_row("enqueue", TASK_NOTE),
        queue_row("remove", other_agent),
        queue_row("enqueue", "never sent " + INJECTED),
        queue_row("dequeue"),
    ]
    write_raw(repo, "claude", "sessions/s.jsonl", rows)
    redaction.redact_traces(repo)
    text = read_all_committed(repo, "claude")
    assert INJECTED not in text
    queue = [json.loads(line) for line in text.splitlines()][3:]
    assert queue[0]["content"] == TYPED
    assert TYPED in queue[1]["content"]
    assert [row.get("content") for row in queue[2:]] == [
        WITHHELD_INJECTED, WITHHELD_INJECTED, WITHHELD_INJECTED, None]
    assert [row["operation"] for row in queue] == [
        "enqueue", "enqueue", "enqueue", "remove", "enqueue", "dequeue"]


def test_claude_wrappers_are_withheld_in_the_normalized_tier(repo):
    from tests._capture.agent_adapters import claude_normalize
    write_raw(repo, "claude", "sessions/s1.jsonl", [
        claude_row("user", cwd=str(repo), message={
            "role": "user", "content": TYPED}),
        claude_row("user", cwd=str(repo), origin={"kind": "task-notification"},
                   message={"role": "user", "content": INJECTED}),
        claude_row("user", cwd=str(repo), message={
            "role": "user", "content": "<bash-input>ls src</bash-input>"}),
        claude_row("user", cwd=str(repo), message={
            "role": "user", "content": "<bash-stdout>" + INJECTED
            + "</bash-stdout>"}),
        claude_row("system", cwd=str(repo), subtype="local_command",
                   content="<local-command-stdout>" + INJECTED
                   + "</local-command-stdout>"),
        claude_row("system", cwd=str(repo), subtype="local_command",
                   content=TYPED_COMMAND),
        claude_row("attachment", cwd=str(repo), attachment={
            "type": "queued_command", "prompt": TASK_NOTE,
            "commandMode": "task-notification"}),
        dict(queue_row("enqueue", TYPED), cwd=str(repo)),
        dict(queue_row("enqueue", TASK_NOTE), cwd=str(repo)),
    ])
    claude_normalize.normalize_all(repo)
    normalized = repo / ".ai-traces" / "claude" / "normalized" / "s1.jsonl"
    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    for _ in range(2):
        redaction.redact_traces(repo)
        for path in (normalized, stream):
            body = path.read_text(encoding="utf-8")
            assert INJECTED not in body
            events = [json.loads(line) for line in body.splitlines()]
            # A typed `!` command survives every pass.
            prompts = sorted(e["text"] for e in events
                             if e["event_type"] == "user_prompt")
            assert len(prompts) == 2 and prompts[1] == TYPED
            assert prompts[0].startswith("<bash-input>ls src")
            queued = [e["extras"]["raw_payload"].get("content") for e in events
                      if e["event_type"] == "raw_queue-operation"]
            assert sorted(queued, key=json.dumps) == sorted(
                [TYPED, WITHHELD_INJECTED], key=json.dumps)
            commands = [e["text"] for e in events
                        if e["extras"].get("subtype") == "local_command"]
            assert WITHHELD_INJECTED in commands
            assert any(TYPED in c for c in commands if isinstance(c, str))


def test_claude_slash_command_survives_the_normalized_tier(repo):
    """claude_normalize writes a typed `/` command as a `slash_command`
    event; the normalized tier used to withhold it as an unknown type."""
    from tests._capture.agent_adapters import claude_normalize
    queued = "/review PR 12 " + TYPED
    write_raw(repo, "claude", "sessions/s1.jsonl", [
        claude_row("user", cwd=str(repo), message={
            "role": "user", "content": TYPED_COMMAND}),
        dict(queue_row("enqueue", queued), cwd=str(repo)),
    ])
    claude_normalize.normalize_all(repo)
    normalized = repo / ".ai-traces" / "claude" / "normalized" / "s1.jsonl"
    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    for _ in range(2):
        redaction.redact_traces(repo)
        for path in (normalized, stream):
            events = [json.loads(line) for line in
                      path.read_text(encoding="utf-8").splitlines()]
            commands = [e for e in events if e["event_type"] == "slash_command"]
            assert [e["text"] for e in commands] == [TYPED_COMMAND]
            assert commands[0]["extras"]["slash_command"] == "/review"
            assert [e["extras"]["raw_payload"]["content"] for e in events
                    if e["event_type"] == "raw_queue-operation"] == [queued]


# An edit record is external if ANY of its target keys names an outside
# file, and a target we cannot read as a path fails closed. `{OUT}` and
# `{IN}` are replaced with an outside and an in-project path, `{REPO}` with
# the repo root.
@pytest.mark.parametrize("record", [
    # Only the SECOND target key is external.
    {"file_path": "{IN}", "filePath": "{OUT}", "content": EDIT_NEW},
    # Targets that are not plain strings but still name an outside file.
    {"file_path": ["{IN}", "{OUT}"], "content": EDIT_NEW},
    {"file_path": {"real": "{OUT}"}, "content": EDIT_NEW},
    # Targets that name nothing we can check.
    {"file_path": None, "content": EDIT_NEW},
    {"file_path": 42, "content": EDIT_NEW},
    {"file_path": "   ", "content": EDIT_NEW},
    {"file_path": [], "content": EDIT_NEW},
    {"file_path": {"line": 3}, "content": EDIT_NEW},
    # A space in the path must not make an outside target look in-project.
    {"file_path": "$HOME/a b/x.py", "content": EDIT_NEW},
    {"file_path": "%USERPROFILE%\\My Files\\x.py", "content": EDIT_NEW},
    {"file_path": "$UNSET_VAR_7Q/a b.py", "content": EDIT_NEW},
    {"file_path": "secret dir/key.txt", "content": EDIT_NEW},
    {"file_path": "{REPO}/secret dir/key.txt", "content": EDIT_NEW},
    {"file_path": "my notes.env", "content": EDIT_NEW},
])
def test_edit_record_with_any_external_or_unreadable_target_is_withheld(
        repo, tmp_path_factory, record):
    with (repo / ".gitignore").open("a", encoding="utf-8") as f:
        f.write("secret dir/\n*.env\n")
    outside = str(tmp_path_factory.mktemp("outside") / "private.py")
    record = json.loads(json.dumps(record)
                        .replace("{OUT}", outside)
                        .replace("{IN}", str(repo / "src" / "main.py"))
                        .replace("{REPO}", str(repo)))

    out = json.dumps(redaction.Redactor(repo).redact(record))

    assert EDIT_NEW not in out
    assert outside not in out


@pytest.mark.parametrize("record", [
    {"file_path": ["{IN}"], "content": EDIT_NEW},
    {"file_path": "{IN}", "path": "src", "content": EDIT_NEW},
    # A tool's JSON schema: the "target" is a description, not a path.
    # Real sessions carry hundreds of these, and they are not file text.
    {"file_path": {"type": "string",
                   "description": "The absolute path to the file"},
     "content": {"type": "string", "description": EDIT_NEW}},
])
def test_edit_record_whose_targets_are_all_in_project_is_kept(repo, record):
    record = json.loads(json.dumps(record)
                        .replace("{IN}", str(repo / "src" / "main.py")))

    out = json.dumps(redaction.Redactor(repo).redact(record))

    assert EDIT_NEW in out


def test_in_project_edit_text_is_kept_when_the_repo_path_has_a_space(
        tmp_path):
    spaced_repo = make_repo(tmp_path / "My Projects" / "repo")
    target = str(spaced_repo / "src" / "main.py")
    write_raw(spaced_repo, "claude", "sessions/s.jsonl",
              [edited_text_file_row(target)])
    write_raw(spaced_repo, "cursor", "hooks/hooks-c1.jsonl",
              [cursor_edit_row(target)])

    redaction.redact_traces(spaced_repo)

    assert EDIT_NEW in read_all_committed(spaced_repo, "claude")
    assert EDIT_NEW in read_all_committed(spaced_repo, "cursor")


# --- Layer 4c: prompts and agent messages are NEVER withheld -------------
#
# Instructor scope: "I only care about keeping file content THE AGENT READS
# out of the transcript. Anything the USER pastes in is fair game."

@pytest.mark.parametrize("rows", [
    [{"type": "event_msg", "payload": {
        "type": "user_message", "message": "KEEPME I pasted /opt/x/y.py here"}}],
    [{"type": "event_msg", "payload": {
        "type": "agent_message", "message": "KEEPME see /opt/x/y.py"}}],
    # A user message the student typed (its typed record is the first row).
    # Developer messages and injected user-role text are covered by
    # test_injected_context_is_withheld_and_typed_text_kept.
    [{"type": "event_msg", "payload": {
        "type": "user_message", "message": "KEEPME /opt/x/y.py"}},
     {"type": "response_item", "payload": {
         "type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "KEEPME /opt/x/y.py"}]}}],
])
def test_prompts_and_agent_messages_are_never_withheld(repo, rows):
    text, _ = run_rows(repo, rows)
    assert "KEEPME" in text
    assert "withheld" not in text
    # Path anonymization still applies -- identity is a separate concern.
    assert "/opt/x/y.py" not in text
    assert "<external:" in text


def test_prompt_beside_a_tainted_call_is_still_kept(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "p.py"
    outside.write_text("x\n", encoding="utf-8")
    text, rows = run_rows(repo, [
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": "KEEPME please read it"}},
        js_exec(f"cat {outside}"),
        js_output(),
    ])
    assert "KEEPME please read it" in text
    assert READ_SECRET not in text


# --- Layer 4d: DEFAULT DENY on unrecognized adapters ---------------------
#
# The taint join can only fire on shapes it recognizes, so an adapter whose
# vocabulary nobody has measured used to get path anonymization ONLY and its
# tool output passed through. vscode_chat is exactly that case: registered,
# shipping, committed -- and unverified. Recognition is therefore default
# deny: unknown structure means withheld, never passed through.

def test_recognized_vocabularies_are_pinned():
    """Adding a vocabulary is a SECURITY decision -- it means "the join is
    verified to fire on this shape". This test exists so an addition shows
    up as a deliberate edit in review, never as a quiet expansion."""
    assert redaction.RECOGNIZED_VOCABULARIES == frozenset({
        "codex_rollout",     # call_id join: 3,530 pairs, 0 orphans
        "claude_session",    # tool_use.id -> tool_result.tool_use_id: 85/85
        "capture_hook_log",  # our own hook records; edit text gated by target
    })


def test_vscode_chat_session_content_is_withheld_not_committed(
        repo, tmp_path_factory):
    """A realistic VS Code chat-panel session: real JSON that parses fine,
    with a tool invocation reading an out-of-project file. Its vocabulary is
    unverified, so its content must not be committed."""
    outside = tmp_path_factory.mktemp("other") / "config"
    outside.write_text("x\n", encoding="utf-8")
    session = {
        "version": 3,
        "requesterUsername": "student",
        "requests": [{
            "message": {"text": "read my ssh config"},
            "response": [{"value": "Here is what I found:"}],
            "result": {"timings": {"totalElapsed": 1200}},
            "toolInvocationSerialized": {
                "toolId": "copilot_readFile",
                "toolSpecificData": {"rawInput": {"filePath": str(outside)}},
                "resultDetails": [{"uri": {"path": str(outside)}}],
                "content": [{"value": READ_SECRET}],
            },
        }],
    }
    path = (repo / ".ai-traces" / "vscode_chat" / "raw" / "sessions"
            / "abc123.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session), encoding="utf-8")

    redaction.redact_traces(repo)

    out_dir = repo / ".ai-traces" / "vscode_chat" / redaction.REDACTED_SUBDIR
    committed = "".join(p.read_text(encoding="utf-8")
                        for p in out_dir.rglob("*") if p.is_file())
    assert READ_SECRET not in committed
    assert str(outside) not in committed
    marker = out_dir / "sessions" / ("abc123.json" + redaction.WITHHELD_SUFFIX)
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["withheld"] is True
    assert data["reason"] == "unrecognized_record_vocabulary"
    assert data["bytes"] > 0

    health = capture_health.build_health(repo, present_names=["vscode_chat"])
    assert health["adapters"]["vscode_chat"]["withheld_unrecognized"] is True
    assert health["adapters"]["codex"]["withheld_unrecognized"] is False


def test_unknown_adapter_transcript_fails_closed(repo):
    """A future adapter nobody has measured degrades to withheld."""
    path = (repo / ".ai-traces" / "some_new_tool" / "raw" / "sessions"
            / "s.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"kind": "toolResult", "text": READ_SECRET}) + "\n", encoding="utf-8")
    redaction.redact_traces(repo)
    out_dir = (repo / ".ai-traces" / "some_new_tool"
               / redaction.REDACTED_SUBDIR)
    committed = "".join(p.read_text(encoding="utf-8")
                        for p in out_dir.rglob("*") if p.is_file())
    assert READ_SECRET not in committed
    assert (out_dir / "sessions" / ("s.jsonl" + redaction.WITHHELD_SUFFIX)).exists()


def test_recognized_vendor_transcripts_are_not_over_withheld(repo):
    """Guard against default-deny going too far: the two measured
    vocabularies, and our own hook records, still commit their content."""
    codex_rows = [{"type": "event_msg",
                   "payload": {"type": "user_message", "message": "KEEP_CODEX"}}]
    write_rollout(repo, codex_rows)
    claude = repo / ".ai-traces" / "claude" / "raw" / "sessions" / "s.jsonl"
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(json.dumps(
        {"type": "user", "uuid": "u1", "sessionId": "s1",
         "message": {"content": [{"type": "text", "text": "KEEP_CLAUDE"}]}})
        + "\n", encoding="utf-8")
    hooks = repo / ".ai-traces" / "cursor" / "raw" / "hooks" / "edits-s1.jsonl"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text(json.dumps(
        {"adapter_name": "cursor", "event_type": "file_edit",
         "session_id": "s1", "file": "src/main.py", "edit_count": 2,
         "content_sha256": "abc"}) + "\n", encoding="utf-8")

    redaction.redact_traces(repo)

    assert "KEEP_CODEX" in redacted_text(repo)
    assert "KEEP_CLAUDE" in (
        repo / ".ai-traces" / "claude" / redaction.REDACTED_SUBDIR
        / "sessions" / "s.jsonl").read_text(encoding="utf-8")
    assert "file_edit" in (
        repo / ".ai-traces" / "cursor" / redaction.REDACTED_SUBDIR
        / "hooks" / "edits-s1.jsonl").read_text(encoding="utf-8")


def test_aider_chat_transcript_is_withheld_but_user_input_is_kept(repo):
    """Prose transcripts have no vocabulary to verify, so agent-read content
    in them cannot be bounded -- withheld. The input history is different:
    every line is something the STUDENT typed (rule 2 keeps it)."""
    (repo / ".aider.chat.history.md").write_text(
        "#### explain this\n" + READ_SECRET + "\n", encoding="utf-8")
    (repo / ".aider.input.history").write_text(
        "# 2026-08-14\n+explain the parser to me\n", encoding="utf-8")
    redaction.redact_traces(repo)
    out = repo / ".ai-traces" / "aider" / redaction.REDACTED_SUBDIR
    assert (out / (".aider.chat.history.md"
                   + redaction.WITHHELD_SUFFIX)).exists()
    assert not (out / ".aider.chat.history.md").exists()
    kept = (out / ".aider.input.history").read_text(encoding="utf-8")
    assert "explain the parser to me" in kept


# --- Layer 4e: the NORMALIZED tier ---------------------------------------
#
# `normalized/*.jsonl` and `interaction-stream.jsonl` are OUR schema, and
# they are what an instructor actually reads. Withholding there depends on
# the taint join recognizing our event types, so those types get the same
# default-deny + pinned-membership treatment as the vendor vocabularies:
# one token of drift must fail closed, not leak.

def write_normalized(repo, rows, adapter="codex", name="s1.jsonl"):
    path = repo / ".ai-traces" / adapter / "normalized" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def normalized_event(event_type, **fields):
    """The shape ai_traces.make_event produces."""
    base = {
        "adapter_name": "codex", "session_id": "sess-1", "turn_id": "turn-1",
        "ts": "2026-08-14T10:00:00Z", "event_type": event_type,
        "cwd": "", "evidence_refs": [".ai-traces/codex/raw/r.jsonl:3"],
        "event_id": "e" + event_type,
    }
    base.update(fields)
    return base


def test_normalized_event_types_are_pinned():
    """Adding an event type asserts "the taint join handles this record
    shape". It must be a visible edit in review, never a quiet one."""
    assert redaction.NORMALIZED_EVENT_TYPES == frozenset({
        "session_start", "session_end", "turn_start", "turn_end",
        "user_prompt", "slash_command", "assistant_message", "reasoning",
        "system_message", "attachment", "unclassified_text",
        "tool_start", "tool_result",
        "approval_request", "hook_event",
        "file_edit",
    })
    assert redaction.normalized_event_recognized("raw_response_item")
    assert not redaction.normalized_event_recognized("tool_output")
    assert not redaction.normalized_event_recognized("")


def test_taint_join_fires_on_a_normalized_call_result_pair(repo,
                                                           tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "passwd"
    outside.write_text("x\n", encoding="utf-8")
    path = write_normalized(repo, [
        normalized_event("tool_start", tool_use_id="c1", tool_name="exec",
                         command=f"cat {outside}"),
        normalized_event("tool_result", tool_use_id="c1",
                         text="root:x:0:0:ROOTLEAK"),
    ])
    redaction.redact_traces(repo)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert "ROOTLEAK" not in path.read_text(encoding="utf-8")
    assert rows[1]["withheld"] is True
    assert rows[1]["reason"] == "external_read"


def test_normalized_in_project_result_survives_verbatim(repo):
    path = write_normalized(repo, [
        normalized_event("tool_start", tool_use_id="c2", tool_name="exec",
                         command="cat src/main.py"),
        normalized_event("tool_result", tool_use_id="c2", text="print('hi')"),
    ])
    redaction.redact_traces(repo)
    body = path.read_text(encoding="utf-8")
    assert "print('hi')" in body
    assert "withheld" not in body


def test_normalized_event_type_drift_fails_closed(repo, tmp_path_factory):
    """ONE TOKEN off ("tool_output" instead of "tool_result") must not leak.

    This is the drift case: a typo, a refactor, or a normalizer nobody has
    written yet (Cursor's) emitting an event type the join does not know."""
    outside = tmp_path_factory.mktemp("other") / "passwd"
    outside.write_text("x\n", encoding="utf-8")
    path = write_normalized(repo, [
        normalized_event("tool_start", tool_use_id="c3",
                         command=f"cat {outside}"),
        normalized_event("tool_output", tool_use_id="c3",
                         text="root:x:0:0:ROOTLEAK"),
    ])
    redaction.redact_traces(repo)
    body = path.read_text(encoding="utf-8")
    assert "ROOTLEAK" not in body
    rows = [json.loads(l) for l in body.splitlines() if l.strip()]
    assert rows[1]["withheld"] is True
    assert rows[1]["reason"] == "unrecognized_event_type"


def test_withheld_normalized_record_stays_attributable(repo,
                                                       tmp_path_factory):
    """A withheld record the attribution pass cannot identify is a gap it
    cannot label -- cyq.6 consumes exactly these fields."""
    outside = tmp_path_factory.mktemp("other") / "f.txt"
    outside.write_text("x\n", encoding="utf-8")
    path = write_normalized(repo, [
        normalized_event("tool_start", tool_use_id="c4",
                         command=f"cat {outside}"),
        normalized_event("tool_result", tool_use_id="c4", text="LEAKME"),
    ])
    redaction.redact_traces(repo)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    withheld = rows[1]
    assert "LEAKME" not in json.dumps(rows)
    for key in ("event_type", "adapter_name", "session_id", "turn_id", "cwd",
                "evidence_refs", "event_id"):
        assert key in withheld, f"lost identity key: {key}"
    assert withheld["event_type"] == "tool_result"
    assert withheld["evidence_refs"] == [".ai-traces/codex/raw/r.jsonl:3"]


def test_interaction_stream_is_covered_by_the_same_rules(repo,
                                                         tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "g.txt"
    outside.write_text("x\n", encoding="utf-8")
    stream = repo / ".ai-traces" / "interaction-stream.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    with stream.open("w", encoding="utf-8") as f:
        f.write(json.dumps(normalized_event(
            "tool_start", tool_use_id="c5", command=f"cat {outside}")) + "\n")
        f.write(json.dumps(normalized_event(
            "tool_result", tool_use_id="c5", text="STREAMLEAK")) + "\n")
    redaction.redact_traces(repo)
    body = stream.read_text(encoding="utf-8")
    assert "STREAMLEAK" not in body
    assert str(outside) not in body


# --- Layer 5: fail closed on unparseable input ---------------------------

def test_unparseable_transcript_is_withheld_not_committed(repo):
    path = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / "rollout-2.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json at all, and mentions " + SECRET + "\n",
                    encoding="utf-8")
    report = redaction.redact_traces(repo)

    out_dir = repo / ".ai-traces" / "codex" / redaction.REDACTED_SUBDIR / "rollouts"
    committed = "".join(
        p.read_text(encoding="utf-8") for p in out_dir.rglob("*") if p.is_file())
    assert SECRET not in committed
    assert "not json at all" not in committed
    marker = out_dir / "rollout-2.jsonl.withheld.json"
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["withheld"] is True
    assert data["reason"] == "unparseable"
    assert data["bytes"] > 0
    assert report.unparseable


def test_capture_health_surfaces_withheld_unparseable(repo):
    path = repo / ".ai-traces" / "codex" / "raw" / "rollouts" / "rollout-3.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json\n", encoding="utf-8")
    redaction.redact_traces(repo)
    health = capture_health.build_health(repo, present_names=["codex"])
    assert health["adapters"]["codex"]["withheld_unparseable"] is True
    assert health["adapters"]["claude"]["withheld_unparseable"] is False


def test_redaction_is_idempotent(repo):
    """Byte-stable output across runs -- tree-SHA dedupe depends on it."""
    write_rollout(repo, [
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": f"token {SECRET}"}},
    ])
    redaction.redact_traces(repo)
    first = redacted_text(repo)
    redaction.redact_traces(repo)
    assert redacted_text(repo) == first


# --- Layer 5b: cost and crash safety -------------------------------------
#
# redact_traces runs on EVERY snapshot trigger (pytest, sitecustomize exit,
# post-commit, agent Stop hooks), and a hook kills the snapshot child after
# 25 seconds. So an unchanged input must cost next to nothing, and a run
# killed mid-way must never leave a half-written derivative behind.

def count_redaction_work(monkeypatch):
    calls = []
    real = redaction.redact_jsonl_rows

    def spy(redactor, rows, **options):
        calls.append(len(rows))
        return real(redactor, rows, **options)

    monkeypatch.setattr(redaction, "redact_jsonl_rows", spy)
    return calls


def test_unchanged_raw_files_are_not_redacted_again(repo, monkeypatch):
    import os
    raw = write_rollout(repo, [
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": "first"}}])
    redaction.redact_traces(repo)
    first = redacted_text(repo)
    calls = count_redaction_work(monkeypatch)

    # Unchanged input: no redaction work at all.
    report = redaction.redact_traces(repo)
    assert calls == []
    assert redacted_text(repo) == first
    assert report.skipped_unchanged

    # Touched but byte-identical (a re-copy): the hash confirms, still skipped.
    stat = raw.stat()
    os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    redaction.redact_traces(repo)
    assert calls == []

    # A deleted derivative is regenerated.
    out = (repo / ".ai-traces" / "codex" / redaction.REDACTED_SUBDIR
           / "rollouts" / "rollout-1.jsonl")
    out.unlink()
    redaction.redact_traces(repo)
    assert calls == [1]
    assert redacted_text(repo) == first

    # Changed content is redacted again.
    with raw.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "event_msg", "payload": {
            "type": "agent_message", "message": "second"}}) + "\n")
    redaction.redact_traces(repo)
    assert calls == [1, 2]
    assert "second" in redacted_text(repo)

    # A policy input changing (here: .gitignore) invalidates the cache.
    with (repo / ".gitignore").open("a", encoding="utf-8") as f:
        f.write("notes/\n")
    redaction.redact_traces(repo)
    assert calls == [1, 2, 2]


def test_a_killed_write_never_leaves_a_half_written_derivative(
        repo, monkeypatch):
    raw = write_rollout(repo, [
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": "v1 " * 50}}])
    redaction.redact_traces(repo)
    first = redacted_text(repo)
    with raw.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "event_msg", "payload": {
            # Much longer than v1, so "half the new payload" cuts into v2.
            "type": "agent_message", "message": "v2 " * 500}}) + "\n")

    real_write_text = Path.write_text

    def killed_mid_write(self, data, *args, **kwargs):
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)
        raise KeyboardInterrupt("simulated kill")

    monkeypatch.setattr(Path, "write_text", killed_mid_write)
    with pytest.raises(KeyboardInterrupt):
        redaction.redact_traces(repo)
    monkeypatch.undo()

    # The committed derivative is still the complete previous version.
    assert redacted_text(repo) == first

    # A hard kill (SIGKILL) cannot clean up its temp file; the next pass
    # must, because redacted/ is force-staged and would commit it.
    out_dir = (repo / ".ai-traces" / "codex" / redaction.REDACTED_SUBDIR
               / "rollouts")
    stray = out_dir / (".rollout-1.jsonl.4242" + redaction.TEMP_SUFFIX)
    stray.write_text("partial", encoding="utf-8")
    redaction.redact_traces(repo)
    assert "v2" in redacted_text(repo)
    leftovers = [p.name for p in (repo / ".ai-traces").rglob("*")
                 if p.name.endswith(redaction.TEMP_SUFFIX)]
    assert leftovers == []


def test_huge_path_shaped_blobs_are_not_resolved_on_disk(repo):
    """Base64 image data matches the absolute-path pattern (letters, digits,
    `+` and `/`). A real 52 MB rollout held a 3.8 MB such "path", and
    resolving it on disk took minutes. No real path is that long, so it is
    treated as external without touching the filesystem."""
    import time
    # ~1.4 MB: resolving this on disk once takes ~10s, so the test fails
    # if even a single lookup happens.
    blob = "data:image/png;base64,/" + "/".join(["iVBORw0KGgo"] * 120_000)
    write_rollout(repo, [
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": "KEEP " + blob}}])
    start = time.perf_counter()
    redaction.redact_traces(repo)
    assert time.perf_counter() - start < 3.0
    text = redacted_text(repo)
    assert "KEEP" in text
    assert "iVBORw0KGgo/iVBORw0KGgo" not in text  # rendered as a sentinel


def test_credential_scrub_is_linear_on_long_identifier_runs():
    """Measured on real rollouts: this one pattern took 10s of a 20s pass
    on a 25MB file, because its unbounded prefix was retried from every
    word boundary inside long dash-joined runs."""
    import time
    start = time.perf_counter()
    # Sized so the old quadratic pattern needs several seconds; the linear
    # one needs milliseconds.
    redaction.scrub_secrets("a-" * 6_000)
    redaction.scrub_secrets("token" * 4_000)
    assert time.perf_counter() - start < 2.0
    # Long names are still scrubbed -- no length cap was traded for speed.
    long_name = "X" * 5000 + "_TOKEN=abcdefghijklmnop"
    assert "abcdefghijklmnop" not in redaction.scrub_secrets(long_name)
    assert "abcdefghijklmnop" not in redaction.scrub_secrets(
        "a=token=abcdefghijklmnop")


# --- Layer 6: staging changes + pinned anti-evasion control --------------

def test_raw_dirs_are_unstaged_so_only_the_derivative_is_committed(repo):
    (repo / ".ai-traces" / "codex" / "raw" / "rollouts").mkdir(parents=True)
    (repo / ".ai-traces" / "cursor" / "raw" / "hooks").mkdir(parents=True)
    paths = redaction.raw_unstage_paths(repo)
    # Hook-only adapters (cursor has no pull-side adapter class) must be
    # covered too -- that is why this is derived from disk, not from the
    # adapter registry.
    assert ".ai-traces/codex/raw" in paths
    assert ".ai-traces/cursor/raw" in paths


def test_heartbeat_stays_committed_in_this_repo():
    """Gitignore `raw/` specifically -- ignoring the whole adapter dir would
    destroy the evidence-AND-no-heartbeat untrusted-hooks conjunction."""
    def ignored(rel):
        return subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", rel],
            cwd=REPO_ROOT, capture_output=True).returncode == 0

    assert ignored(".ai-traces/codex/raw/rollouts/r.jsonl")
    assert not ignored(".ai-traces/codex/heartbeat/hb.json")
    assert not ignored(".ai-traces/codex/redacted/rollouts/r.jsonl")
    assert not ignored(".ai-traces/capture-health.json")


def test_force_add_anti_evasion_control_is_still_in_place():
    """Attack K: a student gitignoring their own trace dir must not hide it.

    Redaction governs student content QUOTED INSIDE traces; force_paths
    governs the trace FILES THEMSELVES. Do not merge the two rules.
    """
    for adapter in (CodexAdapter(), ClaudeAdapter(), VSCodeChatAdapter()):
        assert ".ai-traces" in adapter.force_paths(), adapter.name
