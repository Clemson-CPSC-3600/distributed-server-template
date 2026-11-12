"""Manual CLI entrypoint for the capture orchestrator.

Usage::

    python -m tests._capture snapshot [--reason "instructor checkpoint"]

`--reason` is forwarded to the orchestrator and ends up as a
`manual_reason: ...` line in the snapshot commit body. Manual
snapshots always go through, regardless of dedupe state, per
spec decision row 6.

Exit codes:
    0  — snapshot created (commit SHA written to stdout)
    2  — orchestrator returned None (capture disabled, lock contention,
         or internal failure — see .test-runs.log for details)
    >0 — argparse usage error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from tests._capture import orchestrator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests._capture",
        description="Manual capture-orchestrator CLI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser(
        "snapshot",
        help="Take a manual auto-track snapshot of the current repo.",
    )
    snap.add_argument(
        "--reason", default=None,
        help="Free-form reason; recorded in the snapshot commit body.",
    )
    for name, help_text in (("doctor", "Read local capture and last upload status."),
                            ("report", "Count events across the full snapshot history.")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true", help="Emit structured metadata only.")
        command.add_argument("--repo", type=Path, help="Project or bare instructor-mirror directory.")
        if name == "report":
            command.add_argument("--ref", help="Snapshot ref, including a refs/students/... mirror ref.")
    return parser


def main(argv: Optional[List[str]] = None, cwd: Optional[Path] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo = getattr(args, "repo", None) or (Path(cwd) if cwd is not None else Path.cwd())

    if args.cmd in {"doctor", "report"}:
        if args.cmd == "doctor":
            from tests._capture.doctor import build_report
        else:
            from tests._capture.report import build_report
        result = build_report(repo, ref=args.ref) if args.cmd == "report" else build_report(repo)
        sys.stdout.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        return 0 if args.cmd == "report" or result["ok"] else 1

    if args.cmd == "snapshot":
        sha = orchestrator.take_snapshot(
            repo,
            trigger="manual",
            pytest_session_id=None,
            manual_reason=args.reason,
        )
        if sha is None:
            return 2
        sys.stdout.write(sha + "\n")
        return 0
    # argparse's required=True means this should never trigger.
    return 1


if __name__ == "__main__":
    sys.exit(main())
