"""Read-only setup and upload diagnostics; unknown is never healthy."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tests._capture import git_ops, runtime_triggers, upload


def _snapshot_health(repo: Path, tip: str | None) -> dict | None:
    """Read structured health from the exact snapshot, never from local cache."""
    if not tip:
        return None
    try:
        path = ".ai-traces/capture-health.json"
        entry = git_ops.run_git(["ls-tree", tip, "--", path], repo, timeout=30.0)
        fields = entry.stdout.split()
        if entry.returncode or len(fields) != 4 or fields[:2] not in (
                ["100644", "blob"], ["100755", "blob"]):
            return None
        result = git_ops.run_git(["cat-file", "blob", fields[2]], repo, timeout=30.0)
        if result.returncode:
            return None
        health = json.loads(result.stdout)
        if not isinstance(health, dict) or health.get("schema_version") != "1":
            return None
        return health
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        return None


def _snapshot_health_affirmative(health: dict) -> bool:
    package = health.get("capture_package")
    if (health.get("redaction_ok") is not True
            or health.get("snapshot_ok") is not True
            or not isinstance(package, str)
            or not re.fullmatch(r"[0-9a-f]{64}", package)):
        return False
    adapters = health.get("adapters", {})
    if not isinstance(adapters, dict):
        return False
    return all(isinstance(entry, dict)
               and isinstance(entry.get("errors", []), list)
               and not entry.get("errors") for entry in adapters.values())


def build_report(repo: Path) -> dict:
    errors = []
    if os.environ.get("CAPTURE_DISABLED") == "1":
        errors.append("capture_disabled_by_environment")
    try:
        config = json.loads((repo / "project-template-config.json").read_text(encoding="utf-8"))
        enabled = config.get("capture_enabled") if isinstance(config, dict) else None
        if enabled is not True:
            errors.append("capture_not_enabled")
    except FileNotFoundError:
        enabled = None
        errors.append("capture_configuration_missing")
    except (OSError, ValueError):
        enabled = None
        errors.append("capture_configuration_invalid")
    is_git = git_ops.is_git_repo(repo)
    if not is_git:
        errors.append("not_a_git_repository")
    origin_configured = is_git and git_ops.run_git(
        ["remote", "get-url", "origin"], repo, timeout=5.0).returncode == 0
    if not origin_configured:
        errors.append("upload_origin_missing")
    tip = git_ops.read_auto_track_tip(repo) if is_git else None
    receipt = upload.read_receipt(repo, tip)
    snapshot_health = _snapshot_health(repo, tip)
    if snapshot_health is None:
        errors.append("snapshot_health_unknown")
    elif not _snapshot_health_affirmative(snapshot_health):
        errors.append("snapshot_health_not_affirmative")
    # Keep local checks too: a failed new attempt must remain visible even
    # when the last successfully uploaded snapshot has healthy metadata.
    health_path = repo / ".ai-traces/capture-health.json"
    try:
        health = json.loads(health_path.read_text(encoding="utf-8"))
        health_present = isinstance(health, dict) and health.get("schema_version") == "1"
    except (OSError, ValueError):
        health_present = False
    if not health_present:
        health = {}
        errors.append("capture_health_unknown")
    if health.get("redaction_ok") is not True:
        errors.append("redaction_failed" if health.get("redaction_ok") is False else "redaction_unknown")
    if health.get("snapshot_ok") is not True:
        errors.append("snapshot_failed" if health.get("snapshot_ok") is False else "snapshot_unknown")
    summaries, warnings = {}, []
    adapters = health.get("adapters")
    adapters = adapters if isinstance(adapters, dict) else {}
    for name, entry in adapters.items():
        if name not in {"codex", "claude", "cursor", "vscode_chat", "aider"} or not isinstance(entry, dict):
            continue
        summaries[name] = {key: entry.get(key) is True for key in
                           ("present", "heartbeat", "evidence", "withheld_unparseable", "withheld_unrecognized")}
        adapter_errors = entry.get("errors")
        summaries[name]["error_count"] = len(adapter_errors) if isinstance(adapter_errors, list) else 0
        if summaries[name]["error_count"]:
            errors.append(name + "_ingest_error")
        if entry.get("evidence") and not entry.get("heartbeat"):
            warnings.append(name + "_hooks_not_observed")
        if entry.get("withheld_unparseable") or entry.get("withheld_unrecognized"):
            warnings.append(name + "_transcript_withheld")
    if health.get("workspace_root_mismatch"):
        warnings.append("workspace_root_mismatch")
    if not tip:
        errors.append("no_local_snapshot")
    if receipt["status"] != "confirmed":
        errors.append("latest_upload_" + receipt["status"])
    package = health.get("capture_package")
    package = package if isinstance(package, str) and re.fullmatch(r"[0-9a-f]{64}", package) else "unknown"
    if package == "unknown":
        errors.append("capture_package_unknown")
    return {"schema_version": 1, "ok": not errors,
            "configuration": {"capture_enabled": enabled is True},
            "origin_configured": origin_configured,
            "local_snapshot": tip, "upload": receipt,
            "health_record_present": health_present,
            "snapshot_health_record_present": snapshot_health is not None,
            "capture_package": package,
            "adapters": summaries, "warnings": warnings,
            "bootstrap": runtime_triggers.installation_state(repo), "errors": errors}
