"""Synthetic Git history and malformed-record reporting regressions."""
import json
import subprocess

import pytest

from tests._capture import git_ops, metadata
from tests._capture.report import build_report


def git(repo, *args):
    result = subprocess.run(['git', *args], cwd=repo, text=True,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def work(tmp_path):
    repo = tmp_path / 'work'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Synthetic report')
    git(repo, 'config', 'user.email', 'report@example.invalid')
    (repo / 'src').mkdir()
    for index in range(4):
        (repo / 'src/main.py').write_text(f'value = {index}\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', f'ordinary baseline {index}')
    return repo


def snapshot(work, session='snapshot-session'):
    message = metadata.format_commit_message(
        session_id=session, status='completed', result=metadata.TestResult(),
        diff_added=0, diff_removed=0, files_changed=[], hostname_hash='synthetic',
        trigger='manual')
    sha = git_ops.snapshot_to_auto_track(
        work, message, ['src', '.ai-traces'], 'main', git(work, 'rev-parse', 'HEAD'))
    assert sha
    return sha


def write_stream(work, rows):
    path = work / '.ai-traces/interaction-stream.jsonl'
    path.parent.mkdir(exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


def event(identity='event-private', session='session-private', **fields):
    return {'adapter_name': 'codex', 'event_type': 'user_prompt',
            'event_id': identity, 'session_id': session,
            'text': 'PRIVATE_CONVERSATION_CANARY', **fields}


def test_four_baseline_commits_and_one_actual_snapshot_count_one(work):
    snapshot(work)
    report = build_report(work)
    assert report['snapshot_commits'] == 1
    assert report['archive_commits_scanned'] == 5


def test_main_commits_with_copied_traces_are_not_snapshot_rows(work):
    snapshot(work)
    write_stream(work, [event()])
    git(work, 'add', '.')
    git(work, 'commit', '-qm', 'ordinary work including copied trace files')
    snapshot(work, 'second-snapshot')
    report = build_report(work)
    assert report['snapshot_commits'] == 2
    assert report['archive_commits_scanned'] == 7
    assert report['adapters']['codex']['sessions'] == 1


def test_legacy_snapshot_messages_are_counted_without_a_new_marker(work):
    tree = git(work, 'rev-parse', 'HEAD^{tree}')
    legacy = git(work, 'commit-tree', tree, '-p', git(work, 'rev-parse', 'HEAD'),
                 '-m', 'test-run: 0/0 passed\n\nsession_id: legacy-private')
    git(work, 'update-ref', git_ops.AUTO_TRACK_REF, legacy)
    snapshot(work)
    assert build_report(work)['snapshot_commits'] == 2


def test_recovered_fork_records_survive_without_inflating_primary_count(work):
    write_stream(work, [event('fork-event-private', 'fork-session-private')])
    fork = snapshot(work, 'fork')
    git(work, 'update-ref', 'refs/auto-track/recovered/' + fork, fork)
    git(work, 'update-ref', '-d', git_ops.AUTO_TRACK_REF)
    write_stream(work, [event('primary-event-private', 'primary-session-private')])
    snapshot(work, 'primary')
    write_stream(work, [])
    snapshot(work, 'merge')
    report = build_report(work)
    assert report['snapshot_commits'] == 2
    assert report['archive_commits_scanned'] == 7
    assert report['adapters']['codex']['sessions'] == 2
    assert report['historical_sessions_not_in_current_stream'] == 2
    serialized = json.dumps(report)
    for secret in ('fork-event-private', 'fork-session-private', 'PRIVATE_CONVERSATION_CANARY'):
        assert secret not in serialized


@pytest.mark.parametrize('malformed', [
    [], {}, None, 'PRIVATE_CONVERSATION_CANARY',
    {'adapter_name': [], 'event_type': 'user_prompt'},
    {'adapter_name': {}, 'event_type': 'user_prompt'},
    {'event_type': 'user_prompt'},
    {'adapter_name': None, 'event_type': 'user_prompt'},
    {'adapter_name': '', 'event_type': 'user_prompt'},
    {'adapter_name': 'codex', 'event_type': []},
    {'adapter_name': 'codex', 'event_type': {}},
    {'adapter_name': 'codex'},
    {'adapter_name': 'codex', 'event_type': None},
    {'adapter_name': 'codex', 'event_type': ''},
    event(event_id=[]), event(session_id={}),
])
def test_malformed_record_is_counted_and_valid_record_survives(work, malformed):
    write_stream(work, [malformed, event()])
    snapshot(work)
    report = build_report(work)
    assert report['malformed_records'] == 1
    assert report['adapters']['codex']['user_prompts'] == 1
    assert report['adapters']['codex']['events'] == 1
    assert report['adapters']['codex']['sessions'] == 1
    serialized = json.dumps(report)
    assert 'PRIVATE_CONVERSATION_CANARY' not in serialized
    assert 'event-private' not in serialized
    assert 'session-private' not in serialized
