"""Synthetic release validation and trusted workflow-mode regressions."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools.capture_release import stamp, verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def package(tmp_path):
    (tmp_path / 'a.py').write_text('pass\n')
    (tmp_path / 'project-template-config.json').write_text('{"capture_enabled":true}')
    stamp(tmp_path, paths=['a.py'])
    return tmp_path


@pytest.mark.parametrize('raw', ['{', '[]', 'null', '{}', '{"course_start":false}',
                                '{"course_start":""}', '{"course_start":"not-a-date"}',
                                '{"course_start":"2026-02-30"}'])
def test_existing_invalid_ingestion_floor_fails_release(package, raw):
    (package / 'assignment-config.json').write_text(raw)
    result = verify(package)
    assert not result['ok']
    assert 'assignment_course_start_invalid' in result['errors']


@pytest.mark.parametrize('value', ['2026-08-19', '2026-08-19T00:00:00Z',
                                  '2026-08-19T00:00:00+00:00'])
def test_supported_ingestion_floors_pass_release(package, value):
    (package / 'assignment-config.json').write_text(json.dumps({'course_start': value}))
    assert verify(package)['ok']


def test_minimal_package_without_assignment_config_still_valid(package):
    assert verify(package)['ok']


def test_instructor_layout_can_validate_its_own_capture_disabled_config(package):
    (package / '.claude-student').mkdir()
    (package / 'create-assignment.sh').write_text('#!/bin/sh\n')
    (package / 'project-template-config.json').write_text(
        '{"distribution_mode":"instructor","capture_enabled":false}')
    assert verify(package)['ok']


@pytest.mark.parametrize('instructor,config,expected', [
    (True, {'course_start': None}, True),
    (False, {'course_start': None}, False),
    (True, {}, False),
    (True, {'course_start': 'not-a-date'}, False),
])
def test_explicit_null_floor_is_only_an_instructor_choice(package, instructor, config, expected):
    if instructor:
        (package / '.claude-student').mkdir()
        (package / 'create-assignment.sh').write_text('#!/bin/sh\n')
        (package / 'project-template-config.json').write_text(
            '{"distribution_mode":"instructor","capture_enabled":false}')
    (package / 'assignment-config.json').write_text(json.dumps(config))
    assert verify(package)['ok'] is expected


@pytest.mark.parametrize('entries', ['', 'TODO', 'TBD', 'YYYY-MM-DD HH:MM | tool name | brief description',
                                     '2026-02-30 12:00 | ChatGPT | Explain a timeout',
                                     '2026-11-09 12:00 | TODO | Explain a timeout',
                                     '2026-11-09 12:00 | ChatGPT | TODO add later'])
def test_incomplete_attestation_fails_submission(package, entries):
    folder = package / '.ai-traces'
    folder.mkdir()
    (folder / 'external-attestation.txt').write_text(
        'Instructions\n----- begin entries -----\n' + entries + '\n')
    assert not verify(package, submission=True)['ok']


def test_attestation_instructions_without_delimiter_fail_submission(package):
    folder = package / '.ai-traces'
    folder.mkdir()
    (folder / 'external-attestation.txt').write_text('External AI usage attestation\nIf you used AI, log it here.\n')
    assert not verify(package, submission=True)['ok']


@pytest.mark.parametrize('entries', ['no uncaptured AI use',
                                    '2026-11-09 12:00 | ChatGPT | Explain a timeout',
                                    '2026-11-09 12:00 | ChatGPT | None of the suggested tests passed',
                                    '2026-11-09 12:00 | ChatGPT | Explain a timeout\n2026-11-09 13:30 | Copilot | Suggest test cases'])
def test_actual_declarations_pass_submission(package, entries):
    folder = package / '.ai-traces'
    folder.mkdir()
    (folder / 'external-attestation.txt').write_text(
        'Instructions\n----- begin entries -----\n' + entries + '\n')
    assert verify(package, submission=True)['ok']


def _integrity_command():
    text = (ROOT / '.github/workflows/integrity.yml').read_text()
    return text.split('        run: |\n', 1)[1].split('      - name:', 1)[0]


@pytest.mark.skipif(os.name == 'nt', reason='GitHub integrity job runs on Ubuntu/bash')
@pytest.mark.parametrize('repository,expected', [
    ('clemson-teaching/python-class-project-starter', []),
    ('Clemson-CPSC-3600/distributed-server-reference-implementation', ['--mode', 'student']),
    ('Clemson-CPSC-3600/distributed-server-template', ['--mode', 'student']),
    ('Clemson-CPSC-3600/distributed-server-f2026-synthetic', ['--mode', 'student']),
    ('someone/python-class-project-starter', ['--mode', 'student']),
    ('', ['--mode', 'student']),
])
def test_workflow_mode_uses_exact_trusted_repository_identity(tmp_path, repository, expected):
    # The fixture advertises instructor mode. Student repos must still receive
    # the forced student argument; editable configuration cannot choose the mode.
    (tmp_path / 'project-template-config.json').write_text('{"distribution_mode":"instructor"}')
    executable = tmp_path / 'python'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_TEST_ARGS"\n')
    executable.chmod(0o755)
    args = tmp_path / 'args'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               GITHUB_REPOSITORY=repository, GITHUB_OUTPUT=str(tmp_path / 'output'),
               CAPTURE_TEST_ARGS=str(args))
    result = subprocess.run(['bash', '-c', _integrity_command()], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert args.read_text().splitlines() == ['tools/verify_integrity.py', *expected]
