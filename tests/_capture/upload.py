"""Acknowledged uploads. Receipts are local metadata, never conversation content."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._capture import git_ops


def _receipt_path(repo: Path, sha: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ValueError("invalid snapshot id")
    result = git_ops.run_git(["rev-parse", "--git-path", "capture-upload"], repo)
    if result.returncode:
        raise ValueError("not a git repository")
    base = Path(result.stdout.strip())
    return (base if base.is_absolute() else repo / base) / (sha + ".json")


def read_receipt(repo: Path, sha: str | None) -> dict:
    if not sha:
        return {"status": "no_snapshot"}
    try:
        path = _receipt_path(repo, sha)
        confirmed = path.with_suffix(".confirmed.json")
        data = json.loads((confirmed if confirmed.exists() else path).read_text(encoding="utf-8"))
        if data.get("requested_sha") != sha:
            raise ValueError("receipt mismatch")
        status = data.get("status")
        if status not in {"pending", "failed", "confirmed"}:
            raise ValueError("invalid receipt")
        if status == "confirmed" and data.get("acknowledged_sha") != sha:
            raise ValueError("unacknowledged receipt")
        error = data.get("error")
        if error not in {None, "push_failed", "acknowledgment_mismatch", "upload_unavailable"}:
            error = "unknown_upload_error"
        acknowledged = data.get("acknowledged_sha")
        if not isinstance(acknowledged, str) or not re.fullmatch(r"[0-9a-f]{40,64}", acknowledged):
            acknowledged = None
        try:
            checked_at = datetime.fromisoformat(data["checked_at"]).isoformat()
        except (ValueError, TypeError, KeyError):
            checked_at = None
        return {"status": status, "requested_sha": sha, "acknowledged_sha": acknowledged,
                "error": error, "checked_at": checked_at}
    except FileNotFoundError:
        return {"status": "pending", "requested_sha": sha}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"status": "unknown", "requested_sha": sha}


def write_receipt(repo: Path, sha: str, status: str, *, acknowledged_sha=None,
                  error=None) -> dict:
    receipt = {"status": status, "requested_sha": sha,
               "acknowledged_sha": acknowledged_sha, "error": error,
               "checked_at": datetime.now(timezone.utc).isoformat()}
    # A redundant older attempt must not erase an acknowledgment for this SHA.
    existing = read_receipt(repo, sha)
    if existing["status"] == "confirmed" and status != "confirmed":
        return existing
    path = _receipt_path(repo, sha)
    if status == "confirmed":
        # A separate monotonic record prevents concurrent older failures from
        # racing a successful acknowledgment out of existence.
        path = path.with_suffix(".confirmed.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as output:
            name = output.name
            json.dump(receipt, output, sort_keys=True)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return receipt


def push_snapshot(repo: Path, sha: str) -> dict:
    """Push one immutable snapshot and confirm the advertised remote SHA.

    No force push; origin is the repository's configured destination. Network
    operations are bounded. Raw Git errors/URLs are never put in a receipt.
    """
    write_receipt(repo, sha, "pending")
    try:
        result = git_ops.run_git(
            ["push", "origin", f"{sha}:refs/heads/auto-track"], repo, timeout=60)
        if result.returncode:
            return write_receipt(repo, sha, "failed", error="push_failed")
        result = git_ops.run_git(
            ["ls-remote", "--refs", "origin", "refs/heads/auto-track"], repo,
            timeout=30)
        acknowledged = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else None
        if acknowledged != sha:
            return write_receipt(repo, sha, "failed", error="acknowledgment_mismatch")
        return write_receipt(repo, sha, "confirmed", acknowledged_sha=sha)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return write_receipt(repo, sha, "failed", error="upload_unavailable")


if __name__ == "__main__":
    result = push_snapshot(Path(sys.argv[1]).resolve(), sys.argv[2])
    print("Capture upload:", result["status"], result.get("error") or "")
    raise SystemExit(0 if result["status"] == "confirmed" else 1)
