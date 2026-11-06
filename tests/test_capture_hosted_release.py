"""Hosted checks must stay read-only and must not turn unknown into ready."""
import json
from types import SimpleNamespace

from tools.capture_release import hosted


def test_hosted_check_requires_protection_and_exact_mirror_snapshot(monkeypatch):
    calls = []

    def request(args, **kwargs):
        calls.append(args)
        endpoint = args[2]
        if endpoint.endswith("rules/branches/auto-track"):
            data = [{"type": "deletion"}, {"type": "non_fast_forward"}]
        elif endpoint.endswith("branches/auto-track"):
            data = {"protected": True, "commit": {"sha": "a" * 40}}
        else:
            data = {"object": {"sha": "a" * 40}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))

    monkeypatch.setattr("tools.capture_release.subprocess.run", request)
    assert hosted("course/synthetic", "instructor/mirror", "students/synthetic/auto-track")["ok"]
    assert all(args[:2] == ["gh", "api"] and len(args) == 3 for args in calls)


def test_missing_rules_and_unspecified_mirror_are_not_ready(monkeypatch):
    monkeypatch.setattr("tools.capture_release.subprocess.run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="[]"))
    report = hosted("course/synthetic")
    assert report["protection"]["state"] == "missing"
    assert report["mirror"]["state"] == "unknown"
    assert not report["ok"]


def test_hosted_auth_failure_is_unknown(monkeypatch):
    monkeypatch.setattr("tools.capture_release.subprocess.run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout=""))
    report = hosted("course/synthetic")
    assert report["protection"]["state"] == "unknown"
    assert not report["ok"]
