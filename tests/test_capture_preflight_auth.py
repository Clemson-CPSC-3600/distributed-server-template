"""Preflight checks synthetic push transports without retrieving credentials."""
import os
import subprocess

import pytest

from tools import preflight


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('GIT_') or key in ('CI', 'GITHUB_ACTIONS'):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('VIRTUAL_ENV', str(tmp_path / 'venv'))
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'project-template-config.json').write_text('{"capture_enabled":true}')
    return tmp_path


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)


@pytest.mark.parametrize('url', ['git@example.invalid:course/project.git',
                               'ssh://git@example.invalid/course/project.git',
                               '/synthetic/local.git', 'file:///synthetic/local.git'])
def test_non_http_push_does_not_require_credential_helper(repo, url):
    git(repo, 'remote', 'add', 'origin', url)
    assert preflight.check_environment(repo) == []


def test_url_scoped_https_helper_is_recognized_without_execution(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    git(repo, 'config', 'credential.https://example.invalid.helper', '!never-execute-synthetic-helper')
    assert preflight.check_environment(repo) == []


def test_url_scoped_empty_reset_cancels_generic_helper(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    git(repo, 'config', 'credential.helper', 'synthetic-helper')
    git(repo, 'config', 'credential.https://example.invalid.helper', '')
    assert any('credential helper' in item for item in preflight.check_environment(repo))


def test_helper_after_scoped_reset_remains_usable(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    git(repo, 'config', 'credential.https://example.invalid.helper', '')
    git(repo, 'config', '--add', 'credential.https://example.invalid.helper', 'synthetic-helper')
    assert preflight.check_environment(repo) == []


def test_uses_push_destination_instead_of_https_fetch_destination(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    git(repo, 'remote', 'set-url', '--push', 'origin', 'git@example.invalid:course/project.git')
    assert preflight.check_environment(repo) == []


def test_checks_every_https_push_destination(repo):
    git(repo, 'remote', 'add', 'origin', 'git@example.invalid:course/project.git')
    git(repo, 'remote', 'set-url', '--add', '--push', 'origin', 'https://one.invalid/project.git')
    git(repo, 'remote', 'set-url', '--add', '--push', 'origin', 'https://two.invalid/project.git')
    git(repo, 'config', 'credential.https://one.invalid.helper', 'synthetic-helper')
    assert any('credential helper' in item for item in preflight.check_environment(repo))
    git(repo, 'config', 'credential.https://two.invalid.helper', 'synthetic-helper')
    assert preflight.check_environment(repo) == []


def test_push_url_rewrite_is_respected(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    git(repo, 'config', 'url.ssh://git@example.invalid/.pushInsteadOf', 'https://example.invalid/')
    assert preflight.check_environment(repo) == []


def test_missing_https_helper_still_blocks(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    assert any('credential helper' in item for item in preflight.check_environment(repo))


def test_ci_still_bypasses_helper_requirement(repo, monkeypatch):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/course/project.git')
    monkeypatch.setenv('CI', 'true')
    assert preflight.check_environment(repo) == []


def test_missing_origin_reports_only_remote_problem(repo):
    failures = preflight.check_environment(repo)
    assert len(failures) == 1 and 'origin' in failures[0]


def test_later_generic_reset_cancels_prior_scoped_helper(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/project.git')
    git(repo, 'config', 'credential.https://example.invalid.helper', 'synthetic-helper')
    git(repo, 'config', 'credential.helper', '')
    assert any('credential helper' in item for item in preflight.check_environment(repo))


def test_later_generic_helper_restores_prior_scoped_reset(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/project.git')
    git(repo, 'config', 'credential.https://example.invalid.helper', '')
    git(repo, 'config', 'credential.helper', 'synthetic-helper')
    assert preflight.check_environment(repo) == []


def test_multiline_helper_is_one_nonempty_value(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/project.git')
    git(repo, 'config', 'credential.helper', '!never-execute-synthetic-helper\n\n')
    assert preflight.check_environment(repo) == []


def test_nonmatching_scoped_reset_does_not_cancel_generic_helper(repo):
    git(repo, 'remote', 'add', 'origin', 'https://example.invalid/project.git')
    git(repo, 'config', 'credential.helper', 'synthetic-helper')
    git(repo, 'config', 'credential.https://other.invalid.helper', '')
    assert preflight.check_environment(repo) == []
