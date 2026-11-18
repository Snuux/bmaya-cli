"""Checks that need a Maya with a window: run with `bmaya exec -f tests/maya/check_gui.py`.

Start that Maya with `bmaya start --gui` and BMAYA_HOME pointed at a scratch folder, never
in the user's own Maya. `result` says per case where the marker was drawn and where the
projection put it, with and without the camera's overscan.
"""

from typing import Any

import bmaya_bridge
from bmaya_bridge.handlers import COMMANDS
from bmaya_bridge.handlers._sampling import camera_view
from bmaya_bridge.projection import project
from maya import cmds
from PySide6 import QtGui

POINT = (3.0, 2.0, -1.0)


def bright_centroid(path: str) -> tuple[float, float]:
    image = QtGui.QImage(path)
    xs = ys = count = 0.0
    for y in range(image.height()):
        for x in range(image.width()):
            if QtGui.qGray(image.pixel(x, y)) > 200:
                xs += x + 0.5
                ys += y + 0.5
                count += 1
    if not count:
        return -1.0, -1.0
    return xs / count, ys / count


def build() -> str:
    cmds.file(new=True, force=True)
    ball = cmds.polySphere(name="marker", radius=0.15)[0]
    cmds.move(*POINT, ball)
    shader = cmds.shadingNode("surfaceShader", asShader=True)
    cmds.setAttr(shader + ".outColor", 1, 1, 1, type="double3")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
    cmds.connectAttr(shader + ".outColor", engine + ".surfaceShader")
    cmds.sets(ball, edit=True, forceElement=engine)
    camera = cmds.camera(focalLength=35, name="aim_cam")[0]
    cmds.move(9, 5, 14, camera)
    cmds.viewLookAt(camera, position=(0, 0, 0))
    cmds.select(clear=True)
    return camera


def miss(seen: tuple[float, float], pixel: tuple[float, float] | None) -> float:
    if pixel is None:
        return 1e9
    return round(max(abs(seen[0] - pixel[0]), abs(seen[1] - pixel[1])), 2)


def run() -> dict[str, Any]:
    camera = build()
    shape = cmds.listRelatives(camera, shapes=True)[0]
    cases = []
    for fit, aspect, overscan in (
        (1, "16:9", 1.0),
        (1, "16:9", 1.3),
        (0, "1:1", 1.3),
        (3, "", 1.0),
    ):
        cmds.setAttr(shape + ".filmFit", fit)
        cmds.setAttr(shape + ".overscan", overscan)
        params = {"camera": camera, "light": "unlit", "max_size": 400, "aspect": aspect}
        shot = COMMANDS["get_viewport_screenshot"](params)
        width, height = shot["width"], shot["height"]
        seen = bright_centroid(shot["path"])
        plain = project(POINT, camera_view(camera, width, height, False), width, height)
        scanned = project(POINT, camera_view(camera, width, height, True), width, height)
        cases.append(
            {
                "fit": fit,
                "aspect": aspect or "view",
                "overscan": overscan,
                "size": [width, height],
                "miss_plain": miss(seen, plain),
                "miss_overscan": miss(seen, scanned),
            }
        )
    trail = COMMANDS["get_viewport_screenshot"](
        {"camera": camera, "frames": "1:4", "trail": "marker", "fit": "marker"}
    )
    return {"bridge": bmaya_bridge.__file__, "cases": cases, "trail": trail.get("trail")}


result = run()
