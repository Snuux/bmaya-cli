from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, NamedTuple

from maya import cmds

from bmaya_bridge import framespec
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers import _sampling
from bmaya_bridge.handlers._common import display_name, frame, resolve
from bmaya_bridge.handlers._qt import qt_core, qt_gui
from bmaya_bridge.handlers.motion import driver_curves, split_names
from bmaya_bridge.projection import Vec, View, inside, project

if TYPE_CHECKING:
    from PySide6.QtGui import QColor, QImage, QPainter

MAX_NODES = 4
MAX_POINTS = 600
KEY_LABELS = 16
KEYS_LISTED = 30
LABEL_NEAR = 12.0
LABEL_TRIES = 4
COLORS = ((255, 190, 0), (0, 200, 255), (255, 80, 200), (120, 230, 90))
SHADOW = (0, 0, 0, 130)

Pixel = tuple[float, float]


class Trail(NamedTuple):
    names: list[str]
    frames: list[float]
    paths: list[list[Vec]]
    keys: list[float]
    span: tuple[float, float]


def trail_nodes(raw: str) -> list[str]:
    names = split_names(raw)
    if len(names) > MAX_NODES:
        raise BridgeError(
            f"--trail takes at most {MAX_NODES} nodes, got {len(names)}.",
            hint="Pick the nodes whose arcs matter: hands, feet, the head, a prop's tip.",
        )
    return [resolve(name) for name in names]


def trail_span(requested: list[float]) -> tuple[float, float]:
    if len(requested) > 1:
        return requested[0], requested[-1]
    return (
        cmds.playbackOptions(query=True, minTime=True),
        cmds.playbackOptions(query=True, maxTime=True),
    )


def key_frames(nodes: list[str], span: tuple[float, float]) -> list[float]:
    times = set()
    for curve in driver_curves(nodes, "names"):
        for at in cmds.keyframe(curve, query=True, timeChange=True) or []:
            if span[0] - 1e-6 <= at <= span[1] + 1e-6:
                times.add(round(at, 4))
    return sorted(times)


def trail_frames(span: tuple[float, float], extra: list[float]) -> list[float]:
    first, last = span
    count = framespec.range_count(first, last, 1.0)
    step = max(1, math.ceil(count / MAX_POINTS))
    frames = framespec.parse(f"{framespec.label(first)}:{framespec.label(last)}:{step}")
    within = {round(at, 4) for at in extra if first <= at <= last}
    return sorted(set(frames) | within)


def build_trail(nodes: list[str], span: tuple[float, float], tiles: list[float]) -> Trail:
    """World paths of the nodes over the span. Moves the current time; restore it after."""
    keys = key_frames(nodes, span)
    frames = trail_frames(span, keys + list(tiles))
    poses = _sampling.sample_poses(nodes, frames, _sampling.deadline_after())
    paths = [[pose.position for pose in samples] for samples in poses]
    return Trail([display_name(node) for node in nodes], frames, paths, keys, span)


def draw_runs(painter: QPainter, pixels: list[Pixel | None], color: QColor) -> None:
    gui = qt_gui()
    core = qt_core()
    runs: list[list[Pixel]] = [[]]
    for pixel in pixels:
        if pixel is None:
            runs.append([])
        else:
            runs[-1].append(pixel)
    for pen in (gui.QPen(gui.QColor(*SHADOW), 3.5), gui.QPen(color, 1.6)):
        painter.setPen(pen)
        for run in runs:
            if len(run) > 1:
                painter.drawPolyline(gui.QPolygonF([core.QPointF(x, y) for x, y in run]))


def draw_label(painter: QPainter, x: float, y: float, text: str) -> None:
    gui = qt_gui()
    core = qt_core()
    painter.setPen(gui.QColor(0, 0, 0, 200))
    painter.drawText(core.QPointF(x + 1, y + 1), text)
    painter.setPen(gui.QColor(255, 255, 255))
    painter.drawText(core.QPointF(x, y), text)


def place_labels(painter: QPainter, labels: list[tuple[Pixel, str]], taken: list[Any]) -> None:
    """Key numbers: those on nearly the same spot share one label, the rest step aside."""
    core = qt_core()
    merged: list[tuple[Pixel, list[str]]] = []
    for pixel, text in labels:
        for anchor, texts in merged:
            if abs(anchor[0] - pixel[0]) < LABEL_NEAR and abs(anchor[1] - pixel[1]) < LABEL_NEAR:
                texts.append(text)
                break
        else:
            merged.append((pixel, [text]))
    metrics = painter.fontMetrics()
    for (x, y), texts in merged:
        text = ", ".join(texts)
        box = core.QRectF(
            x + 5, y - 5 - metrics.ascent(), metrics.horizontalAdvance(text), metrics.height()
        )
        for _ in range(LABEL_TRIES):
            if not any(box.intersects(other) for other in taken):
                break
            box.translate(0, metrics.height())
        taken.append(box)
        draw_label(painter, box.left(), box.top() + metrics.ascent(), text)


def draw_legend(painter: QPainter, names: list[str], height: int) -> None:
    gui = qt_gui()
    core = qt_core()
    metrics = painter.fontMetrics()
    row = metrics.height() + 4
    width = max(metrics.horizontalAdvance(name) for name in names) + 26
    top = height - row * len(names) - 6
    painter.fillRect(core.QRectF(4, top, width, row * len(names) + 2), gui.QColor(0, 0, 0, 150))
    for index, name in enumerate(names):
        y = top + index * row + 2
        painter.fillRect(core.QRectF(9, y + 3, 10, row - 8), gui.QColor(*COLORS[index]))
        draw_label(painter, 24, y + metrics.ascent() + 1, name)


def draw(image: QImage, trail: Trail, view: View, at: float, legend: bool) -> int:
    """Draws the paths onto the image and returns how many points fall outside it."""
    gui = qt_gui()
    core = qt_core()
    width = image.width()
    height = image.height()
    keys = set(trail.keys)
    current = round(at, 4)
    missed = 0
    taken: list[Any] = []
    painter = gui.QPainter(image)
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        for index in range(len(trail.names)):
            color = gui.QColor(*COLORS[index])
            pixels = [project(point, view, width, height) for point in trail.paths[index]]
            missed += sum(1 for p in pixels if p is None or not inside(p, width, height))
            draw_runs(painter, pixels, color)
            labels: list[tuple[Pixel, str]] = []
            for step, pixel in enumerate(pixels):
                if pixel is None:
                    continue
                spot = core.QPointF(*pixel)
                if trail.frames[step] in keys:
                    painter.setPen(gui.QPen(gui.QColor(255, 255, 255), 1.0))
                    painter.setBrush(color)
                    painter.drawEllipse(spot, 3.5, 3.5)
                    if len(keys) <= KEY_LABELS:
                        labels.append((pixel, f"{frame(trail.frames[step])}"))
                else:
                    painter.setPen(gui.QPen(core.Qt.NoPen))
                    painter.setBrush(color)
                    painter.drawEllipse(spot, 1.6, 1.6)
            place_labels(painter, labels, taken)
            if current in trail.frames:
                pixel = pixels[trail.frames.index(current)]
                if pixel is not None:
                    painter.setBrush(core.Qt.NoBrush)
                    painter.setPen(gui.QPen(gui.QColor(*SHADOW), 4.0))
                    painter.drawEllipse(core.QPointF(*pixel), 7.0, 7.0)
                    painter.setPen(gui.QPen(color, 2.2))
                    painter.drawEllipse(core.QPointF(*pixel), 7.0, 7.0)
        if legend:
            draw_legend(painter, trail.names, height)
    finally:
        painter.end()
    return missed


class TrailPainter:
    """Decorates each rendered frame with the trail, seen through `camera` at that frame."""

    def __init__(self, trail: Trail, camera: str, overscan: bool) -> None:
        self.trail = trail
        self.camera = camera
        self.overscan = overscan
        self.outside = 0

    def __call__(self, at: float, image: QImage, first: bool) -> None:
        cmds.currentTime(at, update=True)
        view = _sampling.camera_view(self.camera, image.width(), image.height(), self.overscan)
        missed = draw(image, self.trail, view, at, first)
        if first:
            self.outside = missed

    def summary(self) -> dict[str, Any]:
        return {
            "nodes": self.trail.names,
            "range": [frame(self.trail.span[0]), frame(self.trail.span[1])],
            "keys": [frame(at) for at in self.trail.keys[:KEYS_LISTED]],
            "outside": self.outside,
        }
