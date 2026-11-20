"""Write the FBX files in tests/fixtures and what Maya says about them, in fixtures.json.

    mayapy tests/maya/make_fixtures.py [OUT_DIR]

The files are written to a neutral folder first (C:/bmaya_fixtures by default) because an FBX
keeps the absolute path of its document and textures; they are then copied next to this repo's
tests. Run it again when the readers need a new case.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PATH = REPO / "src" / "bmaya_cli" / "maya" / "python"
FIXTURES = REPO / "tests" / "fixtures"

cmds: Any = None
mel: Any = None
om: Any = None


def write_png(path: Path, size: int, color: tuple[int, int, int]) -> None:
    row = b"\x00" + bytes(color) * size
    body = zlib.compress(row * size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", body) + chunk(b"IEND", b"")
    )


def new_scene(unit: str = "cm", time: str = "ntsc") -> None:
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear=unit, time=time)


def export(
    path: Path, animation: bool = False, bake: tuple[float, float] = (0, 0), **extra: object
) -> None:
    from bmaya_bridge.handlers import export as exporting

    options: dict[str, Any] = dict(exporting.PRESETS["godot"])
    options.update(exporting.COMMON_OPTIONS)
    options["animation"] = animation
    options["bake"] = list(bake)
    options.update(extra)
    exporting.ensure_fbx_plugin()
    if animation:
        # The plugin keeps baking at the frame rate of an earlier export until it is reloaded.
        cmds.unloadPlugin("fbxmaya")
        cmds.loadPlugin("fbxmaya")
    exporting.apply_options(options)
    takes = extra.get("takes", [])
    mel.eval("FBXExportSplitAnimationIntoTakes -clear")
    if takes:
        mel.eval("FBXExportDeleteOriginalTakeOnSplitAnimation -v true")
        for name, start, end in takes:
            mel.eval(f'FBXExportSplitAnimationIntoTakes -v "{name}" {start} {end}')
    mel.eval(f'FBXExport -f "{path.as_posix()}"')
    mel.eval("FBXExportSplitAnimationIntoTakes -clear")


def mesh_truth(transform: str) -> dict[str, Any]:
    return {
        "verts": cmds.polyEvaluate(transform, vertex=True),
        "polys": cmds.polyEvaluate(transform, face=True),
        "tris": cmds.polyEvaluate(transform, triangle=True),
    }


def joint_truth(root: str) -> dict[str, Any]:
    out = {}
    for joint in [root] + list(
        reversed(cmds.listRelatives(root, allDescendents=True, type="joint") or [])
    ):
        matrix = om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
        rows = [[matrix.getElement(r, c) for c in range(3)] for r in range(3)]
        axes = [om.MVector(*row).normal() for row in rows]
        parent = cmds.listRelatives(joint, parent=True, type="joint") or [""]
        out[joint] = {
            "parent": parent[0],
            "at": cmds.xform(joint, query=True, worldSpace=True, translation=True),
            "axes": [[axis.x, axis.y, axis.z] for axis in axes],
        }
    return out


def textured_crate(name: str, size: float, folder: Path) -> str:
    crate = cmds.polyCube(name=name, width=size, height=size, depth=size)[0]
    cmds.delete(crate, constructionHistory=True)
    texture = folder / "crate.png"
    write_png(texture, 8, (180, 120, 60))
    material = cmds.shadingNode("lambert", asShader=True, name="crate_mat")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="crate_matSG")
    cmds.connectAttr(material + ".outColor", engine + ".surfaceShader")
    file_node = cmds.shadingNode("file", asTexture=True, name="crate_file")
    cmds.setAttr(file_node + ".fileTextureName", texture.as_posix(), type="string")
    cmds.connectAttr(file_node + ".outColor", material + ".color")
    cmds.sets(crate, edit=True, forceElement=engine)
    return crate


def make_crate(folder: Path) -> dict[str, Any]:
    new_scene()
    crate = textured_crate("crate", 100, folder)
    export(folder / "crate.fbx")
    export(folder / "crate_ascii.fbx", ascii=True)
    return {
        "unit": "cm",
        "fps": 30,
        "meshes": {"crate": mesh_truth(crate)},
        "textures": [(folder / "crate.png").as_posix()],
    }


def make_crate_meters(folder: Path) -> dict[str, Any]:
    new_scene(unit="m", time="film")
    crate = cmds.polyCube(name="crate_m", width=1, height=1, depth=1)[0]
    cmds.delete(crate, constructionHistory=True)
    export(folder / "crate_m.fbx")
    return {"unit": "m", "fps": 24, "meshes": {"crate_m": mesh_truth(crate)}}


def build_walker() -> tuple[str, str]:
    cmds.select(clear=True)
    root = cmds.joint(name="root", position=(0, 0, 0))
    cmds.joint(name="spine", position=(0, 10, 5))
    cmds.joint(name="head", position=(3, 25, 5))
    cmds.joint(root, edit=True, orientJoint="xyz", secondaryAxisOrient="yup", children=True)
    cmds.select(clear=True)
    body = cmds.polyCylinder(name="body", radius=4, height=25, subdivisionsHeight=6)[0]
    cmds.move(1, 12.5, 4, body)
    cmds.makeIdentity(body, apply=True, translate=True)
    cmds.delete(body, constructionHistory=True)
    cmds.skinCluster(
        ["root", "spine", "head"], body, toSelectedBones=True, maximumInfluences=2, name="body_skin"
    )
    cmds.setKeyframe(root, attribute="translateY", time=0, value=0)
    cmds.setKeyframe(root, attribute="translateY", time=16, value=4)
    cmds.setKeyframe("spine", attribute="rotateZ", time=16, value=0)
    cmds.setKeyframe("spine", attribute="rotateZ", time=32, value=40)
    cmds.currentTime(0)
    return root, body


def make_walker(folder: Path) -> dict[str, Any]:
    new_scene()
    root, body = build_walker()
    truth = {
        "unit": "cm",
        "fps": 30,
        "meshes": {"body": {**mesh_truth(body), "influences": 3}},
        "joints": joint_truth(root),
        "takes": {"walk": [0, 16], "wave": [16, 32]},
    }
    takes = [("walk", 0, 16), ("wave", 16, 32)]
    export(folder / "walker.fbx", animation=True, bake=(0, 32), takes=takes)
    export(
        folder / "walker_7400.fbx", animation=True, bake=(0, 32), takes=takes, version="FBX201400"
    )
    return truth


def make_bones(folder: Path) -> dict[str, Any]:
    new_scene()
    cmds.select(clear=True)
    root = cmds.joint(name="arm", position=(5, 2, 0))
    cmds.joint(name="elbow", position=(15, 2, -3))
    cmds.joint(name="wrist", position=(24, 6, -3))
    cmds.joint(root, edit=True, orientJoint="xyz", secondaryAxisOrient="zup", children=True)
    cmds.setAttr(root + ".rotate", 10, 20, 30)
    cmds.setAttr("elbow.rotateOrder", 3)
    cmds.setAttr("elbow.rotate", 0, 35, -15)
    cmds.select(clear=True)
    export(folder / "bones.fbx")
    return {"unit": "cm", "fps": 30, "joints": joint_truth(root)}


def make_spin(folder: Path) -> dict[str, Any]:
    new_scene(time="film")
    cube = cmds.polyCube(name="spinner")[0]
    cmds.delete(cube, constructionHistory=True)
    cmds.setKeyframe(cube, attribute="rotateY", time=1, value=0)
    cmds.setKeyframe(cube, attribute="rotateY", time=24, value=360)
    cmds.playbackOptions(minTime=1, maxTime=24)
    export(folder / "clip_spin.fbx", animation=True, bake=(1, 24))
    return {
        "unit": "cm",
        "fps": 24,
        "meshes": {"spinner": mesh_truth(cube)},
        "takes": {"": [1, 24]},
    }


def main() -> int:
    global cmds, mel, om
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "C:/bmaya_fixtures")
    out.mkdir(parents=True, exist_ok=True)
    # An FBX records the projects folder and the active project, both under the home folder
    # by default.
    os.environ["MAYA_PROJECTS_DIR"] = out.as_posix()

    import maya.standalone

    maya.standalone.initialize(name="python")
    sys.path.insert(0, str(BRIDGE_PATH))
    import maya.api.OpenMaya as openmaya
    from maya import cmds as maya_cmds
    from maya import mel as maya_mel

    cmds = maya_cmds
    mel = maya_mel
    om = openmaya
    cmds.workspace(out.as_posix(), openWorkspace=True)
    try:
        truth = {
            "crate.fbx": make_crate(out),
            "crate_m.fbx": make_crate_meters(out),
            "walker.fbx": make_walker(out),
            "bones.fbx": make_bones(out),
            "clip_spin.fbx": make_spin(out),
        }
        truth["walker_7400.fbx"] = truth["walker.fbx"]
        (out / "fixtures.json").write_text(json.dumps(truth, indent=1, sort_keys=True) + "\n")
        FIXTURES.mkdir(exist_ok=True)
        for name in [*truth, "crate_ascii.fbx", "crate.png", "fixtures.json"]:
            shutil.copy2(out / name, FIXTURES / name)
    finally:
        maya.standalone.uninitialize()
    print("wrote", sorted(p.name for p in FIXTURES.iterdir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
