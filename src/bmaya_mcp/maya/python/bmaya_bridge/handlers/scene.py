from __future__ import annotations

import os
from typing import Any

from maya import cmds

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    anim_curves,
    bool_param,
    display_name,
    display_type,
    frame,
    int_param,
    is_default_camera,
    key_times,
    listing,
    resolve,
    rnd,
    rnd_list,
    shapes_of,
    text_param,
    time_info,
)

SELECTION_LIMIT = 20
CHILDREN_LIMIT = 20
HISTORY_LIMIT = 15
ANIM_LIMIT = 40
MAX_UNDO_STEPS = 50
HISTORY_NOISE = {"groupId", "groupParts", "tweak"}

FILE_TYPES = {
    ".ma": "mayaAscii",
    ".mb": "mayaBinary",
    ".fbx": "FBX",
    ".obj": "OBJ",
}

PLUGINS = {
    ".fbx": "fbxmaya",
    ".obj": "objExport",
}


def scene_path() -> str:
    return cmds.file(query=True, sceneName=True) or ""


def scene_state() -> dict[str, Any]:
    return {
        "scene": scene_path() or "untitled",
        "modified": bool(cmds.file(query=True, modified=True)),
    }


def node_entry(node: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": display_name(node), "type": display_type(node)}
    if cmds.objectType(node, isAType="transform"):
        children = cmds.listRelatives(node, children=True, type="transform") or []
        if children:
            entry["children"] = len(children)
    return entry


def root_of(path: str) -> str:
    return "|" + path.lstrip("|").split("|", 1)[0]


def find_nodes(query: str, node_type: str) -> list[str]:
    roots = cmds.ls(assemblies=True, long=True) or []
    camera_roots = {root for root in roots if is_default_camera(root)}
    if not query and not node_type:
        return [root for root in roots if root not in camera_roots]

    if node_type and node_type not in cmds.allNodeTypes():
        raise BridgeError(
            f"Unknown node type '{node_type}'.",
            hint="Use a Maya node type such as mesh, joint, camera, transform or skinCluster.",
        )
    kwargs: dict[str, Any] = {"long": True, "type": node_type or "transform"}
    if query:
        pattern = query if any(c in query for c in "*?") else f"*{query}*"
        nodes = cmds.ls(pattern, recursive=True, **kwargs) or []
    else:
        nodes = cmds.ls(**kwargs) or []
    return [node for node in nodes if root_of(node) not in camera_roots]


def get_scene_info(params: dict[str, Any]) -> dict[str, Any]:
    query = text_param(params, "query")
    node_type = text_param(params, "node_type")
    limit = int_param(params, "limit", 50, 1, 500)

    nodes = find_nodes(query, node_type)
    selection = cmds.ls(selection=True) or []

    result: dict[str, Any] = {
        "file": scene_path() or "untitled",
        "modified": bool(cmds.file(query=True, modified=True)),
        "unit": cmds.currentUnit(query=True, linear=True),
        "up": cmds.upAxis(query=True, axis=True),
    }
    result.update(time_info())
    result["selection"] = listing(selection, SELECTION_LIMIT)
    result["nodes"] = [node_entry(node) for node in nodes[:limit]]
    result["shown"] = min(len(nodes), limit)
    result["total"] = len(nodes)
    return result


def transform_info(path: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "t": rnd_list(cmds.xform(path, query=True, worldSpace=True, translation=True)),
        "r": rnd_list(cmds.xform(path, query=True, worldSpace=True, rotation=True)),
        "s": rnd_list(cmds.xform(path, query=True, worldSpace=True, scale=True)),
    }
    try:
        bbox = cmds.exactWorldBoundingBox(path)
    except RuntimeError:
        bbox = []
    if bbox and bbox[0] <= bbox[3]:
        info["bbox"] = rnd_list(bbox)
    info["visible"] = bool(cmds.getAttr(path + ".visibility"))

    parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
    if parents:
        info["parent"] = display_name(parents[0])
    children = cmds.listRelatives(path, children=True, type="transform", fullPath=True) or []
    if children:
        names = [display_name(child) for child in children]
        info["children"] = listing(names, CHILDREN_LIMIT)
    return info


def material_names(shape: str) -> list[str]:
    engines = cmds.listConnections(shape, type="shadingEngine") or []
    materials = []
    for engine in dict.fromkeys(engines):
        shaders = cmds.listConnections(engine + ".surfaceShader") or [engine]
        for shader in shaders:
            if shader not in materials:
                materials.append(shader)
    return materials


def mesh_info(shape: str) -> dict[str, Any]:
    counts = cmds.polyEvaluate(shape, vertex=True, edge=True, face=True, triangle=True)
    info: dict[str, Any] = {
        "shape": display_name(shape),
        "verts": counts["vertex"],
        "edges": counts["edge"],
        "faces": counts["face"],
        "tris": counts["triangle"],
        "uv_sets": cmds.polyUVSet(shape, query=True, allUVSets=True) or [],
    }
    color_sets = cmds.polyColorSet(shape, query=True, allColorSets=True) or []
    if color_sets:
        info["color_sets"] = color_sets
    info["materials"] = material_names(shape)

    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    types = [cmds.nodeType(node) for node in history]
    types = [t for t in types if t not in HISTORY_NOISE]
    if types:
        info["history"] = listing(types, HISTORY_LIMIT)
    skins = cmds.ls(history, type="skinCluster") or []
    if skins:
        influences = cmds.skinCluster(skins[0], query=True, influence=True) or []
        info["skin"] = {"name": skins[0], "influences": len(influences)}
    return info


def joint_info(path: str) -> dict[str, Any]:
    descendants = cmds.listRelatives(path, allDescendents=True, type="joint") or []
    return {
        "orient": rnd_list(cmds.getAttr(path + ".jointOrient")[0]),
        "radius": rnd(cmds.getAttr(path + ".radius")),
        "child_joints": len(descendants),
    }


def anim_info(node: str) -> dict[str, Any] | None:
    entries = []
    for attr, curve in anim_curves(node):
        times = key_times(curve)
        entry: dict[str, Any] = {"attr": attr, "keys": len(times)}
        if times:
            entry["range"] = [frame(min(times)), frame(max(times))]
        entries.append(entry)
    if not entries:
        return None
    return listing(entries, ANIM_LIMIT)


def get_object_info(params: dict[str, Any]) -> dict[str, Any]:
    path = resolve(text_param(params, "name"))
    node_type = cmds.nodeType(path)
    info: dict[str, Any] = {"name": path, "type": node_type}

    transform = ""
    shapes: list[str] = []
    if cmds.objectType(path, isAType="transform"):
        transform = path
        shapes = shapes_of(path)
        if shapes:
            info["shape_type"] = cmds.nodeType(shapes[0])
    elif cmds.objectType(path, isAType="shape"):
        shapes = [path]
        parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
        transform = parents[0] if parents else ""

    if transform:
        info.update(transform_info(transform))
    meshes = [shape for shape in shapes if cmds.nodeType(shape) == "mesh"]
    if meshes:
        info["mesh"] = mesh_info(meshes[0])
    if node_type == "joint":
        info["joint"] = joint_info(path)

    anim = anim_info(transform or path)
    if anim:
        info["anim"] = anim
    return info


def require_path(path: str, action: str) -> str:
    if not path:
        raise BridgeError(
            f"scene_file action '{action}' needs a path.",
            hint="Pass an absolute file path, for example C:/projects/char.ma.",
        )
    return os.path.abspath(os.path.expanduser(path)).replace("\\", "/")


def require_existing(path: str, action: str) -> str:
    path = require_path(path, action)
    if not os.path.isfile(path):
        raise BridgeError(
            f"File not found: {path}",
            hint="Check the path. It must be an absolute path to an existing file.",
        )
    return path


def refuse_unsaved(action: str, force: bool) -> None:
    if force or not cmds.file(query=True, modified=True):
        return
    raise BridgeError(
        f"The current scene has unsaved changes, so '{action}' was refused.",
        hint="Save first with scene_file action='save', "
        "or repeat with force=true to discard the changes.",
    )


def file_type(path: str, allowed: tuple[str, ...]) -> str:
    extension = os.path.splitext(path)[1].lower()
    if extension not in allowed:
        raise BridgeError(
            f"Unsupported file extension '{extension or '(none)'}'.",
            hint="Use one of: " + ", ".join(allowed) + ".",
        )
    plugin = PLUGINS.get(extension)
    if plugin and not cmds.pluginInfo(plugin, query=True, loaded=True):
        cmds.loadPlugin(plugin, quiet=True)
    return FILE_TYPES[extension]


def scene_file(params: dict[str, Any]) -> dict[str, Any]:
    action = text_param(params, "action").lower()
    path = text_param(params, "path")
    force = bool_param(params, "force")

    if action == "new":
        refuse_unsaved(action, force)
        cmds.file(new=True, force=True)
        return scene_state()

    if action == "open":
        path = require_existing(path, action)
        file_type(path, (".ma", ".mb"))
        refuse_unsaved(action, force)
        cmds.file(path, open=True, force=True, ignoreVersion=True)
        return scene_state()

    if action == "save":
        if path:
            path = require_path(path, action)
            kind = file_type(path, (".ma", ".mb"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cmds.file(rename=path)
            cmds.file(save=True, type=kind, force=True)
            return scene_state()
        current = scene_path()
        if not current:
            raise BridgeError(
                "The scene is untitled, so there is nowhere to save it.",
                hint="Pass a path ending in .ma or .mb to save it as a new file.",
            )
        kind = file_type(current, (".ma", ".mb"))
        cmds.file(save=True, type=kind, force=True)
        return scene_state()

    if action == "import":
        path = require_existing(path, action)
        kind = file_type(path, (".ma", ".mb", ".fbx", ".obj"))
        before = set(cmds.ls(assemblies=True, long=True) or [])
        cmds.file(path, i=True, type=kind, ignoreVersion=True)
        after = cmds.ls(assemblies=True, long=True) or []
        added = [display_name(node) for node in after if node not in before]
        result = scene_state()
        result["imported"] = listing(added, 50)
        return result

    raise BridgeError(
        f"Unknown scene_file action '{action}'.",
        hint="Use one of: new, open, save, import.",
    )


def undo(params: dict[str, Any]) -> dict[str, Any]:
    steps = int_param(params, "steps", 1, 1, MAX_UNDO_STEPS)
    if not cmds.undoInfo(query=True, state=True):
        raise BridgeError(
            "Undo is turned off in this Maya session.",
            hint="Turn it on with execute_maya_code: cmds.undoInfo(state=True).",
        )
    undone = 0
    for _ in range(steps):
        if cmds.undoInfo(query=True, undoQueueEmpty=True):
            break
        try:
            cmds.undo()
        except RuntimeError:
            break
        undone += 1
    result: dict[str, Any] = {"undone": undone}
    if undone < steps:
        result["queue_empty"] = True
    top = cmds.undoInfo(query=True, undoName=True)
    if top:
        result["next"] = top
    return result
