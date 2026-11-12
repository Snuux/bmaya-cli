from __future__ import annotations

from typing import Any

from maya import cmds

from bmaya_bridge.handlers._common import (
    anim_curves,
    display_name,
    frame,
    key_times,
    listing,
    resolve,
    text_param,
    time_info,
)

NODE_LIMIT = 30
CURVE_LIMIT = 60
LIST_LIMIT = 20
TIME_CURVE_TYPES = ("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT")


def key_range(times: list[float]) -> list[float | int]:
    return [frame(min(times)), frame(max(times))]


def curve_targets(curve: str) -> list[str]:
    return cmds.listConnections(curve + ".output", source=False, destination=True) or []


def animated_nodes(curves: list[str]) -> list[dict[str, Any]]:
    by_node: dict[str, dict[str, Any]] = {}
    for curve in curves:
        times = key_times(curve)
        for node in curve_targets(curve):
            entry = by_node.setdefault(
                node, {"name": node, "curves": 0, "first": None, "last": None}
            )
            entry["curves"] += 1
            if times:
                first = min(times)
                last = max(times)
                entry["first"] = first if entry["first"] is None else min(entry["first"], first)
                entry["last"] = last if entry["last"] is None else max(entry["last"], last)

    result = []
    for entry in by_node.values():
        item: dict[str, Any] = {"name": entry["name"], "curves": entry["curves"]}
        if entry["first"] is not None:
            item["range"] = [frame(entry["first"]), frame(entry["last"])]
        result.append(item)
    return result


def skeleton_roots() -> list[dict[str, Any]]:
    roots = []
    for joint in cmds.ls(type="joint", long=True) or []:
        parent = cmds.listRelatives(joint, parent=True, fullPath=True) or []
        if parent and cmds.nodeType(parent[0]) == "joint":
            continue
        descendants = cmds.listRelatives(joint, allDescendents=True, type="joint") or []
        roots.append({"name": display_name(joint), "joints": len(descendants) + 1})
    return roots


def owner_name(shape: str) -> str:
    if cmds.objectType(shape, isAType="shape"):
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents:
            return display_name(parents[0])
    return display_name(shape)


def skin_clusters() -> list[dict[str, Any]]:
    result = []
    for skin in cmds.ls(type="skinCluster") or []:
        influences = cmds.skinCluster(skin, query=True, influence=True) or []
        geometry = cmds.skinCluster(skin, query=True, geometry=True) or []
        entry: dict[str, Any] = {"name": skin, "influences": len(influences)}
        if geometry:
            entry["mesh"] = owner_name(geometry[0])
        if cmds.getAttr(skin + ".maintainMaxInfluences"):
            entry["max_influences"] = cmds.getAttr(skin + ".maxInfluences")
        result.append(entry)
    return result


def blend_shapes() -> list[dict[str, Any]]:
    result = []
    for node in cmds.ls(type="blendShape") or []:
        targets = cmds.listAttr(node + ".weight", multi=True) or []
        geometry = cmds.blendShape(node, query=True, geometry=True) or []
        entry: dict[str, Any] = {"name": node, "targets": len(targets)}
        if geometry:
            entry["mesh"] = owner_name(geometry[0])
        result.append(entry)
    return result


def anim_layers() -> list[dict[str, Any]]:
    result = []
    for layer in cmds.ls(type="animLayer") or []:
        entry: dict[str, Any] = {"name": layer}
        if cmds.getAttr(layer + ".mute"):
            entry["muted"] = True
        weight = cmds.getAttr(layer + ".weight")
        if weight != 1.0:
            entry["weight"] = round(weight, 4)
        result.append(entry)
    return result


def scene_overview() -> dict[str, Any]:
    curves = cmds.ls(type=list(TIME_CURVE_TYPES)) or []
    driven = len(cmds.ls(type="animCurve") or []) - len(curves)
    result = time_info()
    result["curves"] = len(curves)
    if driven:
        result["driven_key_curves"] = driven

    animated = animated_nodes(curves)
    ranges = [entry["range"] for entry in animated if "range" in entry]
    if ranges:
        result["key_range"] = [min(r[0] for r in ranges), max(r[1] for r in ranges)]

    sections = {
        "animated": animated,
        "skeletons": skeleton_roots(),
        "skins": skin_clusters(),
        "blend_shapes": blend_shapes(),
        "layers": anim_layers(),
    }
    for key, items in sections.items():
        if items:
            limit = NODE_LIMIT if key == "animated" else LIST_LIMIT
            result[key] = listing(items, limit)
    return result


def tangent_summary(curve: str) -> list[str]:
    in_types = cmds.keyTangent(curve, query=True, inTangentType=True) or []
    out_types = cmds.keyTangent(curve, query=True, outTangentType=True) or []
    return sorted(set(in_types) | set(out_types))


def curve_entry(attr: str, curve: str) -> dict[str, Any]:
    times = key_times(curve)
    entry: dict[str, Any] = {"attr": attr, "keys": len(times)}
    if times:
        entry["range"] = key_range(times)
    entry["tangents"] = tangent_summary(curve)
    pre = cmds.getAttr(curve + ".preInfinity", asString=True).lower()
    post = cmds.getAttr(curve + ".postInfinity", asString=True).lower()
    if pre != "constant" or post != "constant":
        entry["infinity"] = [pre, post]
    if cmds.nodeType(curve) not in TIME_CURVE_TYPES:
        entry["driven"] = True
    return entry


def hierarchy_summary(root: str) -> dict[str, Any]:
    joints = [root] + (
        cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    )
    animated = 0
    curve_count = 0
    times: list[float] = []
    for joint in joints:
        curves = anim_curves(joint)
        if curves:
            animated += 1
        curve_count += len(curves)
        for _, curve in curves:
            times.extend(key_times(curve))

    summary: dict[str, Any] = {
        "joints": len(joints),
        "animated_joints": animated,
        "curves": curve_count,
    }
    if times:
        summary["key_range"] = key_range(times)
    skins = set()
    for joint in joints:
        skins.update(cmds.listConnections(joint, type="skinCluster") or [])
    if skins:
        summary["skins"] = sorted(skins)
    return summary


def get_animation_info(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    if not name:
        return scene_overview()

    path = resolve(name)
    entries = [curve_entry(attr, curve) for attr, curve in anim_curves(path)]
    result: dict[str, Any] = {"name": display_name(path), "type": cmds.nodeType(path)}
    result["fps"] = time_info()["fps"]
    result["curves"] = listing(entries, CURVE_LIMIT)
    if cmds.nodeType(path) == "joint":
        result["hierarchy"] = hierarchy_summary(path)
    return result
