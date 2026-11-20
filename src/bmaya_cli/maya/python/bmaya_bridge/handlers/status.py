"""What changed in the scene since it was opened, created or saved.

The bridge takes a snapshot at those moments (and when it starts on an unmodified scene);
`scene_status` takes another and compares them.
"""

from __future__ import annotations

import itertools
import json
import operator
import os
import time
from datetime import datetime
from typing import Any, NamedTuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
from maya import cmds

from bmaya_bridge import scenediff
from bmaya_bridge.handlers._common import listing, rnd
from bmaya_bridge.scenediff import Node, Signature

CHANGES_SHOWN = 30
COMMANDS_SHOWN = 20
STARTUP_CAMERAS = frozenset({"persp", "top", "front", "side"})
KEPT_TYPES = frozenset(
    {"gameFbxExporter", "timeSliderBookmark", "standardSurface", "surfaceShader", "dagPose"}
)
KEPT_KINDS = (
    om.MFn.kDagNode,
    om.MFn.kAnimCurve,
    om.MFn.kGeometryFilt,
    om.MFn.kShadingEngine,
    om.MFn.kLambert,
    om.MFn.kFileTexture,
    om.MFn.kPlace2dTexture,
    om.MFn.kBump,
    om.MFn.kSet,
)
CHANNELS = ("translate", "rotate", "scale")


class Baseline(NamedTuple):
    taken: str
    exact: str
    why: str
    scene: str
    file_time: float | None
    nodes: dict[str, Node]
    settings: Signature


BASELINE: list[Baseline] = []


def kept(obj: om.MObject, fn: om.MFnDependencyNode) -> bool:
    if fn.isDefaultNode or fn.isShared:
        return False
    if fn.typeName in KEPT_TYPES:
        return True
    return any(obj.hasFn(kind) for kind in KEPT_KINDS)


def startup_camera(obj: om.MObject) -> bool:
    if not obj.hasFn(om.MFn.kDagNode):
        return False
    path = om.MFnDagNode(obj).fullPathName()
    return path.split("|")[1] in STARTUP_CAMERAS if path.count("|") >= 1 else False


def plug_values(fn: om.MFnDependencyNode, name: str) -> tuple[float, ...]:
    plug = fn.findPlug(name, False)
    if plug.isCompound:
        return tuple(scenediff.rounded(plug.child(k).asDouble()) for k in range(plug.numChildren()))
    return (scenediff.rounded(plug.asDouble()),)


def transform_sig(obj: om.MObject, fn: om.MFnDependencyNode) -> Signature:
    sig: Signature = {channel: plug_values(fn, channel) for channel in CHANNELS}
    if obj.hasFn(om.MFn.kJoint):
        sig["jointOrient"] = plug_values(fn, "jointOrient")
    sig["visibility"] = fn.findPlug("visibility", False).asBool()
    dag = om.MFnDagNode(obj)
    sig["parent"] = om.MFnDagNode(dag.parent(0)).fullPathName() if dag.parentCount() else ""
    return sig


def mesh_sig(obj: om.MObject) -> Signature:
    dag = om.MFnDagNode(obj)
    sig: Signature = {"intermediate": dag.isIntermediateObject}
    try:
        mesh = om.MFnMesh(obj)
        points = mesh.getPoints()
    except (RuntimeError, ValueError):
        return {**sig, "geometry": "none"}
    flat = list(itertools.chain.from_iterable(map(tuple, points)))
    sig["topology"] = (mesh.numVertices, mesh.numPolygons, mesh.numEdges)
    sig["points"] = scenediff.checksum(flat)
    if mesh.numUVs():
        us, vs = mesh.getUVs()
        _, ids = mesh.getAssignedUVs()
        ids = list(ids)
        if ids:
            pick = operator.itemgetter(*ids)
            sig["uvs"] = scenediff.checksum(both(pick(us), pick(vs), len(ids)), 5)
    engines = cmds.listConnections(dag.fullPathName(), type="shadingEngine") or []
    sig["materials"] = tuple(sorted(set(engines)))
    return sig


def both(us: float | tuple[float, ...], vs: float | tuple[float, ...], count: int) -> list[float]:
    """U and v of every corner in turn; itemgetter gives a bare number for one corner."""
    if count == 1:
        return [us, vs]
    out = [0.0] * (2 * count)
    out[0::2] = us
    out[1::2] = vs
    return out


def curve_sig(obj: om.MObject) -> Signature:
    curve = oma.MFnAnimCurve(obj)
    times = [curve.input(k).asUnits(om.MTime.kSeconds) for k in range(curve.numKeys)]
    values = [curve.value(k) for k in range(curve.numKeys)]
    return {"keys": (curve.numKeys, scenediff.checksum(times), scenediff.checksum(values))}


def skin_sig(obj: om.MObject) -> Signature:
    skin = oma.MFnSkinCluster(obj)
    flat: list[float] = []
    for index in range(skin.numOutputConnections()):
        path = skin.getPathAtIndex(skin.indexForOutputConnection(index))
        component = om.MFnSingleIndexedComponent()
        every = component.create(om.MFn.kMeshVertComponent)
        component.setCompleteData(om.MFnMesh(path).numVertices)
        weights, _ = skin.getWeights(path, every)
        flat.extend(weights)
    return {"weights": scenediff.checksum(flat, 5), "influences": len(skin.influenceObjects())}


def other_sig(fn: om.MFnDependencyNode) -> Signature:
    name = fn.name()
    kind = fn.typeName
    if kind == "file":
        return {
            "path": cmds.getAttr(name + ".fileTextureName") or "",
            "color_space": cmds.getAttr(name + ".colorSpace") or "",
        }
    if kind == "timeSliderBookmark":
        return {
            "range": (
                seconds(cmds.getAttr(name + ".timeRangeStart")),
                seconds(cmds.getAttr(name + ".timeRangeStop")),
            ),
            "label": cmds.getAttr(name + ".name") or "",
        }
    if kind == "gameFbxExporter":
        rows = []
        for index in cmds.getAttr(name + ".animClips", multiIndices=True) or []:
            plug = f"{name}.animClips[{index}]"
            rows.append(
                (
                    cmds.getAttr(plug + ".animClipName") or "",
                    cmds.getAttr(plug + ".animClipStart"),
                    cmds.getAttr(plug + ".animClipEnd"),
                )
            )
        return {"clips": tuple(rows), "path": cmds.getAttr(name + ".exportPath") or ""}
    return {}


def seconds(frames: float) -> float:
    """Frames in the scene's time unit as seconds, so a new frame rate alone is no change."""
    return scenediff.rounded(om.MTime(frames, om.MTime.uiUnit()).asUnits(om.MTime.kSeconds))


def signature(obj: om.MObject, fn: om.MFnDependencyNode) -> Signature:
    if obj.hasFn(om.MFn.kTransform):
        return transform_sig(obj, fn)
    if obj.hasFn(om.MFn.kMesh):
        return mesh_sig(obj)
    if obj.hasFn(om.MFn.kAnimCurve):
        return curve_sig(obj)
    if obj.hasFn(om.MFn.kSkinClusterFilter):
        return skin_sig(obj)
    return other_sig(fn)


def node_name(obj: om.MObject, fn: om.MFnDependencyNode) -> str:
    if obj.hasFn(om.MFn.kDagNode):
        return om.MFnDagNode(obj).partialPathName()
    return fn.name()


def snapshot() -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    found = om.MItDependencyNodes()
    while not found.isDone():
        obj = found.thisNode()
        fn = om.MFnDependencyNode(obj)
        if kept(obj, fn) and not startup_camera(obj):
            nodes[fn.uuid().asString()] = Node(node_name(obj, fn), fn.typeName, signature(obj, fn))
        found.next()
    return nodes


def settings() -> Signature:
    return {
        "unit": cmds.currentUnit(query=True, linear=True),
        "time": cmds.currentUnit(query=True, time=True),
        "up": cmds.upAxis(query=True, axis=True),
        "playback": (
            seconds(cmds.playbackOptions(query=True, minTime=True)),
            seconds(cmds.playbackOptions(query=True, maxTime=True)),
        ),
    }


def file_time(path: str) -> float | None:
    return os.path.getmtime(path) if path and os.path.isfile(path) else None


def take_baseline(why: str) -> None:
    """Remembers the scene as it is now; the bridge calls this after open, new and save."""
    scene = cmds.file(query=True, sceneName=True) or ""
    now = datetime.now()
    BASELINE.clear()
    BASELINE.append(
        Baseline(
            now.isoformat(timespec="seconds"),
            now.isoformat(timespec="milliseconds"),
            why,
            scene,
            file_time(scene),
            snapshot(),
            settings(),
        )
    )


def take_baseline_if_clean(why: str) -> None:
    if not cmds.file(query=True, modified=True):
        take_baseline(why)


def journal_commands(since: str) -> list[dict[str, str]]:
    from bmaya_bridge import server

    bridge = server.current()
    if bridge is None:
        return []
    out = []
    try:
        with open(bridge.journal.path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("event") != "start" or record.get("t", "") < since:
                    continue
                if record.get("command") in READ_ONLY:
                    continue
                out.append(
                    {
                        "t": record["t"][11:19],
                        "client": record.get("client", ""),
                        "command": record.get("command", ""),
                    }
                )
    except OSError:
        return []
    return out


READ_ONLY = frozenset(
    {
        "ping",
        "scene_status",
        "get_scene_info",
        "get_object_info",
        "get_viewport_screenshot",
        "render_frames",
        "get_model_views",
        "audit_mesh",
        "diff_mesh",
        "get_animation_info",
        "plot_animation",
        "get_motion",
        "get_rig_info",
        "get_skin_info",
        "save_skin_weights",
        "get_bones",
        "get_topology",
        "get_uv",
        "bake_uv",
        "export_fbx",
        "plan_game_export",
        "export_game_file",
    }
)


def scene_status(params: dict[str, Any]) -> dict[str, Any]:
    scene = cmds.file(query=True, sceneName=True) or ""
    modified = bool(cmds.file(query=True, modified=True))
    result: dict[str, Any] = {"scene": scene or "untitled", "modified": modified}
    if scene and os.path.isfile(scene):
        result["file"] = {
            "saved": datetime.fromtimestamp(os.path.getmtime(scene)).isoformat(timespec="seconds"),
            "size": os.path.getsize(scene),
        }
    base = BASELINE[0] if BASELINE else None
    if base is None or base.scene != scene:
        result["baseline"] = None
        result["note"] = (
            "no baseline for this scene: it was changed before the bridge could record it. "
            "Save it (bmaya file save) and later calls compare with that save"
        )
        return result
    result["baseline"] = {"taken": base.taken, "why": base.why}
    now = file_time(scene)
    if base.file_time is not None and now is not None and abs(now - base.file_time) > 1e-3:
        result["file"]["changed_on_disk"] = True
    started = time.perf_counter()
    found = scenediff.compare(base.nodes, snapshot(), base.settings, settings())
    result["added"] = listing(found.added, CHANGES_SHOWN)
    result["deleted"] = listing(found.deleted, CHANGES_SHOWN)
    result["renamed"] = listing(found.renamed, CHANGES_SHOWN)
    result["changed"] = listing(found.changed, CHANGES_SHOWN)
    if found.settings:
        result["settings"] = found.settings
    commands = journal_commands(base.exact)
    result["commands"] = listing(commands, COMMANDS_SHOWN)
    same = not (found.added or found.deleted or found.renamed or found.changed or found.settings)
    result["same"] = same
    if same and modified:
        result["note"] = (
            "the modified flag is set but nothing bmaya compares differs: a value set back, "
            "a selection, an export, or a change of a kind it does not track (render settings, "
            "UI state)"
        )
    result["seconds"] = rnd(time.perf_counter() - started, 2)
    return result
