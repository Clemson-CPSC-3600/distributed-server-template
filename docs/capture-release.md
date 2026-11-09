# Capture readiness and releases

Capture has separate stages: tool detection, transcript ingestion, sanitization,
local snapshot creation, remote acknowledgment, and instructor reporting.
Passing assignment tests or the file-integrity check proves none of these by
itself. An absent or unsupported record is unknown, never proof of no AI use.

## Student diagnostics

Run these with the assignment's virtual-environment Python:

```
python -m tests._capture doctor --json
python -m tests._capture report --json
```

`doctor` is read-only. It reports configuration, bootstrap information, whether
a health record exists, and the last acknowledged upload of the latest local
snapshot. `pending`, `failed`, `unknown` and `no_snapshot` are distinct states.
A confirmed receipt means that exact SHA was advertised by the configured
push destination at the recorded time; it does not perform a new network check.
Receipts are bound to a hashed destination identity as well as the snapshot.
Changing the destination makes the old acknowledgment unknown until an upload
to the new destination is confirmed; raw destination URLs are never retained.
Multiple push destinations and chained URL rewrites are reported as unknown;
a single stable Git URL alias is supported.
Receipts live inside Git's private metadata directory, contain no conversation
text, and are not part of submissions or committed snapshots.

Normal capture continues to upload in the background. Every upload now pushes
an immutable snapshot SHA and records acknowledgment or failure. If offline,
work is saved locally; a later capture trigger retries the current snapshot.
For an immediate retry without changing the code, run a manual snapshot:

```
python -m tests._capture snapshot --reason "retry upload"
```

`report` counts known event types across the preserved snapshot graph, including
merge snapshots and recovered offline forks. It deduplicates event IDs and reports historical sessions
absent from the newest tree. It emits no student text, session identifiers,
commands or transcript paths. Snapshot counts exclude ordinary starter/main commits; archive scan counts
include the complete reachable history. Malformed structured rows are counted
and skipped while other records remain available. Missing prompts alongside assistant replies are
reported as incomplete coverage. Files disappearing from a later working-tree
snapshot do not mean older evidence was destroyed.
When two checkouts create divergent offline histories, recovery keeps the local
fork reachable and includes it as an additional parent of the next snapshot.
This preserves both histories without force-pushing or rewriting main.
Instructor mirrors can select one archived history with
`report --repo /path/to/mirror.git --ref refs/students/SUFFIX/auto-track --json`;
no student text is emitted.

The health file differentiates detected tools, evidence, hook heartbeats,
withheld records, ingest errors and workspace mismatch. Unknown formats must
remain visible as withheld/unsupported states. The policy and redaction
boundary in AI_POLICY.md still govern what is retained.

## Preparing a package

After intentional capture changes, run from the authoring starter:

```
python tools/verify_integrity.py --update
python tools/capture_release.py stamp
python tools/capture_release.py verify
python tools/capture_release.py smoke
```

The manifest pins production capture files, hook configurations, compatibility
fixtures and capture tests. Hashes normalize CRLF to LF. Generation checks the
actual student package against this manifest before its first commit. Both
generation scripts stop on a missing manifest or drift. Mutable assignment
configuration is validated semantically: student capture must be boolean true
and an existing assignment configuration must contain a valid ISO course-start
date. Missing mandatory health results or package identity remain unknown in
student diagnostics.

The synthetic smoke test isolates personal Git and AI configuration before
setup. It never contacts GitHub or reads personal AI sessions. Failures retain
Git operation names and status/timeout codes, without raw logs or paths.
It creates two checkouts and a temporary local bare remote, checks both sides
of a conversation, redaction, health, offline failure, upload acknowledgment,
fresh-clone ancestry and a history-aware report. It is necessary but does not
claim to test real Windows, editor authorization or hosted mirror deployment.

To update an existing project, review a dry run first:

```
python tools/capture_release.py sync --target /path/to/project
python tools/capture_release.py sync --target /path/to/project --apply
```

Sync only copies files enumerated in the verified capture release. It refuses
assignment source, solution, protocol, bundle-test and mutable configuration
paths. It performs no deletions, pushes, publication or remote configuration.
Review assignment-specific tooling customizations before applying the package.
When a legacy project lacks assignment-config.json, supply `--course-start`
with its already-configured term start. Accepted dates are normalized to
`YYYY-MM-DD` in both preview and apply before any target files are written.
Existing ingestion floors are preserved. Noncanonical paths and case variants
of protected assignment/configuration paths are rejected before writes.
At submission time, `verify --submission` additionally rejects an untouched
attestation seed or placeholder. Keep the entries marker and write either
`no uncaptured AI use` or completed `YYYY-MM-DD HH:MM | tool | description`
lines below it. Ordinary development checks permit the initial blank.

## Before publishing

- Run capture tests, privacy tests and the smoke test in the generated package.
- Verify protocol, starter/solution sources and graded bundle tests are unchanged.
- Record the package SHA from `verify` with the release review.
- Check the organization's auto-track protection and instructor mirror with
  read-only hosted checks. No local test proves these services are configured.
- Exercise supported editors and operating systems using synthetic accounts;
  hook heartbeats and upload acknowledgment must be observed, not assumed.
- Verify outside-tool attestations are completed at submission time. The generic
  file-integrity check allows a seeded blank during development; it is not a
  submission-completeness check.

Read-only hosted checks can be run without changing permissions:

```
python tools/capture_release.py hosted --repository OWNER/STUDENT-REPO --mirror OWNER/MIRROR --mirror-ref students/SUFFIX/auto-track
```

This requires effective force-push/deletion protection and the exact current
snapshot in the named mirror ref. Missing access or an unspecified mirror is
unknown and returns nonzero. A mirror lagging its schedule is reported as not
yet matched; the tool never repairs settings or starts a deployment itself.

Publishing and permission changes require the instructor's normal approval.
