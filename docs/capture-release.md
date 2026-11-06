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
remote at the recorded time; it does not perform a new network check.
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
commands or transcript paths. Missing prompts alongside assistant replies are
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
configuration is validated semantically: student capture must be boolean true.

The synthetic smoke test never contacts GitHub or reads personal AI sessions.
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
with its already-configured term start. Existing ingestion floors are preserved.
At submission time, `verify --submission` additionally rejects an untouched
attestation seed; ordinary development checks permit the initial blank.

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
