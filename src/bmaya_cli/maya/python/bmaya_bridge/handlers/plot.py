from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, NamedTuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
from maya import cmds

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    anim_curves,
    display_name,
    frame,
    int_param,
    key_times,
    listing,
    resolve,
    rnd,
    text_param,
)
from bmaya_bridge.handlers._qt import qt_core, qt_gui
from bmaya_bridge.handlers._sampling import linear_scale
from bmaya_bridge.handlers._timeline import clips, resolve_frames
from bmaya_bridge.handlers.animation import TIME_CURVE_TYPES
from bmaya_bridge.handlers.motion import split_names
from bmaya_bridge.handlers.viewport import new_image_path

if TYPE_CHECKING:
    from PySide6.QtGui import QPainter

MAX_CURVES = 12
MAX_SAMPLES = 1500
MAX_SPAN = 2000
SAMPLES_PER_FRAME = 4
LEFT = 64
RIGHT = 14
TOP = 8
AXIS = 26
EULER_JUMP = 180.0
BACKGROUND = (34, 34, 34)
PANEL = (44, 44, 44)
GRID = (70, 70, 70)
TEXT = (210, 210, 210)
AXIS_COLORS = {"X": (235, 85, 85), "Y": (110, 215, 95), "Z": (95, 150, 255)}
OTHER_COLORS = ((240, 190, 60), (200, 120, 255), (90, 220, 220), (255, 140, 60), (230, 230, 230))
GROUPS = ("translate", "rotate", "scale", "other")


class Plotted(NamedTuple):
    attr: str
    group: str
    times: list[float]
    values: list[float]
    key_times: list[float]
    key_values: list[float]
    angular: bool


def group_of(attr: str, curve: str) -> str:
    for group in GROUPS[:3]:
        if attr.startswith(group):
            return group
    return "rotate" if cmds.nodeType(curve) == "animCurveTA" else "other"


def wanted(node: str, attr: str, names: list[str]) -> bool:
    if not names:
        return True
    if attr in names:
        return True
    short = cmds.attributeQuery(attr.split("[")[0], node=node, shortName=True)
    return short in names or any(attr.startswith(name) for name in names)


def node_curves(path: str, names: list[str]) -> list[tuple[str, str]]:
    found = [
        (attr, curve)
        for attr, curve in anim_curves(path)
        if cmds.nodeType(curve) in TIME_CURVE_TYPES and wanted(path, attr, names)
    ]
    if not found:
        hint = "`bmaya anim NAME` lists the keyed attributes of a node."
        if names:
            raise BridgeError(
                f"'{display_name(path)}' has no keys on {', '.join(names)}.", hint=hint
            )
        raise BridgeError(f"'{display_name(path)}' has no keys to plot.", hint=hint)
    return found


def plot_range(params: dict[str, Any], curves: list[tuple[str, str]]) -> tuple[float, float]:
    chosen = resolve_frames(params)
    if chosen:
        first, last = chosen.values[0], chosen.values[-1]
    else:
        times = [at for _, curve in curves for at in key_times(curve)]
        first, last = min(times), max(times)
    if last - first < 1:
        first, last = first - 1, last + 1
    if last - first > MAX_SPAN:
        raise BridgeError(
            f"{framespan(first, last)} spans {last - first:g} frames; a plot shows {MAX_SPAN}.",
            hint="Pass --frames A:B or --clip NAME for a shorter range.",
        )
    return first, last


def framespan(first: float, last: float) -> str:
    return f"{frame(first)}:{frame(last)}"


def to_scene(value: float, curve: str) -> float:
    kind = cmds.nodeType(curve)
    if kind == "animCurveTA":
        return math.degrees(value)
    if kind == "animCurveTL":
        return value * linear_scale()
    return value


def sample(attr: str, curve: str, first: float, last: float) -> Plotted:
    selection = om.MSelectionList()
    selection.add(curve)
    animation = oma.MFnAnimCurve(selection.getDependNode(0))
    count = min(MAX_SAMPLES, int((last - first) * SAMPLES_PER_FRAME) + 1)
    times = [first + (last - first) * index / (count - 1) for index in range(count)]
    unit = om.MTime.uiUnit()
    values = [to_scene(animation.evaluate(om.MTime(at, unit)), curve) for at in times]
    keyed = key_times(curve)
    keyed_values = cmds.keyframe(curve, query=True, valueChange=True) or []
    angular = cmds.nodeType(curve) == "animCurveTA"
    return Plotted(attr, group_of(attr, curve), times, values, keyed, keyed_values, angular)


def color_for(attr: str, index: int) -> tuple[int, int, int]:
    if attr[-1:] in AXIS_COLORS and attr[:-1] in GROUPS:
        return AXIS_COLORS[attr[-1]]
    return OTHER_COLORS[index % len(OTHER_COLORS)]


def euler_jumps(curve: Plotted) -> list[float]:
    if not curve.angular:
        return []
    jumps = []
    for index in range(1, len(curve.key_times)):
        if abs(curve.key_values[index] - curve.key_values[index - 1]) > EULER_JUMP:
            jumps.append(curve.key_times[index])
    return jumps


def tick_step(span: float, width: int) -> float:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if span / step * 48 <= width:
            return float(step)
    return 1000.0


class Canvas(NamedTuple):
    left: float
    right: float
    first: float
    last: float

    def x(self, at: float) -> float:
        return self.left + (at - self.first) / (self.last - self.first) * (self.right - self.left)


def draw_panel(
    painter: QPainter,
    canvas: Canvas,
    top: float,
    height: float,
    group: str,
    curves: list[Plotted],
) -> None:
    gui = qt_gui()
    core = qt_core()
    painter.fillRect(
        core.QRectF(canvas.left, top, canvas.right - canvas.left, height), gui.QColor(*PANEL)
    )
    values = [value for curve in curves for value in curve.values]
    low, high = min(values), max(values)
    if high - low < 1e-6:
        low, high = low - 1.0, high + 1.0
    pad = (high - low) * 0.08
    low, high = low - pad, high + pad

    def y(value: float) -> float:
        return top + (1.0 - (value - low) / (high - low)) * height

    painter.setPen(gui.QPen(gui.QColor(*GRID), 1.0))
    if low < 0.0 < high:
        painter.drawLine(core.QPointF(canvas.left, y(0.0)), core.QPointF(canvas.right, y(0.0)))
    painter.setPen(gui.QColor(*TEXT))
    for value, offset in ((high - pad, 12.0), (low + pad, -2.0)):
        painter.drawText(core.QPointF(4, y(value) + offset - 4), f"{rnd(value, 2)}")
    painter.drawText(core.QPointF(4, top + height / 2 + 4), group)

    legend_x = canvas.left + 6
    for index, curve in enumerate(curves):
        color = gui.QColor(*color_for(curve.attr, index))
        points = [
            core.QPointF(canvas.x(at), y(curve.values[step])) for step, at in enumerate(curve.times)
        ]
        painter.setPen(gui.QPen(color, 1.6))
        painter.drawPolyline(gui.QPolygonF(points))
        painter.setPen(gui.QPen(gui.QColor(20, 20, 20), 1.0))
        painter.setBrush(color)
        for index_key, at in enumerate(curve.key_times):
            if canvas.first <= at <= canvas.last:
                spot = core.QPointF(canvas.x(at), y(curve.key_values[index_key]))
                painter.drawEllipse(spot, 3.2, 3.2)
        painter.setPen(gui.QPen(gui.QColor(255, 60, 60), 2.0))
        for at in euler_jumps(curve):
            if canvas.first <= at <= canvas.last:
                painter.drawLine(
                    core.QPointF(canvas.x(at), top), core.QPointF(canvas.x(at), top + 10)
                )
        painter.setPen(color)
        painter.drawText(core.QPointF(legend_x, top + 14), curve.attr)
        legend_x += painter.fontMetrics().horizontalAdvance(curve.attr) + 12
    painter.setBrush(core.Qt.NoBrush)


def draw_clips(painter: QPainter, canvas: Canvas, height: float) -> None:
    gui = qt_gui()
    core = qt_core()
    for index, clip in enumerate(clips()):
        start, end = clip["range"]
        if end < canvas.first or start > canvas.last:
            continue
        left = canvas.x(max(start, canvas.first))
        right = canvas.x(min(end, canvas.last))
        shade = gui.QColor(255, 255, 255, 10 if index % 2 else 18)
        painter.fillRect(core.QRectF(left, TOP, right - left, height - TOP - 8), shade)
        painter.setPen(gui.QColor(*TEXT))
        painter.drawText(core.QPointF(left + 4, height - 14), clip["name"])


def draw_axis(painter: QPainter, canvas: Canvas, top: float) -> None:
    gui = qt_gui()
    core = qt_core()
    step = tick_step(canvas.last - canvas.first, int(canvas.right - canvas.left))
    painter.setPen(gui.QColor(*TEXT))
    at = math.ceil(canvas.first / step) * step
    while at <= canvas.last + 1e-6:
        x = canvas.x(at)
        painter.drawLine(core.QPointF(x, top), core.QPointF(x, top + 5))
        painter.drawText(core.QPointF(x - 8, top + 18), f"{frame(at)}")
        at += step


def render_plot(
    curves: list[Plotted], first: float, last: float, width: int
) -> tuple[str, int, int]:
    gui = qt_gui()
    groups = [group for group in GROUPS if any(curve.group == group for curve in curves)]
    panel_height = max(150.0, min(280.0, width * 0.26))
    height = int(TOP + len(groups) * (panel_height + 8) + AXIS)
    image = gui.QImage(width, height, gui.QImage.Format_RGB32)
    image.fill(gui.QColor(*BACKGROUND))
    canvas = Canvas(float(LEFT), float(width - RIGHT), first, last)
    painter = gui.QPainter(image)
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        bottom = TOP + len(groups) * (panel_height + 8)
        for index, group in enumerate(groups):
            top = TOP + index * (panel_height + 8)
            members = [curve for curve in curves if curve.group == group]
            draw_panel(painter, canvas, top, panel_height, group, members)
        draw_clips(painter, canvas, bottom)
        draw_axis(painter, canvas, bottom)
    finally:
        painter.end()
    path = new_image_path("curves")
    if not image.save(path, "PNG"):
        raise BridgeError(f"Could not write the plot to {path}.")
    return path, width, height


def plot_animation(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    path = resolve(name)
    width = int_param(params, "max_size", 1000, 400, 2000)
    names = split_names(text_param(params, "attrs"))
    curves = node_curves(path, names)
    total = len(curves)
    curves = curves[:MAX_CURVES]
    first, last = plot_range(params, curves)
    plotted = [sample(attr, curve, first, last) for attr, curve in curves]
    image, width, height = render_plot(plotted, first, last, width)
    entries = []
    for curve in plotted:
        keys = [at for at in curve.key_times if first <= at <= last]
        entry: dict[str, Any] = {
            "attr": curve.attr,
            "keys": len(keys),
            "min": rnd(min(curve.values), 3),
            "max": rnd(max(curve.values), 3),
        }
        jumps = [at for at in euler_jumps(curve) if first <= at <= last]
        if jumps:
            entry["euler_jumps"] = [frame(at) for at in jumps]
        entries.append(entry)
    result: dict[str, Any] = {
        "path": image,
        "width": width,
        "height": height,
        "node": display_name(path),
        "range": [frame(first), frame(last)],
    }
    result["curves"] = {**listing(entries, MAX_CURVES), "total": total}
    return result
