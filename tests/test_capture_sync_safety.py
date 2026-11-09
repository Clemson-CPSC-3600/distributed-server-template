"""Sync preflights synthetic targets before changing any package files."""
import json
import os
from pathlib import Path

import pytest

from tools.capture_release import stamp, sync


@pytest.fixture
def pair(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    for root in (source, target):
        root.mkdir()
        (root / "project-template-config.json").write_text('{"capture_enabled":true}')
    (source / "tools").mkdir()
    (source / "tools/preflight.py").write_text("# new capture\n")
    stamp(source, paths=["tools/preflight.py"])
    return source, target


def link(path, destination, directory=False):
    try:
        path.symlink_to(destination, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Symlink creation unavailable: {error}")


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("kind", ["file", "parent", "floor"])
def test_symlink_destinations_rejected_before_any_write(pair, tmp_path, apply, kind):
    source, target = pair
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.py"
    victim.write_text("# preserved\n")
    if kind == "floor":
        link(target / "assignment-config.json", outside / "missing.json")
    elif kind == "parent":
        link(target / "tools", outside, directory=True)
    else:
        (target / "tools").mkdir()
        (target / "src").mkdir()
        victim = target / "src/main.py"
        victim.write_text("# preserved\n")
        link(target / "tools/preflight.py", "../src/main.py")
    with pytest.raises(ValueError):
        sync(source, target, apply=apply, course_start="2026-08-19")
    assert victim.read_text() == "# preserved\n"
    assert not (target / "tools/CAPTURE_RELEASE.json").exists()
    assert not (outside / "missing.json").exists()
    if kind != "floor":
        assert not (target / "assignment-config.json").exists()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("content", ['{"course_start":null}', '{"course_start":"bad-date"}', '{broken', '{}', '[]'])
def test_invalid_existing_floor_leaves_package_untouched(pair, apply, content):
    source, target = pair
    floor = target / "assignment-config.json"
    floor.write_text(content)
    with pytest.raises(ValueError, match="course_start"):
        sync(source, target, apply=apply, course_start="2026-08-19")
    assert floor.read_text() == content
    assert not (target / "tools").exists()


@pytest.mark.parametrize("content", ['{"course_start":"2026-08-19"}\n', '{"course_start":"2026-08-19T08:00:00-05:00","section":"synthetic"}\n'])
def test_existing_valid_floor_preserved_with_direct_destinations(pair, content):
    source, target = pair
    floor = target / "assignment-config.json"
    floor.write_bytes(content.encode())
    planned = sync(source, target)
    assert not (target / "tools").exists()
    assert sync(source, target, apply=True)["actions"] == planned["actions"]
    assert floor.read_bytes() == content.encode()
    assert (target / "tools/preflight.py").read_text() == "# new capture\n"


def test_missing_floor_and_explicit_root_symlink(pair, tmp_path):
    source, target = pair
    selected = tmp_path / "selected-project"
    link(selected, target, directory=True)
    planned = sync(source, selected, course_start="20260819")
    assert not (target / "assignment-config.json").exists()
    assert sync(source, selected, apply=True, course_start="20260819")["actions"] == planned["actions"]
    assert json.loads((target / "assignment-config.json").read_text())["course_start"] == "2026-08-19"


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("kind", ["directory_destination", "file_ancestor"])
def test_nonregular_destinations_rejected_before_floor_creation(pair, apply, kind):
    source, target = pair
    if kind == "directory_destination":
        (target / "tools/preflight.py").mkdir(parents=True)
    else:
        (target / "tools").write_text("preserve ancestor")
    with pytest.raises(ValueError):
        sync(source, target, apply=apply, course_start="2026-08-19")
    assert not (target / "assignment-config.json").exists()


def test_genuine_instructor_null_floor_is_preserved(pair):
    source, target = pair
    (target / ".claude-student").mkdir()
    (target / "create-assignment.sh").write_text("# instructor layout\n")
    (target / "project-template-config.json").write_text('{"capture_enabled":false,"distribution_mode":"instructor"}')
    floor = target / "assignment-config.json"
    floor.write_text('{"course_start":null}\n')
    sync(source, target, apply=True)
    assert floor.read_text() == '{"course_start":null}\n'


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("name", ["src./main.py", "src /main.py", "tools/file:stream", "src"])
def test_windows_path_aliases_rejected_before_floor_creation(pair, apply, name):
    source, target = pair
    # These spellings are filesystem aliases on Windows, so construct just the
    # synthetic manifest here; sync must reject it before considering writes.
    from unittest.mock import patch
    manifest = source / "tools/CAPTURE_RELEASE.json"
    content = json.loads(manifest.read_text())
    content["files"][name] = "synthetic"
    manifest.write_text(json.dumps(content))
    with patch("tools.capture_release.verify", return_value={"ok": True}):
        with pytest.raises(ValueError):
            sync(source, target, apply=apply, course_start="2026-08-19")
    assert not (target / "assignment-config.json").exists()
    assert not (target / "tools").exists()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("location", ["protected_source", "outside"])
def test_hardlinked_destination_rejected_before_floor_creation(pair, tmp_path, apply, location):
    source, target = pair
    victim = (target / "src/main.py" if location == "protected_source"
              else tmp_path / "outside.py")
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("# preserve shared inode\n")
    (target / "tools").mkdir()
    try:
        os.link(victim, target / "tools/preflight.py")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Hardlink creation unavailable: {error}")
    with pytest.raises(ValueError, match="shared inode"):
        sync(source, target, apply=apply, course_start="2026-08-19")
    assert victim.read_text() == "# preserve shared inode\n"
    assert not (target / "assignment-config.json").exists()
    assert not (target / "tools/CAPTURE_RELEASE.json").exists()


@pytest.mark.parametrize("apply", [False, True])
def test_hardlinked_floor_rejected_before_package_mutation(pair, tmp_path, apply):
    source, target = pair
    shared = tmp_path / "shared-floor.json"
    shared.write_text('{"course_start":"2026-08-19"}\n')
    try:
        os.link(shared, target / "assignment-config.json")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Hardlink creation unavailable: {error}")
    with pytest.raises(ValueError, match="shared inode"):
        sync(source, target, apply=apply)
    assert shared.read_text() == '{"course_start":"2026-08-19"}\n'
    assert not (target / "tools").exists()
