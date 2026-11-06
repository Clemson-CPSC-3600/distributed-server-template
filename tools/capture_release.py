#!/usr/bin/env python3
"""Pin, check and transfer capture packages; smoke-test with local synthetic data.

Sync is dry-run unless --apply is supplied. It cannot touch assignment sources,
solutions, protocol files, bundle tests or mutable course configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import verify_integrity

MANIFEST = "tools/CAPTURE_RELEASE.json"
PROTECTED = ("src/", "template/", "solution/", "crc_support/")


def _source(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("invalid package path")
    return verify_integrity.source_path(root, name, verify_integrity.distribution_mode(root))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _fingerprint(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def package_paths(root: Path) -> list[str]:
    paths = set(verify_integrity.TRACKED)
    paths.update({"tools/verify_integrity.py", "tools/INTEGRITY_HASHES.txt",
                  "tools/capture_release.py", "tools/preflight.py", "run_tests.py",
                  "tools/setup_credentials.py", "tools/validate_student_repo.py",
                  "PROCESS_TRACKING.md", ".github/workflows/integrity.yml", ".github/workflows/capture-release.yml",
                  "docs/capture-release.md"})
    prefixes = ("test_capture", "test_codex", "test_claude", "test_ai_traces",
                "test_agent_adapter", "test_runtime", "test_workspace_root",
                "test_force_paths", "test_post_commit", "test_sitecustomize",
                "test_course_floor", "test_private_work", "test_orchestrator",
                "test_platform_compat", "test_attestation_template",
                "test_disclosure", "test_process_tracking", "test_ai_policy")
    for path in (root / "tests").glob("test_*.py"):
        if path.name.startswith(prefixes):
            paths.add(path.relative_to(root).as_posix())
    for path in (root / "tests/fixtures").rglob("*"):
        if path.is_file():
            paths.add(path.relative_to(root).as_posix())
    # Include every capture module, rather than relying on a manually updated list.
    for path in (root / "tests/_capture").rglob("*.py"):
        paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def stamp(root: Path, paths: list[str] | None = None) -> dict:
    files = {name: _digest(_source(root, name)) for name in (paths if paths is not None else package_paths(root))}
    manifest = {"schema_version": 1, "package_sha256": _fingerprint(files), "files": files}
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(root: Path, submission: bool = False) -> dict:
    errors = verify_integrity.configuration_errors(root, verify_integrity.distribution_mode(root))
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("empty or invalid package file list")
        if manifest.get("schema_version") != 1 or manifest.get("package_sha256") != _fingerprint(files):
            raise ValueError("manifest fingerprint mismatch")
        for name, expected in files.items():
            path = _source(root, name)
            if not path.is_file():
                errors.append("missing:" + name)
            elif _digest(path) != expected:
                errors.append("changed:" + name)
        if "tools/verify_integrity.py" in files:
            for name in verify_integrity.SOFT_TRACKED:
                path = root / name
                if not path.is_file() or not path.stat().st_size:
                    errors.append("required_configuration_missing:" + name)
        if submission:
            path = root / ".ai-traces/external-attestation.txt"
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            marker = "----- begin entries -----"
            entries = text.split(marker, 1)[-1].strip() if marker in text else text.strip()
            if not entries:
                errors.append("attestation_incomplete")
    except (OSError, ValueError, KeyError, TypeError):
        errors.append("capture_release_manifest_invalid")
        manifest = {}
    return {"ok": not errors, "package_sha256": manifest.get("package_sha256"), "errors": errors}


def sync(source: Path, target: Path, apply: bool = False, course_start: str | None = None) -> dict:
    source_result = verify(source)
    if not source_result["ok"]:
        raise ValueError("source package is not verified: " + ",".join(source_result["errors"]))
    manifest = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    actions = []
    floor = target / "assignment-config.json"
    if not floor.exists():
        if course_start is None:
            raise ValueError("target lacks assignment-config.json; provide --course-start YYYY-MM-DD")
        date.fromisoformat(course_start)
        actions.append({"action": "create", "path": "assignment-config.json", "course_start": course_start})
        if apply:
            floor.write_text(json.dumps({"course_start": course_start}, indent=2) + "\n", encoding="utf-8")
    for name in sorted([*manifest["files"], MANIFEST]):
        if name.startswith(PROTECTED) or name in {"project-template-config.json", "PROTOCOL.md"} or name.startswith("tests/test_bundle"):
            raise ValueError("capture package includes assignment content")
        src, dest = _source(source, name), _source(target, name)
        if dest.is_file() and _digest(dest) == _digest(src):
            continue
        actions.append({"action": "change" if dest.exists() else "create", "path": name})
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return {"applied": apply, "actions": actions, "deletes": [],
            "package_sha256": manifest["package_sha256"]}


def smoke() -> dict:
    """Exercise real capture/Git with two clones and a local-only bare remote."""
    from tests._capture import git_ops, orchestrator
    from tests._capture.agent_adapters.codex import CodexAdapter
    from tests._capture.report import build_report
    from tests._capture.upload import push_snapshot

    checks = {}
    def git(repo, *args):
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                                text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("synthetic Git operation failed")
        return result.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="capture-release-") as directory:
        base = Path(directory)
        work, remote, second, home = [base / name for name in ("work", "remote.git", "second", "codex")]
        work.mkdir()
        (work / "src").mkdir()
        (work / "tests").mkdir()
        (work / "src/main.py").write_text("pass\n")
        (work / "tests/.gitkeep").write_text("")
        (work / "project-template-config.json").write_text('{"distribution_mode":"student","capture_enabled":true}')
        git(work, "init", "-q", "-b", "main")
        git(work, "config", "user.email", "release@example.invalid")
        git(work, "config", "user.name", "Release smoke")
        git(work, "add", ".")
        git(work, "commit", "-qm", "synthetic baseline")
        git(work, "remote", "add", "origin", str(remote))
        (home / "sessions").mkdir(parents=True)
        canary = "SYNTHETIC_OUTSIDE_PROJECT_CONTEXT_DO_NOT_ARCHIVE"
        rows = [
            {"type": "session_meta", "payload": {"id": "synthetic-release", "cwd": str(work)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Explain a timeout."}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Explain a timeout."}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "A timeout can trigger retransmission."}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /outside/project\n" + canary}]}},
        ]
        (home / "sessions/rollout-release.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        env = {"CODEX_HOME": str(home), "_CAPTURE_SUBPROCESS": "1"}
        with patch.dict(os.environ, env), patch.object(orchestrator, "installed_adapters", return_value=[CodexAdapter()]), patch.object(git_ops, "push_auto_track_background"):
            os.environ.pop("CAPTURE_DISABLED", None)
            first = orchestrator.take_snapshot(work, trigger="manual", pytest_session_id=None, force=True)
            if not first:
                raise RuntimeError("synthetic capture did not produce a snapshot")
            report = build_report(work)
            codex = report["adapters"].get("codex", {})
            checks["prompts"] = codex.get("user_prompts") == 1
            checks["assistant"] = codex.get("assistant_messages") == 1
            paths = git(work, "ls-tree", "-r", "--name-only", first).splitlines()
            checks["privacy"] = not any("/raw/" in p for p in paths) and not any(canary in git(work, "show", first + ":" + p) for p in paths if p.startswith(".ai-traces/"))
            checks["health"] = ".ai-traces/capture-health.json" in paths
            checks["offline_failure_visible"] = push_snapshot(work, first)["status"] == "failed"
            git(base, "init", "--bare", "-q", str(remote))
            git(base, "--git-dir=" + str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
            git(work, "push", "origin", "main")
            checks["upload_confirmed"] = push_snapshot(work, first)["status"] == "confirmed"
            git(base, "clone", "-q", str(remote), str(second))
            git(second, "config", "user.email", "release@example.invalid")
            git(second, "config", "user.name", "Release smoke")
            next_sha = orchestrator.take_snapshot(second, trigger="manual", pytest_session_id=None, force=True)
            if not next_sha:
                log = second / ".test-runs.log"
                detail = log.read_text(encoding="utf-8")[-1800:] if log.exists() else "no capture log"
                raise RuntimeError("second synthetic clone snapshot failed: " + detail)
            parents = git(second, "show", "-s", "--format=%P", next_sha).split() if next_sha else []
            checks["second_clone_history"] = bool(parents) and parents[0] == first
            archived = build_report(second)
            checks["history_report"] = archived["adapters"].get("codex", {}).get("sessions") == 1 and archived["historical_sessions_not_in_current_stream"] == 1
            checks["second_clone_upload"] = bool(next_sha) and push_snapshot(second, next_sha)["status"] == "confirmed"
    return {"ok": all(checks.values()), "checks": checks}


def hosted(repository: str, mirror: str | None = None, mirror_ref: str | None = None) -> dict:
    """Read-only hosted release check. Missing access/configuration is unknown."""
    def request(endpoint):
        try:
            result = subprocess.run(["gh", "api", endpoint], capture_output=True,
                                    text=True, timeout=30)
            return json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    for slug in (repository, mirror):
        if slug is not None and not re.fullmatch(r"[\w.-]+/[\w.-]+", slug):
            raise ValueError("repository must be owner/name")
    rules = request(f"repos/{repository}/rules/branches/auto-track")
    branch = request(f"repos/{repository}/branches/auto-track")
    types = {rule.get("type") for rule in rules if isinstance(rule, dict)} if isinstance(rules, list) else set()
    protection = "verified" if {"non_fast_forward", "deletion"} <= types else "unknown" if rules is None else "missing"
    if protection != "verified" and isinstance(branch, dict) and branch.get("protected"):
        classic = request(f"repos/{repository}/branches/auto-track/protection")
        if isinstance(classic, dict) and classic.get("allow_force_pushes", {}).get("enabled") is False and classic.get("allow_deletions", {}).get("enabled") is False:
            protection = "verified"
        elif classic is None:
            protection = "unknown"
    mirror_state = "unknown"
    reason = "mirror_not_configured"
    if mirror and mirror_ref:
        if not re.fullmatch(r"(?:refs/)?students/[\w.-]+/auto-track", mirror_ref):
            raise ValueError("mirror ref must identify one students/<suffix>/auto-track ref")
        relative_ref = mirror_ref[5:] if mirror_ref.startswith("refs/") else mirror_ref
        saved = request(f"repos/{mirror}/git/ref/{relative_ref}")
        current = branch.get("commit", {}).get("sha") if isinstance(branch, dict) else None
        mirrored = saved.get("object", {}).get("sha") if isinstance(saved, dict) else None
        if current and mirrored:
            mirror_state = "verified" if current == mirrored else "behind_or_diverged"
            reason = "exact_snapshot_present" if current == mirrored else "snapshot_not_matched"
        else:
            reason = "mirror_or_source_ref_unavailable"
    return {"ok": protection == "verified" and mirror_state == "verified",
            "protection": {"state": protection, "required_rules": ["non_fast_forward", "deletion"]},
            "mirror": {"state": mirror_state, "reason": reason}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stamp", "verify", "smoke", "sync", "hosted"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--submission", action="store_true", help="Require a completed attestation entry.")
    parser.add_argument("--course-start", help="Existing term start, required when adding a missing ingestion floor.")
    parser.add_argument("--repository", help="Hosted owner/name to inspect without changing it.")
    parser.add_argument("--mirror", help="Instructor mirror owner/name.")
    parser.add_argument("--mirror-ref", help="Exact students/<suffix>/auto-track mirror ref.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "stamp":
            result = stamp(args.root)
        elif args.command == "verify":
            result = verify(args.root, submission=args.submission)
        elif args.command == "smoke":
            result = smoke()
        elif args.command == "hosted" and args.repository:
            result = hosted(args.repository, args.mirror, args.mirror_ref)
        elif args.command == "hosted":
            parser.error("hosted requires --repository")
        elif args.target:
            result = sync(args.root, args.target, args.apply, args.course_start)
        else:
            parser.error("sync requires --target")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
