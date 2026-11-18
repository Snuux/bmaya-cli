"""World points to image pixels through a Maya camera. Pure Python: no maya import."""

from __future__ import annotations

from typing import NamedTuple

Vec = tuple[float, float, float]


class View(NamedTuple):
    """A camera as the renderer frames it.

    `inverse` is the camera's inverse world matrix in Maya's row-vector order (16 values).
    left, right, bottom and top bound the view at distance 1 for a perspective camera, and
    in world units for an orthographic one.
    """

    inverse: tuple[float, ...]
    left: float
    right: float
    bottom: float
    top: float
    ortho: bool
    near: float


def to_camera(point: Vec, inverse: tuple[float, ...]) -> Vec:
    x, y, z = point
    m = inverse
    return (
        x * m[0] + y * m[4] + z * m[8] + m[12],
        x * m[1] + y * m[5] + z * m[9] + m[13],
        x * m[2] + y * m[6] + z * m[10] + m[14],
    )


def project(point: Vec, view: View, width: int, height: int) -> tuple[float, float] | None:
    """Pixel position of a world point, or None when it is behind the camera."""
    x, y, z = to_camera(point, view.inverse)
    if not view.ortho:
        depth = -z
        if depth <= view.near:
            return None
        x, y = x / depth, y / depth
    px = (x - view.left) / (view.right - view.left) * width
    py = (1.0 - (y - view.bottom) / (view.top - view.bottom)) * height
    return px, py


def inside(pixel: tuple[float, float], width: int, height: int) -> bool:
    return 0.0 <= pixel[0] <= width and 0.0 <= pixel[1] <= height
