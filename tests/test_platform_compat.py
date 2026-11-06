"""Unit tests for platform_compat.path_within_repo — the shared containment
helper used by codex_ingest, claude_ingest, and vscode_chat_ingest to decide
whether a recorded cwd/workspace folder belongs to the course repo.

Covers the required containment behaviors:
  * exact match and true subdirectory match ("subdir" tests, exercised via
    the ingester-level tests too)
  * sibling-name-prefix exclusion (``repo-2`` must NOT match ``repo`` just
    because the string starts with the same characters) — this is what
    forces a post-resolve ``Path.is_relative_to``-equivalent implementation
    instead of naive ``str.startswith``
  * a foreign project nested inside the repo (its own ``.git`` directory)
    must be excluded — privacy: its transcripts must not enter the graded
    repo
  * a git worktree nested inside the repo (``.git`` as a FILE, not a
    directory) is treated the same as a foreign nested repo and excluded
  * darwin case-insensitivity: a candidate path that differs only in case
    from the real repo path must still match on macOS's default
    case-insensitive-but-case-preserving filesystem
"""
import sys

import pytest

from tests._capture import platform_compat


def test_exact_match_is_within(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert platform_compat.path_within_repo(repo, repo) is True


def test_true_subdirectory_is_within(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    assert platform_compat.path_within_repo(repo / "src", repo) is True


def test_sibling_with_prefixed_name_is_not_within(tmp_path):
    """``repo-2`` starts with the same characters as ``repo`` but is a
    completely different directory. A naive string-prefix check would
    wrongly treat anything under ``repo-2`` as being inside ``repo``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sibling = tmp_path / "repo-2"
    (sibling / "src").mkdir(parents=True)

    assert platform_compat.path_within_repo(sibling / "src", repo) is False


def test_unrelated_directory_is_not_within(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()

    assert platform_compat.path_within_repo(other, repo) is False


def test_nested_foreign_repo_is_excluded(tmp_path):
    """A directory under the course repo that has its own ``.git`` dir is a
    separately version-controlled foreign project; its cwd must NOT match
    the course repo, or its transcripts would leak into a graded commit."""
    repo = tmp_path / "repo"
    nested = repo / "vendor" / "some-other-project"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    (nested / "src").mkdir()

    assert platform_compat.path_within_repo(nested / "src", repo) is False
    assert platform_compat.path_within_repo(nested, repo) is False


def test_nested_git_worktree_is_excluded(tmp_path):
    """A git worktree's ``.git`` is a FILE (``gitdir: <path>``), not a
    directory. It still marks an independently version-controlled subtree,
    so it is excluded the same way a nested foreign repo is."""
    repo = tmp_path / "repo"
    nested = repo / "vendor" / "worktree-project"
    nested.mkdir(parents=True)
    (nested / ".git").write_text("gitdir: /somewhere/else/.git\n", encoding="utf-8")
    (nested / "src").mkdir()

    assert platform_compat.path_within_repo(nested / "src", repo) is False


def test_repos_own_git_dir_does_not_exclude_it(tmp_path):
    """The repo's own top-level ``.git`` is expected and must not cause the
    repo (or paths under it) to be treated as foreign."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()

    assert platform_compat.path_within_repo(repo, repo) is True
    assert platform_compat.path_within_repo(repo / "src", repo) is True


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only case-insensitive filesystem behavior")
def test_darwin_case_insensitive_match(tmp_path):
    """Default macOS APFS/HFS+ volumes are case-insensitive but
    case-preserving: a differently-cased candidate path names the same file
    on disk as the real repo path and must still match."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    flipped = tmp_path / "REPO" / "src"

    assert platform_compat.path_within_repo(flipped, repo) is True
