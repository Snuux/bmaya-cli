"""Export as the Game Exporter window's Export button does, one file per call."""

from __future__ import annotations

import os
from typing import Any

from maya import cmds, mel

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    frame,
    scene_kept,
    select_only,
    text_param,
)
from bmaya_bridge.handlers.export import (
    check_shapes,
    ensure_fbx_plugin,
    expectation,
    reload_fbx_plugin,
    run_fbx_export,
)

PLUGIN = "gameFbxExporter"
MEL_FILES = ("gameFbxExporterUtilities", "gameFbxExporterPreset", "gameFbxExporter")
MODEL = 1
CLIPS = 2
TIME_EDITOR = 3
EXPORT_ALL = 1
EXPORT_SELECTED = 2
EXPORT_SET = 3
ONE_FILE = 1
FILE_PER_ROOT = 2
DEFAULT_FILE_VERSION = "FBX202000"
TEXTURE_ATTRS = ("fileTextureName", "colorSpace", "ignoreColorSpaceFileRules")

_sourced = False


def load_game_exporter() -> None:
    global _sourced
    ensure_fbx_plugin()
    if not cmds.pluginInfo(PLUGIN, query=True, loaded=True):
        try:
            cmds.loadPlugin(PLUGIN, quiet=True)
        except RuntimeError as exc:
            raise BridgeError(
                "The gameFbxExporter plugin could not be loaded.",
                hint="Enable gameFbxExporter in Windows > Settings/Preferences > Plug-in Manager.",
            ) from exc
    if not _sourced:
        for name in MEL_FILES:
            mel.eval(f'source "{name}.mel"')
        mel.eval(
            "global proc string bmaya_gameExporterNode() {"
            " global string $gGameFbxExporterCurrentNode;"
            " return $gGameFbxExporterCurrentNode; }"
        )
        _sourced = True


def exporter_nodes(name: str) -> list[str]:
    nodes = []
    if cmds.pluginInfo(PLUGIN, query=True, loaded=True):
        nodes = cmds.ls(type=PLUGIN) or []
    if not name:
        return nodes
    if name not in nodes:
        shown = ", ".join(nodes) or "none"
        raise BridgeError(
            f"No Game Exporter node named '{name}'.",
            hint=f"Game Exporter nodes in this scene: {shown}. Leave the name out to export them "
            "all.",
        )
    if cmds.referenceQuery(name, isNodeReferenced=True):
        raise BridgeError(
            f"'{name}' comes from a referenced file.",
            hint="Open that file and export from there; its own scene owns this node.",
        )
    return [name]


def number(node: str, attr: str) -> int:
    return int(cmds.getAttr(f"{node}.{attr}") or 0)


def text(node: str, attr: str) -> str:
    return cmds.getAttr(f"{node}.{attr}") or ""


def folder_of(node: str) -> str:
    folder = text(node, "exportPath")
    if folder and not os.path.isabs(folder):
        folder = cmds.workspace(expandName=folder)
    return folder.replace("\\", "/").rstrip("/")


def with_fbx(name: str) -> str:
    return name if os.path.splitext(name)[1] else name + ".fbx"


def members_of(node: str) -> tuple[list[str], str]:
    """The nodes this exporter writes, or the reason it writes nothing."""
    kind = number(node, "exportSetIndex")
    if kind == EXPORT_SET:
        name = text(node, "selectionSetName")
        if not name or not cmds.objExists(name):
            return [], f"its selection set '{name}' does not exist"
        members = cmds.sets(name, query=True) or []
        nodes = cmds.ls(members, long=True, objectsOnly=True) or []
        return nodes, "" if nodes else f"its selection set '{name}' is empty"
    if kind == EXPORT_SELECTED:
        nodes = cmds.ls(selection=True, long=True, objectsOnly=True) or []
        return nodes, "" if nodes else "it exports the selection and nothing is selected"
    roots = cmds.ls(assemblies=True, long=True) or []
    nodes = [root for root in roots if not cmds.listRelatives(root, shapes=True, type="camera")]
    return nodes, "" if nodes else "the scene is empty"


def clip_rows(node: str) -> list[tuple[str, float, float]]:
    rows = []
    for index in cmds.getAttr(node + ".animClips", multiIndices=True) or []:
        plug = f"{node}.animClips[{index}]"
        name = cmds.getAttr(plug + ".animClipName") or ""
        if name and cmds.getAttr(plug + ".exportAnimClip"):
            rows.append(
                (name, cmds.getAttr(plug + ".animClipStart"), cmds.getAttr(plug + ".animClipEnd"))
            )
    return rows


def textures_of(nodes: list[str]) -> list[dict[str, str]]:
    shapes = []
    for node in nodes:
        found = cmds.listRelatives(node, allDescendents=True, type="mesh", fullPath=True) or []
        if cmds.nodeType(node) == "mesh":
            found.append(node)
        shapes += found
    engines = list(dict.fromkeys(cmds.listConnections(shapes, type="shadingEngine") or []))
    out = []
    for engine in engines:
        for file_node in cmds.ls(cmds.listHistory(engine) or [], type="file"):
            if any(item["node"] == file_node for item in out):
                continue
            raw = cmds.getAttr(file_node + ".fileTextureName") or ""
            path = cmds.workspace(expandName=raw) if raw else ""
            entry = {"node": file_node, "path": path.replace("\\", "/")}
            if cmds.referenceQuery(file_node, isNodeReferenced=True):
                entry["referenced"] = "1"
            out.append(entry)
    return out


def node_expectation(node: str, nodes: list[str], takes: list[dict[str, Any]]) -> dict[str, Any]:
    options = {"animation": False, "up_axis": "y" if number(node, "upAxis") == 1 else "z"}
    expected = expectation(nodes, options, children=bool(number(node, "includeChildren")))
    expected["takes"] = takes
    if takes:
        expected["meshes"] = []
    return expected


def jobs_of(node: str, only_clip: str) -> tuple[list[dict[str, Any]], str]:
    """The files this exporter writes, or the reason it writes none."""
    if cmds.referenceQuery(node, isNodeReferenced=True):
        return [], "referenced"
    kind = number(node, "exportTypeIndex")
    if kind == TIME_EDITOR:
        return [], "it exports Time Editor clips, which bmaya does not export"
    if kind not in (MODEL, CLIPS):
        return [], f"unknown export type {kind}"
    if number(node, "moveToOrigin"):
        return [], "Move To Origin is on, which bmaya does not do"
    folder = folder_of(node)
    if not folder:
        return [], "it has no export path"
    nodes, reason = members_of(node)
    if reason:
        return [], reason
    file_name = text(node, "exportFilename")
    if kind == MODEL:
        if only_clip:
            return [], "it exports a model, not clips"
        if number(node, "modelFileMode") == FILE_PER_ROOT:
            return [], "it writes a file per root, which bmaya does not do"
        if not file_name:
            return [], "it has no file name"
        job = {
            "node": node,
            "kind": "model",
            "path": f"{folder}/{with_fbx(file_name)}",
            "textures": textures_of(nodes),
            "expected": node_expectation(node, nodes, []),
        }
        return [job], ""
    rows = [row for row in clip_rows(node) if not only_clip or row[0] == only_clip]
    if not rows:
        return (
            [],
            f"it has no clip named '{only_clip}'" if only_clip else "it has no clips to export",
        )
    if number(node, "fileSplitType") == ONE_FILE:
        if not file_name:
            return [], "it writes all clips to one file and has no file name"
        takes = [{"name": name, "frames": [frame(start), frame(end)]} for name, start, end in rows]
        job = {
            "node": node,
            "kind": "clips",
            "path": f"{folder}/{with_fbx(file_name)}",
            "frames": [frame(min(r[1] for r in rows)), frame(max(r[2] for r in rows))],
            "expected": node_expectation(node, nodes, takes),
        }
        return [job], ""
    prefix = os.path.splitext(file_name)[0] if file_name else ""
    jobs = []
    for name, start, end in rows:
        take = {"name": name, "frames": [frame(start), frame(end)]}
        jobs.append(
            {
                "node": node,
                "kind": "clip",
                "clip": name,
                "path": f"{folder}/{with_fbx(prefix + name)}",
                "frames": take["frames"],
                "expected": node_expectation(node, nodes, [take]),
            }
        )
    return jobs, ""


def plan_game_export(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "node")
    only_clip = text_param(params, "clip")
    nodes = exporter_nodes(name)
    if not nodes:
        raise BridgeError(
            "This scene has no Game Exporter nodes.",
            hint="Set one up in File > Game Exporter, or export with `bmaya export FILE.fbx`.",
        )
    jobs: list[dict[str, Any]] = []
    skipped = []
    issues: list[str] = []
    for node in nodes:
        found, reason = jobs_of(node, only_clip)
        if reason:
            skipped.append({"node": display_name(node), "reason": reason})
            continue
        if found[0]["kind"] == "model":
            members, _ = members_of(node)
            issues += check_shapes(members, bool_param(params, "ignore_checks"))
        jobs += found
    if not jobs:
        reasons = "; ".join(f"{item['node']}: {item['reason']}" for item in skipped)
        raise BridgeError(
            f"Nothing to export: {reasons}.",
            hint="`bmaya anim` lists the clips; File > Game Exporter shows each node's settings.",
        )
    return {"jobs": jobs, "skipped": skipped, "issues": issues}


def set_current_node(node: str) -> None:
    """The Game Exporter procedures read the node from this global; its window sets it too."""
    mel.eval(
        f'global string $gGameFbxExporterCurrentNode; $gGameFbxExporterCurrentNode = "{node}";'
    )


def apply_node_settings(node: str) -> None:
    set_current_node(node)
    directory = mel.eval("gameExp_GetFbxExportPresetDirectory()")
    preset = mel.eval("gameExp_GetFbxPresetForExportType()")
    mel.eval(f'FBXLoadExportPresetFile -f "{directory}/{preset}"')
    # A node made by script rather than by the window has no file version yet.
    version = text(node, "fileVersion")
    if not version:
        cmds.setAttr(node + ".fileVersion", DEFAULT_FILE_VERSION, type="string")
    try:
        mel.eval("gameExp_SetFbxPropertiesWithAttributes()")
    finally:
        if not version:
            cmds.setAttr(node + ".fileVersion", "", type="string")


def point_textures(moves: dict[str, str]) -> dict[str, list[Any]]:
    kept = {}
    for file_node, path in moves.items():
        if not cmds.objExists(file_node):
            continue
        kept[file_node] = [cmds.getAttr(f"{file_node}.{attr}") for attr in TEXTURE_ATTRS]
        cmds.setAttr(file_node + ".fileTextureName", path, type="string")
    return kept


def restore_textures(kept: dict[str, list[Any]]) -> None:
    for file_node, (path, space, ignore) in kept.items():
        cmds.setAttr(file_node + ".fileTextureName", path, type="string")
        cmds.setAttr(file_node + ".colorSpace", space, type="string")
        cmds.setAttr(file_node + ".ignoreColorSpaceFileRules", ignore)


def export_game_file(params: dict[str, Any]) -> dict[str, Any]:
    node = text_param(params, "node")
    kind = text_param(params, "kind")
    clip = text_param(params, "clip")
    path = text_param(params, "path")
    load_game_exporter()
    exporter_nodes(node)
    jobs, reason = jobs_of(node, clip if kind == "clip" else "")
    job = next((j for j in jobs if j["kind"] == kind and j.get("clip", "") == clip), None)
    if job is None or job["path"] != path:
        now = job["path"] if job else reason or "nothing"
        raise BridgeError(
            f"The Game Exporter node '{node}' changed since the export was planned: it now "
            f"writes {now}.",
            hint="Run the same `bmaya export --game-exporter` again.",
        )
    members, _ = members_of(node)
    selection = cmds.ls(selection=True, long=True) or []
    os.makedirs(os.path.dirname(path), exist_ok=True)
    flag = number(node, "exportSetIndex") != EXPORT_ALL
    kept: dict[str, list[Any]] = {}
    current = mel.eval("bmaya_gameExporterNode()")
    with scene_kept():
        if kind != "model":
            reload_fbx_plugin()
        mel.eval("FBXPushSettings")
        try:
            apply_node_settings(node)
            kept = point_textures(params.get("textures") or {})
            mel.eval("FBXExportSplitAnimationIntoTakes -clear")
            if kind != "model":
                start, end = job["frames"]
                mel.eval(f"FBXExportBakeComplexStart -v {start}")
                mel.eval(f"FBXExportBakeComplexEnd -v {end}")
                mel.eval("FBXExportDeleteOriginalTakeOnSplitAnimation -v true")
                for take in job["expected"]["takes"]:
                    first, last = take["frames"]
                    mel.eval(f'FBXExportSplitAnimationIntoTakes -v "{take["name"]}" {first} {last}')
            if flag:
                cmds.select(members, replace=True, noExpand=True)
            retried = run_fbx_export(path, flag)
        finally:
            mel.eval("FBXExportSplitAnimationIntoTakes -clear")
            mel.eval("FBXPopSettings")
            restore_textures(kept)
            set_current_node(current)
            select_only(selection)
    result: dict[str, Any] = {"path": path, "size": os.path.getsize(path)}
    if retried:
        result["retried"] = True
    return result
