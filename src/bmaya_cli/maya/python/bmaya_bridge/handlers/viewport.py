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

from bmaya_bridge import framespec
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    frame,
    int_param,
    resolve,
    scene_kept,
    select_only,
    text_param,
)
from bmaya_bridge.handlers._timeline import resolve_frames

MAX_FRAMES = 12
LABEL_HEIGHT = 22
OFFSCREEN_BACKGROUND = (72, 72, 72)
PNG_FORMAT = 32

LIGHTS = ("unlit", "soft", "scene")
LIGHTING_MODE = "hardwareRenderingGlobals.lightingMode"
LIGHTING_ALL = 1
LIGHTING_FULL_AMBIENT = 4
DISPLAY_LIGHTS = {"unlit": "flat", "soft": "all", "scene": "all"}
SOFT_AMBIENT = 0.6
SOFT_KEYS = (((-35.0, -30.0, 0.0), 0.5), ((-10.0, 40.0, 0.0), 0.25))
UNTONEMAPPED_VIEW = "Un-tone-mapped"
TEXTURE_CLAMP = "hardwareRenderingGlobals.enableTextureMaxRes"

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
    folder = os.path.join(tempfile.gettempdir(), "bmaya")
    os.makedirs(folder, exist_ok=True)
    return folder


def new_image_path(tag: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    millis = int(time.time() * 1000) % 1000
    return os.path.join(output_dir(), f"{tag}-{stamp}-{millis:03d}.png").replace("\\", "/")


def parse_aspect(raw: str) -> tuple[int, int] | None:
    if not raw:
        return None
    if raw.lower() == "render":
        return render_resolution()
    parts = raw.lower().replace("x", ":").split(":")
    try:
        width, height = (float(part) for part in parts)
    except ValueError:
        width = height = 0.0
    if width <= 0 or height <= 0:
        raise BridgeError(
            f"Bad aspect '{raw}'.",
            hint="Pass width:height like --aspect 1:1 or --aspect 16:9, or --aspect render "
            "for the scene's render resolution.",
        )
    scale = 1000.0 / max(width, height)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def parse_light(raw: str) -> str:
    light = raw.lower()
    if light and light not in LIGHTS:
        raise BridgeError(
            f"Unknown light '{raw}'.",
            hint="Use --light unlit (texture colors, no shading), soft (even studio light) "
            "or scene (the scene's lights).",
        )
    if light == "scene" and not cmds.ls(lights=True):
        raise BridgeError(
            "The scene has no lights, so --light scene would draw everything black.",
            hint="Use --light soft for a ready studio light, or add lights with bmaya exec "
            "(cmds.directionalLight, cmds.ambientLight).",
        )
    return light


def render_resolution() -> tuple[int, int]:
    width = cmds.getAttr("defaultResolution.width")
    height = cmds.getAttr("defaultResolution.height")
    return max(1, int(width)), max(1, int(height))


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
        hint="`bmaya scene --type camera` lists the cameras.",
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


def capture_panel(
    camera_path: str, frames: list[float], longest: int, aspect: tuple[int, int] | None, light: str
) -> dict[str, Any]:
    panel = model_panel()
    previous_camera = cmds.modelPanel(panel, query=True, camera=True)
    highlight = cmds.modelEditor(panel, query=True, selectionHiliteDisplay=True)
    display_lights = cmds.modelEditor(panel, query=True, displayLights=True)
    textures = cmds.modelEditor(panel, query=True, displayTextures=True)
    light_icons = cmds.modelEditor(panel, query=True, lights=True)

    def render(at: float, path: str, size: tuple[int, int]) -> None:
        playblast_frame(panel, at, path, size)

    try:
        if camera_path:
            cmds.modelPanel(panel, edit=True, camera=camera_path)
        cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=False)
        if light:
            cmds.modelEditor(
                panel, edit=True, displayLights=DISPLAY_LIGHTS[light], displayTextures=True
            )
        if light == "soft":
            cmds.modelEditor(panel, edit=True, lights=False)
        camera = cmds.modelPanel(panel, query=True, camera=True)
        with soft_lights(light, camera), viewer_transform(light):
            # Without a redraw the first playblast after a camera switch can come out empty.
            cmds.refresh(force=True)
            return capture(render, aspect or view_size(panel), frames, longest)
    finally:
        cmds.modelEditor(
            panel,
            edit=True,
            displayLights=display_lights,
            displayTextures=textures,
            lights=light_icons,
        )
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
            hint="Create one: bmaya exec 'cmds.camera()'.",
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


@contextlib.contextmanager
def soft_lights(light: str, camera: str) -> Iterator[None]:
    if light != "soft":
        yield
        return
    selection = cmds.ls(selection=True, long=True) or []
    created = []
    try:
        ambient = cmds.ambientLight(intensity=SOFT_AMBIENT, ambientShade=0)
        created.append(cmds.listRelatives(ambient, parent=True, fullPath=True)[0])
        rig = cmds.group(empty=True, name="bmaya_soft_lights")
        created.append(rig)
        for rotation, intensity in SOFT_KEYS:
            shape = cmds.directionalLight(intensity=intensity)
            cmds.setAttr(shape + ".useRayTraceShadows", False)
            cmds.setAttr(shape + ".useDepthMapShadows", False)
            key = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            key = cmds.parent(key, rig)[0]
            cmds.xform(key, rotation=rotation)
        rotation = cmds.xform(camera, query=True, worldSpace=True, rotation=True)
        cmds.xform(rig, worldSpace=True, rotation=rotation)
        select_only(selection)
        yield
    finally:
        existing = [node for node in created if cmds.objExists(node)]
        if existing:
            cmds.delete(existing)
        select_only(selection)


@contextlib.contextmanager
def lighting_mode(light: str) -> Iterator[None]:
    if not light:
        yield
        return
    saved = cmds.getAttr(LIGHTING_MODE)
    cmds.setAttr(LIGHTING_MODE, LIGHTING_FULL_AMBIENT if light == "unlit" else LIGHTING_ALL)
    try:
        yield
    finally:
        cmds.setAttr(LIGHTING_MODE, saved)


def view_for(light: str) -> str:
    prefs = cmds.colorManagementPrefs
    view = prefs(query=True, viewTransformName=True)
    if light != "unlit":
        return view
    display = prefs(query=True, displayName=True)
    untonemapped = f"{UNTONEMAPPED_VIEW} ({display})"
    if untonemapped in (prefs(query=True, viewTransformNames=True) or []):
        return untonemapped
    return view


@contextlib.contextmanager
def renderer_output(light: str) -> Iterator[None]:
    # ogsRender writes linear colors unless the renderer output transform is on, so offscreen
    # shots came out dark and overexposed. It gets the viewer's transform for the shot.
    prefs = cmds.colorManagementPrefs
    if not prefs(query=True, cmEnabled=True):
        yield
        return
    view = view_for(light)
    if view not in (prefs(query=True, outputTransformNames=True, outputTarget="renderer") or []):
        yield
        return
    enabled = prefs(query=True, outputTransformEnabled=True, outputTarget="renderer")
    name = prefs(query=True, outputTransformName=True, outputTarget="renderer")
    prefs(edit=True, outputTransformEnabled=True, outputTarget="renderer")
    prefs(edit=True, outputTransformName=view, outputTarget="renderer")
    try:
        yield
    finally:
        prefs(edit=True, outputTransformName=name, outputTarget="renderer")
        prefs(edit=True, outputTransformEnabled=enabled, outputTarget="renderer")


@contextlib.contextmanager
def viewer_transform(light: str) -> Iterator[None]:
    prefs = cmds.colorManagementPrefs
    if light != "unlit" or not prefs(query=True, cmEnabled=True):
        yield
        return
    saved = prefs(query=True, viewTransformName=True)
    view = view_for(light)
    if view == saved:
        yield
        return
    prefs(edit=True, viewTransformName=view)
    try:
        yield
    finally:
        prefs(edit=True, viewTransformName=saved)


def texture_limit() -> int:
    if cmds.getAttr("hardwareRenderingGlobals.textureMaxResMode") == 0:
        return int(cmds.getAttr("hardwareRenderingGlobals.textureAutoMaxResolution"))
    return int(cmds.getAttr("hardwareRenderingGlobals.textureMaxResolution"))


def has_textures_over(limit: int) -> bool:
    for node in cmds.ls(type="file") or []:
        size = cmds.getAttr(node + ".outSize")[0]
        if max(size) > limit:
            return True
    return False


@contextlib.contextmanager
def full_size_textures() -> Iterator[None]:
    # Without a window, the viewport's texture downscaling draws every texture above the
    # limit as flat grey. The shot turns it off and reloads; files on disk are untouched.
    if not cmds.getAttr(TEXTURE_CLAMP) or not has_textures_over(texture_limit()):
        yield
        return
    cmds.setAttr(TEXTURE_CLAMP, False)
    try:
        cmds.ogs(reloadTextures=True)
        yield
    finally:
        cmds.setAttr(TEXTURE_CLAMP, True)


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
            "or inspect the scene with `bmaya scene` and `bmaya audit`.",
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


def capture_offscreen(
    camera_path: str, frames: list[float], longest: int, aspect: tuple[int, int] | None, light: str
) -> dict[str, Any]:
    prefix = new_image_path("offscreen")[: -len(".png")]

    def render(at: float, path: str, size: tuple[int, int]) -> None:
        ogs_frame(camera_path, at, path, size)

    with contextlib.ExitStack() as stack:
        stack.enter_context(render_settings(prefix))
        stack.enter_context(openpbr_as_lambert())
        stack.enter_context(full_size_textures())
        stack.enter_context(renderer_output(light))
        stack.enter_context(lighting_mode(light))
        stack.enter_context(soft_lights(light, camera_path))
        return capture(render, aspect or render_resolution(), frames, longest)


def get_viewport_screenshot(params: dict[str, Any]) -> dict[str, Any]:
    longest = int_param(params, "max_size", 800, 200, 2000)
    camera = text_param(params, "camera")
    chosen = resolve_frames(params)
    requested = chosen.values if chosen else []
    aspect = parse_aspect(text_param(params, "aspect"))
    light = parse_light(text_param(params, "light"))
    frames = framespec.thin(requested, MAX_FRAMES)
    current = cmds.currentTime(query=True)
    if not frames:
        frames = [current]

    camera_path = camera_transform(camera) if camera else ""
    offscreen = bool(cmds.about(batch=True))
    with scene_kept():
        if offscreen:
            camera_path = camera_path or default_camera()
            result = capture_offscreen(camera_path, frames, longest, aspect, light)
        else:
            result = capture_panel(camera_path, frames, longest, aspect, light)

    result["frames"] = [frame(at) for at in frames]
    if len(requested) > MAX_FRAMES:
        result["requested"] = len(requested)
    if chosen and chosen.clip:
        result["clip"] = chosen.clip
    if light:
        result["light"] = light
    if offscreen:
        result["offscreen"] = True
    if camera:
        result["camera"] = camera_path.rsplit("|", 1)[-1]
    return result
