"""`bmaya-mcp demo`: builds, animates, checks and exports a small barrel in the live Maya."""

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bmaya_mcp.client import BridgeCommandError, BridgeConnectionError
from bmaya_mcp.sessions import SessionError, SessionManager

CLIENT_LABEL = "bmaya-mcp-demo"
FIRST_FRAME = 1
LAST_FRAME = 24
SHEET_FRAMES = "1,7,13,19"

BUILD_CODE = """
fresh = PARAMS["fresh_scene"]
offset = 0.0
parent = None
if not fresh:
    meshes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
    if meshes:
        offset = cmds.exactWorldBoundingBox(meshes)[3] + 100.0
    parent = cmds.group(empty=True, name="bmaya_demo#")
    cmds.xform(parent, translation=(offset, 0, 0))

body = cmds.polyCylinder(
    name="barrel_body", radius=30, height=80, subdivisionsX=12, subdivisionsY=4,
    subdivisionsCaps=1,
)[0]
cmds.move(0, 40, 0, body)
pieces = [body]
for height in (18, 62):
    band = cmds.polyTorus(
        name="barrel_band", radius=30.5, sectionRadius=1.5, subdivisionsX=12,
        subdivisionsY=4,
    )[0]
    cmds.move(0, height, 0, band)
    pieces.append(band)
geo = cmds.polyUnite(pieces, name="barrel_geo", mergeUVSets=1)[0]
cmds.delete(geo, constructionHistory=True)
cmds.makeIdentity(geo, apply=True, translate=True, rotate=True, scale=True)
cmds.xform(geo, objectSpace=True, pivots=(0, 0, 0))

material = cmds.shadingNode("lambert", asShader=True, name="barrel_mat")
cmds.setAttr(material + ".color", 0.45, 0.28, 0.14, type="double3")
engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="barrel_matSG")
cmds.connectAttr(material + ".outColor", engine + ".surfaceShader")
cmds.sets(geo, edit=True, forceElement=engine)

root = cmds.group(geo, name="barrel_root")
if parent:
    root = cmds.parent(root, parent)[0]
camera = cmds.rename(cmds.camera(focalLength=35)[0], "barrel_cam")
if parent:
    camera = cmds.parent(camera, parent)[0]
cmds.xform(camera, worldSpace=True, translation=(offset + 170, 140, 230))
cmds.viewLookAt(camera, position=(offset, 45, 0))

result = {
    "root": cmds.ls(root, long=True)[0],
    "geo": cmds.ls(cmds.listRelatives(root, children=True, type="transform"), long=True)[0],
    "camera": cmds.ls(camera, long=True)[0],
    "group": cmds.ls(parent, long=True)[0] if parent else "",
}
"""

ANIMATE_CODE = """
root = PARAMS["root"]
for frame, height in ((1, 0), (7, 60), (13, 0), (19, 25), (24, 0)):
    cmds.setKeyframe(root, attribute="translateY", time=frame, value=height)
cmds.keyTangent(root, attribute="translateY", time=(13, 13), inTangentType="linear",
                outTangentType="linear")
cmds.setKeyframe(root, attribute="rotateY", time=PARAMS["first"], value=0)
cmds.setKeyframe(root, attribute="rotateY", time=PARAMS["last"], value=360)
cmds.keyTangent(root, attribute="rotateY", inTangentType="linear", outTangentType="linear")
if PARAMS["fresh_scene"]:
    cmds.playbackOptions(minTime=PARAMS["first"], maxTime=PARAMS["last"])
cmds.select(root, replace=True)
"""

RESTORE_SELECTION_CODE = """
selection = [node for node in PARAMS["selection"] if cmds.objExists(node)]
if selection:
    cmds.select(selection, replace=True)
else:
    cmds.select(clear=True)
"""


class StepFailed(Exception):
    pass


class Demo:
    def __init__(self, manager: SessionManager, out_dir: Path) -> None:
        self.manager = manager
        self.out_dir = out_dir
        self.failures = 0

    def step(self, title: str, action: Callable[[], str], required: bool = True) -> None:
        started = time.perf_counter()
        try:
            summary = action()
        except (BridgeCommandError, BridgeConnectionError, SessionError, OSError) as exc:
            elapsed = (time.perf_counter() - started) * 1000
            self.failures += 1
            print(f"  FAIL {title:<24} {elapsed:6.0f} ms  {describe_error(exc)}")
            if required:
                raise StepFailed(title) from exc
            return
        elapsed = (time.perf_counter() - started) * 1000
        print(f"  ok   {title:<24} {elapsed:6.0f} ms  {summary}")

    def call(self, command: str, **params: object) -> dict[str, Any]:
        return self.manager.call(command, params)

    def run_code(self, code: str, **params: object) -> dict[str, Any]:
        header = f"import json\nPARAMS = json.loads({json.dumps(params)!r})\n"
        return self.call("execute_maya_code", code=header + code)

    def run(self) -> int:
        session = self.manager.resolve()
        print(f"Maya session {session.describe()}")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        fbx_path = (self.out_dir / "prop.fbx").resolve()
        png_path = (self.out_dir / "screenshot.png").resolve()

        scene: dict[str, Any] = {}
        built: dict[str, Any] = {}

        def inspect_scene() -> str:
            scene.update(self.call("get_scene_info", limit=1))
            scene["fresh"] = scene["file"] == "untitled" and not scene["modified"]
            if scene["fresh"]:
                return "untitled and unmodified, so the demo starts a new scene"
            return f"{scene['file']} has content, so the demo builds in its own group"

        def new_scene() -> str:
            self.call("scene_file", action="new")
            return "empty scene"

        def build() -> str:
            reply = self.run_code(BUILD_CODE, fresh_scene=scene["fresh"])
            built.update(reply["result"])
            where = f" inside {built['group']}" if built["group"] else ""
            return f"{built['geo']}{where}"

        def animate() -> str:
            self.run_code(
                ANIMATE_CODE,
                root=built["root"],
                first=FIRST_FRAME,
                last=LAST_FRAME,
                fresh_scene=scene["fresh"],
            )
            return f"bounce and spin on {built['root']}, frames {FIRST_FRAME}-{LAST_FRAME}"

        def audit() -> str:
            report = self.call("audit_mesh", names=built["geo"])
            mesh = report["meshes"][0]
            issues = ", ".join(mesh["issues"]) or "no issues"
            return f"{mesh['verts']} verts, {mesh['tris']} tris, {issues}"

        def animation() -> str:
            info = self.call("get_animation_info", name=built["root"])
            curves = info["curves"]["items"]
            keys = sum(curve["keys"] for curve in curves)
            return f"{len(curves)} curves, {keys} keys at {info['fps']} fps"

        def export() -> str:
            reply = self.call(
                "export_fbx",
                path=str(fbx_path),
                preset="unity",
                selection_only=True,
                animation=True,
                start=FIRST_FRAME,
                end=LAST_FRAME,
            )
            return f"{reply['path']} ({reply['size'] / 1024:.0f} KB)"

        def screenshot() -> str:
            reply = self.call(
                "get_viewport_screenshot",
                max_size=800,
                camera=built["camera"],
                frames=SHEET_FRAMES,
            )
            temp = Path(reply["path"])
            shutil.copyfile(temp, png_path)
            temp.unlink()
            return f"{png_path} ({reply['width']}x{reply['height']}, frames {SHEET_FRAMES})"

        def restore_selection() -> str:
            self.run_code(RESTORE_SELECTION_CODE, selection=scene["selection"]["items"])
            return "previous selection back"

        try:
            self.step("inspect scene", inspect_scene)
            if scene["fresh"]:
                self.step("new scene", new_scene)
            self.step("build barrel", build)
            self.step("key animation", animate)
            self.step("audit_mesh", audit, required=False)
            self.step("get_animation_info", animation, required=False)
            self.step("export_fbx (unity)", export, required=False)
            self.step("viewport contact sheet", screenshot, required=False)
            if not scene["fresh"]:
                self.step("restore selection", restore_selection, required=False)
        except StepFailed as exc:
            print(f"Demo stopped: the '{exc}' step failed.")
            return 1
        if self.failures:
            print(f"Demo finished with {self.failures} failed step(s).")
            return 1
        print(f"Demo finished. Files in {self.out_dir.resolve()}")
        return 0


def describe_error(exc: Exception) -> str:
    if isinstance(exc, BridgeCommandError):
        text = f"{exc.message} {exc.hint}".strip()
        if exc.stdout:
            text += f" Printed: {exc.stdout.strip()}"
        return text
    return str(exc)


def run_demo(out_dir: Path, session_id: str = "") -> int:
    manager = SessionManager(label=CLIENT_LABEL)
    try:
        if session_id:
            manager.use_session(session_id)
        return Demo(manager, out_dir).run()
    except SessionError as exc:
        print(f"Cannot run the demo: {exc}")
        print(
            "Start Maya with the bmaya-mcp module installed (`bmaya-mcp install`), "
            "check it with `bmaya-mcp doctor`, then run the demo again. "
            "With several Maya sessions open, pick one with --session."
        )
        return 1
