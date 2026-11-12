"""Acknowledged uploads. Receipts are local metadata, never conversation content."""
from __future__ import annotations

import json
import hashlib
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


def _destination(repo: Path) -> tuple[str, str]:
    """Resolve Git's effective push URL without retaining credentials anywhere.

    A multi-destination remote is deliberately unsupported: acknowledging one
    server cannot establish that all configured servers received the snapshot.
    """
    result = git_ops.run_git(["remote", "get-url", "--push", "--all", "origin"],
                             repo, timeout=5)
    urls = result.stdout.splitlines() if result.returncode == 0 else []
    if not urls:
        raise ValueError("upload_origin_missing")
    if len(urls) != 1 or not urls[0].strip():
        raise ValueError("upload_destination_unknown")
    url = urls[0]
    # Git rewrites URLs only once per resolution. Reusing the resolved URL
    # as a transport argument must not apply another rule and silently send
    # the snapshot elsewhere. --get-url resolves fetch rewriting without
    # contacting a server. Git has no corresponding push-only URL probe,
    # so conservatively reject any matching nonidentity push rewrite.
    checked = git_ops.run_git(["ls-remote", "--get-url", "--", url], repo, timeout=5)
    if checked.returncode or checked.stdout.splitlines() != [url]:
        raise ValueError("upload_destination_unknown")
    rewrites = git_ops.run_git(
        ["config", "--null", "--get-regexp", r"^url\..*\.pushinsteadof$"], repo, timeout=5)
    if rewrites.returncode not in {0, 1}:
        raise ValueError("upload_destination_unknown")
    for entry in rewrites.stdout.split("\0"):
        if not entry:
            continue
        key, separator, prefix = entry.partition("\n")
        if not separator or not key.startswith("url.") or not key.endswith(".pushinsteadof"):
            raise ValueError("upload_destination_unknown")
        replacement = key[len("url."):-len(".pushinsteadof")]
        if url.startswith(prefix) and replacement + url[len(prefix):] != url:
            raise ValueError("upload_destination_unknown")
    return url, hashlib.sha256(url.encode("utf-8")).hexdigest()


def _receipt_path(repo: Path, sha: str, destination_id: str | None = None) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ValueError("invalid snapshot id")
    result = git_ops.run_git(["rev-parse", "--git-path", "capture-upload"], repo)
    if result.returncode:
        raise ValueError("not a git repository")
    base = Path(result.stdout.strip())
    if destination_id is None:
        destination_id = _destination(repo)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", destination_id):
        raise ValueError("invalid destination identity")
    return (base if base.is_absolute() else repo / base) / destination_id / (sha + ".json")


def read_receipt(repo: Path, sha: str | None) -> dict:
    if not sha:
        return {"status": "no_snapshot"}
    try:
        destination_id = _destination(repo)[1]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "unknown", "requested_sha": sha}
    return _read_receipt(repo, sha, destination_id)


def _read_receipt(repo: Path, sha: str, destination_id: str) -> dict:
    try:
        path = _receipt_path(repo, sha, destination_id)
        confirmed = path.with_suffix(".confirmed.json")
        data = json.loads((confirmed if confirmed.exists() else path).read_text(encoding="utf-8"))
        if data.get("requested_sha") != sha:
            raise ValueError("receipt mismatch")
        if data.get("destination_id") != destination_id:
            raise ValueError("receipt destination mismatch")
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
                "destination_id": destination_id, "error": error, "checked_at": checked_at}
    except FileNotFoundError:
        return {"status": "pending", "requested_sha": sha}
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return {"status": "unknown", "requested_sha": sha}


def write_receipt(repo: Path, sha: str, status: str, *, acknowledged_sha=None,
                  error=None, destination_id: str | None = None) -> dict:
    if destination_id is None:
        destination_id = _destination(repo)[1]
    receipt = {"status": status, "requested_sha": sha,
               "destination_id": destination_id,
               "acknowledged_sha": acknowledged_sha, "error": error,
               "checked_at": datetime.now(timezone.utc).isoformat()}
    # A redundant older attempt must not erase an acknowledgment for this SHA.
    existing = _read_receipt(repo, sha, destination_id)
    if existing["status"] == "confirmed" and status != "confirmed":
        return existing
    path = _receipt_path(repo, sha, destination_id)
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
    try:
        destination, destination_id = _destination(repo)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "failed", "requested_sha": sha,
                "error": "upload_destination_unknown"}

    def record(status, **kwargs):
        return write_receipt(repo, sha, status, destination_id=destination_id, **kwargs)

    def destination_unchanged():
        try:
            return _destination(repo)[1] == destination_id
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    changed = {"status": "unknown", "requested_sha": sha,
               "error": "upload_destination_changed"}
    try:
        record("pending")
        result = git_ops.run_git(
            ["push", "--", destination, f"{sha}:refs/heads/auto-track"], repo, timeout=60)
        if not destination_unchanged():
            return changed
        if result.returncode:
            return record("failed", error="push_failed")
        result = git_ops.run_git(
            ["ls-remote", "--refs", "--", destination, "refs/heads/auto-track"], repo,
            timeout=30)
        if not destination_unchanged():
            return changed
        acknowledged = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else None
        if acknowledged != sha:
            return record("failed", error="acknowledgment_mismatch")
        return record("confirmed", acknowledged_sha=sha)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        if not destination_unchanged():
            return changed
        return record("failed", error="upload_unavailable")


if __name__ == "__main__":
    result = push_snapshot(Path(sys.argv[1]).resolve(), sys.argv[2])
    print("Capture upload:", result["status"], result.get("error") or "")
    raise SystemExit(0 if result["status"] == "confirmed" else 1)
