from __future__ import annotations

import math
import os
import tempfile
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

from maya import cmds

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import frame, int_param, resolve, text_param

MAX_FRAMES = 12
LABEL_HEIGHT = 22


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
    # Playblast PNGs have a fully transparent background; without this the viewport
    # background vanishes in the contact sheet and in image viewers.
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


def capture(panel: str, frames: list[float], longest: int) -> dict[str, Any]:
    view_width, view_height = view_size(panel)
    if len(frames) == 1:
        path = new_image_path("viewport")
        size = fit(view_width, view_height, longest)
        playblast_frame(panel, frames[0], path, size)
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
            playblast_frame(panel, at, tile_path, tile)
            tiles.append((at, tile_path))
        path = new_image_path("contact")
        contact_sheet(tiles, tile, path)
    finally:
        for _, tile_path in tiles:
            if os.path.isfile(tile_path):
                os.remove(tile_path)
    return {"path": path, "width": tile[0] * columns, "height": tile[1] * rows}


def get_viewport_screenshot(params: dict[str, Any]) -> dict[str, Any]:
    if cmds.about(batch=True):
        raise BridgeError(
            "Viewport screenshots need Maya with a UI; this session runs in batch mode.",
            hint="Run the bridge in an interactive Maya, or inspect the scene with "
            "get_scene_info and get_object_info instead.",
        )
    longest = int_param(params, "max_size", 800, 200, 2000)
    camera = text_param(params, "camera")
    requested = parse_frames(text_param(params, "frames"))
    frames = requested[:MAX_FRAMES]
    current = cmds.currentTime(query=True)
    if not frames:
        frames = [current]

    panel = model_panel()
    camera_path = camera_transform(camera) if camera else ""
    previous_camera = cmds.modelPanel(panel, query=True, camera=True)
    highlight = cmds.modelEditor(panel, query=True, selectionHiliteDisplay=True)

    try:
        if camera_path:
            cmds.modelPanel(panel, edit=True, camera=camera_path)
        cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=False)
        result = capture(panel, frames, longest)
    finally:
        cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=highlight)
        if camera_path:
            cmds.modelPanel(panel, edit=True, camera=previous_camera)
        cmds.currentTime(current, update=True)

    result["frames"] = [frame(at) for at in frames]
    if len(requested) > MAX_FRAMES:
        result["dropped_frames"] = len(requested) - MAX_FRAMES
    if camera_path:
        result["camera"] = camera_path.rsplit("|", 1)[-1]
    return result
