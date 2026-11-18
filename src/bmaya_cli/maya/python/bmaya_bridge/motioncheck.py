"""Motion checks on sampled world poses and on keys. Pure Python: no maya import.

Distances come in centimetres and go out in scene units (`unit` is scene units per cm).
Thresholds scale with the size of what is checked, so they hold for a coin and a giant.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

Vec = tuple[float, float, float]
Quat = tuple[float, float, float, float]

NEIGHBOURS = 3
POP_RATIO = 4.0
POP_MOVE = 0.03
POP_TURN = 25.0
FLIP_TURN = 90.0
FLIP_RATIO = 3.0
PLANT_HEIGHT = 0.02
PLANT_RISE = 0.005
SLIDE_SPEED = 0.004
SLIDE_STEPS = 2
SINK = 0.005
LOOP_MOVE = 0.005
LOOP_TURN = 2.0
LOOP_SPEED_RATIO = 2.0
LOOP_SPEED_FLOOR = 0.002
EULER_JUMP = 180.0
SUBFRAME = 1e-3
DEFAULT_SIZE = 100.0


class Track(NamedTuple):
    path: str
    name: str
    positions: list[Vec]
    rotations: list[Quat]


class Curve(NamedTuple):
    node: str
    attr: str
    times: list[float]
    values: list[float]
    angular: bool


class Setup(NamedTuple):
    frames: list[float]
    spans: list[tuple[int, int]]
    size: float
    unit: float
    up: int
    fps: float


def rnd(value: float, digits: int = 2) -> float | int:
    rounded = round(float(value), digits)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def label(value: float) -> float | int:
    return rnd(value, 3)


def distance(a: Vec, b: Vec) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def minus(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def length(a: Vec) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def turn(a: Quat, b: Quat) -> float:
    """Angle in degrees between two orientations."""
    dot = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def conjugate(q: Quat) -> Quat:
    return (-q[0], -q[1], -q[2], q[3])


def multiply(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def reference_size(points: list[Vec]) -> float:
    """The largest extent of the points, in cm; 0 when there is only one point."""
    if len(points) < 2:
        return 0.0
    return max(max(p[axis] for p in points) - min(p[axis] for p in points) for axis in range(3))


def spans(frames: list[float], clip_ranges: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """Index spans of frames to check as one piece each: one per clip the frames cover.

    Steps that cross from one clip into the next are never compared, and frames between
    clips are left out. Without clips the whole range is one span.
    """
    whole = [(0, len(frames) - 1)] if len(frames) > 1 else []
    found = []
    for start, end in sorted(clip_ranges):
        inside = [i for i, at in enumerate(frames) if start - 1e-6 <= at <= end + 1e-6]
        if len(inside) > 1:
            found.append((inside[0], inside[-1]))
    merged: list[tuple[int, int]] = []
    for first, last in found:
        if merged and first < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    return merged or whole


def steps_of(setup: Setup) -> list[tuple[int, int]]:
    return [(i, i + 1) for first, last in setup.spans for i in range(first, last)]


def per_frame(setup: Setup, index: int) -> float:
    return setup.frames[index + 1] - setup.frames[index]


def spikes(values: list[float], ratio: float, floor: float) -> list[tuple[int, float, float]]:
    found = []
    for index, value in enumerate(values):
        around = (
            values[max(0, index - NEIGHBOURS) : index] + values[index + 1 : index + 1 + NEIGHBOURS]
        )
        if not around:
            continue
        usual = median(around)
        if value > ratio * usual and value - usual > floor:
            found.append((index, value, usual))
    return found


def runs(hits: list[tuple[int, float, float]]) -> list[list[tuple[int, float, float]]]:
    grouped: list[list[tuple[int, float, float]]] = []
    for hit in hits:
        if grouped and hit[0] == grouped[-1][-1][0] + 1:
            grouped[-1].append(hit)
        else:
            grouped.append([hit])
    return grouped


def track_stats(track: Track, setup: Setup) -> dict[str, Any]:
    path = 0.0
    peak = 0.0
    peak_at = setup.frames[0]
    for index, following in steps_of(setup):
        moved = distance(track.positions[index], track.positions[following])
        path += moved
        speed = moved / per_frame(setup, index)
        if speed > peak:
            peak = speed
            peak_at = setup.frames[following]
    heights = [p[setup.up] for p in track.positions]
    return {
        "name": track.name,
        "path": rnd(path * setup.unit),
        "height": [rnd(min(heights) * setup.unit), rnd(max(heights) * setup.unit)],
        "peak_speed": rnd(peak * setup.fps * setup.unit),
        "peak_at": label(peak_at),
    }


def span_pops(track: Track, setup: Setup, first: int, last: int) -> list[dict[str, Any]]:
    moves = []
    turns = []
    for index in range(first, last):
        frames = per_frame(setup, index)
        moves.append(distance(track.positions[index], track.positions[index + 1]) / frames)
        turns.append(turn(track.rotations[index], track.rotations[index + 1]) / frames)

    issues = []
    flipped = set()
    for index, value, _ in spikes(turns, FLIP_RATIO, 0.0):
        if value >= FLIP_TURN:
            flipped.add(index)
            at = first + index
            issues.append(
                {
                    "type": "flip",
                    "node": track.name,
                    "frames": [label(setup.frames[at]), label(setup.frames[at + 1])],
                    "turn": rnd(value, 1),
                }
            )

    def add_pops(hits: list[tuple[int, float, float]], key: str, scale: float) -> None:
        for run in runs([hit for hit in hits if hit[0] not in flipped]):
            at = first + run[0][0]
            end = first + run[-1][0] + 1
            issues.append(
                {
                    "type": "pop",
                    "node": track.name,
                    "frames": [label(setup.frames[at]), label(setup.frames[end])],
                    key: rnd(max(hit[1] for hit in run) * scale, 2 if key == "move" else 1),
                    "usual": rnd(run[0][2] * scale, 2 if key == "move" else 1),
                }
            )

    add_pops(spikes(moves, POP_RATIO, POP_MOVE * setup.size), "move", setup.unit)
    add_pops(spikes(turns, POP_RATIO, POP_TURN), "turn", 1.0)
    return issues


def pop_issues(tracks: list[Track], setup: Setup) -> list[dict[str, Any]]:
    issues = []
    for track in tracks:
        for first, last in setup.spans:
            issues.extend(span_pops(track, setup, first, last))
    return issues


def horizontal(vector: Vec, up: int) -> Vec:
    flat = list(vector)
    flat[up] = 0.0
    return (flat[0], flat[1], flat[2])


def planted_steps(track: Track, setup: Setup) -> list[int]:
    heights = [p[setup.up] for p in track.positions]
    lowest = min(heights)
    near = PLANT_HEIGHT * setup.size
    rise = PLANT_RISE * setup.size
    planted = []
    for index, following in steps_of(setup):
        low = heights[index] - lowest < near and heights[following] - lowest < near
        still = abs(heights[following] - heights[index]) / per_frame(setup, index) < rise
        if low and still:
            planted.append(index)
    return planted


def velocity(track: Track, setup: Setup, index: int) -> Vec:
    moved = minus(track.positions[index + 1], track.positions[index])
    frames = per_frame(setup, index)
    return (moved[0] / frames, moved[1] / frames, moved[2] / frames)


def treadmill(contacts: list[Track], setup: Setup, planted: dict[str, list[int]]) -> Vec:
    """The ground's own speed for a cycle in place: the median planted-foot velocity.

    One contact alone cannot tell a treadmill from a slide, so it gets none.
    """
    if len(contacts) < 2:
        return (0.0, 0.0, 0.0)
    speeds = [
        horizontal(velocity(track, setup, index), setup.up)
        for track in contacts
        for index in planted[track.path]
    ]
    if not speeds:
        return (0.0, 0.0, 0.0)
    return (
        median([v[0] for v in speeds]),
        median([v[1] for v in speeds]),
        median([v[2] for v in speeds]),
    )


def index_runs(indices: list[int]) -> list[list[int]]:
    grouped: list[list[int]] = []
    for index in indices:
        if grouped and index == grouped[-1][-1] + 1:
            grouped[-1].append(index)
        else:
            grouped.append([index])
    return grouped


def contact_report(
    contacts: list[Track], setup: Setup, ground: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    planted = {track.path: planted_steps(track, setup) for track in contacts}
    belt = treadmill(contacts, setup, planted)
    report: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for track in contacts:
        heights = [p[setup.up] for p in track.positions]
        entry: dict[str, Any] = {"lowest": rnd(min(heights) * setup.unit)}
        steps = planted[track.path]
        entry["planted"] = [
            [label(setup.frames[run[0]]), label(setup.frames[run[-1] + 1])]
            for run in index_runs(steps)
        ]
        report[track.name] = entry

        slides = []
        for index in steps:
            drift = minus(horizontal(velocity(track, setup, index), setup.up), belt)
            slides.append((index, length(drift)))
        sliding = [index for index, speed in slides if speed > SLIDE_SPEED * setup.size]
        speeds = dict(slides)
        for run in index_runs(sliding):
            if len(run) < SLIDE_STEPS:
                continue
            total = sum(speeds[index] * per_frame(setup, index) for index in run)
            issues.append(
                {
                    "type": "foot_slide",
                    "node": track.name,
                    "frames": [label(setup.frames[run[0]]), label(setup.frames[run[-1] + 1])],
                    "slide": rnd(total * setup.unit),
                    "speed": rnd(max(speeds[index] for index in run) * setup.unit),
                }
            )

        sunk = [i for i, h in enumerate(heights) if h < ground - SINK * setup.size]
        for run in index_runs(sunk):
            issues.append(
                {
                    "type": "below_ground",
                    "node": track.name,
                    "frames": [label(setup.frames[run[0]]), label(setup.frames[run[-1]])],
                    "depth": rnd((ground - min(heights[i] for i in run)) * setup.unit),
                }
            )
    report["treadmill_speed"] = rnd(length(belt) * setup.fps * setup.unit)
    return report, issues


def loop_report(
    tracks: list[Track], root: Track, setup: Setup
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compares the first and last frame of a cycle; the last frame repeats the first pose."""
    issues: list[dict[str, Any]] = []
    first = setup.spans[0][0] if setup.spans else 0
    last = setup.spans[-1][1] if setup.spans else len(setup.frames) - 1
    travel = minus(root.positions[last], root.positions[first])

    def relative(track: Track, index: int) -> tuple[Vec, Quat]:
        offset = minus(track.positions[index], root.positions[index])
        spin = multiply(conjugate(root.rotations[index]), track.rotations[index])
        return offset, spin

    def relative_velocity(track: Track, index: int) -> Vec:
        before = relative(track, index)[0]
        after = relative(track, index + 1)[0]
        frames = setup.frames[index + 1] - setup.frames[index]
        return (
            (after[0] - before[0]) / frames,
            (after[1] - before[1]) / frames,
            (after[2] - before[2]) / frames,
        )

    for track in tracks:
        if track.path == root.path or last - first < 2:
            continue
        start_offset, start_spin = relative(track, first)
        end_offset, end_spin = relative(track, last)
        move = distance(start_offset, end_offset)
        spun = turn(start_spin, end_spin)
        if move > LOOP_MOVE * setup.size or spun > LOOP_TURN:
            issues.append(
                {
                    "type": "loop_pose",
                    "node": track.name,
                    "frames": [label(setup.frames[first]), label(setup.frames[last])],
                    "move": rnd(move * setup.unit),
                    "turn": rnd(spun, 1),
                }
            )
            continue
        speeds = [relative_velocity(track, index) for index in range(first, last)]
        changes = [length(minus(speeds[i + 1], speeds[i])) for i in range(len(speeds) - 1)]
        out_speed = speeds[0]
        in_speed = speeds[-1]
        jump = length(minus(out_speed, in_speed))
        if jump > max(LOOP_SPEED_RATIO * max(changes), LOOP_SPEED_FLOOR * setup.size):
            issues.append(
                {
                    "type": "loop_speed",
                    "node": track.name,
                    "frames": [label(setup.frames[first]), label(setup.frames[last])],
                    "speed_out": rnd(length(out_speed) * setup.fps * setup.unit),
                    "speed_in": rnd(length(in_speed) * setup.fps * setup.unit),
                }
            )
    return {"root": root.name, "root_travel": [rnd(v * setup.unit) for v in travel]}, issues


def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if "frames" not in a or "frames" not in b:
        return True
    return a["frames"][0] <= b["frames"][1] and b["frames"][0] <= a["frames"][1]


def fold_children(issues: list[dict[str, Any]], tracks: list[Track]) -> list[dict[str, Any]]:
    """A descendant that only inherits its ancestor's problem is counted, not listed."""
    paths = {track.name: track.path for track in tracks}
    kept: list[dict[str, Any]] = []
    for issue in sorted(issues, key=lambda item: paths.get(item["node"], "").count("|")):
        path = paths.get(issue["node"], "")
        owner = None
        for known in kept:
            known_path = paths.get(known["node"], "")
            if not known_path or known["type"] != issue["type"] or not overlaps(known, issue):
                continue
            if path.startswith(known_path + "|"):
                owner = known
                break
        if owner is None:
            kept.append(issue)
        else:
            owner["children"] = owner.get("children", 0) + 1
    return kept


def inside(at: float, ranges: list[tuple[float, float]]) -> bool:
    return any(start - SUBFRAME <= at <= end + SUBFRAME for start, end in ranges)


def key_issues(
    curves: list[Curve], clip_ranges: list[tuple[float, float]], edges: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    """Problems in the keys themselves.

    `clip_ranges` decide which keys are stray: keys outside every one of them. `edges` are
    the clips whose first and last frame should carry a key on every animated channel.
    """
    issues: list[dict[str, Any]] = []
    jumps: dict[str, dict[str, Any]] = {}
    subframes: dict[str, list[float]] = {}
    strays: dict[str, list[float]] = {}
    unkeyed: dict[tuple[str, str, float], int] = {}

    for curve in curves:
        times = curve.times
        if curve.angular:
            for index in range(1, len(times)):
                delta = curve.values[index] - curve.values[index - 1]
                if abs(delta) <= EULER_JUMP:
                    continue
                entry = jumps.setdefault(
                    f"{curve.node}.{curve.attr}",
                    {
                        "type": "euler_jump",
                        "node": curve.node,
                        "attr": curve.attr,
                        "frames": [label(times[index - 1]), label(times[index])],
                        "delta": rnd(delta, 1),
                        "count": 0,
                        "fix": f"cmds.filterCurve(cmds.listConnections('{curve.node}', "
                        "type='animCurveTA'))",
                    },
                )
                entry["count"] += 1
        for at in times:
            if abs(at - round(at)) > SUBFRAME:
                subframes.setdefault(curve.node, []).append(at)
            if clip_ranges and not inside(at, clip_ranges):
                strays.setdefault(curve.node, []).append(at)
        for start, end in edges:
            for edge, name in ((start, "start"), (end, "end")):
                before = any(at < edge - SUBFRAME for at in times)
                after = any(at > edge + SUBFRAME for at in times)
                on = any(abs(at - edge) <= SUBFRAME for at in times)
                if before and after and not on:
                    key = (curve.node, name, edge)
                    unkeyed[key] = unkeyed.get(key, 0) + 1

    for entry in jumps.values():
        if entry["count"] == 1:
            del entry["count"]
        issues.append(entry)
    for node, times in subframes.items():
        issues.append(
            {
                "type": "subframe_keys",
                "node": node,
                "count": len(times),
                "first": label(min(times)),
                "fix": f"cmds.snapKey('{node}', timeMultiple=1)",
            }
        )
    for node, times in strays.items():
        issues.append(
            {"type": "stray_keys", "node": node, "count": len(times), "first": label(min(times))}
        )
    for (node, name, edge), attrs in unkeyed.items():
        issues.append(
            {"type": "clip_edge", "node": node, "edge": name, "frame": label(edge), "attrs": attrs}
        )
    return issues


def limits(size: float, unit: float) -> dict[str, float | int]:
    return {
        "pop": rnd(POP_MOVE * size * unit),
        "slide": rnd(SLIDE_SPEED * size * unit, 3),
        "seam": rnd(LOOP_MOVE * size * unit, 3),
    }
