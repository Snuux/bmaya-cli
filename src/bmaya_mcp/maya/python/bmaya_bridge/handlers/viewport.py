from __future__ import annotations

import contextlib
import math
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

from maya import cmds

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import frame, int_param, resolve, text_param

MAX_FRAMES = 12
LABEL_HEIGHT = 22
OFFSCREEN_VIEW = (1280, 720)
OFFSCREEN_BACKGROUND = (72, 72, 72)
PNG_FORMAT = 32

RenderFrame = Callable[[float, str, tuple[int, int]], None]


def qt_gui() -> ModuleType:
    try:
        from PySide6 import QtGui
    except ImportError:
        from PySide2 import QtGui
    return QtGui


def qt_core() -> ModuleType:
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore
    return QtCore


def output_dir() -> str:
    folder = os.path.join(tempfile.gettempdir(), "bmaya-mcp")
    os.makedirs(folder, exist_ok=True)
    return folder


def new_image_path(tag: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    millis = int(time.time() * 1000) % 1000
    return os.path.join(output_dir(), f"{tag}-{stamp}-{millis:03d}.png").replace("\\", "/")


def parse_frames(raw: str) -> list[float]:
    frames = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            frames.append(float(part))
        except ValueError as exc:
            raise BridgeError(
                f"Bad frame number '{part}' in frames.",
                hint="Pass frames as comma-separated numbers, for example '1,12,24'.",
            ) from exc
    return frames


def model_panel() -> str:
    focused = cmds.getPanel(withFocus=True) or ""
    if focused and cmds.getPanel(typeOf=focused) == "modelPanel":
        return focused
    for panel in cmds.getPanel(visiblePanels=True) or []:
        if cmds.getPanel(typeOf=panel) == "modelPanel":
            return panel
    raise BridgeError(
        "No visible 3D viewport to capture.",
        hint="Show a perspective or camera view in Maya, then try again.",
    )


def camera_transform(name: str) -> str:
    path = resolve(name)
    if cmds.nodeType(path) == "camera":
        return cmds.listRelatives(path, parent=True, fullPath=True)[0]
    if cmds.listRelatives(path, shapes=True, type="camera"):
        return path
    raise BridgeError(
        f"'{name}' is not a camera.",
        hint="Use get_scene_info with node_type='camera' to list cameras.",
    )


def view_size(panel: str) -> tuple[int, int]:
    import maya.api.OpenMayaUI as omui

    view = omui.M3dView.getM3dViewFromModelPanel(panel)
    width = view.portWidth()
    height = view.portHeight()
    if width <= 1 or height <= 1:
        raise BridgeError(
            "The viewport has not been drawn yet.",
            hint="Maya is still starting or its window is minimized. Try again in a few seconds.",
        )
    return width, height


def fit(width: int, height: int, longest: int) -> tuple[int, int]:
    scale = longest / float(max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def playblast_frame(panel: str, at: float, path: str, size: tuple[int, int]) -> None:
    cmds.playblast(
        editorPanelName=panel,
        frame=[at],
        format="image",
        compression="png",
        completeFilename=path,
        widthHeight=list(size),
        percent=100,
        quality=100,
        viewer=False,
        showOrnaments=False,
        offScreen=True,
        forceOverwrite=True,
        clearCache=True,
        throwOnError=True,
    )
    if not os.path.isfile(path):
        raise BridgeError(
            f"Playblast did not write frame {frame(at)}.",
            hint="Check the Script Editor for playblast errors.",
        )


def load_opaque(path: str) -> QImage:
    # Playblast PNGs have a transparent background; RGB32 drops the alpha so the background shows.
    gui = qt_gui()
    return gui.QImage(path).convertToFormat(gui.QImage.Format_RGB32)


def contact_sheet(tiles: list[tuple[float, str]], size: tuple[int, int], path: str) -> None:
    gui = qt_gui()
    core = qt_core()
    columns = math.ceil(math.sqrt(len(tiles)))
    rows = math.ceil(len(tiles) / columns)
    width, height = size
    sheet = gui.QImage(columns * width, rows * height, gui.QImage.Format_RGB32)
    sheet.fill(gui.QColor(40, 40, 40))

    painter = gui.QPainter(sheet)
    try:
        font = painter.font()
        font.setPixelSize(LABEL_HEIGHT - 6)
        font.setBold(True)
        painter.setFont(font)
        for index, (at, tile_path) in enumerate(tiles):
            x = (index % columns) * width
            y = (index // columns) * height
            painter.drawImage(core.QRect(x, y, width, height), load_opaque(tile_path))
            label = f"{frame(at)}"
            label_width = painter.fontMetrics().horizontalAdvance(label) + 12
            box = core.QRect(x, y, label_width, LABEL_HEIGHT)
            painter.fillRect(box, gui.QColor(0, 0, 0, 170))
            painter.setPen(gui.QColor(255, 255, 255))
            painter.drawText(box, core.Qt.AlignCenter, label)
    finally:
        painter.end()
    if not sheet.save(path, "PNG"):
        raise BridgeError(f"Could not write the contact sheet to {path}.")


def capture(
    render: RenderFrame, view: tuple[int, int], frames: list[float], longest: int
) -> dict[str, Any]:
    view_width, view_height = view
    if len(frames) == 1:
        path = new_image_path("viewport")
        size = fit(view_width, view_height, longest)
        render(frames[0], path, size)
        image = load_opaque(path)
        if not image.save(path, "PNG"):
            raise BridgeError(f"Could not write the screenshot to {path}.")
        return {"path": path, "width": image.width(), "height": image.height()}

    columns = math.ceil(math.sqrt(len(frames)))
    rows = math.ceil(len(frames) / columns)
    sheet_width, sheet_height = fit(view_width * columns, view_height * rows, longest)
    tile = (max(1, sheet_width // columns), max(1, sheet_height // rows))

    tiles = []
    try:
        for at in frames:
            tile_path = new_image_path(f"tile-{len(tiles)}")
            render(at, tile_path, tile)
            tiles.append((at, tile_path))
        path = new_image_path("contact")
        contact_sheet(tiles, tile, path)
    finally:
        for _, tile_path in tiles:
            if os.path.isfile(tile_path):
                os.remove(tile_path)
    return {"path": path, "width": tile[0] * columns, "height": tile[1] * rows}


def capture_panel(camera_path: str, frames: list[float], longest: int) -> dict[str, Any]:
    panel = model_panel()
    previous_camera = cmds.modelPanel(panel, query=True, camera=True)
    highlight = cmds.modelEditor(panel, query=True, selectionHiliteDisplay=True)

    def render(at: float, path: str, size: tuple[int, int]) -> None:
        playblast_frame(panel, at, path, size)

    try:
        if camera_path:
            cmds.modelPanel(panel, edit=True, camera=camera_path)
        cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=False)
        return capture(render, view_size(panel), frames, longest)
    finally:
        cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=highlight)
        if camera_path:
            cmds.modelPanel(panel, edit=True, camera=previous_camera)


def default_camera() -> str:
    if cmds.objExists("|persp"):
        return "|persp"
    cameras = cmds.ls(type="camera", long=True) or []
    if not cameras:
        raise BridgeError(
            "The scene has no camera to render from.",
            hint="Create one with execute_maya_code: cmds.camera().",
        )
    return cmds.listRelatives(cameras[0], parent=True, fullPath=True)[0]


@contextlib.contextmanager
def render_settings(prefix: str) -> Iterator[None]:
    saved = {
        "imageFilePrefix": cmds.getAttr("defaultRenderGlobals.imageFilePrefix") or "",
        "imageFormat": cmds.getAttr("defaultRenderGlobals.imageFormat"),
        "animation": cmds.getAttr("defaultRenderGlobals.animation"),
    }
    cmds.setAttr("defaultRenderGlobals.imageFilePrefix", prefix, type="string")
    cmds.setAttr("defaultRenderGlobals.imageFormat", PNG_FORMAT)
    cmds.setAttr("defaultRenderGlobals.animation", False)
    try:
        yield
    finally:
        cmds.setAttr(
            "defaultRenderGlobals.imageFilePrefix", saved["imageFilePrefix"], type="string"
        )
        cmds.setAttr("defaultRenderGlobals.imageFormat", saved["imageFormat"])
        cmds.setAttr("defaultRenderGlobals.animation", saved["animation"])


def shading_plugs(shader: str) -> list[str]:
    plugs = cmds.listConnections(shader + ".outColor", plugs=True, source=False, destination=True)
    return [plug for plug in plugs or [] if plug.endswith(".surfaceShader")]


@contextlib.contextmanager
def openpbr_as_lambert() -> Iterator[None]:
    # Without a UI the hardware renderer draws openPBRSurface, the default material, as flat
    # green. A lambert with the same base color stands in while the frames render.
    try:
        shaders = cmds.ls(type="openPBRSurface") or []
    except RuntimeError:
        shaders = []
    swapped = []
    try:
        for shader in shaders:
            plugs = shading_plugs(shader)
            if not plugs:
                continue
            stand_in = cmds.createNode("lambert", skipSelect=True)
            swapped.append((shader, stand_in, plugs))
            source = cmds.listConnections(
                shader + ".baseColor", plugs=True, source=True, destination=False
            )
            if source:
                cmds.connectAttr(source[0], stand_in + ".color")
            else:
                color = cmds.getAttr(shader + ".baseColor")[0]
                cmds.setAttr(stand_in + ".color", *color, type="double3")
            for plug in plugs:
                cmds.connectAttr(stand_in + ".outColor", plug, force=True)
        yield
    finally:
        for shader, stand_in, plugs in swapped:
            for plug in plugs:
                cmds.connectAttr(shader + ".outColor", plug, force=True)
            cmds.delete(stand_in)


def ogs_frame(camera_path: str, at: float, path: str, size: tuple[int, int]) -> None:
    cmds.currentTime(at, update=True)
    written = cmds.ogsRender(camera=camera_path, width=size[0], height=size[1], currentFrame=True)
    if not written or not os.path.isfile(written):
        raise BridgeError(
            f"The offscreen renderer did not write frame {frame(at)}.",
            hint="Maya's hardware renderer may have no usable GPU here. Use a Maya with a UI, "
            "or inspect the scene with get_scene_info and audit_mesh.",
        )
    gui = qt_gui()
    try:
        rendered = gui.QImage(written)
        image = gui.QImage(rendered.size(), gui.QImage.Format_RGB32)
        image.fill(gui.QColor(*OFFSCREEN_BACKGROUND))
        painter = gui.QPainter(image)
        try:
            painter.drawImage(0, 0, rendered)
        finally:
            painter.end()
        if not image.save(path, "PNG"):
            raise BridgeError(f"Could not write the screenshot to {path}.")
    finally:
        os.remove(written)


def capture_offscreen(camera_path: str, frames: list[float], longest: int) -> dict[str, Any]:
    prefix = new_image_path("offscreen")[: -len(".png")]

    def render(at: float, path: str, size: tuple[int, int]) -> None:
        ogs_frame(camera_path, at, path, size)

    with render_settings(prefix), openpbr_as_lambert():
        return capture(render, OFFSCREEN_VIEW, frames, longest)


def get_viewport_screenshot(params: dict[str, Any]) -> dict[str, Any]:
    longest = int_param(params, "max_size", 800, 200, 2000)
    camera = text_param(params, "camera")
    requested = parse_frames(text_param(params, "frames"))
    frames = requested[:MAX_FRAMES]
    current = cmds.currentTime(query=True)
    if not frames:
        frames = [current]

    camera_path = camera_transform(camera) if camera else ""
    offscreen = bool(cmds.about(batch=True))
    clean = not cmds.file(query=True, modified=True)
    try:
        if offscreen:
            result = capture_offscreen(camera_path or default_camera(), frames, longest)
        else:
            result = capture_panel(camera_path, frames, longest)
    finally:
        cmds.currentTime(current, update=True)
        if offscreen and clean:
            cmds.file(modified=False)

    result["frames"] = [frame(at) for at in frames]
    if len(requested) > MAX_FRAMES:
        result["dropped_frames"] = len(requested) - MAX_FRAMES
    if offscreen:
        result["offscreen"] = True
    if camera_path:
        result["camera"] = camera_path.rsplit("|", 1)[-1]
    return result
