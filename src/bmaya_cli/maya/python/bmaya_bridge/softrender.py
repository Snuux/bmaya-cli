"""Draws meshes into a pixel buffer with a depth test. Pure Python: no maya import.

A point on the canvas is (x, y, nearness): pixels from the top left corner, and a number
that grows towards the camera and is linear across the image, so it can be interpolated.
"""

from __future__ import annotations

import math
from array import array
from typing import NamedTuple

from bmaya_bridge.projection import Vec, View, to_camera

Color = tuple[int, int, int]
Spot = tuple[float, float, float]

STYLES = ("parts", "silhouette", "xray", "weights", "influence")
COLORED = ("weights", "influence")
BACKGROUND: Color = (236, 236, 236)
EDGE: Color = (25, 25, 25)
CUT: Color = (220, 30, 30)
SILHOUETTE: Color = (20, 20, 20)
LIGHT = (0.35, 0.5, 0.8)
AMBIENT = 0.6
BACK_FACE = 0.45
NOWHERE = -1e300
DEPTH_BIAS = 0.004
PALETTE: tuple[Color, ...] = (
    (31, 119, 180),
    (255, 127, 14),
    (44, 160, 44),
    (214, 39, 40),
    (148, 103, 189),
    (140, 86, 75),
    (227, 119, 194),
    (127, 127, 127),
    (188, 189, 34),
    (23, 190, 207),
    (255, 187, 120),
    (152, 223, 138),
)


class Part(NamedTuple):
    """One mesh: world points, triangles as a flat list of point indices, edges as pairs.

    `colors`, one per point, blend across each triangle in the weights and influence styles.
    """

    points: list[Vec]
    triangles: list[int]
    edges: list[tuple[int, int]]
    color: Color
    colors: list[Color] | None = None


class Cut(NamedTuple):
    """What a section keeps: the parts on the far side of the plane and the cut outline."""

    parts: list[Part]
    outline: list[tuple[Vec, Vec]]


class Canvas:
    def __init__(self, width: int, height: int, background: Color = BACKGROUND) -> None:
        self.width = width
        self.height = height
        self.pixels = array("I", [pack(background)]) * (width * height)
        self.depth = array("d", [NOWHERE]) * (width * height)

    def color_at(self, x: int, y: int) -> Color:
        value = self.pixels[y * self.width + x]
        return (value >> 16) & 255, (value >> 8) & 255, value & 255

    def covered(self) -> int:
        return sum(1 for value in self.depth if value != NOWHERE)


def pack(color: Color) -> int:
    return 0xFF000000 | (color[0] << 16) | (color[1] << 8) | color[2]


def mix(color: Color, other: Color, share: float) -> Color:
    return (
        int(round(color[0] + (other[0] - color[0]) * share)),
        int(round(color[1] + (other[1] - color[1]) * share)),
        int(round(color[2] + (other[2] - color[2]) * share)),
    )


def scaled(color: Color, factor: float) -> Color:
    return (
        min(255, int(color[0] * factor)),
        min(255, int(color[1] * factor)),
        min(255, int(color[2] * factor)),
    )


def part_color(index: int) -> Color:
    return PALETTE[index % len(PALETTE)]


def fill_triangle(canvas: Canvas, a: Spot, b: Spot, c: Spot, value: int) -> None:
    if a[1] > b[1]:
        a, b = b, a
    if b[1] > c[1]:
        b, c = c, b
    if a[1] > b[1]:
        a, b = b, a
    ax, ay, an = a
    bx, by, bn = b
    cx, cy, cn = c
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(area) < 1e-12:
        return
    slope_x = ((bn - an) * (cy - ay) - (cn - an) * (by - ay)) / area
    slope_y = ((cn - an) * (bx - ax) - (bn - an) * (cx - ax)) / area
    width = canvas.width
    pixels = canvas.pixels
    depth = canvas.depth
    top = max(0, math.ceil(ay - 0.5))
    bottom = min(canvas.height, math.ceil(cy - 0.5))
    long_slope = (cx - ax) / (cy - ay)
    upper_slope = (bx - ax) / (by - ay) if by > ay else 0.0
    lower_slope = (cx - bx) / (cy - by) if cy > by else 0.0
    for y in range(top, bottom):
        middle = y + 0.5
        left = ax + (middle - ay) * long_slope
        if middle < by:
            right = ax + (middle - ay) * upper_slope
        else:
            right = bx + (middle - by) * lower_slope
        if left > right:
            left, right = right, left
        start = max(0, math.ceil(left - 0.5))
        end = min(width, math.ceil(right - 0.5))
        if start >= end:
            continue
        nearness = an + (start + 0.5 - ax) * slope_x + (middle - ay) * slope_y
        first = y * width + start
        for index in range(first, first + end - start):
            if nearness > depth[index]:
                depth[index] = nearness
                pixels[index] = value
            nearness += slope_x


def fill_blended(canvas: Canvas, corners: list[tuple[Spot, Color]]) -> None:
    """A triangle whose corner colors blend across it."""
    corners = sorted(corners, key=lambda corner: corner[0][1])
    (ax, ay, an), first = corners[0]
    (bx, by, bn), second = corners[1]
    (cx, cy, cn), third = corners[2]
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(area) < 1e-12:
        return

    def slopes(at_a: float, at_b: float, at_c: float) -> tuple[float, float]:
        along = ((at_b - at_a) * (cy - ay) - (at_c - at_a) * (by - ay)) / area
        down = ((at_c - at_a) * (bx - ax) - (at_b - at_a) * (cx - ax)) / area
        return along, down

    near_x, near_y = slopes(an, bn, cn)
    red_x, red_y = slopes(first[0], second[0], third[0])
    green_x, green_y = slopes(first[1], second[1], third[1])
    blue_x, blue_y = slopes(first[2], second[2], third[2])
    width = canvas.width
    pixels = canvas.pixels
    depth = canvas.depth
    top = max(0, math.ceil(ay - 0.5))
    bottom = min(canvas.height, math.ceil(cy - 0.5))
    long_slope = (cx - ax) / (cy - ay)
    upper_slope = (bx - ax) / (by - ay) if by > ay else 0.0
    lower_slope = (cx - bx) / (cy - by) if cy > by else 0.0
    for y in range(top, bottom):
        middle = y + 0.5
        left = ax + (middle - ay) * long_slope
        if middle < by:
            right = ax + (middle - ay) * upper_slope
        else:
            right = bx + (middle - by) * lower_slope
        if left > right:
            left, right = right, left
        start = max(0, math.ceil(left - 0.5))
        end = min(width, math.ceil(right - 0.5))
        if start >= end:
            continue
        dx = start + 0.5 - ax
        dy = middle - ay
        nearness = an + dx * near_x + dy * near_y
        red = first[0] + dx * red_x + dy * red_y
        green = first[1] + dx * green_x + dy * green_y
        blue = first[2] + dx * blue_x + dy * blue_y
        row = y * width + start
        for index in range(row, row + end - start):
            if nearness > depth[index]:
                depth[index] = nearness
                pixels[index] = 0xFF000000 | (int(red) << 16) | (int(green) << 8) | int(blue)
            nearness += near_x
            red += red_x
            green += green_x
            blue += blue_x


def draw_line(
    canvas: Canvas, a: Spot, b: Spot, value: int, thickness: int, bias: float | None
) -> None:
    """A line of `thickness` pixels. With a bias it is hidden behind nearer surfaces."""
    width = canvas.width
    height = canvas.height
    pixels = canvas.pixels
    depth = canvas.depth
    span_x = b[0] - a[0]
    span_y = b[1] - a[1]
    steps = int(max(abs(span_x), abs(span_y))) + 1
    if steps > 4 * (width + height):
        return
    step_x = span_x / steps
    step_y = span_y / steps
    step_n = (b[2] - a[2]) / steps
    across = 1 if abs(span_x) >= abs(span_y) else 0
    x, y, nearness = a
    for _ in range(steps + 1):
        column = int(x)
        row = int(y)
        for offset in range(thickness):
            px = column if across else column + offset
            py = row + offset if across else row
            if 0 <= px < width and 0 <= py < height:
                index = py * width + px
                if bias is None or nearness + bias >= depth[index]:
                    pixels[index] = value
        x += step_x
        y += step_y
        nearness += step_n


def place(point: Vec, view: View, width: int, height: int) -> tuple[Spot, Vec] | None:
    """Canvas spot and camera-space position of a world point; None behind the camera."""
    camera = to_camera(point, view.inverse)
    x, y, z = camera
    if view.ortho:
        nearness = z
    else:
        distance = -z
        if distance <= view.near:
            return None
        x, y = x / distance, y / distance
        nearness = 1.0 / distance
    px = (x - view.left) / (view.right - view.left) * width
    py = (1.0 - (y - view.bottom) / (view.top - view.bottom)) * height
    return (px, py, nearness), camera


def brightness(a: Vec, b: Vec, c: Vec, front: bool) -> float:
    """How bright a face seen from the camera is: brighter when it faces the light."""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-20:
        return 1.0
    facing = (nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / length
    if not front:
        return BACK_FACE * (AMBIENT + (1.0 - AMBIENT) * max(0.0, -facing))
    return AMBIENT + (1.0 - AMBIENT) * max(0.0, facing)


def crossing(a: Vec, b: Vec, axis: int, position: float) -> Vec:
    share = (position - a[axis]) / (b[axis] - a[axis])
    return (
        a[0] + (b[0] - a[0]) * share,
        a[1] + (b[1] - a[1]) * share,
        a[2] + (b[2] - a[2]) * share,
    )


def cut_part(part: Part, axis: int, position: float, outline: list[tuple[Vec, Vec]]) -> Part:
    points = list(part.points)
    colors = list(part.colors) if part.colors is not None else None
    kept = [point[axis] <= position for point in points]
    triangles: list[int] = []

    def added(near: int, far: int) -> int:
        if colors is not None:
            span = points[far][axis] - points[near][axis]
            share = (position - points[near][axis]) / span if span else 0.0
            colors.append(mix(colors[near], colors[far], share))
        points.append(crossing(points[near], points[far], axis, position))
        return len(points) - 1

    source = part.triangles
    for start in range(0, len(source), 3):
        corners = source[start : start + 3]
        inside = [index for index in corners if kept[index]]
        if len(inside) == 3:
            triangles.extend(corners)
            continue
        if not inside:
            continue
        # Rotated so the corner that stands alone on its side of the plane comes first.
        alone_kept = len(inside) == 1
        lead = next(i for i in range(3) if kept[corners[i]] == alone_kept)
        first, second, third = corners[lead], corners[(lead + 1) % 3], corners[(lead + 2) % 3]
        to_second = added(first, second)
        to_third = added(first, third)
        outline.append((points[to_second], points[to_third]))
        if alone_kept:
            triangles.extend((first, to_second, to_third))
        else:
            triangles.extend((to_second, second, third))
            triangles.extend((to_second, third, to_third))

    edges: list[tuple[int, int]] = []
    for one, two in part.edges:
        if kept[one] and kept[two]:
            edges.append((one, two))
        elif kept[one] or kept[two]:
            near, far = (one, two) if kept[one] else (two, one)
            edges.append((near, added(near, far)))
    return Part(points, triangles, edges, part.color, colors)


def cut(parts: list[Part], axis: int, position: float) -> Cut:
    """The parts with everything above `position` on the axis removed."""
    outline: list[tuple[Vec, Vec]] = []
    return Cut([cut_part(part, axis, position, outline) for part in parts], outline)


def bounds(parts: list[Part]) -> tuple[Vec, Vec] | None:
    low = [math.inf, math.inf, math.inf]
    high = [-math.inf, -math.inf, -math.inf]
    for part in parts:
        for point in part.points:
            for axis in range(3):
                if point[axis] < low[axis]:
                    low[axis] = point[axis]
                if point[axis] > high[axis]:
                    high[axis] = point[axis]
    if low[0] > high[0]:
        return None
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def triangle_count(parts: list[Part]) -> int:
    return sum(len(part.triangles) // 3 for part in parts)


def wire_fits(triangles: int, tile: int) -> bool:
    """Whether edges stay readable: past this they merge into a black blob."""
    return triangles <= tile * tile // 16


def render(
    parts: list[Part],
    view: View,
    width: int,
    height: int,
    style: str = "parts",
    wire: bool = True,
    thickness: int = 1,
    outline: list[tuple[Vec, Vec]] | None = None,
) -> Canvas:
    canvas = Canvas(width, height)
    placed = []
    low = math.inf
    high = -math.inf
    for part in parts:
        spots = [place(point, view, width, height) for point in part.points]
        placed.append(spots)
        for spot in spots:
            if spot is not None:
                low = min(low, spot[0][2])
                high = max(high, spot[0][2])
    bias = (high - low) * DEPTH_BIAS if high > low else 0.0

    for index, part in enumerate(parts):
        spots = placed[index]
        triangles = part.triangles
        flat = pack(SILHOUETTE if style == "silhouette" else mix(part.color, BACKGROUND, 0.72))
        colors = part.colors or []
        blended = style in COLORED and len(colors) == len(part.points)
        for start in range(0, len(triangles), 3):
            a = spots[triangles[start]]
            b = spots[triangles[start + 1]]
            c = spots[triangles[start + 2]]
            if a is None or b is None or c is None:
                continue
            value = flat
            if style == "parts" or blended:
                (ax, ay, _), (bx, by, _), (cx, cy, _) = a[0], b[0], c[0]
                front = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay) < 0
                light = brightness(a[1], b[1], c[1], front)
                if blended:
                    corners = [
                        (a[0], scaled(colors[triangles[start]], light)),
                        (b[0], scaled(colors[triangles[start + 1]], light)),
                        (c[0], scaled(colors[triangles[start + 2]], light)),
                    ]
                    fill_blended(canvas, corners)
                    continue
                value = pack(scaled(part.color, light))
            fill_triangle(canvas, a[0], b[0], c[0], value)

    if wire and style != "silhouette":
        if style == "xray":
            for index, part in enumerate(parts):
                hidden = pack(mix(part.color, BACKGROUND, 0.35))
                draw_edges(canvas, part, placed[index], hidden, 1, None)
        for index, part in enumerate(parts):
            color = scaled(part.color, 0.4) if style == "xray" else EDGE
            draw_edges(canvas, part, placed[index], pack(color), thickness, bias)

    for start_point, end_point in outline or []:
        a = place(start_point, view, width, height)
        b = place(end_point, view, width, height)
        if a is not None and b is not None:
            draw_line(canvas, a[0], b[0], pack(CUT), thickness + 1, None)
    return canvas


def draw_edges(
    canvas: Canvas,
    part: Part,
    spots: list[tuple[Spot, Vec] | None],
    value: int,
    thickness: int,
    bias: float | None,
) -> None:
    for one, two in part.edges:
        a = spots[one]
        b = spots[two]
        if a is not None and b is not None:
            draw_line(canvas, a[0], b[0], value, thickness, bias)
