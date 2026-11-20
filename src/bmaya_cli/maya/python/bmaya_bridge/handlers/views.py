from __future__ import annotations

import contextlib
import math
import os
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, NamedTuple

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import skinweights, softrender
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers import _sampling, skin, viewport
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    int_param,
    rnd,
    rnd_list,
    scene_kept,
    text_param,
)
from bmaya_bridge.handlers._qt import qt_core, qt_gui
from bmaya_bridge.handlers.framing import aim
from bmaya_bridge.handlers.mesh import target_meshes
from bmaya_bridge.projection import Vec, View, project

if TYPE_CHECKING:
    from PySide6.QtGui import QImage, QPainter

VIEWS = ("front", "back", "left", "right", "top", "bottom", "persp", "persp-back")
DEFAULT_VIEWS = ("front", "right", "top", "persp")
# Camera rotations in a Y-up world; a Z-up scene turns the whole rig.
ROTATIONS = {
    "front": (0.0, 0.0, 0.0),
    "back": (0.0, 180.0, 0.0),
    "right": (0.0, 90.0, 0.0),
    "left": (0.0, -90.0, 0.0),
    "top": (-90.0, 0.0, 0.0),
    "bottom": (90.0, 0.0, 0.0),
    "persp": (-25.0, 35.0, 0.0),
    "persp-back": (-25.0, 215.0, 0.0),
}
# The view whose camera sits on the plus side of each world axis, by up axis.
SECTION_VIEWS = {1: ("right", "top", "front"), 2: ("right", "back", "top")}
STYLES = (*softrender.STYLES, "shaded")
AXES = "xyz"
RIG = "bmaya_views"
ORTHO_MARGIN = 1.3
SMOOTH_TILE = 800
SMOOTH_WORK = 600_000
MAX_WORK = 3_000_000
DRAW_SECONDS = 30.0
LEGEND_LIMIT = 12
MEASURE_GAP = 14
ON_LIGHT = (70, 70, 70)
ON_DARK = (235, 235, 235)

Box = tuple[Vec, Vec]


class Tile(NamedTuple):
    label: str
    view: str
    section: bool


class Section(NamedTuple):
    axis: int
    position: float


def parse_views(raw: str) -> list[str]:
    if not raw:
        return list(DEFAULT_VIEWS)
    if raw.lower() == "all":
        return list(VIEWS)
    names = [name.strip().lower() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in VIEWS]
    if unknown or not names:
        raise BridgeError(
            f"Unknown view '{unknown[0] if unknown else raw}'.",
            hint="Use --views with names from: " + ", ".join(VIEWS) + ", or --views all.",
        )
    return list(dict.fromkeys(names))


def parse_style(raw: str) -> str:
    style = raw.lower() or "parts"
    if style not in STYLES:
        raise BridgeError(
            f"Unknown style '{raw}'.",
            hint="Use --style parts (a color per mesh, edges on top), silhouette, xray "
            "(see through to the inside), weights --joint J (one joint's skin weights), "
            "influence (a color per joint) or shaded (Maya's own render with materials).",
        )
    return style


def skin_rows(shape: str) -> list[skinweights.Row] | None:
    clusters = skin.clusters_of(shape)
    if not clusters:
        return None
    return skin.read_skin(clusters[0], shape).rows


def joint_named(joint: str, counts: dict[str, int]) -> str:
    if joint in counts:
        return joint
    matches = [name for name in counts if name.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == joint]
    if len(matches) == 1:
        return matches[0]
    ranked = sorted(counts, key=lambda name: (-counts[name], name))
    raise BridgeError(
        f"'{joint}' is not an influence of these meshes." if joint else "Which joint?",
        hint="--style weights shows one joint's weights: --joint "
        + (ranked[0] if ranked else "NAME")
        + ". The influences, most vertices first: "
        + ", ".join(ranked[:LEGEND_LIMIT])
        + (f" and {len(ranked) - LEGEND_LIMIT} more" if len(ranked) > LEGEND_LIMIT else "")
        + ".",
    )


def hex_color(color: softrender.Color) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def colored_parts(
    meshes: list[str], parts: list[softrender.Part], style: str, joint: str
) -> tuple[list[softrender.Part], list[dict[str, Any]], dict[str, Any]]:
    """Parts painted with skin weights, the legend for them and what the result reports."""
    rows_by_mesh = [skin_rows(shape) for shape in meshes]
    if all(rows is None for rows in rows_by_mesh):
        raise BridgeError(
            "None of these meshes is skinned.",
            hint="`bmaya skin` lists the skinned meshes; --style parts draws any mesh.",
        )
    counts: dict[str, int] = {}
    for rows in rows_by_mesh:
        for row in rows or []:
            for name in row:
                counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts, key=lambda name: (-counts[name], name))
    unskinned = any(rows is None for rows in rows_by_mesh)
    painted = []
    report: dict[str, Any] = {}
    if style == "weights":
        joint = joint_named(joint, counts)
        for index, rows in enumerate(rows_by_mesh):
            if rows is None:
                colors = [skinweights.NO_SKIN] * len(parts[index].points)
            else:
                colors = [skinweights.heat(row.get(joint, 0.0)) for row in rows]
            painted.append(parts[index]._replace(colors=colors))
        legend = [
            {"name": f"{joint} {share:g}", "color": hex_color(skinweights.heat(share))}
            for share in (1.0, 0.75, 0.5, 0.25, 0.0)
        ]
        report["joint"] = joint
    else:
        palette = {name: softrender.part_color(index) for index, name in enumerate(ranked)}
        for index, rows in enumerate(rows_by_mesh):
            if rows is None:
                colors = [skinweights.NO_SKIN] * len(parts[index].points)
            else:
                colors = [skinweights.mixed_color(row, palette) for row in rows]
            painted.append(parts[index]._replace(colors=colors))
        legend = [
            {"name": name, "color": hex_color(palette[name])} for name in ranked[:LEGEND_LIMIT]
        ]
        report["influences"] = {
            "items": legend,
            "shown": len(legend),
            "total": len(ranked),
        }
        if len(ranked) > len(softrender.PALETTE):
            report["note"] = (
                f"{len(ranked)} influences share {len(softrender.PALETTE)} colors; "
                "name fewer meshes or use --style weights --joint J for one joint"
            )
    if unskinned:
        legend = legend + [{"name": "no skin", "color": hex_color(skinweights.NO_SKIN)}]
    return painted, legend, report


def parse_section(raw: str, box: Box) -> Section | None:
    if not raw:
        return None
    name, _, value = raw.lower().partition("=")
    name = name.strip()
    if name not in AXES or len(name) != 1:
        raise BridgeError(
            f"Bad section '{raw}'.",
            hint="Pass a world axis, with a position if the middle is not what you want: "
            "--section x or --section y=12.5.",
        )
    axis = AXES.index(name)
    low, high = box[0][axis], box[1][axis]
    if not value.strip():
        return Section(axis, (low + high) / 2.0)
    try:
        position = float(value)
    except ValueError as exc:
        raise BridgeError(
            f"Bad section position '{value}'.", hint="A number, like --section x=0.5."
        ) from exc
    if not low < position < high:
        raise BridgeError(
            f"A section at {name}={rnd(position)} misses the model, which spans "
            f"{rnd(low)} to {rnd(high)} on {name}.",
            hint=f"Pick a position inside that span, or --section {name} for the middle.",
        )
    return Section(axis, position)


def shown_meshes(names: str) -> tuple[list[str], str]:
    meshes, source = target_meshes(names)
    if source == "scene":
        meshes = [mesh for mesh in meshes if _sampling.dag_path(mesh).isVisible()]
    if not meshes:
        raise BridgeError(
            "No meshes to draw.",
            hint="Name the meshes or groups to look at: bmaya views NAME. "
            "`bmaya scene --type mesh` lists them.",
        )
    return meshes, source


def read_part(shape: str, index: int, with_edges: bool) -> softrender.Part:
    mesh = om.MFnMesh(_sampling.dag_path(shape))
    points = [(point.x, point.y, point.z) for point in mesh.getPoints(om.MSpace.kWorld)]
    _, triangles = mesh.getTriangles()
    edges = []
    if with_edges:
        edges = [tuple(mesh.getEdgeVertices(edge)) for edge in range(mesh.numEdges)]
    return softrender.Part(points, list(triangles), edges, softrender.part_color(index))


def part_name(shape: str) -> str:
    return display_name(cmds.listRelatives(shape, parent=True, fullPath=True)[0])


def corners(box: Box) -> list[Vec]:
    low, high = box
    return [
        (x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])
    ]


def frame_ortho(camera: str, box: Box) -> None:
    """Tightens an orthographic camera onto what it sees of the box, not the box's sphere."""
    matrix = cmds.xform(camera, query=True, worldSpace=True, matrix=True)
    reach = 0.0
    center = [(box[0][axis] + box[1][axis]) / 2.0 for axis in range(3)]
    for corner in corners(box):
        offset = [corner[axis] - center[axis] for axis in range(3)]
        for row in (0, 4):
            along = sum(offset[axis] * matrix[row + axis] for axis in range(3))
            reach = max(reach, abs(along))
    shape = cmds.listRelatives(camera, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".orthographicWidth", max(reach, 1e-3) * 2.0 * ORTHO_MARGIN)


@contextlib.contextmanager
def view_cameras(tiles: list[Tile], box: Box) -> Iterator[dict[str, str]]:
    """A temporary camera per tile, by label, framed on the box; deleted after."""
    rig = cmds.group(empty=True, name=RIG)
    try:
        rig = cmds.ls(rig, long=True)[0]
        if _sampling.up_index() == 2:
            cmds.setAttr(rig + ".rotateX", 90.0)
        cameras = {}
        for entry in tiles:
            ortho = not entry.view.startswith("persp")
            camera = cmds.camera(orthographic=ortho)[0]
            camera = cmds.ls(cmds.parent(camera, rig)[0], long=True)[0]
            cmds.setAttr(camera + ".rotate", *ROTATIONS[entry.view], type="double3")
            for shape in cmds.listRelatives(camera, shapes=True, fullPath=True) or []:
                cmds.setAttr(shape + ".renderable", False)
            aim(camera, ([*box[0]], [*box[1]]), (1, 1))
            if ortho:
                frame_ortho(camera, box)
            cameras[entry.label] = camera
        yield cameras
    finally:
        if cmds.objExists(rig):
            cmds.delete(rig)


def to_image(canvas: softrender.Canvas, tile: int) -> QImage:
    gui = qt_gui()
    core = qt_core()
    data = canvas.pixels.tobytes()
    image = gui.QImage(
        data, canvas.width, canvas.height, canvas.width * 4, gui.QImage.Format_RGB32
    ).copy()
    if canvas.width == tile:
        return image
    return image.scaled(tile, tile, core.Qt.IgnoreAspectRatio, core.Qt.SmoothTransformation)


def length_text(value: float, unit: str) -> str:
    return f"{rnd(value, 2)} {unit}"


def draw_measures(
    painter: QPainter, view: View, box: Box, tile: int, unit: str, color: tuple[int, int, int]
) -> None:
    """Width and height of the box as this orthographic view sees it, as dimension lines."""
    gui = qt_gui()
    core = qt_core()
    pixels = [project(corner, view, tile, tile) for corner in corners(box)]
    xs = [pixel[0] for pixel in pixels if pixel is not None]
    ys = [pixel[1] for pixel in pixels if pixel is not None]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    per_pixel = (view.right - view.left) / tile
    painter.setPen(gui.QPen(gui.QColor(*color), 1.2))
    metrics = painter.fontMetrics()

    below = min(tile - metrics.height() - 4, bottom + MEASURE_GAP)
    painter.drawLine(core.QPointF(left, below), core.QPointF(right, below))
    for x in (left, right):
        painter.drawLine(core.QPointF(x, below - 4), core.QPointF(x, below + 4))
    text = length_text((right - left) * per_pixel, unit)
    painter.drawText(
        core.QPointF(
            (left + right - metrics.horizontalAdvance(text)) / 2.0, below + metrics.ascent() + 2
        ),
        text,
    )

    beside = max(metrics.height() + 4, left - MEASURE_GAP)
    painter.drawLine(core.QPointF(beside, top), core.QPointF(beside, bottom))
    for y in (top, bottom):
        painter.drawLine(core.QPointF(beside - 4, y), core.QPointF(beside + 4, y))
    text = length_text((bottom - top) * per_pixel, unit)
    painter.save()
    painter.translate(beside - 4, (top + bottom + metrics.horizontalAdvance(text)) / 2.0)
    painter.rotate(-90)
    painter.drawText(core.QPointF(0, 0), text)
    painter.restore()


def draw_legend(painter: QPainter, parts: list[dict[str, Any]], tile: int) -> None:
    gui = qt_gui()
    core = qt_core()
    metrics = painter.fontMetrics()
    row = metrics.height() + 3
    width = max(metrics.horizontalAdvance(part["name"]) for part in parts) + 28
    left = tile - width - 4
    painter.fillRect(core.QRectF(left, 4, width, row * len(parts) + 4), gui.QColor(0, 0, 0, 150))
    for index, part in enumerate(parts):
        y = 6 + index * row
        painter.fillRect(core.QRectF(left + 6, y + 2, 12, row - 5), gui.QColor(part["color"]))
        painter.setPen(gui.QColor(255, 255, 255))
        painter.drawText(core.QPointF(left + 23, y + metrics.ascent()), part["name"])


def annotate(
    image: QImage,
    view: View,
    box: Box,
    unit: str,
    color: tuple[int, int, int],
    legend: list[dict[str, Any]],
) -> None:
    gui = qt_gui()
    painter = gui.QPainter(image)
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        font = painter.font()
        font.setPixelSize(max(11, image.width() // 45))
        painter.setFont(font)
        if view.ortho:
            draw_measures(painter, view, box, image.width(), unit, color)
        if legend:
            draw_legend(painter, legend, image.width())
    finally:
        painter.end()


def cut_box(box: Box, section: Section) -> Box:
    high = list(box[1])
    high[section.axis] = section.position
    return box[0], (high[0], high[1], high[2])


def shaded_tile(camera: str, light: str, tile: int, offscreen: bool) -> QImage:
    def shoot(render: viewport.RenderFrame, view: tuple[int, int], decorate: object) -> dict:
        path = viewport.new_image_path("view")
        render(cmds.currentTime(query=True), path, (tile, tile))
        return {"path": path}

    capture = viewport.capture_offscreen if offscreen else viewport.capture_panel
    path = capture(camera, (tile, tile), light, None, shoot)["path"]
    try:
        return viewport.load_opaque(path)
    finally:
        if os.path.isfile(path):
            os.remove(path)


def clip_at(camera: str, section: Section) -> None:
    """Moves the near clip plane of a camera on the plus side of the axis onto the cut."""
    position = cmds.xform(camera, query=True, worldSpace=True, translation=True)
    shape = cmds.listRelatives(camera, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".nearClipPlane", max(1e-3, position[section.axis] - section.position))


def plan_tiles(views: list[str], section: Section | None) -> list[Tile]:
    tiles = [Tile(name, name, False) for name in views]
    if section:
        axis = AXES[section.axis]
        view = SECTION_VIEWS[_sampling.up_index()][section.axis]
        tiles.append(Tile(f"section {axis}={rnd(section.position, 3)}", view, True))
    return tiles


def get_model_views(params: dict[str, Any]) -> dict[str, Any]:
    longest = int_param(params, "max_size", 1600, 400, 2000)
    style = parse_style(text_param(params, "style"))
    views = parse_views(text_param(params, "views"))
    light = viewport.parse_light(text_param(params, "light"))
    if light and style != "shaded":
        raise BridgeError(
            "--light goes with --style shaded; the other styles are drawn without lights.",
            hint="bmaya views NAME --style shaded --light soft",
        )
    joint = text_param(params, "joint")
    if joint and style != "weights":
        raise BridgeError(
            "--joint goes with --style weights.",
            hint=f"bmaya views NAME --style weights --joint {joint}",
        )
    meshes, source = shown_meshes(text_param(params, "names"))
    section_text = text_param(params, "section")
    tile_count = len(views) + (1 if section_text else 0)
    columns = tile_count if tile_count <= 3 else math.ceil(math.sqrt(tile_count))
    tile = longest // columns
    triangles = sum(int(cmds.polyEvaluate(shape, triangle=True)) for shape in meshes)
    work = triangles * tile_count
    notes = []
    drawn = style != "shaded"
    if drawn and work > MAX_WORK:
        raise BridgeError(
            f"{triangles} triangles in {tile_count} views are too many to draw in time.",
            hint="Name fewer meshes, pass fewer --views, or use --style shaded, "
            "which Maya renders on the GPU.",
        )
    wire = drawn and style != "silhouette" and not bool_param(params, "no_wire")
    if wire and not softrender.wire_fits(triangles, tile):
        wire = False
        notes.append(
            f"edges left out: {triangles} triangles are too dense for {tile} px tiles; "
            "name fewer meshes, pass fewer --views or a bigger --size to see them"
        )
    scale = 2 if tile <= SMOOTH_TILE and work <= SMOOTH_WORK else 1

    parts = [read_part(shape, index, wire) for index, shape in enumerate(meshes)]
    painted: dict[str, Any] = {}
    legend: list[dict[str, Any]] = []
    if style in softrender.COLORED:
        parts, legend, painted = colored_parts(meshes, parts, style, joint)
    box = softrender.bounds(parts)
    if box is None:
        raise BridgeError("The meshes have no points to draw.", hint="`bmaya audit` shows why.")
    section = parse_section(section_text, box)
    tiles = plan_tiles(views, section)
    deadline = time.perf_counter() + DRAW_SECONDS

    unit = cmds.currentUnit(query=True, linear=True)
    names = list(dict.fromkeys(part_name(shape) for shape in meshes))
    if drawn and style in ("parts", "xray"):
        colors: dict[str, softrender.Color] = {}
        for index, shape in enumerate(meshes):
            colors.setdefault(part_name(shape), parts[index].color)
        legend = [
            {"name": name, "color": "#{:02x}{:02x}{:02x}".format(*colors[name])}
            for name in names[:LEGEND_LIMIT]
        ]

    cut = softrender.cut(parts, section.axis, section.position) if section and drawn else None
    offscreen = bool(cmds.about(batch=True))
    images = []
    with scene_kept(), view_cameras(tiles, box) as cameras:
        for index, entry in enumerate(tiles):
            camera = cameras[entry.label]
            seen = cut_box(box, section) if entry.section and section else box
            if drawn and time.perf_counter() > deadline:
                raise BridgeError(
                    f"Drawing stopped after {DRAW_SECONDS:g} seconds, at view {index + 1} of "
                    f"{len(tiles)}.",
                    hint="Name fewer meshes, pass fewer --views or a smaller --size, or use "
                    "--style shaded, which Maya renders on the GPU.",
                )
            if drawn:
                view = _sampling.camera_view(camera, tile, tile)
                canvas = softrender.render(
                    cut.parts if entry.section and cut else parts,
                    view,
                    tile * scale,
                    tile * scale,
                    style,
                    wire,
                    scale,
                    cut.outline if entry.section and cut else None,
                )
                image = to_image(canvas, tile)
                ink = ON_LIGHT
            else:
                if entry.section and section:
                    clip_at(camera, section)
                image = shaded_tile(camera, light, tile, offscreen)
                view = _sampling.camera_view(camera, tile, tile, overscan=not offscreen)
                ink = ON_DARK
            annotate(image, view, seen, unit, ink, legend if index == 0 else [])
            images.append((entry.label, image))

    path = viewport.new_image_path("views")
    width, height = viewport.compose_sheet(images, columns, (tile, tile), path)
    result: dict[str, Any] = {
        "path": path,
        "width": width,
        "height": height,
        "views": [entry.label for entry in tiles],
        "style": style,
        "source": source,
        "size": rnd_list(box[1][axis] - box[0][axis] for axis in range(3)),
        "unit": unit,
        "up": AXES[_sampling.up_index()],
        "meshes": len(names),
        "triangles": triangles,
    }
    if drawn:
        result["wire"] = wire
    if painted:
        note = painted.pop("note", "")
        if note:
            notes.append(note)
        result.update(painted)
    elif legend:
        result["parts"] = legend
        result["shown"] = len(legend)
        result["total"] = len(names)
    if section:
        result["section"] = {"axis": AXES[section.axis], "at": rnd(section.position)}
    if light:
        result["light"] = light
    if offscreen and not drawn:
        result["offscreen"] = True
    if notes:
        result["note"] = "; ".join(notes)
    return result
