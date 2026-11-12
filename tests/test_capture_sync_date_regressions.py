"""Synthetic sync plans must create valid floors without partial writes."""
import json

import pytest

from tools.capture_release import stamp, sync, verify


@pytest.fixture
def package_pair(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    for root in (source, target):
        root.mkdir()
        (root / "project-template-config.json").write_text('{"capture_enabled":true}')
    (source / "capture.py").write_text("pass\n")
    stamp(source, paths=["capture.py"])
    return source, target


@pytest.mark.parametrize("value", ["2026-08-19", "20260819", "2026-W34-3"])
def test_sync_normalizes_floor_identically_in_plan_and_apply(package_pair, value):
    source, target = package_pair
    planned = sync(source, target, course_start=value)
    assert not (target / "assignment-config.json").exists()
    applied = sync(source, target, apply=True, course_start=value)
    assert planned["actions"] == applied["actions"]
    floor_action = next(action for action in planned["actions"]
                        if action["path"] == "assignment-config.json")
    assert floor_action["course_start"] == "2026-08-19"
    assert json.loads((target / "assignment-config.json").read_text()) == {
        "course_start": "2026-08-19"}
    assert verify(target)["ok"]


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-02-30",
                                  "2026-08-19T00:00:00Z"])
def test_missing_or_malformed_floor_leaves_target_untouched(package_pair, apply, value):
    source, target = package_pair
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError):
        sync(source, target, apply=apply, course_start=value)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_sync_preserves_existing_floor_verbatim(package_pair):
    source, target = package_pair
    floor = target / "assignment-config.json"
    original = b'{"course_start":"2026-01-12T08:00:00-05:00","section":"synthetic"}\n'
    floor.write_bytes(original)
    planned = sync(source, target, course_start="invalid-but-unused")
    applied = sync(source, target, apply=True, course_start="invalid-but-unused")
    assert planned["actions"] == applied["actions"]
    assert floor.read_bytes() == original
    assert verify(target)["ok"]


@pytest.mark.parametrize("apply", [False, True])
def test_protected_package_is_rejected_before_creating_floor(package_pair, apply):
    source, target = package_pair
    (source / "src").mkdir()
    (source / "src/main.py").write_text("pass\n")
    stamp(source, paths=["capture.py", "src/main.py"])
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError, match="assignment content"):
        sync(source, target, apply=apply, course_start="2026-08-19")
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


@pytest.mark.parametrize("apply", [False, True])
def test_packaged_assignment_config_cannot_overwrite_existing_floor(package_pair, apply):
    source, target = package_pair
    (source / "assignment-config.json").write_text('{"course_start":"2026-08-19"}')
    (target / "assignment-config.json").write_text('{"course_start":"2026-01-12"}')
    stamp(source, paths=["capture.py", "assignment-config.json"])
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError, match="assignment content"):
        sync(source, target, apply=apply)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("name", ["./assignment-config.json", "./src/main.py", "src//main.py", r".\assignment-config.json"])
def test_noncanonical_manifest_paths_cannot_bypass_protection(package_pair, apply, name):
    source, target = package_pair
    (source / "assignment-config.json").write_text('{"course_start":"2026-08-19"}')
    candidate = source / name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text('{"course_start":"2026-08-19"}' if 'assignment-config' in name else 'pass\n')
    (target / "assignment-config.json").write_text('{"course_start":"2026-01-12"}')
    stamp(source, paths=["capture.py", name])
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError, match="noncanonical"):
        sync(source, target, apply=apply)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('name', ['SRC/main.py', 'Assignment-Config.json', 'protocol.md'])
def test_case_variants_cannot_target_protected_windows_paths(package_pair, apply, name):
    source, target = package_pair
    (source / 'assignment-config.json').write_text('{"course_start":"2026-08-19"}')
    candidate = source / name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text('{"course_start":"2026-08-19"}' if 'config' in name.lower() else 'synthetic\n')
    (target / 'assignment-config.json').write_text('{"course_start":"2026-01-12"}')
    stamp(source, paths=['capture.py', name])
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError, match='assignment content'):
        sync(source, target, apply=apply)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before
