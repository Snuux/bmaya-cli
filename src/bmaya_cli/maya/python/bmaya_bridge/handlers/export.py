from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from maya import cmds, mel

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers import _gltf
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    fps,
    frame,
    listing,
    number_text,
    rnd,
    scene_kept,
    select_only,
    text_param,
)
from bmaya_bridge.handlers._shapes import mesh_transforms, preflight
from bmaya_bridge.handlers._timeline import clips, find_clip

NODE_LIMIT = 30
EXPECTED_LIMIT = 500
REFUSALS_SHOWN = 3

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


def reload_fbx_plugin() -> bool:
    """The plugin can keep baking at the frame rate of an earlier export until it is reloaded.

    Reloading puts the exporter's settings back to its defaults, so it is done only when the
    command line found keys baked at the wrong rate in the written file."""
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        return False
    try:
        cmds.unloadPlugin("fbxmaya")
    except RuntimeError:
        return False
    ensure_fbx_plugin()
    return True


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


def target_path(raw: str, suffixes: tuple[str, ...] = (".fbx",)) -> str:
    ending = " or ".join(suffixes)
    example = f"C:/game/Assets/hero{suffixes[0]}"
    if not raw:
        raise BridgeError(
            "No export path given.",
            hint=f"Pass an absolute path ending in {ending}, for example {example}.",
        )
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        raise BridgeError(
            f"Export path must be absolute: {raw}",
            hint=f"Pass a full path ending in {ending}, for example {example}.",
        )
    if os.path.splitext(path)[1].lower() not in suffixes:
        raise BridgeError(
            f"Export path must end in {ending}: {raw}",
            hint=f"Change the file extension to {ending}.",
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
    commands.append("FBXExportSplitAnimationIntoTakes -clear")
    if options["animation"]:
        start, end = options["bake"]
        # Without its own take the file's take spans the playback range, not the baked frames.
        commands += [
            f"FBXExportBakeComplexStart -v {start}",
            f"FBXExportBakeComplexEnd -v {end}",
            "FBXExportBakeComplexStep -v 1",
            "FBXExportBakeResampleAnimation -v true",
            "FBXExportApplyConstantKeyReducer -v false",
            "FBXExportDeleteOriginalTakeOnSplitAnimation -v true",
            f'FBXExportSplitAnimationIntoTakes -v "{options["take"]}" {start} {end}',
        ]
    for command in commands:
        mel.eval(command)


def file_stamp(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def run_fbx_export(path: str, selection_only: bool) -> bool:
    """Writes the file; True when a first attempt failed and a second one was needed."""
    before = file_stamp(path)
    command = f'FBXExport -f "{path}"' + (" -s" if selection_only else "")
    retried = False
    try:
        mel.eval(command)
    except RuntimeError:
        # Without a window the first export after a texture change can fail in a UI
        # callback; the second one goes through.
        if not cmds.about(batch=True):
            raise
        retried = True
        mel.eval(command)
    after = file_stamp(path)
    if after is None:
        raise BridgeError(
            f"FBX export did not write {path}.",
            hint="Check the Script Editor for FBX errors and that the folder is writable.",
        )
    if after == before:
        raise BridgeError(
            f"FBX export left {path} as it was.",
            hint="The file may be read-only or open in another program. Close it and export again.",
        )
    return retried


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def skin_joints(transforms: list[str]) -> list[str]:
    """The joints the skins of these meshes need, wherever they are in the scene."""
    out = []
    for transform in transforms:
        for skin in cmds.ls(cmds.listHistory(transform) or [], type="skinCluster"):
            for joint in cmds.skinCluster(skin, query=True, influence=True) or []:
                if joint not in out:
                    out.append(joint)
    return out


def expectation(
    nodes: list[str],
    options: dict[str, Any],
    children: bool = True,
    name: Callable[[str], str] = leaf,
) -> dict[str, Any]:
    """What the written file should hold; the command line reads the file and compares.
    `name` is the name a node has in the file."""
    transforms = mesh_transforms(nodes)
    if not children:
        transforms = [transform for transform in transforms if transform in nodes]
    joints: list[str] = []
    found = []
    for node in nodes:
        if cmds.nodeType(node) == "joint":
            found.append(node)
        if children:
            below = cmds.listRelatives(node, allDescendents=True, type="joint", fullPath=True)
            found += reversed(below or [])
    for joint in found + skin_joints(transforms):
        written = name(joint)
        if written not in joints:
            joints.append(written)
    meshes = [name(transform) for transform in transforms]
    takes = []
    if options["animation"]:
        takes.append({"name": options["take"], "frames": options["bake"]})
    return {
        "meshes": meshes[:EXPECTED_LIMIT],
        "joints": joints[:EXPECTED_LIMIT],
        "takes": takes,
        "fps": rnd(fps(), 3),
        "up": options["up_axis"],
    }


def check_shapes(nodes: list[str], ignore: bool) -> list[str]:
    blocking, issues = preflight(nodes)
    if blocking and not ignore:
        shown = "; ".join(blocking[:REFUSALS_SHOWN])
        more = len(blocking) - REFUSALS_SHOWN
        if more > 0:
            shown += f"; and {more} more"
        raise BridgeError(
            f"Export refused: {shown}.",
            hint="`bmaya cleanup NAME` turns inside-out faces out and clears the opposite flag; "
            "delete meshes with no faces. `bmaya audit NAME` lists every problem. "
            "--ignore-checks writes the file anyway.",
        )
    return blocking + issues


def export_roots(selection_only: bool) -> list[str]:
    if selection_only:
        nodes = cmds.ls(selection=True, long=True, objectsOnly=True) or []
        if not nodes:
            raise BridgeError(
                "selection_only is set but nothing is selected.",
                hint="Select the objects to export first, or drop --selection.",
            )
        return nodes
    roots = cmds.ls(assemblies=True, long=True) or []
    nodes = [root for root in roots if not cmds.listRelatives(root, shapes=True, type="camera")]
    if not nodes:
        raise BridgeError("The scene is empty, so there is nothing to export.")
    return nodes


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
    nodes = export_roots(selection_only)

    options: dict[str, Any] = dict(PRESETS[preset])
    options.update(COMMON_OPTIONS)
    options["units"] = cmds.currentUnit(query=True, linear=True)
    options["animation"] = animation
    if animation:
        start, end = bake_range(params)
        options["bake"] = [frame(start), frame(end)]
        options["take"] = clip or os.path.splitext(os.path.basename(path))[0]
        if clip:
            options["clip"] = clip

    issues = check_shapes(nodes, bool_param(params, "ignore_checks"))
    expected = expectation(nodes, options)
    ensure_fbx_plugin()
    reloaded = bool_param(params, "reload_fbx") and reload_fbx_plugin()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        apply_options(options)
        retried = run_fbx_export(path, selection_only)
    finally:
        mel.eval("FBXExportSplitAnimationIntoTakes -clear")
        select_only(selection)

    exported = [display_name(node) for node in nodes]
    result: dict[str, Any] = {
        "path": path,
        "size": os.path.getsize(path),
        "preset": preset,
        "options": options,
        "nodes": listing(exported, NODE_LIMIT),
        "issues": issues,
        "expected": expected,
    }
    if retried:
        result["retried"] = True
    if reloaded:
        result["reloaded"] = True
    return result


GLTF_SUFFIXES = (".glb", ".gltf")
TEXTURES_NAMED = 5


def gltf_animations(params: dict[str, Any], path: str) -> list[tuple[str, float, float]]:
    """(name, first frame, last frame) of each animation the file gets."""
    if bool_param(params, "clips"):
        if text_param(params, "clip") or bool_param(params, "animation"):
            raise BridgeError(
                "--clips writes every clip; it cannot go with --clip or --animation.",
                hint="Drop one of them.",
            )
        found = [entry for entry in clips() if entry.get("export", True)]
        found.sort(key=lambda entry: entry["range"][0])
        if not found:
            raise BridgeError(
                "--clips found no clips in the scene.",
                hint="The scene has no Game Exporter clips or time slider bookmarks. "
                "`bmaya clip NAME --add A:B` makes one; --animation exports the playback range.",
            )
        names = [entry["name"] for entry in found]
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            raise BridgeError(
                "Clip names are used twice with different ranges: " + ", ".join(twice) + ".",
                hint="An engine finds animations by name. `bmaya clip` lists the clips; "
                "rename or remove one of each pair.",
            )
        return [(entry["name"], *map(float, entry["range"])) for entry in found]
    clip = text_param(params, "clip")
    if not (bool_param(params, "animation") or clip):
        return []
    start, end = bake_range(params)
    return [(clip or os.path.splitext(os.path.basename(path))[0], start, end)]


def gltf_name(node: str) -> str:
    """A node's name in a glTF file, the way the command line compares names."""
    return leaf(_gltf.written_name(node))


def export_gltf(params: dict[str, Any]) -> dict[str, Any]:
    gltf = _gltf.library()
    from maya_glb_io import exporter

    path = target_path(text_param(params, "path"), GLTF_SUFFIXES)
    binary = path.lower().endswith(".glb")
    selection_only = bool_param(params, "selection_only")
    animations = gltf_animations(params, path)
    nodes = export_roots(selection_only)
    issues = check_shapes(nodes, bool_param(params, "ignore_checks"))
    if cmds.upAxis(query=True, axis=True) == "z":
        issues.append(
            "the scene is Z-up and glTF is Y-up: the file is not turned, so it lies on its back "
            "in an engine"
        )

    options: dict[str, Any] = {
        "up_axis": "y",
        "animation": bool(animations),
        "take": animations[0][0] if animations else "",
        "bake": [frame(animations[0][1]), frame(animations[0][2])] if animations else [],
    }
    expected = expectation(nodes, options, name=gltf_name)
    expected["takes"] = [
        {"name": name, "frames": [frame(first), frame(last)]} for name, first, last in animations
    ]
    library_options: dict[str, Any] = {
        "format": "glb" if binary else "gltf",
        "embedTextures": binary,
        "copyTextures": not binary,
        "convertUnits": True,
        "applyTransforms": False,
        "exportSkin": True,
        "exportMorphTargets": True,
        "exportAnimation": bool(animations),
        "reduceKeys": True,
        "clips": [{"name": name, "start": first, "end": last} for name, first, last in animations],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    before = file_stamp(path)
    with scene_kept():
        written = exporter.export_nodes(nodes, path, options=library_options)
    after = file_stamp(path)
    if after is None or after == before:
        raise BridgeError(
            f"glTF export did not write {path}.",
            hint="Check the Script Editor for [glb_io] errors, and that the file is not open "
            "in another program.",
        )
    lost = [os.path.basename(texture) for texture in written.get("missing_textures", [])]
    if lost:
        issues.append(
            f"{len(lost)} texture files are not on disk, so the file has no such maps: "
            + ", ".join(lost[:TEXTURES_NAMED])
        )
    return {
        "path": path,
        "size": os.path.getsize(path),
        "library": {"name": _gltf.NAME, "version": gltf.__version__},
        "options": {
            "format": library_options["format"],
            "textures": "embedded" if binary else "copied next to the file",
            "unit": "m",
            "animations": [name for name, _, _ in animations],
        },
        "nodes": listing([display_name(node) for node in nodes], NODE_LIMIT),
        "issues": issues,
        "expected": expected,
    }
