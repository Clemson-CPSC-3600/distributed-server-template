# AI Assistance Policy for This Assignment

This course provides access to Codex (OpenAI's coding assistant) through
Clemson's site license. You are encouraged to use it as a tutor. You are
**not** encouraged to use it as a ghostwriter.

## What is captured

Local AI-agent traces for sessions you start inside this repository are saved
to `.ai-traces/` and committed to the auto-track snapshot ref. Four
tools are currently supported:

- **Codex CLI** (`.ai-traces/codex/`, sourced from `~/.codex/sessions/`).
  Full transcripts contributed to the merged `interaction-stream.jsonl`.
- **Claude Code** (`.ai-traces/claude/`, sourced from
  `~/.claude/projects/`). Full transcripts contributed to the merged
  stream.
- **VS Code chat panel** (`.ai-traces/vscode_chat/`, sourced from VS Code's
  own `workspaceStorage/<id>/chatSessions/`). This covers GitHub Copilot
  Chat and any other extension that uses the built-in chat panel. Its
  session format has not been verified for safe redaction, so each
  session file is currently **withheld entirely** — only a marker saying
  a session existed is committed (see "What is removed" below).
- **Cursor** (`.ai-traces/cursor/`, written by the project hooks in
  `.cursor/hooks/` when your Cursor version runs them). The hooks record
  the prompts you type, the files the Cursor agent edits (including the
  edit text for files inside this project), and which files Tab
  completions touched (file and size only, never the text). Cursor's own
  transcript files are copied too, but their format has not been verified
  yet, so they are withheld entirely and only a marker is committed.

Any of these is fine to use. Codex and Claude Code write to the same
unified `.ai-traces/interaction-stream.jsonl` so your instructor reads
one merged transcript regardless of which assistant you chose. Codex,
Claude Code, and Cursor also ship project hooks that record each turn as
it happens and take a snapshot when the assistant finishes; see
`PROCESS_TRACKING.md` for when those hooks are active.

**This is captured:**

- The prompt you typed to a locally captured AI agent.
- The reply the AI agent produced.
- Any tool calls, shell commands, approvals, or denials that the local
  adapter can observe, and the output of those commands and tools where
  the redaction rules below allow it.
- Any file edits the AI agent proposed or made.
- A snapshot of the working-tree files in the allowlist (`src/`, `tests/`,
  `.ai-traces/`, `.codex/`, `AGENTS.md`, `AI_POLICY.md`) on every
  test run, every Python invocation inside the project venv (via an
  `atexit` hook installed into the venv's `sitecustomize.py`), and every
  git commit (via a per-repo `.githooks/post-commit` hook), plus at the
  end of each assistant turn when a Codex, Claude Code, or Cursor project
  hook is active. All of these
  triggers record locally to `refs/auto-track/snapshots` (a side ref,
  not on your working branches) and push to the `auto-track` branch on
  your remote. The branch is visible in your repo's branch list on
  GitHub — that's expected; the remote branch is the canonical copy
  your instructor reads. None of these triggers install anything outside
  the project directory; see `PROCESS_TRACKING.md` for the trigger list
  and scoping details.
- Any commit reachable from your current branch HEAD when `pytest` runs —
  including commits on local branches you have not pushed yourself. We
  record the SHA of your current commit alongside each snapshot so that
  intentional commits you later rebase or amend away are still preserved
  as research data.
- If you run tests during a `git rebase`, `git merge`, or `git cherry-pick`
  operation, the in-progress state is captured even if you later abort the
  operation. The body field `git_state` records the operation context
  (`rebasing`/`merging`/`cherry-picking`/`clean`).
- A setup-health record (`.ai-traces/capture-health.json`) describing whether
  each adapter was detected and whether its ingest reported an error. This
  exists so that "no traces" can be told apart from "the capture broke" —
  it holds no conversation content.
- Whether any AI session was run from a folder **above** this assignment
  (`workspace_root_mismatch`), plus how many times (`ancestor_cwd` in the
  snapshot's commit message). This is recorded because such a session
  cannot be captured at all, and without it your effort would look like no
  effort. **Only the count is recorded** — never the folder names, and
  never any of that session's content. See "Open the right folder" below.

**This is NOT captured:**

- Your OpenAI API token (`.codex/auth.json` is gitignored). Claude Code
  credentials live in `~/.claude/` outside the repo and are never read.
- AI-agent sessions you run in directories outside this assignment. No
  prompt, reply, file edit, or directory name from such a session is ever
  recorded. One narrow exception, stated plainly so this list stays
  honest: if the folder you had open *contains* this assignment, a bare
  count of those sessions is recorded (see `workspace_root_mismatch`
  above). Sessions in unrelated or sibling folders are not counted at all.
- AI-agent sessions you run in tools that are not locally captured here
  (ChatGPT web, Codex cloud, Claude.ai in a browser, etc.). Cursor is
  partly captured (see above) but only through its project hooks; if your
  Cursor version does not run them, nothing from Cursor is recorded. If
  you use any of these tools for this course, paste or
  summarize the interaction in `.ai-traces/external-attestation.txt` so
  your work is auditable.
- The contents of branches not reachable from your current HEAD at
  test-run time.
- Anything you do outside the course repository directory.

## What is removed before anything is committed (redaction)

Your transcripts are **not** committed as your AI tool wrote them. Before a
snapshot is made, a redaction pass rewrites them, using one simple rule:

> **The privacy boundary is the submission boundary.** You submit this whole
> project, so anything inside it is already visible to your instructor and is
> kept as-is. Anything *outside* it is none of the course's business and is
> removed.

Concretely, the committed copy has:

- **Credentials removed.** Anything shaped like an API key, access token,
  private key, or `PASSWORD=...` assignment is replaced with
  `<redacted:...>` — including inside your own prompts. If you paste a token
  by accident, it does not get committed. (Still change it: it was on your
  disk and in your AI tool's own logs.)
- **Your username and folder layout removed.** Absolute paths inside the
  project become project-relative (`/Users/you/.../src/main.py` →
  `src/main.py`). Paths outside the project become an opaque tag like
  `<external:9f3c1a2b>` — the same file always gets the same tag, so the
  history still makes sense, but the name and location are gone.
- **Command and tool output is kept only when it can only show your
  project.** When your assistant runs a command or uses a tool, the call
  itself is always kept (so the record has no invisible holes and shows
  what ran), but what it printed or returned is kept only in two cases:
  (1) a recognized **read-only** command or tool reading files in this
  project — `cat src/main.py`, `sed -n '1,50p' src/main.py`,
  `git status`, plain `rg`, or the assistant's own file-reading and
  editing tools on a project file; (2) running **your own project
  code** — `python src/main.py` for a file git tracks (you have added or
  committed it), `pytest`, or `python run_tests.py`, including when they
  run through this project's own `venv/` or `.venv/` (`venv/bin/python`,
  `.venv/bin/pytest`), which counts the same as a plain `python` or
  `pytest`. Everything else is replaced with
  `{"withheld": true}`, including inline code the assistant writes itself
  (`python -c ...`, `node -e ...`), searches through whole folders
  (`grep -r`), wildcards (`cat *.txt`), archivers, anything piped into a
  shell, commands that print your environment (`env`, `printenv`,
  `whoami`), and any program or tool the redaction pass does not
  recognize. Files outside this project and files you have gitignored — a
  `.env`, your `venv/` — never count as project files: you are not
  submitting them, so `cat ~/.ssh/config`, `cat ../other-project/file.py`
  or `cat .env` has its output withheld. The same goes for files the
  capture layer never commits, such as your Codex login file
  `.codex/auth.json`, whether or not your `.gitignore` lists them.
- **Transcripts we cannot verify are withheld entirely.** The rule above
  works by matching a tool's request to its result. That matching has been
  measured for Codex CLI and Claude Code. For any other tool — today that
  includes **VS Code chat panel / GitHub Copilot Chat** — the transcript's
  structure has not been verified, so rather than risk committing something
  the assistant read, **the whole transcript is withheld**: nothing but a
  marker recording that it happened is committed, and your setup-health file
  records it. You lose no credit for this; it is our conservatism, not your
  mistake. If you want your Copilot Chat work to count as evidence of your
  process, tell your instructor, or use Codex CLI or Claude Code, which are
  captured in full.
- **Your email address removed** where the tool recorded one.
- **Text your AI tool adds on its own removed.** Tools slip their own text
  into a conversation: their system prompt, the instruction files they load
  — `AGENTS.md` and `CLAUDE.md`, **including global ones in your home
  folder** such as `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md` —
  descriptions of your environment, installed tools and skills, hook
  output, notices about background tasks, error messages from the AI
  service, the titles a tool makes up for a conversation, your account
  ids, and the output of commands you run inside the tool (Claude Code's
  `!` and `/` commands). You did not type any of it, so these
  **tool-injected instructions are not captured**: each one is replaced
  with `{"withheld": true, "reason": "injected_context"}` (or
  `not_project_content`). Codex Desktop and the VS Code extension also put
  context of their own **inside the message you type**: the page open in
  the app's browser, the file and the text you have selected in your
  editor, and the files you mention. That context is withheld and only
  your request (what follows "My request") is kept; if the two cannot be
  told apart, the whole message is withheld. The same goes for a session
  another agent starts — a sub-agent (in Claude Code, its whole side
  conversation), or an approval reviewer that checks a command before it
  runs. Everything in that session's prompt was written by an agent (a
  reviewer's prompt is a copy of your conversation, command output
  included), so none of it is kept. The prompts Codex's hooks log do not
  say which kind of session they came from, so one is kept only once that
  session's own Codex transcript has been captured and shows it is yours.
  The one exception is this project's own
  `AGENTS.md`, kept only when the copy is exactly the file you are
  submitting anyway. What you typed yourself is kept: Codex's copy is found
  by matching it to the tool's own record of your prompt, and in Claude Code
  a message counts as yours unless Claude Code marks it as its own or it
  starts with a markup tag (see below).

**Your prompts and the AI's replies are kept**, apart from the exceptions
below. They are the point of the record, and they are course work. Write
them accordingly.

Four things to be clear about:

- **The raw, unredacted transcripts still exist on your own computer**, in
  your AI tool's storage and under `.ai-traces/<tool>/raw/`. That directory
  is gitignored and never committed; only the redacted copy in
  `.ai-traces/<tool>/redacted/` is.
- **What you type or paste is kept, on purpose — with four exceptions.**
  Your prompts, your pasted text, and the assistant's replies are the record
  of your work, and you chose to put them there, so redaction leaves them
  in, except that:
  (1) a message — yours or the assistant's — that holds a **patch or diff**
  whose file header names a file outside this project, a gitignored file,
  or a file the redaction pass cannot work out is withheld as a whole,
  because it may be carrying that file's contents;
  (2) in Claude Code, a message that **starts with a markup tag** (pasted
  HTML beginning `<html>`, for example) looks exactly like the text Claude
  Code inserts on its own, so it is withheld;
  (3) when you run a shell command with `!` in Claude Code, the command you
  typed is kept but its output is not; and
  (4) in Codex Desktop and the VS Code extension, only your request is
  kept from a message the tool added its own context to (see above).
  Redaction exists to stop the *assistant* from surfacing things you did not
  agree to share. So: **do not paste secrets** — passwords, API keys, tokens
  — and do not paste private or personal material, yours or anyone else's,
  into an assistant you are using for this course. Nothing downstream will
  take it back out for you except the credential scrubber's pattern list,
  which is best-effort.
- **The assistant's reasoning is captured too.** Claude Code's "thinking"
  blocks are normalized into `reasoning` events and committed like any other
  authored text. A thinking block is not private just because your editor
  renders it as an aside.
- **Four limits, stated plainly.** (1) If the assistant reads an outside
  file and then *quotes it back in its own reply*, that reply is kept, so the
  quoted text is captured. (2) The output of your own tracked code is
  kept: if a program in your project — or a change the assistant made to
  it — prints a secret (say, the contents of your `.env`), that output is
  captured, along with the code that printed it. Output from commands such
  as `env` and `printenv` is withheld, but the credential scrubber above is
  still best-effort, so never keep secrets where your own code or your
  prompts will print them. (3) Your code is recognized by the path of the
  program that runs it, including the Python interpreter and `pytest` in
  your project's `venv/` or `.venv/`. That folder is gitignored, so if its
  interpreter were replaced with one that also prints other files, that
  output would still be kept. The command that replaced it stays in the
  transcript, though what it wrote into the gitignored folder does not.
  (4) Text pasted with no filename attached cannot be recognized as coming
  from outside the project.

If a transcript cannot be parsed, it is **withheld entirely** rather than
committed, and `capture-health.json` records that it happened. Missing is
always preferred to leaking.

## Retention and access

**Captured history is effectively permanent, and it is not only in your
repo.** Snapshots live on a protected ref that you cannot rewrite or delete,
and a scheduled job copies both your `auto-track` branch and your `main`
branch into a **private instructor-owned mirror repo** about once an hour.
That mirror is **append-only**: it never deletes refs and pushes non-force,
so deleting your repository, deleting the branch, or force-pushing over it
does not remove anything already copied there.

- **Who can read it:** your instructor and the course's teaching
  assistants. The mirror is private; other students cannot see it.
- **How long it is kept:** there is no automatic deletion, so assume it is
  kept indefinitely. Your instructor may delete the private mirror after the
  course ends, but no date has been set.
- **Removal requests:** you **cannot remove** captured content yourself, and
  neither can you remove the mirrored copy. If something genuinely sensitive
  gets captured, send a removal request to your instructor as soon as you notice. Only
  your instructor can remove it, and the sooner they know the fewer copies
  exist.

**Capture has no time bound.** Nothing limits it to "the session you just
ran": when a trigger fires, every transcript in your AI tool's per-user
storage whose recorded working directory is this project is eligible,
including sessions that predate your clone or your last commit. The only
optional bound is a `course_start` date in `assignment-config.json`, which
skips transcript files last modified before the course began; with no such
file there is no floor at all. So on a **shared machine** — the same user
account on a lab computer, or a classmate opening this folder on your
laptop — their sessions in this project directory are captured into your
graded repo. Use your own account.

[PROCESS_TRACKING.md](PROCESS_TRACKING.md) has the full retention, mirror, and access detail.

## Open the right folder

Open **this assignment's folder** as your editor's workspace — the folder
that directly contains `run_tests.py`. Opening a subfolder of it (such as
`src/`) is fine.

Do **not** open a parent folder that merely contains this assignment. If
your layout looks like this:

```
cpsc-3600/
  assignment-1/     <-- open THIS one
    run_tests.py
  assignment-2/
```

then opening `cpsc-3600/` breaks capture. Your code still runs and your
tests still pass, but your AI assistant reports the parent folder as its
location, and the capture layer cannot tell those sessions belong to this
assignment. **Your work would look like no work.**

If this happens, `run_tests.py` prints a warning telling you how to fix it,
and the snapshot records that it occurred (a count only — see above). Just
reopen the assignment folder; nothing you already did is lost.

## How to keep work private

If you want to do truly private exploration (sketching an algorithm you may
not keep, scratch work you do not want recorded, etc.), do it in a scratch
directory **outside the project folder** entirely — not a subfolder of this
repo, and not another clone of it either. A second clone of this same
repository still ships this template's `project-template-config.json` and
its own `.git` history, so the first time you run `pytest` there it installs
its own copy of the capture triggers and starts recording — likely pushing
to the same instructor-visible remote you cloned from. Cloning is not a
private-work escape hatch; a folder with no connection to this project at
all is.

Editing files **inside** this repository is never private, even ones you
delete again before your next commit: whichever trigger fires next snapshots
the working tree as it exists at that moment, deletion afterward does not
un-capture it. Every trigger behaves this way — `pytest` (fires at the
end of every test session, `tests/conftest.py`), `sitecustomize` (fires on
every Python invocation in the project venv via an `atexit` hook,
`tests/_capture/sitecustomize_payload.py`), `git_post_commit` (fires on
every commit, `.githooks/post-commit` → `tests/_capture/post_commit_entry.py`),
and the AI-tool project hooks in `.codex/hooks/`, `.claude/hooks/`, and
`.cursor/hooks/` (fire when the assistant finishes a turn, if active).

Capture requires two things to be true at trigger time: a
`project-template-config.json` findable by walking up from the working
directory, and that directory sitting inside a git repository the capture
layer recognizes. A scratch directory genuinely outside the project folder
has neither, so nothing you do there is ever captured, regardless of which
tool or trigger you use. There is no way to opt out of capture for sessions
started inside the project folder short of disabling it entirely (which
violates course policy).

## Migration from v1

If your assignment repo started the semester under the v1 capture (commits
with `test-run:` subject lines on `main`), the first time you run tests
under v3, the new snapshot ref will second-parent your current `main`
HEAD — meaning both the v1 capture history AND any intentional commits you
authored on `main` before this point become reachable from
`refs/auto-track/snapshots`. v3 does not wipe the slate; it preserves and
reorganizes the prior trail.

## What Codex is configured to do here

The file `AGENTS.md` at the root of this repo tells Codex to act as a
Socratic tutor. It will generally ask you questions and give hints rather
than writing complete solutions. You can override this by insisting, but
the instructor can see that you did (the transcript records your prompt).

Approving a command or edit is captured as part of the interaction record.
Approval means you allowed the tool to proceed; it is not treated as proof
that you authored the resulting code.

## What counts as academic honesty

| You do this... | ...and it's: |
|---|---|
| Ask Codex to explain a concept you just learned | fine |
| Ask Codex to review your code for bugs | fine |
| Ask Codex for a hint on where you're stuck | fine |
| Ask Codex to generate a function, then type it over verbatim | violation |
| Ask Codex to do the assignment, then edit lightly | violation |
| Use an uncaptured AI tool and not record it in `.ai-traces/external-attestation.txt` | violation |
| Delete an AI trace from `.ai-traces/` before submission | violation |

If you're uncertain whether a use is allowed, ask your instructor before
submitting — not after.

## Verifying what was captured

The primary way to audit your local capture trail:

```bash
python -m tests._capture.audit
```

This summarizes every snapshot recorded on the local
`refs/auto-track/snapshots` ref, including timestamps, test counts, and the
`current_head_sha` observed at each run.

For power users who prefer raw git plumbing:

```bash
# Local copy
git log refs/auto-track/snapshots --format="%h %cI %s"
git show <sha>          # full body of a single snapshot

# Pushed copy (what your instructor sees)
git fetch origin auto-track
git log origin/auto-track --format="%h %cI %s"
```

## If Codex is down or you prefer not to use it

You are never required to use Codex. This policy only covers what happens
**if** you use it.
