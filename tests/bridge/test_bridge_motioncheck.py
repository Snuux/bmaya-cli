from __future__ import annotations

import math

from bmaya_bridge import motioncheck as mc

STILL = (0.0, 0.0, 0.0, 1.0)


def setup(
    frames: list[float], clips: list[tuple[float, float]] | None = None, size: float = 100.0
) -> mc.Setup:
    return mc.Setup(frames, mc.spans(frames, clips or []), size, 1.0, 1, 24.0)


def track(path: str, positions: list[mc.Vec], rotations: list[mc.Quat] | None = None) -> mc.Track:
    return mc.Track(path, path.rsplit("|", 1)[-1], positions, rotations or [STILL] * len(positions))


def yaw(degrees: float) -> mc.Quat:
    half = math.radians(degrees) / 2
    return (0.0, math.sin(half), 0.0, math.cos(half))


def frames_to(last: int) -> list[float]:
    return [float(at) for at in range(last + 1)]


def test_a_one_frame_jump_is_a_pop_and_its_child_only_counts() -> None:
    frames = frames_to(20)
    xs = [float(at) + (9.0 if at > 10 else 0.0) for at in range(21)]
    root = track("|root", [(x, 0.0, 0.0) for x in xs])
    child = track("|root|hand", [(x, 10.0, 0.0) for x in xs])
    issues = mc.fold_children(mc.pop_issues([root, child], setup(frames)), [root, child])
    assert issues == [
        {"type": "pop", "node": "root", "frames": [10, 11], "move": 10, "usual": 1, "children": 1}
    ]


def test_eases_holds_and_sudden_starts_are_not_pops() -> None:
    frames = frames_to(24)
    eased = [50.0 * (3 * (at / 24) ** 2 - 2 * (at / 24) ** 3) for at in range(25)]
    punch = [0.0 if at < 10 else 5.0 * (at - 10) for at in range(25)]
    tracks = [
        track("|eased", [(x, 0.0, 0.0) for x in eased]),
        track("|punch", [(x, 0.0, 0.0) for x in punch]),
        track("|turning", [(0.0, 0.0, 0.0)] * 25, [yaw(at * 4.0) for at in range(25)]),
    ]
    assert mc.pop_issues(tracks, setup(frames)) == []


def test_a_half_turn_in_one_frame_is_a_flip() -> None:
    frames = frames_to(20)
    turns = [at * 2.0 + (170.0 if at >= 10 else 0.0) for at in range(21)]
    arm = track("|arm", [(0.0, 0.0, 0.0)] * 21, [yaw(value) for value in turns])
    issues = mc.pop_issues([arm], setup(frames))
    assert [issue["type"] for issue in issues] == ["flip"], issues
    assert issues[0]["frames"] == [9, 10] and issues[0]["turn"] == 172, issues


def test_steps_between_clips_are_not_compared() -> None:
    frames = frames_to(20)
    xs = [float(at) + (50.0 if at > 10 else 0.0) for at in range(21)]
    layout = track("|root", [(x, 0.0, 0.0) for x in xs])
    assert mc.spans(frames, [(0, 10), (11, 20)]) == [(0, 10), (11, 20)]
    assert mc.pop_issues([layout], setup(frames, [(0, 10), (11, 20)])) == []
    assert mc.pop_issues([layout], setup(frames))[0]["frames"] == [10, 11]
    assert mc.spans(frames, [(0, 12), (10, 20)]) == [(0, 20)]
    assert mc.spans(frames, [(0, 10), (10, 20)]) == [(0, 10), (10, 20)]
    assert mc.spans(frames, [(40, 60)]) == [(0, 20)]


def walk_in_place(drift_from: int = 99) -> tuple[mc.Track, mc.Track]:
    left = []
    right = []
    for at in range(21):
        if at <= 10:
            left.append((-2.0 * at, 0.0, 0.0))
            right.append((-20.0 + 2.0 * at, 5.0 * math.sin(math.pi * at / 10), 10.0))
        else:
            left.append((-20.0 + 2.0 * (at - 10), 5.0 * math.sin(math.pi * (at - 10) / 10), 0.0))
            side = 10.0 + max(0, min(at, 17) - drift_from)
            right.append((-2.0 * (at - 10), 0.0, side))
    return track("|foot_l", left), track("|foot_r", right)


def test_a_walk_in_place_has_a_treadmill_but_no_slide() -> None:
    left, right = walk_in_place()
    report, issues = mc.contact_report([left, right], setup(frames_to(20)), 0.0)
    assert issues == []
    assert report["treadmill_speed"] == 48
    assert report["foot_l"] == {"lowest": 0, "planted": [[0, 10]]}
    assert report["foot_r"]["planted"] == [[10, 20]]


def test_a_planted_foot_that_drifts_sideways_slides() -> None:
    left, right = walk_in_place(drift_from=13)
    _, issues = mc.contact_report([left, right], setup(frames_to(20)), 0.0)
    assert issues == [
        {"type": "foot_slide", "node": "foot_r", "frames": [13, 17], "slide": 4, "speed": 1}
    ]


def test_a_foot_under_the_ground_is_reported() -> None:
    heights = [0.0] * 21
    heights[5] = -1.0
    heights[6] = -0.8
    foot = track("|foot", [(0.0, h, 0.0) for h in heights])
    _, issues = mc.contact_report([foot], setup(frames_to(20)), 0.0)
    assert issues == [{"type": "below_ground", "node": "foot", "frames": [5, 6], "depth": 1}]


def test_loop_seam_pose_and_speed() -> None:
    frames = frames_to(24)
    root = track("|root", [(0.0, 0.0, 0.0)] * 25)

    def circle(at: int) -> mc.Vec:
        angle = 2 * math.pi * at / 24
        return (10 * math.cos(angle), 50.0, 10 * math.sin(angle))

    smooth = track("|root|hand", [circle(at) for at in range(25)])
    report, issues = mc.loop_report([root, smooth], root, setup(frames))
    assert issues == [] and report == {"root": "root", "root_travel": [0, 0, 0]}

    shifted = track("|root|hand", [circle(at) for at in range(24)] + [(13.0, 50.0, 0.0)])
    issues = mc.loop_report([root, shifted], root, setup(frames))[1]
    assert issues[0]["type"] == "loop_pose" and issues[0]["move"] == 3, issues

    arc = track("|root|hand", [(at * (24 - at) / 10, 50.0, 0.0) for at in range(25)])
    issues = mc.loop_report([root, arc], root, setup(frames))[1]
    assert [issue["type"] for issue in issues] == ["loop_speed"], issues


def test_key_problems() -> None:
    curves = [
        mc.Curve("arm", "rotateX", [0, 10, 20], [0, 350, 340], True),
        mc.Curve("arm", "translateY", [0, 12.5, 30, 50], [0, 1, 2, 3], False),
        mc.Curve("hip", "translateX", [0, 32], [0, 1], False),
    ]
    issues = mc.key_issues(curves, (0, 60), [(0, 32)], [(0, 32)])
    by_type = {issue["type"]: issue for issue in issues}
    jump = by_type["euler_jump"]
    assert (jump["node"], jump["attr"], jump["frames"], jump["delta"]) == (
        "arm",
        "rotateX",
        [0, 10],
        350,
    )
    assert "filterCurve" in jump["fix"]
    assert by_type["subframe_keys"]["count"] == 1 and by_type["subframe_keys"]["first"] == 12.5
    assert by_type["stray_keys"] == {"type": "stray_keys", "node": "arm", "count": 1, "first": 50}
    assert by_type["clip_edge"] == {
        "type": "clip_edge",
        "node": "arm",
        "edge": "end",
        "frame": 32,
        "attrs": 1,
    }
    assert len(issues) == 4
    elsewhere = mc.key_issues(curves, (100, 200), [(0, 32)], [])
    assert elsewhere == []


def test_size_and_limits() -> None:
    assert mc.reference_size([(0, 0, 0), (10, 176, -5)]) == 176
    assert mc.reference_size([(1, 2, 3)]) == 0
    assert mc.limits(176.0, 1.0) == {"pop": 5.28, "slide": 0.704, "seam": 0.88}
