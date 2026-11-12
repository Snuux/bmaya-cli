"""Checks the Maya-side command handlers in a headless mayapy session.

Run with: "C:\\Program Files\\Autodesk\\Maya2027\\bin\\mayapy.exe" tests/maya/check_handlers.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PATH = REPO / "src" / "bmaya_mcp" / "maya" / "python"

cmds: Any = None
om: Any = None
mel: Any = None
COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
NO_UNDO_CHUNK: frozenset[str] = frozenset()
BridgeError: type[Exception] = Exception
WORK_DIR = Path(tempfile.mkdtemp(prefix="bmaya-check-"))


def call(command: str, **params: object) -> dict[str, Any]:
    if command in NO_UNDO_CHUNK:
        result = COMMANDS[command](params)
    else:
        cmds.undoInfo(openChunk=True)
        try:
            result = COMMANDS[command](params)
        finally:
            cmds.undoInfo(closeChunk=True)
    json.dumps(result, allow_nan=False)
    return result


def expect_error(command: str, contains: str = "", **params: object) -> Exception:
    try:
        call(command, **params)
    except BridgeError as exc:
        text = f"{exc.message} {exc.hint}"
        assert exc.hint, f"{command} raised BridgeError without a hint: {exc.message}"
        assert contains in text, f"expected {contains!r} in {text!r}"
        return exc
    raise AssertionError(f"{command}({params}) did not raise BridgeError")


def assert_read_only(command: str, **params: object) -> dict[str, Any]:
    selection = cmds.ls(selection=True, long=True)
    cmds.flushUndo()
    result = call(command, **params)
    assert cmds.undoInfo(query=True, undoQueueEmpty=True), f"{command} left an undo entry"
    assert cmds.ls(selection=True, long=True) == selection, f"{command} changed the selection"
    return result


def build_scene() -> None:
    cmds.file(new=True, force=True)
    cmds.playbackOptions(minTime=1, maxTime=24, animationStartTime=1, animationEndTime=24)

    crate = cmds.polyCube(name="crate")[0]
    cmds.move(2, 0, 0, crate)
    props = cmds.group(crate, name="props")
    cmds.polySphere(name="pSphere1")
    shot_cam = cmds.camera()[0]
    cmds.rename(shot_cam, "shotCam")

    for parent in ("left", "right"):
        group = cmds.group(empty=True, name=parent)
        cmds.parent(cmds.polyCube(name="dup")[0], group)
    cmds.parent("left", props)

    cmds.select(clear=True)
    root = cmds.joint(name="root", position=(0, 0, 0))
    cmds.joint(name="spine", position=(0, 2, 0))
    cmds.joint(name="head", position=(0, 4, 0))
    cmds.setKeyframe(root, attribute="rotateZ", time=1, value=0)
    cmds.setKeyframe(root, attribute="rotateZ", time=24, value=45)
    cmds.setKeyframe("spine", attribute="rotateX", time=5, value=10)
    cmds.setKeyframe("spine", attribute="rotateX", time=20, value=-10)
    cmds.setInfinity("spine", attribute="rotateX", postInfinite="cycle")

    body = cmds.polyCylinder(name="body", height=4, subdivisionsHeight=4)[0]
    cmds.move(0, 2, 0, body)
    cmds.makeIdentity(body, apply=True, translate=True)
    cmds.delete(body, constructionHistory=True)
    cmds.skinCluster(["root", "spine", "head"], body, toSelectedBones=True, name="bodySkin")

    smile = cmds.duplicate(body, name="body_smile")[0]
    cmds.move(0, 0.5, 0, smile + ".vtx[0:5]", relative=True)
    cmds.blendShape(smile, body, name="faceShapes", frontOfChain=True)
    cmds.delete(smile)

    clean = cmds.polyCube(name="clean_box")[0]
    cmds.delete(clean, constructionHistory=True)
    material = cmds.shadingNode("lambert", asShader=True, name="box_mat")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="box_matSG")
    cmds.connectAttr(material + ".outColor", engine + ".surfaceShader")
    cmds.sets(clean, edit=True, forceElement=engine)

    build_broken_mesh()
    cmds.select(clear=True)


def build_broken_mesh() -> None:
    points = [
        (0, 0, 0),
        (1, 0, 0),
        (1.5, 0, 1),
        (0.5, 0, 1.5),
        (-0.5, 0, 1),
        (0, 1, 0),
        (1, -1, 0),
        (2, 0, 0),
        (3, 0, 0),
    ]
    counts = [5, 3, 3, 3]
    connects = [0, 1, 2, 3, 4, 0, 1, 5, 1, 0, 6, 1, 7, 8]
    transform = cmds.createNode("transform", name="pCube7")
    selection = om.MSelectionList()
    selection.add(transform)
    om.MFnMesh().create(
        [om.MPoint(*p) for p in points],
        counts,
        connects,
        parent=selection.getDependNode(0),
    )
    cmds.move(0, 0, 5, transform)


def check_get_scene_info() -> str:
    info = assert_read_only("get_scene_info")
    names = [node["name"] for node in info["nodes"]]
    assert "props" in names and "pSphere1" in names, names
    assert "persp" not in names and "shotCam" in names, names
    assert info["total"] == len(names) == info["shown"], info
    assert info["unit"] == "cm" and info["up"] == "y" and info["fps"] == 24, info
    assert info["playback"] == [1, 24], info
    props = next(node for node in info["nodes"] if node["name"] == "props")
    assert props["type"] == "group" and props["children"] == 2, props

    joints = call("get_scene_info", node_type="joint")
    assert joints["total"] == 3, joints
    found = call("get_scene_info", query="crat")
    assert [n["name"] for n in found["nodes"]] == ["crate"], found
    limited = call("get_scene_info", query="dup*", limit=1)
    assert limited["shown"] == 1 and limited["total"] > 1, limited
    for abstract in ("light", "constraint", "shape"):
        call("get_scene_info", node_type=abstract)
    expect_error("get_scene_info", "node type", node_type="notAType")
    return f"{info['total']} top nodes, joints={joints['total']}, query/limit ok"


def check_get_object_info() -> str:
    crate = assert_read_only("get_object_info", name="crate")
    assert crate["name"] == "|props|crate", crate
    assert crate["t"] == [2, 0, 0] and crate["parent"] == "props", crate
    mesh = crate["mesh"]
    assert mesh["verts"] == 8 and mesh["tris"] == 12 and len(mesh["materials"]) == 1, mesh
    assert "polyCube" in mesh["history"]["items"], mesh

    body = call("get_object_info", name="body")
    assert body["mesh"]["skin"] == {"name": "bodySkin", "influences": 3}, body

    root = call("get_object_info", name="root")
    assert root["joint"]["child_joints"] == 2, root
    assert root["anim"]["items"][0] == {"attr": "rotateZ", "keys": 2, "range": [1, 24]}, root

    expect_error("get_object_info", "|", name="dup")
    expect_error("get_object_info", "get_scene_info", name="nothing_here")
    expect_error("get_object_info", "get_scene_info", name="")
    return "transform, mesh, skin, joint, anim, ambiguous and missing names ok"


def check_execute_maya_code() -> str:
    result = call(
        "execute_maya_code",
        code="print('hello')\nresult = {'v': om.MVector(1, 2, 3), 'n': cmds.ls('crate')}",
    )
    assert result["stdout"] == "hello\n", result
    assert result["result"] == {"v": [1.0, 2.0, 3.0], "n": ["crate"]}, result
    assert result["truncated"] is False, result

    big = call("execute_maya_code", code="print('x' * 20000)\nresult = list(range(5000))")
    assert big["truncated"] is True and len(big["stdout"]) < 8200, len(big["stdout"])
    assert isinstance(big["result"], str) and len(big["result"]) < 8200, len(big["result"])

    try:
        call("execute_maya_code", code="a = 1\nraise ValueError('boom')")
    except ValueError:
        frames = traceback.extract_tb(sys.exc_info()[2])
        assert frames[-1].filename == "<agent>" and frames[-1].lineno == 2, frames[-1]
    else:
        raise AssertionError("exception from user code was swallowed")

    expect_error("execute_maya_code", "code", code="  ")
    return "stdout, result conversion, truncation, traceback line ok"


def check_get_viewport_screenshot() -> str:
    expect_error("get_viewport_screenshot", "batch")
    return "BridgeError in batch mode as expected"


def check_audit_mesh() -> str:
    cmds.select("crate", replace=True)
    by_selection = assert_read_only("audit_mesh")
    assert by_selection["source"] == "selection" and by_selection["total"] == 1, by_selection

    report = call("audit_mesh", names="pCube7, clean_box")
    broken, clean = report["meshes"]
    issues = " | ".join(broken["issues"])
    for expected in (
        "1 n-gons",
        "non-manifold edges",
        "zero-area faces",
        "no UVs",
        "no material",
        "unfrozen transform",
        "default name 'pCube7'",
    ):
        assert expected in issues, f"{expected!r} missing from {issues}"
    assert clean["issues"] == [], clean
    assert report["totals"]["with_issues"] == 1, report

    cmds.select(clear=True)
    whole = assert_read_only("audit_mesh")
    assert whole["source"] == "scene" and whole["total"] >= 6, whole
    expect_error("audit_mesh", "get_scene_info", names="missing_mesh")
    return f"broken mesh flagged ({len(broken['issues'])} issues), clean mesh clean"


def check_get_animation_info() -> str:
    overview = assert_read_only("get_animation_info")
    assert overview["curves"] == 2 and overview["key_range"] == [1, 24], overview
    assert overview["skeletons"]["items"] == [{"name": "root", "joints": 3}], overview
    skin = overview["skins"]["items"][0]
    assert skin["influences"] == 3 and skin["mesh"] == "body", skin
    assert overview["blend_shapes"]["items"][0]["targets"] == 1, overview

    root = call("get_animation_info", name="root")
    curve = root["curves"]["items"][0]
    assert curve["attr"] == "rotateZ" and curve["keys"] == 2 and curve["range"] == [1, 24], curve
    assert curve["tangents"], curve
    assert root["hierarchy"]["joints"] == 3 and root["hierarchy"]["animated_joints"] == 2, root
    assert root["hierarchy"]["skins"] == ["bodySkin"], root
    spine = call("get_animation_info", name="spine")["curves"]["items"][0]
    assert spine["infinity"] == ["constant", "cycle"], spine
    return "overview, curves, skeleton, skin, blend shape ok"


def check_export_fbx() -> str:
    target = WORK_DIR / "new folder" / "hero.fbx"
    cmds.select("crate", replace=True)
    result = call("export_fbx", path=str(target), preset="unreal", animation=True)
    assert target.is_file() and result["size"] == target.stat().st_size > 0, result
    assert result["options"]["triangulate"] is True and result["options"]["bake"] == [1, 24]
    assert mel.eval("FBXExportTriangulate -q") == 1, "unreal triangulate not applied"
    assert mel.eval("FBXExportSmoothingGroups -q") == 1, "unreal smoothing groups not applied"
    assert mel.eval("FBXExportBakeComplexEnd -q") == 24, "bake end not applied"
    assert cmds.ls(selection=True) == ["crate"], cmds.ls(selection=True)

    only = call("export_fbx", path=str(WORK_DIR / "crate.fbx"), selection_only=True)
    assert only["preset"] == "unity" and only["nodes"]["items"] == ["crate"], only
    assert mel.eval("FBXExportTriangulate -q") == 0, "unity triangulate not applied"
    assert mel.eval("FBXExportCameras -q") == 0, "cameras not turned off"

    cmds.select(clear=True)
    expect_error("export_fbx", "nothing is selected", path=str(target), selection_only=True)
    expect_error("export_fbx", ".fbx", path=str(WORK_DIR / "x.obj"))
    expect_error("export_fbx", "unity", path=str(target), preset="cryengine")
    return f"unreal+anim {result['size']} bytes, selection-only and errors ok"


def check_scene_file() -> str:
    saved = call("scene_file", action="save", path=str(WORK_DIR / "scenes" / "check.ma"))
    assert saved["scene"].endswith("scenes/check.ma") and saved["modified"] is False, saved

    cmds.polyCube(name="unsaved_change")
    expect_error("scene_file", "force", action="new")
    expect_error("scene_file", "force", action="open", path=saved["scene"])
    reopened = call("scene_file", action="open", path=saved["scene"], force=True)
    assert not cmds.objExists("unsaved_change") and reopened["modified"] is False, reopened

    fresh = call("scene_file", action="new")
    assert fresh["scene"] == "untitled", fresh
    expect_error("scene_file", "path", action="save")

    imported = call("scene_file", action="import", path=str(WORK_DIR / "crate.fbx"))
    assert imported["imported"]["total"] >= 1, imported
    imported_ma = call("scene_file", action="import", path=saved["scene"])
    assert imported_ma["imported"]["total"] == 8, imported_ma

    expect_error("scene_file", "not found", action="open", path=str(WORK_DIR / "missing.ma"))
    expect_error("scene_file", "new, open", action="rename")
    return "save as, unsaved guard, force open, new, fbx and ma import ok"


def check_undo() -> str:
    cmds.file(new=True, force=True)
    cmds.flushUndo()
    call("execute_maya_code", code="cmds.polyCube(name='first')")
    call("execute_maya_code", code="cmds.polyCube(name='second')")
    call("get_scene_info")
    result = call("undo")
    assert result["undone"] == 1, result
    assert cmds.objExists("first") and not cmds.objExists("second"), result

    drained = call("undo", steps=10)
    assert drained["undone"] == 1 and drained.get("queue_empty") is True, drained
    assert not cmds.objExists("first"), drained
    return "single step, early stop on empty queue ok"


def check_bridge_dispatch() -> str:
    from bmaya_bridge.journal import Journal
    from bmaya_bridge.server import BridgeServer

    journal = Journal(WORK_DIR / "logs", crash_log=False)
    bridge = BridgeServer(journal, schedule=lambda callback: callback())

    def send(command: str, **params: object) -> dict[str, Any]:
        request = {"id": 1, "client": "check", "command": command, "params": params}
        return bridge.handle(request)

    try:
        cmds.file(new=True, force=True)
        cmds.flushUndo()
        for name in ("first", "second"):
            reply = send("execute_maya_code", code=f"cmds.polyCube(name='{name}')")
            assert reply["ok"], reply
        undone = send("undo", steps=1)
        assert undone["ok"] and undone["result"]["undone"] == 1, undone
        assert cmds.objExists("first") and not cmds.objExists("second"), undone
        assert cmds.undoInfo(query=True, redoName=True), "undo left nothing to redo"

        failed = send("execute_maya_code", code="print('before')\nraise ValueError('boom')")
        error = failed["error"]
        assert error["type"] == "ValueError" and error["stdout"] == "before\n", failed
        assert "line 2" in error["hint"], failed
    finally:
        journal.close()
    return f"undo removed only 'second', next undo: {undone['result'].get('next')}"


EXTRA_CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("bridge dispatch", check_bridge_dispatch),
]

CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("get_scene_info", check_get_scene_info),
    ("get_object_info", check_get_object_info),
    ("execute_maya_code", check_execute_maya_code),
    ("get_viewport_screenshot", check_get_viewport_screenshot),
    ("audit_mesh", check_audit_mesh),
    ("get_animation_info", check_get_animation_info),
    ("export_fbx", check_export_fbx),
    ("scene_file", check_scene_file),
    ("undo", check_undo),
]


def main() -> int:
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        return run_checks()
    finally:
        maya.standalone.uninitialize()


def run_checks() -> int:
    global cmds, om, mel, COMMANDS, NO_UNDO_CHUNK, BridgeError

    sys.path.insert(0, str(BRIDGE_PATH))

    import maya.api.OpenMaya as openmaya
    from bmaya_bridge.errors import BridgeError as bridge_error
    from bmaya_bridge.handlers import COMMANDS as commands
    from bmaya_bridge.handlers import NO_UNDO_CHUNK as no_undo_chunk
    from maya import cmds as maya_cmds
    from maya import mel as maya_mel

    cmds = maya_cmds
    mel = maya_mel
    om = openmaya
    COMMANDS = commands
    NO_UNDO_CHUNK = no_undo_chunk
    BridgeError = bridge_error

    assert set(COMMANDS) == {name for name, _ in CHECKS}, sorted(COMMANDS)

    checks = CHECKS + EXTRA_CHECKS
    failures = 0
    lines = []
    try:
        build_scene()
        for name, check in checks:
            try:
                lines.append(f"PASS {name}: {check()}")
            except Exception as exc:
                failures += 1
                detail = traceback.format_exc(limit=-2).strip().splitlines()
                lines.append(f"FAIL {name}: {type(exc).__name__}: {exc}")
                lines.extend("    " + line for line in detail)
    finally:
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    print()
    print("\n".join(lines))
    print(f"{len(checks) - failures}/{len(checks)} checks passed")
    sys.stdout.flush()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
