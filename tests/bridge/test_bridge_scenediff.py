from bmaya_bridge import scenediff
from bmaya_bridge.scenediff import Node


def test_compare_finds_added_deleted_renamed_and_changed() -> None:
    before = {
        "a": Node("crate", "transform", {"translate": (0.0, 0.0, 0.0), "visibility": True}),
        "b": Node("crateShape", "mesh", {"verts": 8, "points": 1.0}),
        "c": Node("old_light", "transform", {}),
    }
    after = {
        "a": Node("crate_v2", "transform", {"translate": (0.0, 1.0, 0.0), "visibility": True}),
        "b": Node("crate_v2Shape", "mesh", {"verts": 8, "points": 1.5}),
        "d": Node("pCube1", "transform", {}),
    }
    found = scenediff.compare(before, after, {"fps": 30.0}, {"fps": 24.0})
    assert found.added == [{"name": "pCube1", "type": "transform"}]
    assert found.deleted == [{"name": "old_light", "type": "transform"}]
    assert found.renamed == [
        {"from": "crate", "to": "crate_v2"},
        {"from": "crateShape", "to": "crate_v2Shape"},
    ]
    assert found.changed == [
        {"name": "crate_v2", "type": "transform", "what": ["translate"]},
        {"name": "crate_v2Shape", "type": "mesh", "what": ["points"]},
    ]
    assert found.settings == ["fps"]


def test_nothing_changed_is_empty() -> None:
    nodes = {"a": Node("crate", "transform", {"translate": (1.0, 2.0, 3.0)})}
    found = scenediff.compare(nodes, dict(nodes), {"fps": 30.0}, {"fps": 30.0})
    assert found == scenediff.Changes([], [], [], [], [])


def test_checksum_sees_a_moved_value_and_ignores_noise() -> None:
    values = [0.1 * index for index in range(1000)]
    base = scenediff.checksum(values)
    moved = list(values)
    moved[500] += 0.01
    assert scenediff.checksum(moved) != base
    noisy = [value + 1e-9 for value in values]
    assert scenediff.checksum(noisy) == base
    swapped = list(values)
    swapped[10], swapped[11] = swapped[11], swapped[10]
    assert scenediff.checksum(swapped) != base
