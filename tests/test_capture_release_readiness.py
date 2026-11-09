"""Synthetic release checks: no real accounts, sessions or network remotes."""
import json
import subprocess
from pathlib import Path
import pytest

def git(repo, *args):
    p = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()

@pytest.fixture
def work(tmp_path):
    repo = tmp_path / 'work'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Release test')
    git(repo, 'config', 'user.email', 'release@example.invalid')
    (repo / 'src').mkdir()
    (repo / 'src/main.py').write_text('pass\n')
    (repo / 'project-template-config.json').write_text('{"distribution_mode":"student","capture_enabled":true}')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    return repo

def test_student_configuration_requires_boolean_true(tmp_path):
    from tools.verify_integrity import configuration_errors
    for value in [None, {}, {'capture_enabled': False}, {'capture_enabled': 'true'}, {'capture_enabled': 1}]:
        path = tmp_path / 'project-template-config.json'
        if value is not None:
            path.write_text(json.dumps(value))
        elif path.exists():
            path.unlink()
        assert configuration_errors(tmp_path, 'student')
    (tmp_path / 'project-template-config.json').write_text('{"capture_enabled":true}')
    assert configuration_errors(tmp_path, 'student') == []

def test_upload_receipt_confirms_exact_remote_snapshot(work, tmp_path):
    from tests._capture.upload import push_snapshot, read_receipt
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', '-q', str(remote))
    git(work, 'remote', 'add', 'origin', str(remote))
    sha = git(work, 'rev-parse', 'HEAD')
    receipt = push_snapshot(work, sha)
    assert receipt['status'] == 'confirmed'
    assert receipt['acknowledged_sha'] == sha
    assert read_receipt(work, sha)['status'] == 'confirmed'

def test_failed_upload_is_visible_and_retry_recovers(work, tmp_path):
    from tests._capture.upload import push_snapshot, read_receipt
    sha = git(work, 'rev-parse', 'HEAD')
    git(work, 'remote', 'add', 'origin', str(tmp_path / 'offline.git'))
    assert push_snapshot(work, sha)['status'] == 'failed'
    assert read_receipt(work, sha)['status'] == 'failed'
    git(tmp_path, 'init', '--bare', '-q', str(tmp_path / 'offline.git'))
    assert push_snapshot(work, sha)['status'] == 'confirmed'

def test_history_report_keeps_session_removed_by_later_merge_snapshot(work):
    from tests._capture.report import build_report
    p = work / '.ai-traces/interaction-stream.jsonl'
    p.parent.mkdir()
    p.write_text(json.dumps({'event_id': 'e1', 'adapter_name': 'codex', 'session_id': 's1', 'event_type': 'user_prompt'}) + '\n')
    git(work, 'add', '.')
    git(work, 'commit', '-qm', 'synthetic session')
    older = git(work, 'rev-parse', 'HEAD')
    git(work, 'update-ref', 'refs/auto-track/snapshots', older)
    p.unlink()
    git(work, 'add', '-A')
    tree = git(work, 'write-tree')
    newer = git(work, 'commit-tree', tree, '-p', older, '-p', git(work, 'rev-parse', 'HEAD^'), '-m', 'synthetic snapshot merge')
    git(work, 'update-ref', 'refs/auto-track/snapshots', newer)
    report = build_report(work)
    assert report['adapters']['codex']['sessions'] == 1
    assert report['adapters']['codex']['user_prompts'] == 1
    assert report['current_stream_sessions'] == 0
    assert report['historical_sessions_not_in_current_stream'] == 1

def test_doctor_reports_upload_pending_then_confirmed(work, tmp_path):
    from tests._capture.doctor import build_report
    from tests._capture.upload import push_snapshot
    sha = git(work, 'rev-parse', 'HEAD')
    git(work, 'update-ref', 'refs/auto-track/snapshots', sha)
    assert build_report(work)['upload']['status'] == 'unknown'
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', '-q', str(remote))
    git(work, 'remote', 'add', 'origin', str(remote))
    assert build_report(work)['upload']['status'] == 'pending'
    push_snapshot(work, sha)
    assert build_report(work)['upload']['status'] == 'confirmed'

def test_release_verifier_rejects_changed_package(tmp_path):
    from tools.capture_release import stamp, verify
    root = tmp_path / 'package'
    root.mkdir()
    (root / 'a.py').write_text('pass\n')
    (root / 'project-template-config.json').write_text('{"capture_enabled":true}')
    stamp(root, paths=['a.py'])
    assert verify(root)['ok']
    (root / 'a.py').write_text('changed\n')
    assert not verify(root)['ok']

def test_release_smoke_checks_both_clones_and_offline_retry():
    from tools.capture_release import smoke
    report = smoke()
    assert report['ok'], report
    assert all((report['checks'][key] for key in ['prompts', 'assistant', 'privacy', 'health', 'offline_failure_visible', 'upload_confirmed', 'second_clone_history', 'history_report']))

def test_redaction_crash_never_commits_unsanitized_traces(work, monkeypatch, tmp_path):
    from tests._capture import orchestrator, git_ops, redaction
    from tests._capture.agent_adapters.codex import CodexAdapter
    home = tmp_path / 'codex'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('CAPTURE_DISABLED', raising=False)
    path = work / '.ai-traces/codex/normalized/session.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text('{"private":"SYNTHETIC_PRIVATE_CANARY"}\n')
    monkeypatch.setattr(orchestrator, 'installed_adapters', lambda _: [CodexAdapter()])
    monkeypatch.setattr(git_ops, 'push_auto_track_background', lambda *_: None)

    def broken(_):
        raise RuntimeError('synthetic redaction failure')
    monkeypatch.setattr(redaction, 'redact_traces', broken)
    sha = orchestrator.take_snapshot(work, trigger='manual', pytest_session_id=None, force=True)
    assert sha, 'Code snapshot should remain possible when AI capture is withheld'
    paths = git(work, 'ls-tree', '-r', '--name-only', sha).splitlines()
    assert not any(('/normalized/' in p or '/raw/' in p or '/redacted/' in p or p.endswith('interaction-stream.jsonl') for p in paths))
    health = json.loads(git(work, 'show', sha + ':.ai-traces/capture-health.json'))
    assert health['redaction_ok'] is False

def test_preflight_warns_when_student_capture_configuration_is_missing(work):
    from tools.preflight import check_warnings
    (work / 'project-template-config.json').unlink()
    assert check_warnings(work), 'Missing configuration must not silently disable capture'

def test_older_failure_cannot_erase_upload_acknowledgment(work, tmp_path):
    from tests._capture.upload import push_snapshot, read_receipt, write_receipt, _receipt_path
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', '-q', str(remote))
    git(work, 'remote', 'add', 'origin', str(remote))
    sha = git(work, 'rev-parse', 'HEAD')
    assert push_snapshot(work, sha)['status'] == 'confirmed'
    _receipt_path(work, sha).write_text(json.dumps({'status': 'failed', 'requested_sha': sha}))
    assert read_receipt(work, sha)['status'] == 'confirmed'
    assert write_receipt(work, sha, 'pending')['status'] == 'confirmed'

def test_submission_check_rejects_blank_attestation_seed(tmp_path):
    from tools.capture_release import stamp, verify
    root = tmp_path / 'package'
    root.mkdir()
    (root / 'a.py').write_text('pass\n')
    (root / 'project-template-config.json').write_text('{"capture_enabled":true}')
    traces = root / '.ai-traces'
    traces.mkdir()
    attestation = traces / 'external-attestation.txt'
    attestation.write_text('Instructions\n----- begin entries -----\n\n')
    stamp(root, paths=['a.py'])
    assert verify(root)['ok']
    assert not verify(root, submission=True)['ok']
    attestation.write_text('Instructions\n----- begin entries -----\nno uncaptured AI use\n')
    assert verify(root, submission=True)['ok']

def test_history_report_never_emits_conversation_text(work):
    from tests._capture.report import build_report
    p = work / '.ai-traces/interaction-stream.jsonl'
    p.parent.mkdir()
    p.write_text(json.dumps({'event_id': 'private', 'adapter_name': 'codex', 'session_id': 'private-session', 'event_type': 'user_prompt', 'text': 'PRIVATE_SYNTHETIC_MESSAGE'}) + '\n')
    git(work, 'add', '.')
    git(work, 'commit', '-qm', 'synthetic')
    git(work, 'update-ref', 'refs/auto-track/snapshots', git(work, 'rev-parse', 'HEAD'))
    report = build_report(work)
    assert report['adapters']['codex']['user_prompts'] == 1
    assert 'PRIVATE_SYNTHETIC_MESSAGE' not in json.dumps(report)
    assert 'private-session' not in json.dumps(report)

def test_committed_health_never_contains_exception_text(tmp_path):
    from tests._capture.capture_health import build_health
    health = build_health(tmp_path, present_names=['codex'], errors_by_adapter={'codex': ['PRIVATE_EXCEPTION_CANARY /outside/personal/path']})
    assert health['adapters']['codex']['errors'] == ['adapter_ingest_failed']
    assert 'PRIVATE_EXCEPTION_CANARY' not in json.dumps(health)

def test_report_cli_reads_named_history_in_bare_mirror(work, tmp_path, capsys):
    from tests._capture.__main__ import main
    path = work / '.ai-traces/interaction-stream.jsonl'
    path.parent.mkdir()
    path.write_text(json.dumps({'event_id': 'e1', 'adapter_name': 'codex', 'session_id': 's1', 'event_type': 'user_prompt'}) + '\n')
    git(work, 'add', '.')
    git(work, 'commit', '-qm', 'synthetic session')
    mirror = tmp_path / 'mirror.git'
    git(tmp_path, 'init', '--bare', '-q', str(mirror))
    git(tmp_path, '--git-dir=' + str(mirror), 'fetch', str(work), 'HEAD:refs/students/synthetic/auto-track')
    assert main(['report', '--repo', str(mirror), '--ref', 'refs/students/synthetic/auto-track', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['adapters']['codex']['sessions'] == 1

def test_divergent_snapshot_histories_remain_reachable_and_reportable(work):
    from tests._capture import git_ops
    from tests._capture.report import build_report
    base = git(work, 'rev-parse', 'HEAD')
    stream = work / '.ai-traces/interaction-stream.jsonl'
    stream.parent.mkdir()

    def archive(session):
        stream.write_text(json.dumps({'event_id': session, 'session_id': session, 'adapter_name': 'codex', 'event_type': 'user_prompt'}) + '\n')
        git(work, 'add', '.')
        tree = git(work, 'write-tree')
        return git(work, 'commit-tree', tree, '-p', base, '-m', 'synthetic archived snapshot')
    remote = archive('remote-session')
    local = archive('local-session')
    git(work, 'update-ref', git_ops.AUTO_TRACK_REF, local)
    git(work, 'update-ref', git_ops.AUTO_TRACK_ORIGIN_TIP_REF, remote)
    stream.write_text('')
    sha = git_ops.snapshot_to_auto_track(work, 'synthetic recovery', ['src', '.ai-traces'], 'main', base)
    assert sha
    assert subprocess.run(['git', 'merge-base', '--is-ancestor', local, sha], cwd=work).returncode == 0
    assert subprocess.run(['git', 'merge-base', '--is-ancestor', remote, sha], cwd=work).returncode == 0
    assert build_report(work)['adapters']['codex']['sessions'] == 2

def test_unstage_failure_aborts_snapshot_and_is_visible(work, tmp_path, monkeypatch):
    from tests._capture import orchestrator, git_ops
    from tests._capture.agent_adapters.codex import CodexAdapter
    from tests._capture.doctor import build_report
    home = tmp_path / 'codex'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('CAPTURE_DISABLED', raising=False)
    raw = work / '.ai-traces/codex/raw/rollouts/rollout-audit.jsonl'
    raw.parent.mkdir(parents=True)
    raw.write_text('{"type":"session_meta","payload":{"id":"synthetic","cwd":' + json.dumps(str(work)) + '}}\n')
    monkeypatch.setattr(orchestrator, 'installed_adapters', lambda _: [CodexAdapter()])
    monkeypatch.setattr(git_ops, 'push_auto_track_background', lambda *_: None)
    original = subprocess.run

    def failing_remove(args, *a, **kw):
        if args[:2] == ['git', 'rm']:
            return subprocess.CompletedProcess(args, 1, stdout='', stderr='synthetic exclusion failure')
        return original(args, *a, **kw)
    monkeypatch.setattr(subprocess, 'run', failing_remove)
    assert orchestrator.take_snapshot(work, trigger='manual', pytest_session_id=None, force=True) is None
    assert 'snapshot_failed' in build_report(work)['errors']
