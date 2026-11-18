from __future__ import annotations

import math
from typing import Any

from maya import cmds

from bmaya_bridge import framespec, motioncheck
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers import _sampling
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    fps,
    frame,
    listing,
    number_text,
    resolve,
    scene_kept,
    text_param,
)
from bmaya_bridge.handlers._timeline import clips, resolve_frames
from bmaya_bridge.handlers.animation import TIME_CURVE_TYPES

MAX_NODES = 200
MAX_FRAMES = 1000
UPSTREAM_LIMIT = 5000
VALUE_LIMIT = 3000
DIGITS = {"doubleLinear": 2, "doubleAngle": 1}
MOTION_STEP = 2.0
NODE_LIMIT = 20
ISSUE_LIMIT = 40
ORDER = (
    "flip",
    "pop",
    "loop_pose",
    "loop_speed",
    "foot_slide",
    "below_ground",
    "euler_jump",
    "clip_edge",
    "subframe_keys",
    "stray_keys",
)


def split_names(raw: str) -> list[str]:
    return [name.strip() for name in raw.split(",") if name.strip()]


def unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def named_nodes(names: list[str]) -> list[str]:
    found = []
    for name in names:
        matches = cmds.ls(name, long=True, type="transform") or []
        if not matches:
            raise BridgeError(
                f"No transform or joint matches '{name}'.",
                hint="`bmaya scene --query NAME` finds names; * and ? work in names.",
            )
        for match in matches:
            found.extend(group_members(match))
    return unique(found)


def group_members(node: str) -> list[str]:
    """A group stands for the transforms under it; any other node for itself."""
    if cmds.nodeType(node) != "transform" or cmds.listRelatives(node, shapes=True):
        return [node]
    below = cmds.listRelatives(node, allDescendents=True, fullPath=True, type="transform")
    members = [path for path in below or [] if not is_camera_or_light(path)]
    return sorted(members) or [node]


def is_camera_or_light(node: str) -> bool:
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    return any(cmds.objectType(shape, isAType="camera") for shape in shapes) or bool(
        cmds.ls(shapes, lights=True)
    )


def animated_transforms() -> list[str]:
    curves = cmds.ls(type=list(TIME_CURVE_TYPES)) or []
    targets = cmds.listConnections(curves, source=False, destination=True) if curves else []
    transforms = cmds.ls(targets or [], long=True, type="transform") or []
    return [node for node in unique(transforms) if not is_camera_or_light(node)]


def pick_nodes(params: dict[str, Any]) -> tuple[list[str], str]:
    names = split_names(text_param(params, "names"))
    if names:
        return named_nodes(names), "names"
    selection = cmds.ls(selection=True, long=True, type="transform") or []
    if selection:
        return selection, "selection"
    joints = cmds.ls(type="joint", long=True) or []
    nodes = unique(joints + animated_transforms())
    if not nodes:
        raise BridgeError(
            "Nothing to check: no names given, nothing selected, no joints and no animation.",
            hint="Name the nodes to check: bmaya motion hand_r foot_l.",
        )
    return nodes, "scene"


def pick_frames(params: dict[str, Any]) -> tuple[list[float], str]:
    chosen = resolve_frames(params)
    if chosen:
        frames, clip = chosen.values, chosen.clip
    else:
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        frames, clip = framespec.parse(f"{framespec.label(start)}:{framespec.label(end)}"), ""
    if len(frames) > MAX_FRAMES:
        raise BridgeError(
            f"{len(frames)} frames to sample; the most is {MAX_FRAMES}.",
            hint="Use a step (--frames 0:2000:4), a shorter range or --clip NAME.",
        )
    if len(frames) < 2:
        raise BridgeError(
            "Motion needs at least two frames.",
            hint="Pass a range: --frames 0:30, or --clip NAME.",
        )
    return frames, clip


def ancestors(path: str) -> list[str]:
    parts = path.split("|")
    return ["|".join(parts[:index]) for index in range(2, len(parts))]


def common_root(paths: list[str]) -> str:
    """The deepest node above or at every path; "" when they share none or it is the only one."""
    split = [path.split("|") for path in paths]
    shared: list[str] = []
    for index in range(min(len(parts) for parts in split)):
        if len({parts[index] for parts in split}) != 1:
            break
        shared.append(split[0][index])
    root = "|".join(shared) if len(shared) > 1 else ""
    if all(path == root for path in paths):
        return ""
    return root


def world_track(count: int) -> motioncheck.Track:
    return motioncheck.Track("", "world", [(0.0, 0.0, 0.0)] * count, [(0.0, 0.0, 0.0, 1.0)] * count)


def driver_curves(paths: list[str], source: str) -> list[str]:
    if source == "scene":
        return cmds.ls(type=list(TIME_CURVE_TYPES)) or []
    nodes = set(paths)
    for path in paths:
        nodes.update(ancestors(path))
    for handle in cmds.ls(type="ikHandle", long=True) or []:
        chain = cmds.ls(cmds.ikHandle(handle, query=True, jointList=True) or [], long=True)
        if nodes.intersection(chain or []):
            nodes.add(handle)
            nodes.update(ancestors(handle))
    return upstream_curves(nodes)


def upstream_curves(start: set[str]) -> list[str]:
    """Time curves feeding the nodes through any chain of connections (constraints, IK).

    listHistory is no use here: on a transform it returns only its shape's history.
    """
    seen = set(start)
    level = sorted(start)
    curves = []
    while level and len(seen) < UPSTREAM_LIMIT:
        sources = cmds.listConnections(level, source=True, destination=False) or []
        level = []
        for node in cmds.ls(unique(sources), long=True) or []:
            if node in seen:
                continue
            seen.add(node)
            if cmds.nodeType(node) in TIME_CURVE_TYPES:
                curves.append(node)
            else:
                level.append(node)
    return curves


def curve_data(curve: str) -> motioncheck.Curve | None:
    plugs = cmds.listConnections(curve + ".output", plugs=True, source=False, destination=True)
    if not plugs:
        return None
    node, _, attr = plugs[0].partition(".")
    times = cmds.keyframe(curve, query=True, timeChange=True) or []
    values = cmds.keyframe(curve, query=True, valueChange=True) or []
    angular = cmds.nodeType(curve) == "animCurveTA"
    return motioncheck.Curve(display_name(node), attr, times, values, angular)


def overlapping(
    ranges: list[tuple[float, float]], frames: list[float]
) -> list[tuple[float, float]]:
    return [(start, end) for start, end in ranges if start <= frames[-1] and end >= frames[0]]


def reference_size(tracks: list[motioncheck.Track], nodes: list[str], unit: float) -> float:
    size = motioncheck.reference_size([track.positions[0] for track in tracks])
    if size >= 1.0:
        return size
    box = cmds.exactWorldBoundingBox(nodes)
    extent = max(box[3] - box[0], box[4] - box[1], box[5] - box[2]) / unit
    return extent if extent >= 1.0 else motioncheck.DEFAULT_SIZE


def contact_paths(params: dict[str, Any]) -> list[str]:
    return [resolve(name) for name in split_names(text_param(params, "contacts"))]


def sort_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(issue: dict[str, Any]) -> tuple[int, float]:
        frames = issue.get("frames") or [issue.get("frame", issue.get("first", 0))]
        return ORDER.index(issue["type"]), float(frames[0])

    return sorted(issues, key=key)


def attr_plugs(node: str, names: list[str]) -> list[tuple[str, str]]:
    plugs = []
    for name in names:
        if not cmds.attributeQuery(name, node=node, exists=True):
            keyable = cmds.listAttr(node, keyable=True) or []
            raise BridgeError(
                f"'{display_name(node)}' has no attribute '{name}'.",
                hint="Its keyable attributes: " + ", ".join(keyable[:15]) + ".",
            )
        long_name = cmds.attributeQuery(name, node=node, longName=True)
        children = cmds.attributeQuery(long_name, node=node, listChildren=True) or []
        for attr in children or [long_name]:
            plugs.append((attr, f"{node}.{attr}"))
    return plugs


def stride_for(count: int, columns: int) -> int:
    if columns * 2 > VALUE_LIMIT:
        raise BridgeError(
            f"{columns} value columns are too many to list.",
            hint="Name fewer nodes, or fewer attributes in --attrs.",
        )
    return max(1, math.ceil(count * columns / VALUE_LIMIT))


def world_values(
    nodes: list[str], tracks: dict[str, motioncheck.Track], frames: list[float], unit: float
) -> dict[str, Any]:
    stride = stride_for(len(frames), len(nodes) * 3)
    picked = range(0, len(frames), stride)
    table = {}
    for path in nodes:
        positions = tracks[path].positions
        table[tracks[path].name] = {
            axis: [motioncheck.rnd(positions[i][index] * unit) for i in picked]
            for index, axis in enumerate("xyz")
        }
    return {"frames": framespec.as_spec([frames[i] for i in picked]), "nodes": table}


def attr_values(nodes: list[str], frames: list[float], attrs: list[str]) -> dict[str, Any]:
    columns = [(path, attr, plug) for path in nodes for attr, plug in attr_plugs(path, attrs)]
    picked = frames[:: stride_for(len(frames), len(columns))]
    samples = _sampling.sample_plugs(
        [plug for _, _, plug in columns], picked, _sampling.deadline_after()
    )
    table: dict[str, dict[str, list[float | int]]] = {}
    for index, (path, attr, plug) in enumerate(columns):
        digits = DIGITS.get(cmds.getAttr(plug, type=True), 3)
        rounded = [motioncheck.rnd(value, digits) for value in samples[index]]
        table.setdefault(display_name(path), {})[attr] = rounded
    return {"frames": framespec.as_spec(picked), "nodes": table}


def get_motion(params: dict[str, Any]) -> dict[str, Any]:
    nodes, source = pick_nodes(params)
    sampled_total = len(nodes)
    nodes = nodes[:MAX_NODES]
    frames, clip = pick_frames(params)
    contacts = contact_paths(params)
    loop = bool_param(params, "loop")
    unit = _sampling.linear_scale()
    ground = (number_text(params, "ground") or 0.0) / unit

    root = common_root(nodes + contacts) if loop else ""
    hidden = [path for path in [root] + contacts if path and path not in nodes]
    paths = nodes + hidden
    attrs = split_names(text_param(params, "attrs"))
    with scene_kept():
        poses = _sampling.sample_poses(paths, frames, _sampling.deadline_after())
        table = attr_values(nodes, frames, attrs) if attrs else None
    tracks = {
        path: motioncheck.Track(
            path,
            display_name(path),
            [pose.position for pose in poses[index]],
            [pose.rotation for pose in poses[index]],
        )
        for index, path in enumerate(paths)
    }
    shown = [tracks[path] for path in nodes]

    all_clips = [(float(c["range"][0]), float(c["range"][1])) for c in clips()]
    near = overlapping(all_clips, frames)
    size = reference_size(shown, nodes, unit)
    step = framespec.uniform_step(frames)
    setup = motioncheck.Setup(
        frames, motioncheck.spans(frames, near), size, unit, _sampling.up_index(), fps()
    )

    result: dict[str, Any] = {"range": [frame(frames[0]), frame(frames[-1])]}
    if clip:
        result["clip"] = clip
    result.update(
        {
            "step": frame(step) if step else "uneven",
            "fps": frame(fps()),
            "unit": cmds.currentUnit(query=True, linear=True),
            "up": "z" if setup.up == 2 else "y",
            "size": motioncheck.rnd(size * unit),
            "limits": motioncheck.limits(size, unit),
        }
    )
    if len(near) > 1:
        result["clips_checked"] = [[frame(a), frame(b)] for a, b in near]

    issues: list[dict[str, Any]] = []
    stats = [motioncheck.track_stats(track, setup) for track in shown]
    stats.sort(key=lambda entry: -entry["path"])
    result["nodes"] = {**listing(stats, NODE_LIMIT), "source": source}
    if sampled_total > MAX_NODES:
        result["nodes"]["sampled"] = MAX_NODES
        result["nodes"]["total"] = sampled_total

    if step and step <= MOTION_STEP:
        issues.extend(motioncheck.pop_issues(shown, setup))
        if contacts:
            report, found = motioncheck.contact_report(
                [tracks[path] for path in contacts], setup, ground
            )
            result["contacts"] = report
            issues.extend(found)
        if loop:
            anchor = tracks[root] if root else world_track(len(frames))
            info, found = motioncheck.loop_report(shown, anchor, setup, set(contacts))
            result["loop"] = info
            issues.extend(found)
    else:
        result["note"] = (
            "Motion checks need evenly spaced frames at most 2 apart, so only the keys were "
            "checked. Use --frames A:B or A:B:2."
        )

    curves = [data for data in map(curve_data, driver_curves(nodes, source)) if data]
    anim_range = (
        cmds.playbackOptions(query=True, animationStartTime=True),
        cmds.playbackOptions(query=True, animationEndTime=True),
    )
    window = (frames[0], frames[-1])
    issues.extend(motioncheck.key_issues(curves, window, all_clips or [anim_range], near))

    issues = sort_issues(motioncheck.fold_children(issues, list(tracks.values())))
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["type"]] = counts.get(issue["type"], 0) + 1
    result["issues"] = {**listing(issues, ISSUE_LIMIT), "counts": counts}
    if table:
        result["values"] = table
    elif bool_param(params, "values"):
        result["values"] = world_values(nodes, tracks, frames, unit)
    return result
