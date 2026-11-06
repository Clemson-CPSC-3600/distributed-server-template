"""Read-only setup and upload diagnostics; unknown is never healthy."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from tests._capture import git_ops, runtime_triggers, upload


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
    health_path = repo / ".ai-traces/capture-health.json"
    try:
        health = json.loads(health_path.read_text(encoding="utf-8"))
        health_present = isinstance(health, dict) and health.get("schema_version") == "1"
    except (OSError, ValueError):
        health_present = False
    if not health_present:
        health = {}
        errors.append("capture_health_unknown")
    elif health.get("redaction_ok") is False:
        errors.append("redaction_failed")
    if health.get("snapshot_ok") is False:
        errors.append("snapshot_failed")
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
    return {"schema_version": 1, "ok": not errors,
            "configuration": {"capture_enabled": enabled is True},
            "origin_configured": origin_configured,
            "local_snapshot": tip, "upload": receipt,
            "health_record_present": health_present,
            "capture_package": package,
            "adapters": summaries, "warnings": warnings,
            "bootstrap": runtime_triggers.installation_state(repo), "errors": errors}
