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
BRIDGE_PATH = REPO / "src" / "bmaya_cli" / "maya" / "python"

cmds: Any = None
om: Any = None
mel: Any = None
COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
NO_UNDO_CHUNK: frozenset[str] = frozenset()
BridgeError: type[Exception] = Exception
WORK_DIR = Path(tempfile.mkdtemp(prefix="bmaya-check-"))
TEXTURE_RED = (200, 60, 40)


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
    build_clips()
    cmds.select(clear=True)


def build_clips() -> None:
    for plugin in ("gameFbxExporter", "timeSliderBookmark"):
        cmds.loadPlugin(plugin, quiet=True)
    exporter = cmds.createNode("gameFbxExporter", name="clips_exporter")
    for index, name, start, end in ((0, "spin", 1, 24), (2, "wave", 12, 18), (3, "", 0, 5)):
        plug = f"{exporter}.animClips[{index}]"
        cmds.setAttr(plug + ".animClipName", name, type="string")
        cmds.setAttr(plug + ".animClipStart", start)
        cmds.setAttr(plug + ".animClipEnd", end)
    cmds.setAttr(exporter + ".animClips[2].exportAnimClip", False)
    for name, start, end in (("spin", 1, 24), ("wave", 12, 20), ("nod", 4, 2)):
        mark = cmds.createNode("timeSliderBookmark", name=f"mark_{name}")
        cmds.setAttr(mark + ".name", name, type="string")
        cmds.setAttr(mark + ".timeRangeStart", start)
        cmds.setAttr(mark + ".timeRangeStop", end)


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
    expect_error("get_object_info", "bmaya scene", name="nothing_here")
    expect_error("get_object_info", "bmaya scene", name="")
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
    expect_error("execute_maya_code", "reloadTextures", code="cmds.ogs(reset=True)")
    return "stdout, result conversion, truncation, traceback line, ogs reset refusal ok"


def check_get_viewport_screenshot() -> str:
    cmds.file(modified=False)
    materials = sorted(cmds.ls(materials=True))
    single = assert_read_only("get_viewport_screenshot", max_size=400)
    assert single["offscreen"] is True and single["frames"] == [1], single
    assert (single["width"], single["height"]) == (400, 225), single
    assert Path(single["path"]).stat().st_size > 1000, single
    assert not cmds.file(query=True, modified=True), "the screenshot marked the scene modified"
    assert sorted(cmds.ls(materials=True)) == materials, "a stand-in material was left behind"
    assert not cmds.getAttr("defaultRenderGlobals.imageFilePrefix"), "render prefix not restored"

    sheet = assert_read_only("get_viewport_screenshot", frames="1,12,24", camera="top")
    assert sheet["frames"] == [1, 12, 24] and sheet["camera"] == "top", sheet
    assert Path(sheet["path"]).is_file(), sheet
    leftovers = [p.name for p in Path(sheet["path"]).parent.glob("offscreen-*")]
    assert not leftovers, leftovers
    assert cmds.currentTime(query=True) == 1.0, "the screenshot moved the current time"
    ranged = assert_read_only("get_viewport_screenshot", frames="1:24", max_size=300)
    assert len(ranged["frames"]) == 12 and ranged["requested"] == 24, ranged
    assert ranged["frames"][0] == 1 and ranged["frames"][-1] == 24, ranged
    zero = assert_read_only("get_viewport_screenshot", frames="0:2", max_size=300)
    assert zero["frames"] == [0, 1, 2], zero
    expect_error("get_viewport_screenshot", "0:30:2", frames="0-30")
    arcs = assert_read_only("get_viewport_screenshot", frames="1:24", trail="head", max_size=300)
    assert arcs["trail"]["nodes"] == ["head"] and arcs["trail"]["range"] == [1, 24], arcs
    assert arcs["trail"]["keys"] == [1, 5, 20, 24], arcs["trail"]
    expect_error("get_viewport_screenshot", "at most 4", trail="a,b,c,d,e")
    nod = assert_read_only("get_viewport_screenshot", clip="nod", max_size=300)
    assert nod["frames"] == [2, 3, 4] and nod["clip"] == "nod", nod
    expect_error("get_viewport_screenshot", "spin [1:24]", clip="missing")
    expect_error("get_viewport_screenshot", "--frames 12:18", clip="wave")
    expect_error("get_viewport_screenshot", "not both", clip="nod", frames="1")
    expect_error("get_viewport_screenshot", "camera", camera="crate")
    expect_error("get_viewport_screenshot", "aspect", aspect="wide")
    expect_error("get_viewport_screenshot", "light", light="moody")
    return f"offscreen frame {single['width']}x{single['height']} and contact sheet ok"


def make_texture(size: int, name: str) -> str:
    from PySide6 import QtGui

    image = QtGui.QImage(size, size, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor(*TEXTURE_RED))
    path = (WORK_DIR / name).as_posix()
    assert image.save(path), path
    return path


def center_color(path: str) -> tuple[int, int, int]:
    from PySide6 import QtGui

    image = QtGui.QImage(path)
    return image.pixelColor(image.width() // 2, image.height() // 2).getRgb()[:3]


def close_to(color: tuple[int, int, int], expected: tuple[int, int, int], spread: int) -> bool:
    return all(abs(a - b) <= spread for a, b in zip(color, expected, strict=True))


def check_screenshot_looks() -> str:
    cmds.file(new=True, force=True)
    card = cmds.polyPlane(name="look_card", width=10, height=10, axis=(0, 0, 1))[0]
    cmds.move(0, 0, -500, card)
    camera = cmds.camera(orthographic=True, orthographicWidth=4)[0]
    camera = cmds.rename(camera, "look_cam")
    cmds.move(0, 0, -480, camera)
    material = cmds.shadingNode("lambert", asShader=True, name="look_mat")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="look_matSG")
    cmds.connectAttr(material + ".outColor", engine + ".surfaceShader")
    cmds.sets(card, edit=True, forceElement=engine)
    texture = cmds.shadingNode("file", asTexture=True, name="look_tex")
    cmds.connectAttr(texture + ".outColor", material + ".color")
    cmds.setAttr(texture + ".fileTextureName", make_texture(8192, "big.png"), type="string")
    cmds.setAttr("defaultResolution.width", 1024)
    cmds.setAttr("defaultResolution.height", 1024)
    cmds.file(modified=False)
    try:
        nodes = sorted(cmds.ls())
        lighting = cmds.getAttr("hardwareRenderingGlobals.lightingMode")
        clamp = cmds.getAttr("hardwareRenderingGlobals.enableTextureMaxRes")
        prefs = cmds.colorManagementPrefs
        output = (
            prefs(query=True, outputTransformEnabled=True, outputTarget="renderer"),
            prefs(query=True, outputTransformName=True, outputTarget="renderer"),
        )

        square = assert_read_only("get_viewport_screenshot", camera="look_cam", max_size=300)
        assert (square["width"], square["height"]) == (300, 300), square
        wide = assert_read_only(
            "get_viewport_screenshot", camera="look_cam", max_size=320, aspect="16:9"
        )
        assert (wide["width"], wide["height"]) == (320, 180), wide

        unlit = assert_read_only("get_viewport_screenshot", camera="look_cam", light="unlit")
        assert unlit["light"] == "unlit", unlit
        color = center_color(unlit["path"])
        assert close_to(color, TEXTURE_RED, 6), f"unlit 8192 texture drawn as {color}"

        expect_error("get_viewport_screenshot", "no lights", camera="look_cam", light="scene")
        sun = cmds.directionalLight()
        cmds.file(modified=False)
        try:
            for light in ("soft", "scene", ""):
                shot = assert_read_only("get_viewport_screenshot", camera="look_cam", light=light)
                color = center_color(shot["path"])
                assert color[0] > 2 * color[1], f"{light or 'default'} light lost texture: {color}"
        finally:
            cmds.delete(cmds.listRelatives(sun, parent=True))
            cmds.file(modified=False)

        assert sorted(cmds.ls()) == nodes, "the screenshot left nodes behind"
        assert not cmds.file(query=True, modified=True), "the screenshot marked the scene modified"
        assert cmds.getAttr("hardwareRenderingGlobals.lightingMode") == lighting
        assert cmds.getAttr("hardwareRenderingGlobals.enableTextureMaxRes") == clamp
        assert output == (
            prefs(query=True, outputTransformEnabled=True, outputTarget="renderer"),
            prefs(query=True, outputTransformName=True, outputTarget="renderer"),
        ), "renderer output transform not restored"
    finally:
        cmds.delete(card, camera, material, engine, texture)
        cmds.setAttr("defaultResolution.width", 960)
        cmds.setAttr("defaultResolution.height", 540)
    return "render aspect, --aspect, unlit colors of an 8192 texture, soft and scene light ok"


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
    expect_error("audit_mesh", "bmaya scene", names="missing_mesh")
    return f"broken mesh flagged ({len(broken['issues'])} issues), clean mesh clean"


def check_get_animation_info() -> str:
    overview = assert_read_only("get_animation_info")
    assert overview["curves"] == 2 and overview["key_range"] == [1, 24], overview
    assert overview["skeletons"]["items"] == [{"name": "root", "joints": 3}], overview
    skin = overview["skins"]["items"][0]
    assert skin["influences"] == 3 and skin["mesh"] == "body", skin
    assert overview["blend_shapes"]["items"][0]["targets"] == 1, overview
    clips = overview["clips"]["items"]
    spin = {"name": "spin", "range": [1, 24], "exporter": "clips_exporter", "bookmark": "mark_spin"}
    assert spin in clips, clips
    wave = {"name": "wave", "range": [12, 18], "exporter": "clips_exporter", "export": False}
    assert wave in clips, clips
    assert {"name": "nod", "range": [2, 4], "bookmark": "mark_nod"} in clips, clips
    assert overview["clips"]["total"] == 4, clips

    root = call("get_animation_info", name="root")
    curve = root["curves"]["items"][0]
    assert curve["attr"] == "rotateZ" and curve["keys"] == 2 and curve["range"] == [1, 24], curve
    assert curve["tangents"], curve
    assert root["hierarchy"]["joints"] == 3 and root["hierarchy"]["animated_joints"] == 2, root
    assert root["hierarchy"]["skins"] == ["bodySkin"], root
    spine = call("get_animation_info", name="spine")["curves"]["items"][0]
    assert spine["infinity"] == ["constant", "cycle"], spine
    return "overview, clips, curves, skeleton, skin, blend shape ok"


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

    from_zero = call("export_fbx", path=str(target), animation=True, start="0", end="5")
    assert from_zero["options"]["bake"] == [0, 5], from_zero["options"]
    old_cli = call("export_fbx", path=str(target), animation=True, start=0, end=0)
    assert old_cli["options"]["bake"] == [1, 24], old_cli["options"]
    clip = call("export_fbx", path=str(target), clip="nod")
    assert clip["options"]["bake"] == [2, 4] and clip["options"]["clip"] == "nod", clip
    assert clip["options"]["animation"] is True, clip["options"]
    expect_error("export_fbx", "not both", path=str(target), clip="nod", start="1")

    cmds.select(clear=True)
    expect_error("export_fbx", "nothing is selected", path=str(target), selection_only=True)
    expect_error("export_fbx", ".fbx", path=str(WORK_DIR / "x.obj"))
    expect_error("export_fbx", "unity", path=str(target), preset="cryengine")
    return f"unreal+anim {result['size']} bytes, selection-only and errors ok"


def build_walker() -> None:
    cmds.file(new=True, force=True)
    cmds.loadPlugin("timeSliderBookmark", quiet=True)
    walker = cmds.group(empty=True, name="walker")
    for name, x in (("foot_l", 10), ("foot_r", -10)):
        cmds.parent(cmds.spaceLocator(name=name)[0], walker)
        cmds.setAttr(name + ".translateX", x)
    cmds.parent(cmds.spaceLocator(name="hand")[0], walker)
    cmds.setAttr("hand.translateY", 100)

    for at, x in ((0, 0), (11, 11), (12, 21), (24, 33)):
        cmds.setKeyframe("hand", attribute="translateX", time=at, value=x)
    cmds.setKeyframe("hand", attribute="rotateY", time=0, value=0)
    cmds.setKeyframe("hand", attribute="rotateY", time=24, value=350)
    cmds.keyTangent("hand", edit=True, inTangentType="linear", outTangentType="linear")

    for at, x in ((0, -10), (5, -10), (10, -5), (24, -5)):
        cmds.setKeyframe("foot_r", attribute="translateX", time=at, value=x)
    cmds.keyTangent("foot_r", edit=True, inTangentType="linear", outTangentType="linear")
    cmds.setKeyframe("foot_r", attribute="translateZ", time=12.5, value=0)
    cmds.setKeyframe("foot_r", attribute="translateZ", time=40, value=0)
    for at, y in ((0, 0), (19, 0), (20, -2), (21, 0), (30, 0)):
        cmds.setKeyframe("foot_l", attribute="translateY", time=at, value=y)
    cmds.keyTangent("foot_l", edit=True, inTangentType="linear", outTangentType="linear")

    mark = cmds.createNode("timeSliderBookmark", name="mark_walk")
    cmds.setAttr(mark + ".name", "walk", type="string")
    cmds.setAttr(mark + ".timeRangeStop", 24)
    cmds.playbackOptions(minTime=0, maxTime=24, animationStartTime=0, animationEndTime=24)
    cmds.currentTime(7)
    cmds.select(clear=True)


def check_get_motion() -> str:
    build_walker()
    cmds.file(modified=False)
    report = assert_read_only(
        "get_motion", names="hand,foot_*", clip="walk", loop=True, contacts="foot_l,foot_r"
    )
    assert cmds.currentTime(query=True) == 7, "motion moved the current time"
    assert not cmds.file(query=True, modified=True), "motion marked the scene modified"
    assert report["range"] == [0, 24] and report["clip"] == "walk" and report["step"] == 1
    assert report["size"] == 100 and report["limits"]["pop"] == 3, report["limits"]
    issues = report["issues"]["items"]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        by_type.setdefault(issue["type"], []).append(issue)
    expected = {
        "pop",
        "loop_pose",
        "foot_slide",
        "below_ground",
        "euler_jump",
        "clip_edge",
        "subframe_keys",
    }
    assert expected <= set(by_type), sorted(by_type)
    assert "stray_keys" not in by_type, "keys after the checked clip were reported"
    pop = by_type["pop"][0]
    assert (pop["node"], pop["frames"], pop["move"]) == ("hand", [11, 12], 10), pop
    slide = by_type["foot_slide"][0]
    assert slide["node"] == "foot_r" and slide["frames"] == [5, 10], slide
    assert by_type["below_ground"][0]["depth"] == 2, by_type["below_ground"]
    assert by_type["clip_edge"][0] == {
        "type": "clip_edge",
        "node": "foot_l",
        "edge": "end",
        "frame": 24,
        "attrs": 1,
    }, by_type["clip_edge"]
    assert by_type["euler_jump"][0]["delta"] == 350, by_type["euler_jump"]
    assert report["contacts"]["foot_l"]["lowest"] == -2, report["contacts"]
    assert report["loop"]["root"] == "walker", report["loop"]
    assert report["nodes"]["source"] == "names" and report["nodes"]["total"] == 3

    wider = call("get_motion", names="foot_*", frames="0:40")
    strays = [issue for issue in wider["issues"]["items"] if issue["type"] == "stray_keys"]
    assert {issue["first"] for issue in strays} == {30, 40}, wider["issues"]
    assert wider.get("clips_checked") is None and "loop" not in wider, wider

    world = call("get_motion", names="hand", frames="0:24", values=True)["values"]
    assert world["frames"] == "0:24" and world["nodes"]["hand"]["x"][12] == 21, world
    assert world["nodes"]["hand"]["y"][0] == 100, world
    attrs = call("get_motion", names="hand", frames="0:24:2", attrs="tx,rotate")["values"]
    hand = attrs["nodes"]["hand"]
    assert attrs["frames"] == "0:24:2", attrs
    assert sorted(hand) == ["rotateX", "rotateY", "rotateZ", "translateX"], hand
    assert hand["translateX"][6] == 21 and hand["rotateY"][-1] == 350, hand
    expect_error("get_motion", "keyable attributes", names="hand", attrs="ik")

    cmds.select("hand")
    picked = call("get_motion", frames="0:24:4")
    assert picked["nodes"]["source"] == "selection" and picked["step"] == 4, picked
    assert "note" in picked and "pop" not in picked["issues"]["counts"], picked
    cmds.select(clear=True)
    whole = call("get_motion")
    assert whole["nodes"]["source"] == "scene" and whole["range"] == [0, 24], whole["nodes"]
    expect_error("get_motion", "bmaya scene", names="nobody")
    expect_error("get_motion", "--clip", names="hand", frames="3")
    return f"{len(issues)} issues of {len(by_type)} kinds found, state kept"


def build_arm_rig() -> None:
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    shoulder = cmds.joint(name="shoulder", position=(0, 0, 0))
    cmds.joint(name="elbow", position=(10, 0, -1))
    wrist = cmds.joint(name="wrist", position=(20, 0, 0))
    handle = cmds.ikHandle(startJoint=shoulder, endEffector=wrist, name="arm_ik")[0]
    controls = cmds.group(empty=True, name="rig_ctrls")
    hand = cmds.circle(name="hand_ctrl")[0]
    pole = cmds.circle(name="elbow_pole")[0]
    fk = cmds.circle(name="fk_ctrl")[0]
    cmds.parent(hand, pole, fk, controls)
    cmds.move(20, 0, 0, hand)
    cmds.move(10, 0, -10, pole)
    cmds.pointConstraint(hand, handle)
    cmds.poleVectorConstraint(pole, handle)
    cmds.orientConstraint(fk, wrist)
    cmds.setAttr(fk + ".rotateOrder", 1)
    for axis in "XYZ":
        cmds.setAttr(f"{hand}.scale{axis}", lock=True, keyable=False)
    cmds.transformLimits(hand, rotationX=(-80, 80), enableRotationX=(True, True))
    cmds.addAttr(hand, longName="ik", attributeType="double", min=0, max=1, dv=1, keyable=True)
    flip = cmds.createNode("reverse", name="ik_to_fk")
    cmds.connectAttr(hand + ".ik", handle + ".ikBlend")
    cmds.connectAttr(hand + ".ik", flip + ".inputX")
    cmds.connectAttr(flip + ".outputX", fk + ".visibility")
    cmds.setKeyframe(hand, attribute="translateY", time=0, value=0)
    cmds.setKeyframe(hand, attribute="translateY", time=10, value=4)
    cmds.currentTime(10)
    cmds.character(hand, name="arm_set")
    cmds.select(clear=True)


def check_get_rig_info() -> str:
    build_arm_rig()
    rig = assert_read_only("get_rig_info", root="rig_ctrls")
    assert rig["root"] == "rig_ctrls" and rig["kind"] == "controls", rig
    controls = {entry["name"]: entry for entry in rig["controls"]["items"]}
    assert sorted(controls) == ["elbow_pole", "fk_ctrl", "hand_ctrl"], sorted(controls)
    hand = controls["hand_ctrl"]
    assert "translate" in hand["keyable"] and "ik" in hand["keyable"], hand
    assert "scale" not in hand["keyable"], hand
    assert hand["values"]["translate"] == [20, 4, 0], hand
    assert hand["limits"]["rotateX"] == [-80, 80] and hand["limits"]["ik"] == [0, 1], hand
    assert hand["keys"] == 2 and hand["range"] == [0, 10], hand
    assert controls["fk_ctrl"]["ro"] == "yzx", controls["fk_ctrl"]

    ik = rig["ik"]["items"][0]
    assert (ik["name"], ik["solver"], ik["blend"]) == ("arm_ik", "ikRPsolver", 1), ik
    assert ik["pole"] == "elbow_pole" and ik["driven_by"] == ["hand_ctrl"], ik
    constraints = rig["constraints"]["items"]
    assert {"type": "orientConstraint", "node": "wrist", "targets": ["fk_ctrl"]} in constraints
    switch = rig["switches"]["items"][0]
    assert switch["attr"] == "hand_ctrl.ik" and switch["range"] == [0, 1], switch
    assert "arm_ik.ikBlend" in switch["drives"], switch
    assert "fk_ctrl.visibility" in switch["drives"], switch
    assert rig["character_sets"]["items"][0]["name"] == "arm_set", rig["character_sets"]

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    cmds.joint(name="hips", position=(0, 90, 0))
    cmds.joint(name="knee", position=(0, 50, 0))
    bones = call("get_rig_info")
    assert bones["kind"] == "joints" and bones["controls"]["total"] == 2, bones
    expect_error("get_rig_info", "bmaya scene", root="nobody")
    return "controls, driven and locked attrs, limits, IK, constraints, switches, joints"


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


def check_shared_maya() -> str:
    from bmaya_bridge.journal import Journal
    from bmaya_bridge.server import BridgeServer

    journal = Journal(WORK_DIR / "logs-shared", crash_log=False)
    bridge = BridgeServer(journal, schedule=lambda callback: callback())

    def send(client: str, command: str, **params: object) -> dict[str, Any]:
        request = {"id": 1, "client": client, "command": command, "params": params}
        return bridge.handle(request)

    def make(client: str, name: str) -> None:
        reply = send(client, "execute_maya_code", code=f"cmds.polyCube(name='{name}')")
        assert reply["ok"], reply

    try:
        cmds.file(new=True, force=True)
        cmds.flushUndo()
        make("claude-1", "mine_a")
        make("claude-1", "mine_b")
        make("codex-2", "theirs")

        mel.eval("select -r mine_a")
        blocked = send("claude-1", "undo", steps=3)
        assert cmds.ls(selection=True) == ["mine_a"], "a refused undo lost the selection"
        assert not blocked["ok"] and "codex-2" in blocked["error"]["message"], blocked
        assert cmds.objExists("theirs"), "undo reverted another client's step"

        partial = send("codex-2", "undo", steps=3)
        assert partial["ok"], partial
        assert partial["result"]["undone"] == 1, partial
        assert partial["result"]["blocked_by"] == "claude-1", partial
        assert "queue_empty" not in partial["result"], partial
        assert cmds.objExists("mine_b") and not cmds.objExists("theirs"), partial

        cmds.polySphere(name="by_hand")
        manual = send("claude-1", "undo")
        assert not manual["ok"] and "Maya UI" in manual["error"]["message"], manual
        assert cmds.objExists("by_hand"), "undo reverted a change made in the UI"
        forced = send("claude-1", "undo", force=True)
        assert forced["ok"] and forced["result"]["undone"] == 1, forced
        assert forced["result"]["ui_entries_undone"] == 1, forced
        assert not cmds.objExists("by_hand") and not cmds.objExists("mine_b"), forced
        make("claude-1", "mine_b")
        make("codex-2", "theirs")
        never = send("claude-1", "undo", force=True)
        assert not never["ok"] and "codex-2" in never["error"]["message"], never
        assert send("codex-2", "undo")["ok"]

        mel.eval("select -r mine_a")
        mel.eval("select -cl")
        own = send("claude-1", "undo", steps=5)
        assert own["ok"] and own["result"]["undone"] == 2, own
        assert own["result"].get("queue_empty") is True, own
        assert not cmds.objExists("mine_a"), own

        cmds.file(modified=False)
        refused = send("claude-1", "scene_file", action="new")
        assert not refused["ok"] and "codex-2" in refused["error"]["message"], refused
        assert "force" in refused["error"]["hint"], refused
        scene = str(WORK_DIR / "scenes" / "check.ma")
        refused_open = send("claude-1", "scene_file", action="open", path=scene)
        assert not refused_open["ok"] and "codex-2" in refused_open["error"]["message"]
        forced = send("claude-1", "scene_file", action="new", force=True)
        assert forced["ok"], forced

        alone = BridgeServer(journal, schedule=lambda callback: callback())
        request = {"id": 1, "client": "claude-1", "command": "scene_file"}
        assert alone.handle({**request, "params": {"action": "new"}})["ok"]
    finally:
        journal.close()
    return "undo stops at other clients and UI edits, scene_file guards a shared Maya"


def check_stop() -> str:
    from bmaya_bridge import lifecycle
    from bmaya_bridge.journal import Journal
    from bmaya_bridge.server import BridgeServer

    journal = Journal(WORK_DIR / "logs-stop", crash_log=False)
    bridge = BridgeServer(journal, schedule=lambda callback: callback())
    asked: list[bool] = []

    def send(client: str, command: str, **params: object) -> dict[str, Any]:
        request = {"id": 1, "client": client, "command": command, "params": params}
        return bridge.handle(request)

    try:
        cmds.file(new=True, force=True)
        with_window = send("claude-1", "stop")
        assert not with_window["ok"] and "window" in with_window["error"]["message"], with_window

        lifecycle.set_exit(lambda: asked.append(True))
        made = send("claude-1", "execute_maya_code", code="cmds.polyCube(name='unsaved')")
        assert made["ok"], made
        unsaved = send("claude-1", "stop")
        assert not unsaved["ok"] and "unsaved" in unsaved["error"]["message"], unsaved
        assert "bmaya file save" in unsaved["error"]["hint"], unsaved

        cmds.file(modified=False)
        stopped = send("claude-1", "stop")
        assert stopped["ok"] and stopped["result"]["stopped"] is True, stopped
        assert stopped["result"]["scene"] == "untitled", stopped
        assert asked == [True]

        shared = send("codex-2", "stop")
        assert not shared["ok"] and "claude-1" in shared["error"]["message"], shared
        assert asked == [True], "a refused stop still ended Maya"
        cmds.polyCube(name="unsaved_again")
        assert send("codex-2", "stop", force=True)["ok"]
        assert asked == [True, True]
    finally:
        lifecycle.set_exit(None)
        journal.close()
    return "stop refuses a Maya with a window, unsaved work and a shared Maya"


def build_reach() -> tuple[str, str]:
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    start = cmds.joint(name="reach_start", position=(0, 0, 0))
    cmds.joint(name="reach_mid", position=(0, 10, 2))
    end = cmds.joint(name="reach_end", position=(0, 20, 0))
    handle = cmds.ikHandle(startJoint=start, endEffector=end, solver="ikRPsolver")[0]
    ctrl = cmds.circle(name="reach_ctrl")[0]
    cmds.move(0, 20, 0, ctrl)
    cmds.pointConstraint(ctrl, handle)
    cmds.setKeyframe(ctrl, attribute="translateY", time=1, value=20)
    cmds.setKeyframe(ctrl, attribute="translateY", time=24, value=12)
    rising = cmds.spaceLocator(name="rising")[0]
    cmds.expression(string=f"{rising}.translateY = frame * 0.5")
    return end, rising


def check_sampling() -> str:
    import time

    from bmaya_bridge.handlers import _sampling
    from bmaya_bridge.handlers._common import scene_kept

    end, rising = build_reach()
    cmds.currentTime(5)
    cmds.select("reach_ctrl")
    cmds.file(modified=False)
    with scene_kept():
        poses = _sampling.sample_poses([end, rising], [1, 12, 24], _sampling.deadline_after())
    tip = [round(pose.position[1], 3) for pose in poses[0]]
    assert tip[0] == 20 and tip[2] == 12, f"IK end did not follow the control: {tip}"
    heights = [round(pose.position[1], 3) for pose in poses[1]]
    assert heights == [0.5, 6, 12], f"expression not evaluated per frame: {heights}"
    assert cmds.currentTime(query=True) == 5, "sampling moved the current time"
    assert cmds.ls(selection=True) == ["reach_ctrl"], "sampling changed the selection"
    assert not cmds.file(query=True, modified=True), "sampling marked the scene modified"
    try:
        _sampling.sample_poses([rising], [1, 2], time.perf_counter() - 1)
    except BridgeError as exc:
        assert "Sampling stopped at frame 1" in exc.message and exc.hint, exc.message
    else:
        raise AssertionError("an expired deadline did not stop sampling")
    assert _sampling.linear_scale() == 1.0 and _sampling.up_index() == 1
    return "IK end, expression, restored time and selection, deadline ok"


def bright_centroid(path: str) -> tuple[float, float]:
    from PySide6 import QtGui

    image = QtGui.QImage(path)
    xs = ys = count = 0.0
    for y in range(image.height()):
        for x in range(image.width()):
            if QtGui.qGray(image.pixel(x, y)) > 200:
                xs += x + 0.5
                ys += y + 0.5
                count += 1
    assert count, f"no bright pixels in {path}"
    return xs / count, ys / count


def build_marker(point: tuple[float, float, float]) -> str:
    cmds.file(new=True, force=True)
    ball = cmds.polySphere(name="marker", radius=0.15)[0]
    cmds.move(*point, ball)
    shader = cmds.shadingNode("surfaceShader", asShader=True)
    cmds.setAttr(shader + ".outColor", 1, 1, 1, type="double3")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
    cmds.connectAttr(shader + ".outColor", engine + ".surfaceShader")
    cmds.sets(ball, edit=True, forceElement=engine)
    camera = cmds.camera(focalLength=35, name="aim_cam")[0]
    cmds.move(9, 5, 14, camera)
    cmds.viewLookAt(camera, position=(0, 0, 0))
    return camera


def check_projection() -> str:
    from bmaya_bridge.handlers import _sampling
    from bmaya_bridge.projection import project

    point = (3.0, 2.0, -1.0)
    camera = build_marker(point)
    shape = cmds.listRelatives(camera, shapes=True)[0]
    ortho = cmds.camera(orthographic=True, orthographicWidth=12, name="flat_cam")[0]
    cmds.move(0, 0, 20, ortho)
    cases = [
        (camera, 1, "16:9", 1.0),
        (camera, 0, "1:1", 1.3),
        (camera, 3, "9:16", 1.0),
        (ortho, 1, "16:9", 1.0),
    ]
    worst = 0.0
    for cam, fit, aspect, overscan in cases:
        cam_shape = cmds.listRelatives(cam, shapes=True)[0]
        cmds.setAttr(cam_shape + ".filmFit", fit)
        cmds.setAttr(cam_shape + ".overscan", overscan)
        shot = call(
            "get_viewport_screenshot", camera=cam, aspect=aspect, light="unlit", max_size=400
        )
        seen = bright_centroid(shot["path"])
        view = _sampling.camera_view(cam, shot["width"], shot["height"])
        pixel = project(point, view, shot["width"], shot["height"])
        assert pixel is not None, (cam, fit, aspect)
        miss = max(abs(seen[0] - pixel[0]), abs(seen[1] - pixel[1]))
        assert miss < 1.5, f"{cam} fit {fit} {aspect}: drawn at {seen}, projected at {pixel}"
        worst = max(worst, miss)
    cmds.setAttr(shape + ".overscan", 1.0)
    cmds.setAttr(shape + ".filmFit", 1)

    mover = cmds.spaceLocator(name="mover")[0]
    cmds.setKeyframe(mover, attribute="translateX", time=1, value=-2)
    cmds.setKeyframe(mover, attribute="translateX", time=10, value=2)
    cmds.playbackOptions(minTime=1, maxTime=10)
    shot = call("get_viewport_screenshot", camera=camera, frames="5", trail="mover", light="unlit")
    assert shot["trail"]["keys"] == [1, 10] and shot["trail"]["outside"] == 0, shot["trail"]
    view = _sampling.camera_view(camera, shot["width"], shot["height"])
    start = project((-2.0, 0.0, 0.0), view, shot["width"], shot["height"])
    from PySide6 import QtGui

    color = QtGui.QImage(shot["path"]).pixelColor(int(start[0]), int(start[1])).getRgb()[:3]
    assert close_to(color, (255, 190, 0), 40), f"no key dot at {start}: {color}"
    return f"sphere lands within {worst:.2f} px of its projection; trail key dot in place"


EXTRA_CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("projection", check_projection),
    ("sampling", check_sampling),
    ("bridge dispatch", check_bridge_dispatch),
    ("shared maya", check_shared_maya),
    ("screenshot looks", check_screenshot_looks),
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
    ("get_motion", check_get_motion),
    ("get_rig_info", check_get_rig_info),
    ("undo", check_undo),
    ("stop", check_stop),
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
