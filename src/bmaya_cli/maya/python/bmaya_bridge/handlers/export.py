from __future__ import annotations

import os
from typing import Any

from maya import cmds, mel

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    frame,
    listing,
    number_text,
    text_param,
)
from bmaya_bridge.handlers._timeline import find_clip

NODE_LIMIT = 30

# Units are not converted: FBXExportConvertUnitString has no effect in Maya 2027, and the
# engines read UnitScaleFactor, which follows the scene's linear unit.
# Only Unreal gets triangulation; Unity and Godot triangulate on import.
PRESETS: dict[str, dict[str, Any]] = {
    "unity": {
        "up_axis": "y",
        "triangulate": False,
        "smoothing_groups": False,
        "tangents": True,
        "version": "FBX202000",
    },
    "unreal": {
        "up_axis": "y",
        "triangulate": True,
        "smoothing_groups": True,
        "tangents": True,
        "version": "FBX202000",
    },
    "godot": {
        "up_axis": "y",
        "triangulate": False,
        "smoothing_groups": False,
        "tangents": True,
        "version": "FBX202000",
    },
}

COMMON_OPTIONS = {
    "skins": True,
    "blend_shapes": True,
    "cameras": False,
    "lights": False,
    "embed_media": False,
    "hard_edges": False,
    "constraints": False,
    "ascii": False,
}


def mel_bool(value: bool) -> str:
    return "true" if value else "false"


def ensure_fbx_plugin() -> None:
    if cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        return
    try:
        cmds.loadPlugin("fbxmaya", quiet=True)
    except RuntimeError as exc:
        raise BridgeError(
            "The fbxmaya plugin could not be loaded.",
            hint="Enable fbxmaya in Windows > Settings/Preferences > Plug-in Manager.",
        ) from exc


def target_path(raw: str) -> str:
    if not raw:
        raise BridgeError(
            "No export path given.",
            hint="Pass an absolute path ending in .fbx, for example C:/game/Assets/hero.fbx.",
        )
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        raise BridgeError(
            f"Export path must be absolute: {raw}",
            hint="Pass a full path ending in .fbx, for example C:/game/Assets/hero.fbx.",
        )
    if os.path.splitext(path)[1].lower() != ".fbx":
        raise BridgeError(
            f"Export path must end in .fbx: {raw}",
            hint="Change the file extension to .fbx.",
        )
    return os.path.abspath(path).replace("\\", "/")


def bake_range(params: dict[str, Any]) -> tuple[float, float]:
    start = number_text(params, "start")
    end = number_text(params, "end")
    clip = text_param(params, "clip")
    if clip:
        if start is not None or end is not None:
            raise BridgeError(
                "Pass --clip or --start/--end, not both.",
                hint="--clip NAME bakes the frames of that clip.",
            )
        first, last = find_clip(clip)["range"]
        return float(first), float(last)
    if start is None:
        start = cmds.playbackOptions(query=True, minTime=True)
    if end is None:
        end = cmds.playbackOptions(query=True, maxTime=True)
    if end < start:
        raise BridgeError(
            f"Bake range end {end} is before start {start}.",
            hint="Pass --start <= --end, or neither to use the playback range.",
        )
    return start, end


def apply_options(options: dict[str, Any]) -> None:
    commands = [
        "FBXResetExport",
        f"FBXExportUpAxis {options['up_axis']}",
        f"FBXExportTriangulate -v {mel_bool(options['triangulate'])}",
        f"FBXExportSmoothingGroups -v {mel_bool(options['smoothing_groups'])}",
        f"FBXExportHardEdges -v {mel_bool(options['hard_edges'])}",
        f"FBXExportTangents -v {mel_bool(options['tangents'])}",
        "FBXExportSmoothMesh -v false",
        f"FBXExportSkins -v {mel_bool(options['skins'])}",
        f"FBXExportShapes -v {mel_bool(options['blend_shapes'])}",
        f"FBXExportCameras -v {mel_bool(options['cameras'])}",
        f"FBXExportLights -v {mel_bool(options['lights'])}",
        f"FBXExportEmbeddedTextures -v {mel_bool(options['embed_media'])}",
        f"FBXExportConstraints -v {mel_bool(options['constraints'])}",
        "FBXExportInputConnections -v true",
        "FBXExportUseSceneName -v false",
        f"FBXExportInAscii -v {mel_bool(options['ascii'])}",
        f"FBXExportFileVersion -v {options['version']}",
        "FBXProperty Export|IncludeGrp|Animation -v " + mel_bool(options["animation"]),
        f"FBXExportBakeComplexAnimation -v {mel_bool(options['animation'])}",
    ]
    if options["animation"]:
        start, end = options["bake"]
        commands += [
            f"FBXExportBakeComplexStart -v {start}",
            f"FBXExportBakeComplexEnd -v {end}",
            "FBXExportBakeComplexStep -v 1",
            "FBXExportBakeResampleAnimation -v true",
            "FBXExportApplyConstantKeyReducer -v false",
        ]
    for command in commands:
        mel.eval(command)


def restore_selection(selection: list[str]) -> None:
    if (cmds.ls(selection=True, long=True) or []) == selection:
        return
    if selection:
        cmds.select(selection, replace=True)
    else:
        cmds.select(clear=True)


def export_fbx(params: dict[str, Any]) -> dict[str, Any]:
    preset = text_param(params, "preset").lower() or "unity"
    if preset not in PRESETS:
        raise BridgeError(
            f"Unknown FBX preset '{preset}'.",
            hint="Use --preset with one of: " + ", ".join(PRESETS) + ". The default is unity.",
        )
    path = target_path(text_param(params, "path"))
    selection_only = bool_param(params, "selection_only")
    clip = text_param(params, "clip")
    animation = bool_param(params, "animation") or bool(clip)

    selection = cmds.ls(selection=True, long=True) or []
    if selection_only:
        nodes = cmds.ls(selection=True, long=True, objectsOnly=True) or []
        if not nodes:
            raise BridgeError(
                "selection_only is set but nothing is selected.",
                hint="Select the objects to export first, or drop --selection.",
            )
    else:
        roots = cmds.ls(assemblies=True, long=True) or []
        nodes = [root for root in roots if not cmds.listRelatives(root, shapes=True, type="camera")]
        if not nodes:
            raise BridgeError("The scene is empty, so there is nothing to export.")

    options: dict[str, Any] = dict(PRESETS[preset])
    options.update(COMMON_OPTIONS)
    options["units"] = cmds.currentUnit(query=True, linear=True)
    options["animation"] = animation
    if animation:
        start, end = bake_range(params)
        options["bake"] = [frame(start), frame(end)]
        if clip:
            options["clip"] = clip

    ensure_fbx_plugin()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        apply_options(options)
        flag = " -s" if selection_only else ""
        mel.eval(f'FBXExport -f "{path}"{flag}')
    finally:
        restore_selection(selection)

    if not os.path.isfile(path):
        raise BridgeError(
            f"FBX export did not write {path}.",
            hint="Check the Script Editor for FBX errors and that the folder is writable.",
        )
    exported = [display_name(node) for node in nodes]
    return {
        "path": path,
        "size": os.path.getsize(path),
        "preset": preset,
        "options": options,
        "nodes": listing(exported, NODE_LIMIT),
    }
