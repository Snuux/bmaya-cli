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
from typing import TYPE_CHECKING, Any, NamedTuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
from maya import cmds

from bmaya_bridge import scenediff
from bmaya_bridge.handlers._common import listing, rnd, scene_kept
from bmaya_bridge.scenediff import Node, Signature

if TYPE_CHECKING:
    from bmaya_bridge.dispatch import Job

CHANGES_SHOWN = 30
COMMANDS_SHOWN = 20
SAMPLED_OVER = 200_000
SAMPLES = 20_000
STARTUP_CAMERAS = frozenset({"persp", "top", "front", "side"})
KEPT_TYPES = frozenset({"gameFbxExporter", "timeSliderBookmark", "dagPose"})
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
CHANNELS = ("translate", "rotate", "scale", "shear", "rotateAxis")
JOINT_PLUGS = ("jointOrient", "preferredAngle")
SHADERS: list[frozenset[str]] = []


class Baseline(NamedTuple):
    taken: str
    exact: str
    why: str
    frame: float
    scene: str
    file_time: float | None
    nodes: dict[str, Node]
    settings: Signature


BASELINE: list[Baseline] = []


def shader_types() -> frozenset[str]:
    if not SHADERS:
        SHADERS.append(frozenset(cmds.listNodeTypes("shader") or []))
    return SHADERS[0]


def kept(obj: om.MObject, fn: om.MFnDependencyNode) -> bool:
    if fn.isDefaultNode or fn.isShared:
        return False
    if fn.typeName in KEPT_TYPES or fn.typeName in shader_types():
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


def uuid_of(obj: om.MObject) -> str:
    return om.MFnDependencyNode(obj).uuid().asString()


def transform_sig(obj: om.MObject, fn: om.MFnDependencyNode) -> Signature:
    sig: Signature = {channel: plug_values(fn, channel) for channel in CHANNELS}
    sig["rotateOrder"] = fn.findPlug("rotateOrder", False).asInt()
    sig["inheritsTransform"] = fn.findPlug("inheritsTransform", False).asBool()
    if obj.hasFn(om.MFn.kJoint):
        for name in JOINT_PLUGS:
            sig[name] = plug_values(fn, name)
        sig["segmentScaleCompensate"] = fn.findPlug("segmentScaleCompensate", False).asBool()
    sig["visibility"] = fn.findPlug("visibility", False).asBool()
    dag = om.MFnDagNode(obj)
    sig["parent"] = uuid_of(dag.parent(0)) if dag.parentCount() else ""
    return sig


def spread(count: int) -> range:
    """Every point of a mesh, or an even sample of a big one."""
    return range(0, count, max(1, count // SAMPLES) if count > SAMPLED_OVER else 1)


def mesh_sig(obj: om.MObject) -> Signature:
    dag = om.MFnDagNode(obj)
    sig: Signature = {"intermediate": dag.isIntermediateObject}
    try:
        mesh = om.MFnMesh(obj)
        count = mesh.numVertices
    except (RuntimeError, ValueError):
        return {**sig, "geometry": "none"}
    sig["topology"] = (count, mesh.numPolygons, mesh.numEdges)
    if count > SAMPLED_OVER:
        flat = []
        for index in spread(count):
            point = mesh.getPoint(index)
            flat.extend((point.x, point.y, point.z))
        sig["sampled"] = True
    else:
        points = mesh.getPoints()
        flat = list(itertools.chain.from_iterable(map(tuple, points)))
    sig["points"] = scenediff.checksum(flat)
    if mesh.numUVs() and count <= SAMPLED_OVER:
        names = mesh.getUVSetNames()
        sig["uv_sets"] = tuple(names)
        for name in names:
            sig["uvs " + name] = uv_checksum(mesh, name)
    if not dag.isIntermediateObject:
        sig["materials"] = materials(obj, mesh)
    return sig


def uv_checksum(mesh: om.MFnMesh, uv_set: str) -> float:
    us, vs = mesh.getUVs(uv_set)
    _, ids = mesh.getAssignedUVs(uv_set)
    ids = list(ids)
    if not ids or not len(us):
        return 0.0
    pick = operator.itemgetter(*ids)
    return scenediff.checksum(both(pick(us), pick(vs), len(ids)), 5)


def materials(obj: om.MObject, mesh: om.MFnMesh) -> tuple[Any, ...]:
    """The shading engines of the mesh's first instance and which face each one gets."""
    paths = om.MDagPath.getAllPathsTo(obj)
    if not len(paths):
        return ()
    sets, faces = mesh.getConnectedShaders(paths[0].instanceNumber())
    engines = tuple(uuid_of(sets[index]) for index in range(len(sets)))
    return engines + (scenediff.checksum([float(face) for face in faces]),)


def both(us: float | tuple[float, ...], vs: float | tuple[float, ...], count: int) -> list[float]:
    """U and v of every corner in turn; itemgetter gives a bare number for one corner."""
    if count == 1:
        return [us, vs]
    out = [0.0] * (2 * count)
    out[0::2] = us
    out[1::2] = vs
    return out


def curve_cvs_sig(obj: om.MObject) -> Signature:
    if obj.hasFn(om.MFn.kNurbsCurve):
        shape = om.MFnNurbsCurve(obj)
        form = (shape.degree, shape.numCVs, shape.form)
    else:
        shape = om.MFnNurbsSurface(obj)
        form = (shape.degreeInU, shape.degreeInV, shape.numCVsInU, shape.numCVsInV)
    points = shape.cvPositions()
    flat = list(itertools.chain.from_iterable(map(tuple, points)))
    return {"form": form, "points": scenediff.checksum(flat)}


def curve_sig(obj: om.MObject) -> Signature:
    """Keys, tangents and infinity. A driven key's input is a number, not a time."""
    curve = oma.MFnAnimCurve(obj)
    inputs = []
    values = []
    tangents = []
    for index in range(curve.numKeys):
        moment = curve.input(index)
        inputs.append(
            moment.asUnits(om.MTime.kSeconds) if isinstance(moment, om.MTime) else float(moment)
        )
        values.append(curve.value(index))
        tangents.append(float(curve.inTangentType(index)))
        tangents.append(float(curve.outTangentType(index)))
        for side in (True, False):
            x, y = curve.getTangentXY(index, side)
            tangents.append(x)
            tangents.append(y)
    return {
        "keys": (curve.numKeys, scenediff.checksum(inputs), scenediff.checksum(values)),
        "tangents": (curve.isWeighted, scenediff.checksum(tangents)),
        "infinity": (curve.preInfinityType, curve.postInfinityType),
    }


def skin_sig(obj: om.MObject) -> Signature:
    skin = oma.MFnSkinCluster(obj)
    flat: list[float] = []
    for index in range(skin.numOutputConnections()):
        path = skin.getPathAtIndex(skin.indexForOutputConnection(index))
        every = om.MObject()
        if path.hasFn(om.MFn.kMesh):
            component = om.MFnSingleIndexedComponent()
            every = component.create(om.MFn.kMeshVertComponent)
            component.addElements(list(spread(om.MFnMesh(path).numVertices)))
        weights, _ = skin.getWeights(path, every)
        flat.extend(weights)
    return {"weights": scenediff.checksum(flat, 5), "influences": len(skin.influenceObjects())}


def shader_sig(fn: om.MFnDependencyNode) -> Signature:
    """The material's own numbers (colors, weights, roughness...), not those that come from
    a connected texture."""
    values = []
    for index in range(fn.attributeCount()):
        attribute = fn.attribute(index)
        if not attribute.hasFn(om.MFn.kNumericAttribute):
            continue
        plug = fn.findPlug(attribute, False)
        if plug.isArray or plug.isDestination or not om.MFnAttribute(attribute).writable:
            continue
        try:
            values.append(plug.asDouble())
        except RuntimeError:
            continue
    return {"settings": scenediff.checksum(values, 4)}


def other_sig(obj: om.MObject, fn: om.MFnDependencyNode) -> Signature:
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
                    seconds(cmds.getAttr(plug + ".animClipStart")),
                    seconds(cmds.getAttr(plug + ".animClipEnd")),
                    bool(cmds.getAttr(plug + ".exportAnimClip")),
                )
            )
        return {"clips": tuple(rows), "path": cmds.getAttr(name + ".exportPath") or ""}
    if obj.hasFn(om.MFn.kLambert) or kind in shader_types():
        return shader_sig(fn)
    return {}


def seconds(frames: float) -> float:
    """Frames in the scene's time unit as seconds, so a new frame rate alone is no change."""
    return scenediff.rounded(om.MTime(frames, om.MTime.uiUnit()).asUnits(om.MTime.kSeconds))


def signature(obj: om.MObject, fn: om.MFnDependencyNode) -> Signature:
    if obj.hasFn(om.MFn.kTransform):
        return transform_sig(obj, fn)
    if obj.hasFn(om.MFn.kMesh):
        return mesh_sig(obj)
    if obj.hasFn(om.MFn.kNurbsCurve) or obj.hasFn(om.MFn.kNurbsSurface):
        return curve_cvs_sig(obj)
    if obj.hasFn(om.MFn.kAnimCurve):
        return curve_sig(obj)
    if obj.hasFn(om.MFn.kSkinClusterFilter):
        return skin_sig(obj)
    return other_sig(obj, fn)


def safe_signature(obj: om.MObject, fn: om.MFnDependencyNode) -> Signature:
    """A node that cannot be read is kept by name with its error, so one odd node does not
    lose the whole snapshot."""
    try:
        return signature(obj, fn)
    except Exception as error:
        return {"unreadable": type(error).__name__}


def label_of(obj: om.MObject, fn: om.MFnDependencyNode) -> str:
    if obj.hasFn(om.MFn.kDagNode):
        return om.MFnDagNode(obj).partialPathName()
    return fn.name()


def snapshot() -> dict[str, Node]:
    """Kept nodes by UUID. A file referenced twice gives its nodes the same UUID in both
    namespaces, so a UUID seen again is keyed with the namespace too."""
    nodes: dict[str, Node] = {}
    found = om.MItDependencyNodes()
    while not found.isDone():
        obj = found.thisNode()
        fn = om.MFnDependencyNode(obj)
        if kept(obj, fn) and not startup_camera(obj):
            key = fn.uuid().asString()
            if key in nodes:
                key = f"{key}@{fn.namespace}"
            nodes[key] = Node(fn.name(), fn.typeName, safe_signature(obj, fn), label_of(obj, fn))
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
    """Remembers the scene as it is now, at the current frame; the bridge calls this after
    open, new and save. Commands count from the moment it is taken, or from the start of
    the bmaya command that made it, unless that was `bmaya file` itself."""
    scene = cmds.file(query=True, sceneName=True) or ""
    now = datetime.now()
    exact = now.isoformat(timespec="milliseconds")
    job = running_job()
    if job is not None and job.command != "scene_file" and job.started:
        exact = job.started
    taken = Baseline(
        now.isoformat(timespec="seconds"),
        exact,
        why,
        cmds.currentTime(query=True),
        scene,
        file_time(scene),
        snapshot(),
        settings(),
    )
    BASELINE.clear()
    BASELINE.append(taken)


def running_job() -> Job | None:
    from bmaya_bridge import server

    bridge = server.current()
    return bridge.dispatcher.running if bridge is not None else None


def take_baseline_if_clean(why: str) -> None:
    if not cmds.file(query=True, modified=True):
        take_baseline(why)


def journal_commands(since: str) -> list[dict[str, str]]:
    from bmaya_bridge import server
    from bmaya_bridge.handlers import UNRECORDED

    bridge = server.current()
    if bridge is None:
        return []
    reads = set(UNRECORDED) | {"ping"}
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
                if record.get("command") in reads:
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
    with scene_kept():
        cmds.currentTime(base.frame, update=True)
        nodes = snapshot()
    found = scenediff.compare(base.nodes, nodes, base.settings, settings())
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
    if any(node.sig.get("sampled") for node in nodes.values()):
        result["sampled"] = (
            f"meshes over {SAMPLED_OVER} vertices are compared on {SAMPLES} of their points, "
            "and without their UVs"
        )
    if same and modified:
        result["note"] = (
            "the modified flag is set but nothing bmaya compares differs: a value set back, "
            "a selection, an export, or a change of a kind it does not track (render settings, "
            "UI state)"
        )
    result["seconds"] = rnd(time.perf_counter() - started, 2)
    return result
