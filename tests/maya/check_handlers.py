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


def check_render_frames() -> str:
    from bmaya_bridge.handlers import viewport

    folder = WORK_DIR / "frames"
    cmds.currentTime(3)
    cmds.file(modified=False)
    first = assert_read_only(
        "render_frames", frames="1:5", count=3, folder=str(folder), max_size=200
    )
    assert (first["rendered"], first["total"], first["last_frame"]) == (3, 5, 3), first
    assert first["range"] == [1, 5] and first["step"] == 1 and first["fps"] == 24, first
    assert first["ms_per_frame"] >= 0 and first["width"] == 200, first
    rest = call("render_frames", frames="1:5", offset=3, count=3, folder=str(folder), max_size=200)
    assert (rest["rendered"], rest["last_frame"]) == (2, 5), rest
    names = sorted(path.name for path in folder.glob("frame_*.png"))
    assert names == [f"frame_{index:05d}.png" for index in range(5)], names
    assert cmds.currentTime(query=True) == 3, "render_frames moved the current time"
    assert not cmds.file(query=True, modified=True), "render_frames marked the scene modified"

    saved = viewport.SEQUENCE_SECONDS
    viewport.SEQUENCE_SECONDS = 0.0
    try:
        hurried = call("render_frames", frames="1:5", count=5, folder=str(WORK_DIR / "rush"))
    finally:
        viewport.SEQUENCE_SECONDS = saved
    assert hurried["rendered"] == 1, hurried

    nod = call("render_frames", clip="nod", folder=str(WORK_DIR / "nod"), max_size=200)
    assert (nod["total"], nod["clip"], nod["range"]) == (3, "nod", [2, 4]), nod
    expect_error("render_frames", "past the last", frames="1:5", offset=9, folder=str(folder))
    expect_error("render_frames", "absolute folder", frames="1:5", folder="relative")
    return "chunks by offset, frame numbering, deadline, clip ok"


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
    numbers = check_audit_numbers()
    shapes = check_audit_shapes()
    return (
        f"broken mesh flagged ({len(broken['issues'])} issues), clean mesh clean; {numbers}; "
        f"{shapes}"
    )


def mirrored_box(name: str) -> str:
    box = cmds.polyCube(name=name, constructionHistory=False)[0]
    cmds.setAttr(box + ".scaleX", -1)
    cmds.makeIdentity(box, apply=True, scale=True)
    return box


def check_audit_shapes() -> str:
    made = []
    try:
        stray = cmds.polyCube(name="audit_stray", constructionHistory=False)[0]
        made.append(stray)
        for index in range(2):
            old = cmds.polySphere(constructionHistory=False)[0]
            shape = cmds.parent(
                cmds.listRelatives(old, shapes=True)[0], stray, shape=True, relative=True
            )
            cmds.setAttr(shape[0] + ".intermediateObject", True)
            cmds.rename(shape[0], f"audit_old{index}")
            cmds.delete(old)
        cmds.polyColorSet(stray, create=True, colorSet="dirt")
        entry = assert_read_only("audit_mesh", names=stray)["meshes"][0]
        assert (
            "2 unused intermediate shapes, left from earlier versions: audit_old0, audit_old1"
            in entry["issues"]
        ), entry
        assert entry["color_sets"] == ["dirt"], entry

        flipped = mirrored_box("audit_flagged")
        made.append(flipped)
        issues = call("audit_mesh", names=flipped)["meshes"][0]["issues"]
        assert any("opposite flag over inside-out faces" in issue for issue in issues), issues
        assert "normals point inward" not in issues, issues

        shared = cmds.polyCube(name="audit_shared", constructionHistory=False)[0]
        copy = cmds.instance(shared, name="audit_shared_copy")[0]
        made += [shared, copy]
        issues = call("audit_mesh", names=shared)["meshes"][0]["issues"]
        assert "audit_sharedShape is instanced under 2 transforms" in issues, issues

        from bmaya_bridge.handlers._shapes import preflight

        blocking, rest = preflight(["|audit_flagged", "|audit_stray"])
        assert len(blocking) == 1 and blocking[0].startswith("audit_flagged: "), blocking
        assert len(rest) == 1 and rest[0].startswith("audit_stray: 2 unused"), rest
    finally:
        cmds.delete([node for node in made if cmds.objExists(node)])
    return "stray, opposite and instanced shapes flagged"


def check_audit_numbers() -> str:
    report = call("audit_mesh", names="clean_box")
    clean = report["meshes"][0]
    assert clean["faces"] == {"tris": 0, "quads": 6, "ngons": 0}, clean
    assert clean["size"] == [1, 1, 1], clean

    crate = call("get_object_info", name="crate")
    assert crate["size"] == [1, 1, 1] and crate["pivot"] == [2, 0, 0], crate

    made = []
    try:
        inward = cmds.polyCube(name="audit_inward", constructionHistory=False)[0]
        made.append(inward)
        cmds.polyNormal(inward, normalMode=0, userNormalMode=0, constructionHistory=False)
        issues = call("audit_mesh", names=inward)["meshes"][0]["issues"]
        assert "normals point inward" in issues, issues

        turned = cmds.polyCube(name="audit_turned", constructionHistory=False)[0]
        made.append(turned)
        cmds.polyNormal(turned + ".f[0]", normalMode=0, userNormalMode=0, constructionHistory=False)
        issues = call("audit_mesh", names=turned)["meshes"][0]["issues"]
        assert "1 faces with flipped winding" in issues, issues
        assert "normals point inward" not in issues, issues

        pair = [cmds.polyCube(constructionHistory=False)[0] for _ in range(2)]
        stacked = cmds.polyUnite(pair, constructionHistory=False, name="audit_stacked")[0]
        made.append(stacked)
        entry = assert_read_only("audit_mesh", names=stacked)["meshes"][0]
        assert any("overlapping UV faces" in issue for issue in entry["issues"]), entry

        mirrored = cmds.polyCube(name="audit_mirrored", constructionHistory=False)[0]
        made.append(mirrored)
        even = assert_read_only("audit_mesh", names=mirrored, symmetry="x")["meshes"][0]
        assert even["symmetry"] == {"axis": "x", "at": 0, "matched": 8, "unmatched": 0}, even
        cmds.move(0, 0.3, 0, mirrored + ".vtx[0]", relative=True)
        uneven = call("audit_mesh", names=mirrored, symmetry="x")["meshes"][0]
        assert uneven["symmetry"]["unmatched"] == 2, uneven
        assert len(uneven["symmetry"]["sample"]) == 2, uneven
        assert "2 verts without a mirror twin across x" in uneven["issues"], uneven
        expect_error("audit_mesh", "--symmetry", names=mirrored, symmetry="w")
    finally:
        cmds.delete([node for node in made if cmds.objExists(node)])
    return "face kinds, size, pivot, inward and flipped normals, UV overlap, symmetry ok"


def pixel(path: str, x: int, y: int) -> tuple[int, int, int]:
    from PySide6 import QtGui

    return QtGui.QImage(path).pixelColor(x, y).getRgb()[:3]


def check_get_model_views() -> str:
    outer = cmds.polyCube(name="views_outer", width=2, height=2, depth=2)[0]
    inner = cmds.polyCube(name="views_inner", width=0.5, height=0.5, depth=0.5)[0]
    dense = cmds.polySphere(name="views_dense", subdivisionsX=200, subdivisionsY=200)[0]
    cmds.select(clear=True)
    cmds.file(modified=False)
    names = f"{outer},{inner}"
    try:
        nodes = sorted(cmds.ls())
        sheet = assert_read_only("get_model_views", names=names, max_size=800)
        assert (sheet["width"], sheet["height"]) == (800, 800), sheet
        assert sheet["views"] == ["front", "right", "top", "persp"], sheet
        assert sheet["size"] == [2, 2, 2] and sheet["wire"] is True, sheet
        assert sheet["parts"][0] == {"name": outer, "color": "#1f77b4"}, sheet
        assert sheet["shown"] == 2 and sheet["total"] == 2, sheet
        red, _, blue = pixel(sheet["path"], 170, 230)
        assert blue > red + 60, f"the front tile does not show the outer part's color: {red} {blue}"

        shape = assert_read_only(
            "get_model_views", names=names, style="silhouette", views="front", max_size=400
        )
        assert pixel(shape["path"], 200, 200) == (20, 20, 20), shape
        assert "parts" not in shape and shape["wire"] is False, shape

        cut = assert_read_only("get_model_views", names=names, section="x", max_size=900)
        assert cut["views"][-1] == "section x=0" and cut["section"] == {"axis": "x", "at": 0}, cut
        assert (cut["width"], cut["height"]) == (900, 600), cut
        red, _, blue = pixel(cut["path"], 450, 450)
        assert red > blue, f"the cut does not show the inner part: {red} {blue}"

        whole = assert_read_only("get_model_views", names=names, views="all", style="xray")
        assert len(whole["views"]) == 8 and whole["style"] == "xray", whole

        heavy = assert_read_only("get_model_views", names=dense, max_size=800)
        assert heavy["wire"] is False and "edges left out" in heavy["note"], heavy

        shaded = assert_read_only(
            "get_model_views", names=names, style="shaded", light="soft", views="front,persp"
        )
        assert shaded["offscreen"] is True and shaded["light"] == "soft", shaded
        assert (shaded["width"], shaded["height"]) == (1600, 800), shaded
        assert max(pixel(shaded["path"], 400, 400)) > 100, "the shaded tile is empty"

        tower = cmds.polyCube(name="views_tower")[0]
        cmds.scale(1, 1, 4, tower)
        cmds.upAxis(axis="z")
        try:
            turned = call(
                "get_model_views", names=tower, views="front,top", section="y", max_size=900
            )
            assert turned["up"] == "z" and turned["views"][-1] == "section y=0", turned
            background = (236, 236, 236)
            assert pixel(turned["path"], 150, 45) != background, "front does not look along Y"
            assert pixel(turned["path"], 45, 150) == background, "front shows the tower lying"
            assert pixel(turned["path"], 345, 150) != background, "top does not look down Z"
            assert pixel(turned["path"], 750, 45) != background, "the section lost the tower"
            assert pixel(turned["path"], 645, 150) == background, "the section is not upright"
        finally:
            cmds.upAxis(axis="y")
            cmds.delete(tower)
            cmds.select(clear=True)
            cmds.file(modified=False)

        assert sorted(cmds.ls()) == nodes, "views left nodes behind"
        assert not cmds.file(query=True, modified=True), "views marked the scene modified"

        check_weight_views()

        expect_error("get_model_views", "--views", names=names, views="front,under")
        expect_error("get_model_views", "--style", names=names, style="clay")
        expect_error("get_model_views", "--section", names=names, section="w")
        expect_error("get_model_views", "inside that span", names=names, section="x=5")
        expect_error("get_model_views", "--style shaded", names=names, light="soft")
        expect_error("get_model_views", "bmaya scene", names="missing_mesh")
    finally:
        cmds.delete(outer, inner, dense)
    return "parts, silhouette, section, xray, dense mesh without edges, shaded ok"


def check_weight_views() -> str:
    weights = assert_read_only(
        "get_model_views",
        names="body",
        style="weights",
        joint="head",
        views="front",
        no_wire=True,
        max_size=400,
    )
    assert weights["joint"] == "head" and "parts" not in weights, weights
    top = pixel(weights["path"], 200, 55)
    bottom = pixel(weights["path"], 200, 340)
    assert top[1] > bottom[1] + 50, f"the top is not warmer than the bottom: {top} {bottom}"
    assert bottom[2] > bottom[0] + 30, f"the bottom shows head weights: {bottom}"

    mixed = assert_read_only(
        "get_model_views", names="body,clean_box", style="influence", max_size=400
    )
    names = [item["name"] for item in mixed["influences"]["items"]]
    assert sorted(names) == ["head", "root", "spine"], mixed
    assert mixed["influences"]["total"] == 3, mixed

    expect_error("get_model_views", "--joint spine", names="body", style="weights")
    expect_error("get_model_views", "not an influence", names="body", style="weights", joint="x")
    expect_error("get_model_views", "--style weights", names="body", joint="head")
    expect_error("get_model_views", "None of these", names="clean_box", style="influence")
    return "weights and influence ok"


def check_diff_mesh() -> str:
    box = cmds.polyCube(name="diff_box", constructionHistory=False)[0]
    cmds.select(clear=True)
    try:
        expect_error("diff_mesh", "--mark", name=box)
        marked = assert_read_only("diff_mesh", name=box, mark=True)
        assert marked["marked"]["verts"] == 8 and marked["marked"]["volume"] == 1, marked
        same = assert_read_only("diff_mesh", name=box)
        assert same["topology_changed"] is False and same["moved"] == 0, same

        cmds.move(0, 1, 0, box + ".vtx[2]", relative=True)
        moved = call("diff_mesh", name=box)
        assert moved["against"] == "mark" and moved["moved"] == 1, moved
        assert moved["max_move"] == 1 and moved["after"]["size"] == [1, 2, 1], moved
        assert moved["moved_box"][1] == moved["moved_box"][4] == 1.5, moved

        copy = cmds.duplicate(box, name="diff_copy")[0]
        cmds.polySmooth(box, constructionHistory=False)
        changed = call("diff_mesh", name=box, against=copy)
        assert changed["topology_changed"] is True and changed["against"] == copy, changed
        assert changed["before"]["verts"] == 8 and changed["after"]["verts"] == 26, changed
        cmds.delete(copy)

        expect_error("diff_mesh", "one mesh at a time", name="props")
        expect_error("diff_mesh", "--against", name=box, mark=True, against=box)
    finally:
        cmds.delete(box)
    return "mark, moved vertices and their box, topology change, --against ok"


def tris_of(node: str) -> int:
    return cmds.polyEvaluate(node, triangle=True)


def check_reduce_mesh() -> str:
    ball = cmds.polySphere(name="reduce_ball", constructionHistory=False)[0]
    cmds.select("crate", replace=True)
    try:
        cmds.flushUndo()
        lean = call("reduce_mesh", name=ball, tris=200)
        assert lean["before"]["tris"] == 760 and lean["after"]["tris"] <= 210, lean
        assert tris_of(ball) == lean["after"]["tris"], lean
        assert "bmaya undo" in lean["next"], lean
        assert cmds.ls(selection=True) == ["crate"], "reduce changed the selection"
        cmds.undo()
        assert tris_of(ball) == 760, "one undo did not take the reduce back"

        half = call("reduce_mesh", name=ball, percent=50, keep_quads=True)
        assert 340 <= half["after"]["tris"] <= 420 and half["percent"] == 50, half
        after = call("diff_mesh", name=ball)
        assert after["before"]["tris"] == 760 and after["topology_changed"] is True, after

        expect_error("reduce_mesh", "--tris", name=ball)
        expect_error("reduce_mesh", "one of them", name=ball, tris=100, percent=50)
        expect_error("reduce_mesh", "between 0 and 100", name=ball, percent=150)
        expect_error("reduce_mesh", "already has", name=ball, tris=5000)
    finally:
        cmds.delete(ball)
        cmds.select(clear=True)
    return "triangle budget, percent, own mark, one undo step ok"


def check_remesh_mesh() -> str:
    import time

    ball = cmds.polySphere(name="remesh_ball", constructionHistory=False)[0]
    dense = cmds.polySphere(
        name="remesh_dense", subdivisionsX=140, subdivisionsY=140, constructionHistory=False
    )[0]
    cmds.select(clear=True)
    try:
        cmds.flushUndo()
        even = call("remesh_mesh", name=ball, to="tris", edge=0.3)
        assert even["after"]["faces"]["quads"] == 0 and even["after"]["tris"] > 50, even
        assert even["edge"] == 0.3 and even["before"]["tris"] == 760, even
        cmds.undo()
        assert tris_of(ball) == 760, "one undo did not take the remesh back"

        finer = call("remesh_mesh", name=ball, to="tris", edge=0.15)
        ratio = finer["after"]["tris"] / even["after"]["tris"]
        assert 3 < ratio < 5, f"half the edge should give about 4x the triangles, got {ratio}"
        cmds.undo()

        by_default = call("remesh_mesh", name=ball)
        assert by_default["to"] == "tris", by_default
        assert 500 < by_default["after"]["tris"] < 1200, by_default
        expect_error("remesh_mesh", "the most is", name=ball, edge=0.0005)

        quads = call("remesh_mesh", name=ball, to="quads", faces=200)
        kinds = quads["after"]["faces"]
        assert kinds["quads"] > 100 and kinds["quads"] > 5 * kinds["tris"], quads

        started = time.perf_counter()
        call("remesh_mesh", name=dense, to="quads", faces=1000)
        spent = time.perf_counter() - started

        expect_error("remesh_mesh", "--to", name=ball, to="hexes")
        expect_error("remesh_mesh", "--faces", name=ball, to="quads", edge=0.3)
        expect_error("remesh_mesh", "--edge", name=ball, to="tris", faces=300)
    finally:
        cmds.delete(ball, dense)
    return f"even triangles, quads, undo ok; retopology of 39k triangles took {spent:.1f}s"


def check_cleanup_mesh() -> str:
    box = cmds.polyCube(name="cleanup_box", constructionHistory=False)[0]
    cmds.polySplitVertex(box + ".vtx[0]", constructionHistory=False)
    cmds.polyNormal(box, normalMode=0, userNormalMode=0, constructionHistory=False)
    cmds.select(clear=True)
    try:
        before = call("audit_mesh", names=box)["meshes"][0]
        assert before["verts"] == 10 and before["open_edges"], before
        uvs = cmds.polyEvaluate(box, uvcoord=True)
        cmds.flushUndo()
        fixed = call("cleanup_mesh", name=box)
        assert fixed["before"]["verts"] == 10 and fixed["after"]["verts"] == 8, fixed
        assert fixed["normals_reversed"] is True, fixed
        left = " | ".join(fixed["issues"])
        assert "inward" not in left and "flipped" not in left, fixed
        assert fixed["after"]["volume"] == 1, fixed
        assert cmds.polyEvaluate(box, uvcoord=True) == uvs, "cleanup merged UVs across seams"
        cmds.undo()
        assert cmds.polyEvaluate(box, vertex=True) == 10, "one undo did not take the cleanup back"
        expect_error("cleanup_mesh", "bmaya scene", name="missing_mesh")
    finally:
        cmds.delete(box)
    flagged = mirrored_box("cleanup_flagged")
    try:
        fixed = call("cleanup_mesh", name=flagged)
        assert fixed["opposite_cleared"] is True and fixed["normals_reversed"] is True, fixed
        assert not cmds.getAttr(flagged + "Shape.opposite"), "cleanup left the opposite flag"
        assert fixed["after"]["volume"] == 1, fixed
        assert not any("opposite" in issue or "inward" in issue for issue in fixed["issues"]), fixed
    finally:
        cmds.delete(flagged)
    return "split vertices merged, normals turned outward, opposite flag cleared, one undo step ok"


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


def check_plot_animation() -> str:
    from PySide6 import QtGui

    plotted = cmds.spaceLocator(name="plotted")[0]
    for at, x in ((1, 0), (6, 4), (12, 1)):
        cmds.setKeyframe(plotted, attribute="translateX", time=at, value=x)
    cmds.setKeyframe(plotted, attribute="rotateY", time=1, value=0)
    cmds.setKeyframe(plotted, attribute="rotateY", time=12, value=350)
    cmds.setKeyframe(plotted, attribute="visibility", time=1, value=1)
    for at, y in ((1, 60), (6, 60.2), (12, 60)):
        cmds.setKeyframe(plotted, attribute="translateY", time=at, value=y)
    try:
        result = assert_read_only("plot_animation", name="plotted", max_size=800)
        assert result["range"] == [1, 12] and result["width"] == 800, result
        curves = {entry["attr"]: entry for entry in result["curves"]["items"]}
        assert curves["translateX"]["keys"] == 3 and curves["translateX"]["max"] == 4, curves
        assert curves["rotateY"]["euler_jumps"] == [12], curves
        assert "visibility" in curves, curves
        from bmaya_bridge.handlers import plot

        split = plot.panels(
            [plot.sample(attr, curve, 1, 12) for attr, curve in plot.node_curves("|plotted", [])]
        )
        translate = [[c.attr for c in band] for group, band in split if group == "translate"]
        assert translate == [["translateX"], ["translateY"]], translate
        image = QtGui.QImage(result["path"])
        assert (image.width(), image.height()) == (800, result["height"]), result
        colors = {image.pixel(x, y) for x in range(0, 800, 7) for y in range(0, image.height(), 7)}
        assert len(colors) > 10, "the plot looks empty"

        only = call("plot_animation", name="plotted", attrs="tx", frames="0:20")
        assert [entry["attr"] for entry in only["curves"]["items"]] == ["translateX"], only
        assert only["range"] == [0, 20], only
        expect_error("plot_animation", "no keys on", name="plotted", attrs="sz")
        expect_error("plot_animation", "no keys to plot", name="crate")
    finally:
        cmds.delete(plotted)
    return "panels, key dots, Euler jump ticks, attribute filter, range ok"


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
    read_back = check_export_read_back(target, clip)
    refused = check_export_refusals(target)

    cmds.select(clear=True)
    expect_error("export_fbx", "nothing is selected", path=str(target), selection_only=True)
    expect_error("export_fbx", ".fbx", path=str(WORK_DIR / "x.obj"))
    expect_error("export_fbx", "unity", path=str(target), preset="cryengine")
    return (
        f"unreal+anim {result['size']} bytes, selection-only and errors ok; {read_back}; {refused}"
    )


def check_export_read_back(target: Path, clip: dict[str, Any]) -> str:
    from bmaya_cli.modelfile import compare, read

    contents = read(target)
    assert [take.name for take in contents.takes] == ["nod"], contents.takes
    assert compare(contents, clip["expected"]) == [], compare(contents, clip["expected"])
    assert set(clip["expected"]["joints"]) == {"root", "spine", "head"}, clip["expected"]
    assert "body" in clip["expected"]["meshes"] and "crate" in clip["expected"]["meshes"]
    named = call("export_fbx", path=str(WORK_DIR / "spin_all.fbx"), animation=True)
    assert [t.name for t in read(WORK_DIR / "spin_all.fbx").takes] == ["spin_all"], named
    return "file read back matches, one take named after the clip or the file"


def check_export_refusals(target: Path) -> str:
    from bmaya_bridge.handlers import export as exporting

    holder = cmds.group(empty=True, name="export_ghost")
    cmds.createNode("mesh", name="export_ghostShape", parent=holder)
    flagged = mirrored_box("export_flagged")
    try:
        expect_error("export_fbx", "export_ghostShape has no faces", path=str(target))
        ghost = call("audit_mesh", names=holder)["meshes"][0]
        assert ghost["issues"] == ["export_ghostShape has no faces"], ghost
        forced = call("export_fbx", path=str(target), ignore_checks=True)
        assert any("has no faces" in issue for issue in forced["issues"]), forced["issues"]
        cmds.delete(holder)
        expect_error("export_fbx", "opposite flag over inside-out faces", path=str(target))
        call("cleanup_mesh", name=flagged)
        call("export_fbx", path=str(target))
    finally:
        cmds.delete([node for node in (holder, flagged) if cmds.objExists(node)])

    real = exporting.mel
    calls = []

    class Flaky:
        @staticmethod
        def eval(command: str) -> object:
            if command.startswith("FBXExport -f"):
                calls.append(command)
                if len(calls) == 1:
                    raise RuntimeError("UI callback failed")
                if mode == "silent":
                    return None
            return real.eval(command)

    try:
        exporting.mel = Flaky
        mode = "retry"
        retried = call("export_fbx", path=str(target))
        assert retried["retried"] is True and len(calls) == 2, (retried, calls)
        calls.clear()
        mode = "silent"
        expect_error("export_fbx", "left", path=str(target))
    finally:
        exporting.mel = real
    return "empty and inside-out meshes refused, retry once, unchanged file caught"


def game_exporter_scene() -> tuple[str, str, str]:
    from bmaya_bridge.handlers.game_export import load_game_exporter

    load_game_exporter()
    texture = make_texture(8, "skin.png")
    material = cmds.shadingNode("lambert", asShader=True, name="game_mat")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="game_matSG")
    cmds.connectAttr(material + ".outColor", engine + ".surfaceShader")
    file_node = cmds.shadingNode("file", asTexture=True, name="game_file")
    cmds.setAttr(file_node + ".fileTextureName", texture, type="string")
    cmds.setAttr(file_node + ".colorSpace", "Raw", type="string")
    cmds.connectAttr(file_node + ".outColor", material + ".color")
    cmds.sets("body", edit=True, forceElement=engine)
    cmds.sets("root", "body", name="game_model_set")
    joints = cmds.ls("root", dagObjects=True, type="joint")
    cmds.sets(joints, name="game_clip_set")
    model = cmds.createNode("gameFbxExporter", name="game_model")
    cmds.setAttr(model + ".exportTypeIndex", 1)
    cmds.setAttr(model + ".exportSetIndex", 3)
    cmds.setAttr(model + ".selectionSetName", "game_model_set", type="string")
    cmds.setAttr(model + ".exportPath", (WORK_DIR / "game").as_posix(), type="string")
    cmds.setAttr(model + ".exportFilename", "body", type="string")
    cmds.setAttr(model + ".includeChildren", True)
    clips = cmds.createNode("gameFbxExporter", name="game_clips")
    cmds.setAttr(clips + ".exportTypeIndex", 2)
    cmds.setAttr(clips + ".exportSetIndex", 3)
    cmds.setAttr(clips + ".fileSplitType", 2)
    cmds.setAttr(clips + ".selectionSetName", "game_clip_set", type="string")
    cmds.setAttr(clips + ".exportPath", (WORK_DIR / "game" / "anim").as_posix(), type="string")
    cmds.setAttr(clips + ".exportFilename", "hero_", type="string")
    for index, name, start, end, on in (
        (0, "spin", 1, 24, True),
        (1, "nod", 2, 4, True),
        (2, "off", 1, 5, False),
    ):
        plug = f"{clips}.animClips[{index}]"
        cmds.setAttr(plug + ".animClipName", name, type="string")
        cmds.setAttr(plug + ".animClipStart", start)
        cmds.setAttr(plug + ".animClipEnd", end)
        cmds.setAttr(plug + ".exportAnimClip", on)
    return model, clips, file_node


def check_plan_game_export() -> str:
    made = game_exporter_scene()
    try:
        cmds.file(modified=False)
        plan = assert_read_only("plan_game_export")
        jobs = {job["path"].rsplit("/", 1)[-1]: job for job in plan["jobs"]}
        assert set(jobs) == {"body.fbx", "hero_spin.fbx", "hero_nod.fbx"}, sorted(jobs)
        assert {"node": "clips_exporter", "reason": "it has no export path"} in plan["skipped"], (
            plan
        )
        model = jobs["body.fbx"]
        assert model["kind"] == "model" and model["textures"][0]["node"] == "game_file", model
        assert model["expected"]["meshes"] == ["body"], model["expected"]
        assert jobs["hero_nod.fbx"]["frames"] == [2, 4], jobs["hero_nod.fbx"]
        one = call("plan_game_export", node="game_clips", clip="nod")
        assert [job["clip"] for job in one["jobs"]] == ["nod"], one
        expect_error("plan_game_export", "game_clips", node="missing_node")
        expect_error("plan_game_export", "no clip named 'walk'", node="game_clips", clip="walk")
        assert not cmds.file(query=True, modified=True), "planning marked the scene modified"
        cmds.setAttr("game_clips.fileSplitType", 1)
        together = call("plan_game_export", node="game_clips")
        assert [job["kind"] for job in together["jobs"]] == ["clips"], together
        assert together["jobs"][0]["path"].endswith("/hero_.fbx"), together["jobs"][0]
        cmds.setAttr("game_clips.exportFilename", "", type="string")
        apart = call("plan_game_export", node="game_clips")
        assert [job["clip"] for job in apart["jobs"]] == ["spin", "nod"], apart
        assert "each clip went to its own file" in apart["issues"][0], apart["issues"]
    finally:
        cmds.delete(
            [
                node
                for node in (*made[:2], "game_model_set", "game_clip_set")
                if cmds.objExists(node)
            ]
        )
    return "model and clip jobs, skipped nodes, clip filter, errors ok"


def check_export_game_file() -> str:
    from bmaya_cli.modelfile import compare, read

    model, clips, file_node = game_exporter_scene()
    try:
        cmds.select("crate", replace=True)
        cmds.file(modified=False)
        plan = call("plan_game_export")
        copied = (WORK_DIR / "game" / "skin_copy.png").as_posix()
        for job in plan["jobs"]:
            moves = {file_node: copied} if job["kind"] == "model" else {}
            params = {k: job.get(k, "") for k in ("node", "kind", "clip", "path")}
            reply = assert_read_only("export_game_file", textures=moves, **params)
            contents = read(job["path"])
            assert compare(contents, job["expected"]) == [], (
                job["path"],
                compare(contents, job["expected"]),
            )
            if job["kind"] == "clip":
                assert [t.name for t in contents.takes] == [job["clip"]], contents.takes
            else:
                assert contents.meshes[0].influences == 3, contents.meshes
                assert contents.textures[0].path.endswith("skin_copy.png"), contents.textures
            assert reply["size"] == Path(job["path"]).stat().st_size, reply
        assert cmds.getAttr(file_node + ".colorSpace") == "Raw", "color space not put back"
        assert cmds.getAttr(file_node + ".fileTextureName").endswith("skin.png"), (
            "path not put back"
        )
        assert cmds.ls(selection=True) == ["crate"], cmds.ls(selection=True)
        assert not cmds.file(query=True, modified=True), "export marked the scene modified"
        cmds.setAttr(model + ".includeChildren", False)
        (job,) = call("plan_game_export", node=model)["jobs"]
        assert set(job["expected"]["joints"]) == {"root", "spine", "head"}, job["expected"]
        call("export_game_file", **{k: job[k] for k in ("node", "kind", "path")})
        lost = compare(read(job["path"]), job["expected"])
        assert lost == ["2 of 3 scene joints are not in the file: spine, head"], lost
        cmds.setAttr(clips + ".exportFilename", "other_", type="string")
        expect_error(
            "export_game_file",
            "changed since",
            node=clips,
            kind="clip",
            clip="nod",
            path=plan["jobs"][-1]["path"],
        )
    finally:
        cmds.select(clear=True)
        cmds.delete(
            [
                node
                for node in (model, clips, "game_model_set", "game_clip_set")
                if cmds.objExists(node)
            ]
        )
    return (
        f"{len(plan['jobs'])} files written as planned, one take each, texture copy and settings "
        "put back, joints left out without children caught"
    )


def check_fbx_frame_rate() -> str:
    from bmaya_cli.modelfile import read, read_back

    found = []
    for unit, rate in (("film", 24.0), ("ntsc", 30.0)):
        cmds.file(new=True, force=True)
        cmds.currentUnit(time=unit)
        cube = cmds.polyCube(name="paced")[0]
        cmds.setKeyframe(cube, attribute="translateY", time=0, value=0)
        cmds.setKeyframe(cube, attribute="translateY", time=16, value=5)
        path = WORK_DIR / f"paced_{unit}.fbx"
        params = {"path": str(path), "animation": True, "start": "0", "end": "16"}
        reply = call("export_fbx", **params)
        back = read_back(str(path), reply["expected"])
        if unit == "ntsc":
            assert back.stale_rate, "the plugin did not keep the earlier rate; the check is moot"
            reply = call("export_fbx", reload_fbx=True, **params)
            assert reply["reloaded"] is True, reply
            back = read_back(str(path), reply["expected"])
        assert not back.stale_rate and back.issues == [], back
        (take,) = read(path).takes
        assert abs(1.0 / take.key_step - rate) < 0.01, (unit, 1.0 / take.key_step)
        found.append(f"{unit} {rate:g}")
    return "stale bake rate found in the file and fixed by a reload: " + ", ".join(found)


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
    grouped = call("get_motion", names="walker", frames="0:24")
    assert grouped["nodes"]["total"] == 3, grouped["nodes"]
    cmds.select(clear=True)
    cmds.joint(name="chain_root", position=(0, 0, 0))
    cmds.joint(name="chain_mid", position=(0, 5, 0))
    cmds.joint(name="chain_tip", position=(0, 10, 0))
    cmds.select(clear=True)
    skeleton = call("get_motion", names="chain_root", frames="0:24")
    assert skeleton["nodes"]["total"] == 3, skeleton["nodes"]
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
    cmds.setKeyframe(shoulder, attribute="rotateY", time=0)
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

    whole = call("get_rig_info")
    joints = {entry["name"]: entry for entry in whole["controls"]["items"]}
    assert joints["shoulder"]["ik"] == "arm_ik", joints["shoulder"]
    assert "rotate" in joints["shoulder"]["driven"], joints["shoulder"]

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


def bright_count(path: str) -> int:
    from PySide6 import QtGui

    image = QtGui.QImage(path)
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if QtGui.qGray(image.pixel(x, y)) > 200
    )


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
    far = cmds.camera(focalLength=35, name="far_cam")[0]
    cmds.move(90, 50, 140, far)
    cmds.viewLookAt(far, position=(0, 0, 0))
    before = cmds.xform(far, query=True, worldSpace=True, matrix=True)
    nodes = sorted(cmds.ls())
    distant = call("get_viewport_screenshot", camera=far, light="unlit", max_size=400)
    framed = call("get_viewport_screenshot", camera=far, fit="marker", light="unlit", max_size=400)
    assert framed["fitted"] == ["marker"] and framed["camera"] == far, framed
    small = bright_count(distant["path"])
    big = bright_count(framed["path"])
    assert big > 20 * max(small, 1), f"--fit did not frame the marker: {small} -> {big} pixels"
    seen = bright_centroid(framed["path"])
    assert abs(seen[0] - 200) < 40 and abs(seen[1] - 112) < 40, f"marker not centred: {seen}"
    assert cmds.xform(far, query=True, worldSpace=True, matrix=True) == before, "camera moved"
    assert sorted(cmds.ls()) == nodes, "the fit camera was left behind"

    cmds.setKeyframe("marker", attribute="translateX", time=1, value=-20)
    cmds.setKeyframe("marker", attribute="translateX", time=10, value=20)
    through_persp = call("get_viewport_screenshot", camera="persp", fit="marker", max_size=300)
    assert through_persp["fitted"] == ["marker"], through_persp
    moving = call(
        "get_viewport_screenshot", camera=far, frames="1:10", fit="marker", trail="marker"
    )
    assert moving["trail"]["outside"] == 0, moving["trail"]
    return f"sphere lands within {worst:.2f} px of its projection; trail and --fit in place"


def skin_rows(mesh: str) -> list[dict[str, float]]:
    from bmaya_bridge.handlers import skin

    shape = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True)[0]
    return skin.read_skin(skin.clusters_of(shape)[0], shape).rows


def worst_gap(rows: list[dict[str, float]], other: list[dict[str, float]]) -> float:
    assert len(rows) == len(other), (len(rows), len(other))
    worst = 0.0
    for index, one in enumerate(rows):
        two = other[index]
        for name in set(one) | set(two):
            worst = max(worst, abs(one.get(name, 0.0) - two.get(name, 0.0)))
    return worst


def arm_mesh(name: str, x: float) -> str:
    mesh = cmds.polyCylinder(
        name=name, height=8, radius=1, subdivisionsAxis=12, subdivisionsHeight=12
    )[0]
    cmds.rotate(0, 0, 90, mesh)
    cmds.move(x, 0, 0, mesh)
    cmds.makeIdentity(mesh, apply=True, translate=True, rotate=True)
    cmds.xform(mesh, pivots=(0, 0, 0), worldSpace=True)
    cmds.delete(mesh, constructionHistory=True)
    return mesh


def arm_rig() -> list[str]:
    """A right arm skinned to skin_root and up_r, lo_r, end_r, with the left joints mirrored."""
    cmds.select(clear=True)
    root = cmds.joint(name="skin_root", position=(0, 0, 0))
    cmds.joint(name="up_r", position=(2, 0, 0))
    cmds.joint(name="lo_r", position=(6, 0, 0))
    cmds.joint(name="end_r", position=(10, 0, 0))
    cmds.mirrorJoint("up_r", mirrorYZ=True, mirrorBehavior=True, searchReplace=("_r", "_l"))
    arm = arm_mesh("arm_r", 6)
    cmds.skinCluster(
        [root, "up_r", "lo_r", "end_r"], arm, toSelectedBones=True, maximumInfluences=3
    )
    plain = cmds.polyCube(name="skin_plain", constructionHistory=False)[0]
    return [root, arm, plain]


def check_get_skin_info() -> str:
    made = arm_rig()
    try:
        report = assert_read_only("get_skin_info", names="arm_r")
        entry = report["meshes"][0]
        assert entry["skin"].startswith("skinCluster"), entry
        assert entry["verts"] == 156 and entry["influences"]["total"] == 4, entry
        assert entry["most_per_vertex"] <= 3 and entry["issues"] == [], entry
        assert entry["bind_pose"] == {"at": True, "moved": {"items": [], "shown": 0, "total": 0}}
        assert entry["method"] == "linear" and entry["normalize"] == "interactive", entry

        tight = call("get_skin_info", names="arm_r", max=1)["meshes"][0]
        assert "verts with more than 1 influences" in tight["issues"][0], tight
        assert tight["sample"]["over"], tight

        rows = call("get_skin_info", names="arm_r", verts="0:2,150")["meshes"][0]["rows"]
        assert [row["vertex"] for row in rows["items"]] == [0, 1, 2, 150], rows
        assert abs(sum(rows["items"][0]["weights"].values()) - 1) < 1e-3, rows

        cluster = entry["skin"]
        cmds.setAttr(cluster + ".normalizeWeights", 0)
        cmds.setAttr(f"{cluster}.weightList[0].weights[0]", 5.0)
        for index in cmds.getAttr(f"{cluster}.weightList[1].weights", multiIndices=True):
            cmds.removeMultiInstance(f"{cluster}.weightList[1].weights[{index}]")
        cmds.setAttr(cluster + ".skinningMethod", 1)
        cmds.setAttr("lo_r.rotateZ", 30)
        broken = call("get_skin_info", names="arm_r")["meshes"][0]
        issues = " | ".join(broken["issues"])
        for expected in (
            "1 verts whose weights do not add up to 1",
            "1 unweighted verts",
            "dual quaternion skinning",
        ):
            assert expected in issues, f"{expected!r} missing from {issues}"
        assert broken["bind_pose"]["at"] is False, broken
        assert broken["bind_pose"]["moved"]["items"] == ["lo_r", "end_r"], broken
        assert broken["sample"]["unweighted"] == [1], broken

        cmds.select("arm_r", replace=True)
        assert call("get_skin_info")["source"] == "selection"
        cmds.select(clear=True)
        assert call("get_skin_info")["source"] == "scene"
        expect_error("get_skin_info", "one mesh", names="arm_r,skin_plain", verts="0")
        expect_error("get_skin_info", "not in the mesh", names="arm_r", verts="9999")
    finally:
        cmds.delete(made)
    return "influences, budget, rows by vertex, broken weights, dq, moved joints ok"


def check_save_skin_weights() -> str:
    made = arm_rig()
    target = WORK_DIR / "weights" / "arm.json"
    try:
        saved = assert_read_only("save_skin_weights", name="arm_r", path=str(target))
        assert saved["verts"] == 156 and saved["joints"] == 4, saved
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["mesh"] == "arm_r" and len(data["weights"]) == 156, sorted(data)
        assert worst_gap(data["weights"], skin_rows("arm_r")) < 1e-6
        expect_error("save_skin_weights", "no skin", name="skin_plain", path=str(target))
    finally:
        cmds.delete(made)
    return f"{saved['size']} bytes, rows match the cluster to 1e-6"


def check_edit_skin() -> str:
    made = arm_rig()
    saved = WORK_DIR / "arm_saved.json"
    try:
        call("save_skin_weights", name="arm_r", path=str(saved))
        original = skin_rows("arm_r")
        lines = [check_edit_load(saved, original, made)]
        lines.append(check_edit_copy(made))
        lines.append(check_edit_mirror(made))
        lines.append(check_edit_trim())
        lines.append(check_edit_refusals(saved))
    finally:
        cmds.delete([node for node in made if cmds.objExists(node)])
    return "; ".join(lines)


def check_edit_load(saved: Path, original: list[dict[str, float]], made: list[str]) -> str:
    cluster = cmds.ls(cmds.listHistory("arm_r"), type="skinCluster")[0]
    cmds.skinPercent(cluster, "arm_r", transformValue=[("skin_root", 1.0)])
    cmds.flushUndo()
    loaded = call("edit_skin", name="arm_r", load=str(saved))
    assert loaded["source"]["verts"] == 156 and loaded["changed"] > 100, loaded
    assert worst_gap(skin_rows("arm_r"), original) < 1e-5
    cmds.undo()
    assert all(row == {"skin_root": 1.0} for row in skin_rows("arm_r")), "undo missed"
    cmds.redo()
    assert worst_gap(skin_rows("arm_r"), original) < 1e-5

    fresh = arm_mesh("arm_fresh", 6)
    made.append(fresh)
    cmds.flushUndo()
    bound = call("edit_skin", name=fresh, load=str(saved), max=3)
    assert bound["bound"] == "arm_fresh_skin", bound
    assert worst_gap(skin_rows(fresh), original) < 1e-5
    assert cmds.getAttr("arm_fresh_skin.maxInfluences") == 3
    cmds.undo()
    assert not cmds.objExists("arm_fresh_skin"), "undo left the new skin"
    cmds.redo()
    assert cmds.objExists("arm_fresh_skin"), "redo lost the skin"

    partial = WORK_DIR / "partial.json"
    partial.write_text(json.dumps({"weights": {"3": {"up": 1}}}), encoding="utf-8")
    one = call("edit_skin", name=fresh, load=str(partial), rename="{}_r")
    assert one["changed"] == 1 and skin_rows(fresh)[3] == {"up_r": 1.0}, one

    extra = WORK_DIR / "extra.json"
    extra.write_text(json.dumps({"weights": {"4": {"up_l": 1}}}), encoding="utf-8")
    added = call("edit_skin", name=fresh, load=str(extra))
    assert added["added"]["items"] == ["up_l"], added
    cmds.undo()
    assert "up_l" not in cmds.skinCluster("arm_fresh_skin", query=True, influence=True)
    return "load, undo/redo, bind from a file, partial load with rename, added joint ok"


def check_edit_copy(made: list[str]) -> str:
    ours = arm_mesh("arm_remesh", 6)
    theirs = arm_mesh("arm_theirs", 6)
    made.extend([ours, theirs])
    for mesh in (ours, theirs):
        cmds.polyRemesh(
            mesh,
            maxEdgeLength=0.4,
            useRelativeValues=False,
            worldSpace=True,
            constructionHistory=False,
        )
    copied = call("edit_skin", name=ours, copy_from="arm_r")
    assert copied["bound"] == "arm_remesh_skin" and copied["source"]["mesh"] == "arm_r", copied
    assert "far" not in copied, copied
    joints = cmds.skinCluster("arm_r", query=True, influence=True)
    reference = cmds.skinCluster(joints, theirs, toSelectedBones=True, maximumInfluences=4)[0]
    source = cmds.ls(cmds.listHistory("arm_r"), type="skinCluster")[0]
    cmds.copySkinWeights(
        sourceSkin=source,
        destinationSkin=reference,
        noMirror=True,
        surfaceAssociation="closestPoint",
        influenceAssociation=["name"],
        normalize=True,
    )
    gap = worst_gap(skin_rows(ours), skin_rows(theirs))
    assert gap < 1e-4, f"copied weights differ from copySkinWeights by {gap}"

    cmds.setAttr("lo_r.rotateZ", 40)
    again = call("edit_skin", name=ours, copy_from="arm_r")
    assert again["changed"] == 0, again
    unbound = arm_mesh("arm_posed", 6)
    made.append(unbound)
    expect_error("edit_skin", "bind pose", name=unbound, copy_from="arm_r")
    cmds.setAttr("lo_r.rotateZ", 0)
    return f"copy onto a remesh matches copySkinWeights within {gap:.1e}, posed source ok"


def check_edit_mirror(made: list[str]) -> str:
    left = arm_mesh("arm_l", 6)
    cmds.setAttr(left + ".scaleX", -1)
    cmds.makeIdentity(left, apply=True, scale=True)
    cmds.setAttr(left + ".opposite", False)
    cmds.polyNormal(left, normalMode=0, userNormalMode=0, constructionHistory=False)
    made.append(left)
    mirrored = call("edit_skin", name=left, copy_from="arm_r", mirror="x")
    assert mirrored["mirror"]["paired"] == 3, mirrored
    assert mirrored["mirror"]["unpaired"]["items"] == ["skin_root"], mirrored
    expected = [
        {name[:-2] + "_l" if name.endswith("_r") else name: weight for name, weight in row.items()}
        for row in skin_rows("arm_r")
    ]
    gap = worst_gap(skin_rows(left), expected)
    assert gap < 1e-5, f"mirrored weights are off by {gap}"

    body = cmds.polyCylinder(
        name="skin_body", height=20, radius=1, subdivisionsAxis=12, subdivisionsHeight=20
    )[0]
    cmds.rotate(0, 0, 90, body)
    cmds.makeIdentity(body, apply=True, rotate=True)
    cmds.delete(body, constructionHistory=True)
    made.append(body)
    joints = ["skin_root", "up_r", "lo_r", "end_r", "up_l", "lo_l", "end_l"]
    cluster = cmds.skinCluster(joints, body, toSelectedBones=True, maximumInfluences=3)[0]
    cmds.skinPercent(cluster, body, transformValue=[("skin_root", 1.0)])
    points = cmds.xform(body + ".vtx[*]", query=True, worldSpace=True, translation=True)
    xs = [points[index] for index in range(0, len(points), 3)]
    for vertex, x in enumerate(xs):
        if x > 4:
            cmds.skinPercent(cluster, f"{body}.vtx[{vertex}]", transformValue=[("lo_r", 1.0)])
    expect_error("edit_skin", "which half", name=body, mirror="x")
    half = call("edit_skin", name=body, mirror="+x")
    assert half["source"] == {"half": "+x"}, half
    rows = skin_rows(body)
    on_left = [vertex for vertex, row in enumerate(rows) if row.get("lo_l", 0) > 0.99]
    assert on_left == [vertex for vertex, x in enumerate(xs) if x < -4], on_left
    assert all(rows[vertex] == {"skin_root": 1.0} for vertex, x in enumerate(xs) if abs(x) < 1e-6)
    return f"mirror onto a flipped copy within {gap:.1e}, half of one mesh ok"


def check_edit_trim() -> str:
    cluster = cmds.ls(cmds.listHistory("arm_r"), type="skinCluster")[0]
    cmds.setAttr(cluster + ".maintainMaxInfluences", False)
    cmds.skinPercent(
        cluster,
        "arm_r.vtx[0]",
        transformValue=[("skin_root", 0.4), ("up_r", 0.3), ("lo_r", 0.25), ("end_r", 0.05)],
    )
    trimmed = call("edit_skin", name="arm_r", prune="0.1", max=2)
    assert trimmed["pruned"] >= 1 and trimmed["capped"] >= 1, trimmed
    first = skin_rows("arm_r")[0]
    assert sorted(first) == ["skin_root", "up_r"], first
    assert abs(first["skin_root"] - 0.4 / 0.7) < 1e-6, first
    assert trimmed["after"]["most_per_vertex"] <= 2, trimmed
    same = call("edit_skin", name="arm_r", normalize=True)
    assert same["changed"] == 0, same
    return f"prune and --max 2 cut {trimmed['capped']} verts"


def check_edit_refusals(saved: Path) -> str:
    expect_error("edit_skin", "Nothing to change", name="arm_r")
    expect_error("edit_skin", "do not go together", name="arm_r", load=str(saved), copy_from="b")
    expect_error("edit_skin", "--rename", name="arm_r", load=str(saved), rename="{}_x")
    expect_error("edit_skin", "--copy-from", name="skin_plain", load=str(saved))
    expect_error("edit_skin", "no skin", name="skin_plain", copy_from="skin_plain")
    expect_error("edit_skin", "no skin", name="skin_plain", prune="0.1")
    expect_error("edit_skin", "itself", name="arm_r", copy_from="arm_r")
    expect_error("edit_skin", "No weights file", name="arm_r", load=str(saved) + ".missing")
    expect_error("edit_skin", "between 0 and 1", name="arm_r", prune="2")
    expect_error("edit_skin", "Bad mirror", name="arm_r", mirror="w")
    far = arm_mesh("arm_far", 60)
    try:
        expect_error("edit_skin", "overlap", name=far, copy_from="arm_r")
    finally:
        cmds.delete(far)
    return "refusals ok"


EXTRA_CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("projection", check_projection),
    ("sampling", check_sampling),
    ("bridge dispatch", check_bridge_dispatch),
    ("shared maya", check_shared_maya),
    ("screenshot looks", check_screenshot_looks),
    ("fbx frame rate", check_fbx_frame_rate),
]

CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("get_scene_info", check_get_scene_info),
    ("get_object_info", check_get_object_info),
    ("execute_maya_code", check_execute_maya_code),
    ("get_viewport_screenshot", check_get_viewport_screenshot),
    ("render_frames", check_render_frames),
    ("audit_mesh", check_audit_mesh),
    ("get_model_views", check_get_model_views),
    ("diff_mesh", check_diff_mesh),
    ("reduce_mesh", check_reduce_mesh),
    ("remesh_mesh", check_remesh_mesh),
    ("cleanup_mesh", check_cleanup_mesh),
    ("get_animation_info", check_get_animation_info),
    ("plot_animation", check_plot_animation),
    ("export_fbx", check_export_fbx),
    ("plan_game_export", check_plan_game_export),
    ("export_game_file", check_export_game_file),
    ("scene_file", check_scene_file),
    ("get_motion", check_get_motion),
    ("get_rig_info", check_get_rig_info),
    ("get_skin_info", check_get_skin_info),
    ("save_skin_weights", check_save_skin_weights),
    ("edit_skin", check_edit_skin),
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
    sys.path.insert(0, str(REPO / "src"))

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
    wanted = sys.argv[1:]
    if wanted:
        checks = [(name, check) for name, check in checks if any(w in name for w in wanted)]
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
