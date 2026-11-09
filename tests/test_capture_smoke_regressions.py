"""The release smoke must exercise synthetic inputs independently of the host."""
import os
import json
import subprocess

import pytest

from tools import capture_release

pytestmark = pytest.mark.timeout(300)


def test_smoke_ignores_host_git_excludes(tmp_path, monkeypatch):
    excludes = tmp_path / "host-excludes"
    excludes.write_text("tests/\n")
    config = tmp_path / "host.gitconfig"
    config.write_text('[core]\n    excludesFile = ' + excludes.as_posix() + '\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert capture_release.smoke()["ok"]


def test_smoke_isolates_config_before_any_git_or_capture(tmp_path, monkeypatch):
    from tests._capture import orchestrator

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "unrelated.git"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'commit.gpgSign=true'")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "personal-claude"))
    monkeypatch.setenv("VSCODE_CONFIG_DIR", str(tmp_path / "personal-vscode"))
    original = capture_release.subprocess.run
    observed = []

    def observe(args, *a, **kw):
        if args[0] == "git":
            env = kw.get("env", os.environ)
            assert "GIT_DIR" not in env
            assert "GIT_CONFIG_PARAMETERS" not in env
            assert env["GIT_CONFIG_NOSYSTEM"] == "1"
            assert env["GIT_CONFIG_GLOBAL"] == os.devnull
            for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "VSCODE_CONFIG_DIR"):
                assert "capture-release-" in env[key]
            observed.append(args[1])
        return original(args, *a, **kw)

    monkeypatch.setattr(capture_release.subprocess, "run", observe)
    monkeypatch.setattr(orchestrator, "take_snapshot", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="synthetic capture did not produce a snapshot"):
        capture_release.smoke()
    assert observed[0] == "init"


@pytest.mark.parametrize("failure", ["returncode", "timeout"])
def test_smoke_retains_only_structured_git_failure_diagnostics(monkeypatch, failure):
    original = capture_release.subprocess.run

    def fail_read_tree(args, *a, **kw):
        if args[:2] == ["git", "read-tree"]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 10, output="PRIVATE_SYNTHETIC_PAYLOAD")
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="PRIVATE_SYNTHETIC_PAYLOAD")
        return original(args, *a, **kw)

    monkeypatch.setattr(capture_release.subprocess, "run", fail_read_tree)
    with pytest.raises(RuntimeError, match="synthetic capture did not produce a snapshot") as error:
        capture_release.smoke()
    message = str(error.value)
    assert "PRIVATE_SYNTHETIC_PAYLOAD" not in message
    details = json.loads(message.split("; diagnostics=", 1)[1])
    expected = {"operation": "read-tree"}
    expected.update({"returncode": 128} if failure == "returncode" else {"status": "timeout"})
    assert expected in details["git_failures"]


def test_report_value_error_retains_sanitized_git_diagnostics(monkeypatch):
    def failed_git(args, *a, **kw):
        return subprocess.CompletedProcess(args, 128, '', 'SYNTHETIC_PRIVATE_ERROR')
    def failed_report(_base):
        subprocess.run(['git', 'log', '--topo-order'], capture_output=True, text=True)
        raise ValueError('SYNTHETIC_PRIVATE_ERROR')
    monkeypatch.setattr(subprocess, 'run', failed_git)
    monkeypatch.setattr(capture_release, '_smoke', failed_report)
    with pytest.raises(RuntimeError, match='diagnostics=') as caught:
        capture_release.smoke()
    message = str(caught.value)
    assert 'SYNTHETIC_PRIVATE_ERROR' not in message
    detail = json.loads(message.split('diagnostics=', 1)[1])
    assert {'operation': 'log', 'returncode': 128} in detail['git_failures']
