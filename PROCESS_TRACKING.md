# Process Tracking in This Assignment

This project automatically records a snapshot of your code when you run the tests, when you run any other Python script in the project venv, when you make a git commit, and — if a Codex, Claude Code, or Cursor project hook is active — when your AI assistant finishes a turn. Locally these snapshots live on a dedicated ref `refs/auto-track/snapshots` and are pushed to the `auto-track` branch on your remote. They do **not** land on `main` or your feature branches, so your normal `git log` on a working branch only shows your intentional commits. The `auto-track` branch is visible in your repo's branch list on GitHub — that's expected; it's where your instructor reads your development history from.

Audit your local trail with `python -m tests._capture.audit`.

## When capture fires

A snapshot is recorded when:

1. **You run `pytest` or `python run_tests.py`** — the standard pytest trigger. Each snapshot's body lists `trigger: pytest`.
2. **You run any Python script in the project venv** — for example `python src/foo.py`, an IDE green-arrow run, the debugger, or a REPL session. Triggered by an `atexit` hook installed into your venv's `sitecustomize.py` the first time you run pytest. The hook only fires when the working directory is inside the project, and it skips itself when pytest is already running so you don't get double snapshots. Snapshot body lists `trigger: sitecustomize`.
3. **You make a git commit in this repo** — fires `.githooks/post-commit`, which is wired up via `core.hooksPath = .githooks` (a per-repo git setting; it does not affect any other git repo on your machine). Snapshot body lists `trigger: git_post_commit`.
4. **An AI assistant finishes a turn in this repo** — this project ships hooks for three assistants. Each records the turn under `.ai-traces/<tool>/` and takes a snapshot through the same manual CLI seam as `python -m tests._capture snapshot`, so the snapshot body lists `trigger: manual` plus a line naming which hook fired, not a dedicated trigger:
   - **Codex** (`.codex/hooks/`) — `manual_reason: codex_stop`. Active if (and only if) you have approved this project's hooks once from the Codex command line (`codex`, then `/hooks`). Until you approve them the hooks are completely inactive — Codex never runs them, and nothing here changes. Approving is worth it: it is what proves your effort was yours.
   - **Claude Code** (`.claude/hooks/`) — `manual_reason: claude_stop`. Active when Claude Code runs this project's hook settings (`.claude/settings.json`), which it does once you trust the project folder.
   - **Cursor** (`.cursor/hooks/`) — `manual_reason: cursor_stop`. Active when your Cursor version runs this project's `.cursor/hooks.json`. These hooks record the prompts you type and the agent's file edits; Tab completions are recorded as file and size only.

   You can read every one of those scripts; they are short, they only write inside this folder, and they never block or alter what the assistant does for you.

None of these triggers install anything outside the project directory. The sitecustomize hook is only installed when your venv lives at `venv/` or `.venv/` inside this folder — a venv stored elsewhere (`~/.virtualenvs/...`, conda, virtualenvwrapper, or a venv belonging to another project) is rejected so capture infrastructure isn't dropped into a venv shared with your other work. The post-commit hook lives inside `.git/config` and `.githooks/` (per-repo only). `run_tests.py` refuses to run unless you're in the local venv and prints the exact setup commands.

## What is captured

Files under `src/`, `tests/`, and `.ai-traces/` are staged into the commit, along with the shipped guardrail files (`AGENTS.md`, `.codex/config.toml`, `AI_POLICY.md`). The commit message carries this metadata:

- A timestamp and session ID
- Test pass/fail counts and duration
- How many lines were added or removed since the previous run
- Your Python version and OS type
- A one-way hash of your machine's hostname (it is used to tell apart your home laptop from a lab machine in a pattern-of-use sense; it isn't designed to be inverted at scale, but be aware it's a plain, unsalted hash — if your instructor already has a specific hostname to check against, they can confirm that guess by hashing it themselves and comparing, the same way they'd verify any password hash)

Local AI-agent traces (`.ai-traces/**/*.jsonl`) are also captured when present. Codex CLI sessions land under `.ai-traces/codex/`, Claude Code sessions under `.ai-traces/claude/`, and VS Code chat-panel sessions (GitHub Copilot Chat and similar) under `.ai-traces/vscode_chat/`. Each adapter pulls from the agent's per-user storage (`~/.codex/sessions/`, `~/.claude/projects/`, and VS Code's `workspaceStorage/<id>/chatSessions/`) and filters by the repo's path. The Codex and Claude adapters parse events into the merged `.ai-traces/interaction-stream.jsonl`; the VS Code chat adapter stores the raw session JSONL only because VS Code's mutation-log format changes between releases. See [AI_POLICY.md](AI_POLICY.md) for the full AI policy.

**Capture never scans your browsing history or reads files outside this project on its own — external content only ever enters a transcript when an AI assistant itself read it, and even then only the tool call is kept (see below).** That said, this is not "nothing about your machine": as listed above, a small amount of machine metadata (your Python version, OS type, and a one-way hostname hash) is captured, and credential scrubbing below is a best-effort filter for known patterns, not a guarantee that nothing sensitive can ever slip through.

### Transcripts are redacted before they are committed

The raw files your AI tool writes are staging only: they stay on your computer under `.ai-traces/<tool>/raw/`, which is gitignored and never committed. What gets committed is a redacted copy under `.ai-traces/<tool>/redacted/` (plus the redacted merged stream, and the redacted per-session event files under `.ai-traces/<tool>/normalized/`). Those event files record, on each tool call, whether its output may be kept (`output_kept`), and later redaction passes trust that record. The folder is not gitignored, so anything that can write files in your project could add a made-up entry there, just as it could fabricate any other trace entry. The rule is: **the privacy boundary is the submission boundary** — you submit this whole project, so in-project content is kept as-is; anything outside it is removed.

Before anything is committed:

- **Credentials are scrubbed** — API keys, tokens, private keys, `PASSWORD=...` assignments — including inside your own prompts, replaced with `<redacted:...>`.
- **Your username and folder layout are removed** — absolute in-project paths become project-relative; paths outside the project become an opaque, repeatable tag like `<external:9f3c1a2b>`.
- **Command and tool output is kept only when it can only show your project.** The call itself is always kept, so the record has no invisible holes and shows what ran; its output is kept only for a recognized **read-only** command or tool reading project files (`cat src/main.py`, `sed -n '1,50p' src/main.py`, `git status`, plain `rg`, the assistant's file-reading and editing tools on a project file) and for running **your own project code** (`python src/main.py` for a file git tracks — one you have added or committed — `pytest`, `python run_tests.py`, also when run through this project's own `venv/` or `.venv/`, such as `venv/bin/python` or `.venv/bin/pytest`, which count the same as a plain `python` or `pytest`). All other output is marked `{"withheld": true}`: inline code the assistant writes (`python -c ...`, `node -e ...`), searches through whole folders (`grep -r`), wildcards (`cat *.txt`), archivers, anything piped into a shell, `env` and `printenv`, any program or tool the redaction pass does not recognize, and reads of files outside the project, gitignored files, or files the capture layer never commits (`.codex/auth.json`), listed in `.gitignore` or not. A file you gitignored (a `.env`, `venv/`) is usually never captured either, because no adapter reads its contents in the first place — but gitignoring something is not a guaranteed way to keep it out: `.codex` and `.ai-traces` are **force-staged** (`git add -Af`) into every snapshot, which overrides *any* `.gitignore` rule that would otherwise exclude them — including one you add yourself — so those two directories cannot be hidden from capture by editing `.gitignore`. The aider adapter uses the same mechanism on individual files: `.aider.chat.history.md` and `.aider.input.history` are force-added even though the template's own `.gitignore` excludes `.aider*` by default (see `tests/_capture/agent_adapters/aider.py`). A small, explicit set of sensitive paths inside those force-staged directories is then dropped again before the commit — `.codex/auth.json`, and each tool's `raw/` transcript subfolder (only the redacted derivative next to it, e.g. `.ai-traces/aider/redacted/`, ends up in the commit).
- **Email addresses recorded by the tool are stripped.**
- **Tool-injected instructions are not captured.** Text your AI tool adds to a conversation on its own — its system prompt, the instruction files it loads (`AGENTS.md`, `CLAUDE.md`, including global ones such as `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md`), environment, tool and skill descriptions, hook output, notices about background tasks, error messages from the AI service, conversation titles the tool makes up, your account ids, the output of commands you run inside the tool (Claude Code's `!` and `/` commands), and the context Codex Desktop and the VS Code extension put in front of the message you type (the page open in the app's browser, your editor's selected text, files you mention — only your request after "My request" is kept, or nothing if the two cannot be told apart) — is replaced with `{"withheld": true, "reason": "injected_context"}` (or `not_project_content`). So is the whole prompt of a session another agent starts (a sub-agent — in Claude Code, its whole side conversation — or an approval reviewer, whose prompt is a copy of your conversation with command output in it). A prompt logged by Codex's hooks is kept only once that session's own Codex transcript has been captured and shows it is yours. Only this project's own `AGENTS.md` can stay, when the copy is exactly the committed file. Your typed prompts are kept: Codex's are recognized by matching them to the tool's own record of what you typed; in Claude Code a message counts as yours unless Claude Code marks it as its own or it starts with a markup tag.
- **A transcript that cannot be parsed is withheld entirely**, and `capture-health.json` records that it happened (`withheld_unparseable`). Missing beats leaking.
- **A transcript whose structure we have not verified is also withheld entirely** (`withheld_unrecognized`). The request-to-result matching above has been measured for Codex CLI and Claude Code; for other tools — currently including the VS Code chat panel / GitHub Copilot Chat and Cursor's own transcript files — the whole transcript is withheld rather than risk committing a file the assistant read. Only a marker is committed. This costs you nothing in credit, but if you want that work visible as process evidence, use Codex CLI or Claude Code.

**Your prompts and the AI's replies are kept** — they are the record of your work, and you chose to put them there. There are four exceptions: a message (yours or the assistant's) holding a **patch or diff** whose file header names a file outside this project, a gitignored file, or a file the redaction pass cannot work out is withheld as a whole, because it may carry that file's contents; in Claude Code, a message that starts with a **markup tag** (pasted HTML, say) looks like Claude Code's own inserted text and is withheld; a shell command you run with `!` in Claude Code keeps the command but not its output; and in Codex Desktop and the VS Code extension only your request is kept from a message the tool added its own context to. Redaction exists to stop the *assistant* from surfacing things you did not agree to share. The limits follow from that: text you paste with no filename attached, an assistant quoting an outside file back in its own reply, and whatever your own tracked code prints (a secret included, along with the code that printed it) are all captured. Your code is recognized by the path of the program that runs it, the interpreter in your gitignored `venv/` or `.venv/` included: if that interpreter were replaced with one that also prints other files, its output would still be kept, while the command that replaced it stays in the transcript. Don't paste private or personal material into an assistant you are using for this course. See [AI_POLICY.md](AI_POLICY.md).

Snapshots land on a protected ref you cannot rewrite, so captured content is effectively permanent. If something genuinely sensitive gets captured, tell your instructor right away — only they can remove it.

## What is NOT captured

- Files outside the allowlist above
- Your hostname (only a hash)
- Keystrokes, timing within a session, cursor position, or anything your editor sees
- AI assistant interactions from tools without a local adapter or project hook (ChatGPT web, Codex cloud, Claude.ai in a browser, etc.). Cursor is captured only through its project hooks; if your Cursor version does not run them, nothing from Cursor is recorded. See [AI_POLICY.md](AI_POLICY.md); uncaptured tool use should be recorded in `.ai-traces/external-attestation.txt` (which is shipped pre-seeded — open it and add your entries, or write "no uncaptured AI use" if that's the case). Deleting or emptying that file is detected by the integrity check.
- Your Codex OpenAI token (`.codex/auth.json` is gitignored).
- Tool-injected instructions and context: system prompts, global instruction files (`~/.codex/AGENTS.md`, `~/.claude/CLAUDE.md`), environment and tool descriptions (see the redaction list above).
- Claude Code authentication credentials (`~/.claude/` lives outside the repo; only the per-project session JSONL under `~/.claude/projects/<repo-slug>/` is read, not credentials).

## Retention and access

Your snapshots do not live in one place. Three copies exist:

1. **On your machine** — `refs/auto-track/snapshots` in your own clone.
2. **In your GitHub repo** — the `auto-track` branch. An org-level rule blocks force-pushes and deletions on it, so you cannot rewrite or delete that history.
3. **In a private instructor mirror** — a separate GitHub repo owned by your instructor. A scheduled job there runs roughly once an hour and copies both your `auto-track` branch **and your `main` branch** into `refs/students/<your-repo>/…` inside that mirror. Yes, `main` too: the mirror holds a copy of your ordinary code history as well as your capture trail.

**The mirror is append-only.** It never deletes refs and it pushes non-force, so once something has been copied there it stays: deleting your repository, deleting the `auto-track` branch, or force-pushing over it does not take back what was already mirrored. (Each run also produces an anomaly digest; GitHub keeps those workflow artifacts for 90 days.)

**Who can read it.** Your instructor and the course's teaching assistants. The mirror repo is private: it is not public and other students cannot see it.

**How long it is kept.** There is no automatic deletion, so assume the data is kept indefinitely. Your instructor may delete the private mirror after the course ends, but no date has been set.

**Removal requests.** You cannot remove captured content yourself — that is exactly what the protected ref and the append-only mirror are for, and it applies to your instructor's copies as much as your own. If something genuinely sensitive gets captured (a credential, someone else's personal information, private material you did not mean to share), send a removal request to your instructor as soon as you notice it. Only your instructor can remove it, and the sooner they hear from you the fewer copies there are to chase.

### Capture is not limited to the session that triggered it

Nothing filters transcripts by "the session you just ran". When a trigger fires, **every** AI-tool transcript in your tool's per-user storage whose recorded working directory is this project is eligible to be copied in — including sessions that predate your clone, your last commit, or even the start of the assignment. There is one optional bound: if this repo ships an `assignment-config.json` with a `course_start` date, transcript files last modified before that date are skipped. When that file is missing or malformed there is **no time floor at all** and everything matching this path is captured.

This matters most on a **shared machine**. If you and someone else use the same user account on a lab machine (or you hand your laptop to a classmate and they open this folder in Codex or Claude Code), their sessions in this project directory land in *your* storage under *your* account and are captured into *your* graded repo. Use your own account, and do not run someone else's session in this folder.

### What "verbatim" means

The committed transcript is a redacted copy, not a summary. Within what redaction leaves in place (see above), it holds the full text of your prompts, the assistant's replies, the shell commands it ran (and their output where the rules above keep it), and the assistant's internal reasoning — Claude Code's "thinking" blocks are normalized into `reasoning` events and committed like any other authored text. Nothing about a thinking block is private just because your editor renders it as an aside.

One exception worth knowing: when a tool's output is too large, Claude Code spills it into a sibling file rather than the transcript. Those spillover files have no verifiable record structure, so their content is withheld wholesale — only a marker recording that a file existed, and its size, is committed.

### Do not paste secrets or personal content

Redaction does not take a secret out of what you type or paste — its exceptions above are about file contents and tool text — so nothing protects a secret you paste into a chat except the credential scrubber's pattern list, which is best-effort. Do not paste secrets, passwords, API keys, or personal information — yours or anyone else's — into an assistant you are using for this course. What the assistant runs is a different matter: the output of `env`, `printenv`, `whoami`, `git config --global user.email`, or inline code such as `python -c ...` is withheld, because none of them is a recognized read-only command on project files. The output of your own tracked code is not withheld, so do not let your program print secrets. If you do paste a credential by accident, rotate it — it was already on your disk and in your AI tool's own logs — and send a removal request (see above).

## Why this exists

Your learning matters more than your final grade. A passing submission tells us you delivered working code. A development history tells us *you* wrote it — by showing how your understanding evolved, where you got stuck, which tests flipped from failing to passing as you learned. This is the process your instructor wants to see.

## You can verify exactly what is committed

The primary way to inspect your local capture trail:

```bash
python -m tests._capture.audit
```

This summarizes every snapshot recorded on `refs/auto-track/snapshots`, including timestamps, test counts, and the commit SHA your `HEAD` pointed at during each run.

If you prefer raw git commands, the snapshot trail lives on its own ref locally and on the `auto-track` branch remotely. To inspect it locally:

```bash
git log refs/auto-track/snapshots --format="%h %cI %s"
git show <sha>          # full body of a single snapshot
```

To inspect the pushed copy:

```bash
git fetch origin auto-track
git log origin/auto-track --format="%h %cI %s"
```

You'll see entries whose subject lines begin with `test-run:`.

## Filtering test-run commits out of your log

Capture commits are stored on `refs/auto-track/snapshots` (locally) and on the `auto-track` branch (remotely) rather than on your working branches, so your normal `git log` on `main` or a feature branch already shows only your own intentional commits — no filtering needed.

For backwards compatibility with v1 repositories that may still have `test-run:` commits on `main` from an earlier semester, this wrapper is still provided:

```
python tools/my_commits.py
```

Works on Windows (PowerShell, cmd, Git Bash), macOS, and Linux — no shell-specific invocation needed. It accepts any `git log` flags (e.g. `-10`, `--since=1.week`). The equivalent raw command if you prefer to type it directly:

```
git log --oneline --invert-grep --grep='^test-run:'
```

Under v3 the filter is a harmless no-op (there are no `test-run:` commits on your branch to strip out).

## If the push fails

The commit always happens locally, even if your machine is offline or your credentials aren't set up. The next successful push will carry all backlogged commits. Warnings are written to `.test-runs.log` in the project root.

If your student repo has no remote configured yet, or your credentials aren't set up, run:

```bash
python tools/setup_credentials.py
```

It will diagnose the most common first-time push problems (no remote, no credential helper, rejected PAT) and print step-by-step fix instructions for your platform.

## What you'll see when a test hangs

If one of your tests blocks indefinitely (most commonly a socket waiting for a connection that never arrives), two things can kill it:

- **Per-test timeout** — the default is 30 seconds per test; a test can override with `@pytest.mark.timeout(N)`. When this fires, the test is marked failed and **on Windows the entire pytest process exits** (this is a quirk of pytest-timeout's thread method, which is the only mechanism that works on Windows). On macOS/Linux with the signal method, only that one test dies and the suite continues.
- **Session watchdog** — a background subprocess that terminates the whole test session if it runs past `max(120s, 3 × n_tests × 30s)`. This is a safety net for the case where pytest itself hangs or a per-test timeout can't interrupt the blocked code path.

Both routes still produce a `test-run:` snapshot on `refs/auto-track/snapshots`. A per-test timeout ends the pytest session with a non-zero exit code, which is translated into a named status — `status: tests_failed` for the common case (pytest exit code 1), or `status: interrupted` / `status: internal_error` / `status: usage_error` / `status: no_tests_collected` for the other pytest exit codes (see the exit-code table in `tests/conftest.py`); an exit code outside that table falls back to `status: pytest_exit_<code>`. If tests still passed overall you'll see `status: completed` instead. A watchdog kill is the one snapshot that does not come from the pytest session itself — the watchdog subprocess takes it after killing the session, so its body lists `trigger: pytest_watchdog` (rather than `trigger: pytest`) and records `status: hang_watchdog_killed`. Either way the run is visible in your auto-track trail (`python -m tests._capture.audit`); you don't need to do anything special.

## Academic integrity note

Tampering with the capture layer (deleting `tests/conftest.py`, editing `tests/_capture/`, force-updating or deleting your local `refs/auto-track/snapshots` ref, or attempting to force-push or delete the remote `auto-track` branch) is treated the same as any other academic integrity violation and is easy to detect. The remote `auto-track` branch is protected by an org-level rule that blocks force-pushes and deletions; attempts to bypass that protection are logged. If you have a legitimate reason to disable capture on a specific machine (for example, a machine where git push authentication cannot be set up), contact your instructor rather than removing the hook.
