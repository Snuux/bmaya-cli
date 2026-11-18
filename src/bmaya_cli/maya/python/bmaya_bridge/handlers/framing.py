from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import framespec
from bmaya_bridge.handlers import _sampling
from bmaya_bridge.handlers._common import resolve
from bmaya_bridge.handlers.motion import split_names

FIT_SAMPLES = 60
FIT_POINTS = 500
MARGIN = 1.1
FIT_CAMERA = "bmaya_fit_cam"

Box = tuple[list[float], list[float]]


def fit_nodes(raw: str) -> list[str]:
    return [resolve(name) for name in split_names(raw)]


def fit_frames(tiles: list[float], requested: list[float]) -> list[float]:
    spread = framespec.thin(requested, FIT_SAMPLES) if requested else []
    return sorted(set(tiles) | set(spread))


def points_of(nodes: list[str]) -> list[str]:
    below = cmds.listRelatives(nodes, allDescendents=True, fullPath=True, type="transform") or []
    return (nodes + below)[:FIT_POINTS]


def grow(box: Box | None, low: list[float], high: list[float]) -> Box:
    if box is None:
        return list(low), list(high)
    return (
        [min(box[0][axis], low[axis]) for axis in range(3)],
        [max(box[1][axis], high[axis]) for axis in range(3)],
    )


def bounds(nodes: list[str], frames: list[float]) -> Box:
    """World box around the nodes, their geometry and their joints over the frames.

    Moves the current time; the caller restores it.
    """
    points = points_of(nodes)
    box: Box | None = None
    for at in frames:
        cmds.currentTime(at, update=True)
        found = cmds.exactWorldBoundingBox(nodes)
        if found[3] >= found[0]:
            box = grow(box, found[:3], found[3:])
        for point in points:
            spot = cmds.xform(point, query=True, worldSpace=True, translation=True)
            box = grow(box, spot, spot)
    assert box is not None
    return box


def lens(camera: str) -> om.MFnCamera:
    path = _sampling.dag_path(camera)
    path.extendToShape()
    return om.MFnCamera(path)


def aim(camera: str, box: Box, shape: tuple[int, int]) -> None:
    low, high = box
    center = [(low[axis] + high[axis]) / 2.0 for axis in range(3)]
    radius = max(0.5 * math.dist(low, high), 1e-3)
    matrix = cmds.xform(camera, query=True, worldSpace=True, matrix=True)
    back = om.MVector(matrix[8], matrix[9], matrix[10]).normal()
    camera_lens = lens(camera)
    camera_shape = camera_lens.fullPathName()
    aspect = shape[0] / float(shape[1])
    left, right, bottom, top = camera_lens.getViewingFrustum(aspect, False, False, True)
    if camera_lens.isOrtho():
        seen = min(right - left, top - bottom) / camera_lens.orthoWidth
        cmds.setAttr(camera_shape + ".orthographicWidth", 2.0 * radius * MARGIN / seen)
        distance = radius * 4.0
    else:
        near = camera_lens.nearClippingPlane
        half = min(right, -left, top, -bottom) / near
        distance = radius * MARGIN / math.sin(math.atan(half))
    position = [center[axis] + back[axis] * distance for axis in range(3)]
    cmds.xform(camera, worldSpace=True, translation=position)
    cmds.setAttr(camera_shape + ".centerOfInterest", distance)
    far = cmds.getAttr(camera_shape + ".farClipPlane")
    cmds.setAttr(camera_shape + ".farClipPlane", max(far, (distance + radius) * 2.0))
    if not camera_lens.isOrtho():
        near_clip = cmds.getAttr(camera_shape + ".nearClipPlane")
        cmds.setAttr(camera_shape + ".nearClipPlane", min(near_clip, (distance - radius) * 0.5))


@contextlib.contextmanager
def fitted_camera(
    camera: str, nodes: list[str], frames: list[float], shape: tuple[int, int]
) -> Iterator[str]:
    """A copy of `camera` that keeps the nodes in view on every frame; deleted after."""
    box = bounds(nodes, frames)
    copy = cmds.duplicate(camera, name=FIT_CAMERA, returnRootsOnly=True)[0]
    try:
        copy = cmds.ls(copy, long=True)[0]
        children = cmds.listRelatives(copy, children=True, fullPath=True) or []
        extras = [child for child in children if cmds.nodeType(child) != "camera"]
        if extras:
            cmds.delete(extras)
        for attr in ("translate", "rotate", "scale"):
            for axis in "XYZ":
                cmds.setAttr(f"{copy}.{attr}{axis}", lock=False)
        # A copy of a renderable camera such as persp makes two renderable cameras, and then
        # ogsRender writes into a per-camera subfolder instead of the path it was given.
        for camera_shape in cmds.listRelatives(copy, shapes=True, fullPath=True) or []:
            cmds.setAttr(camera_shape + ".renderable", False)
        aim(copy, box, shape)
        yield copy
    finally:
        if cmds.objExists(copy):
            cmds.delete(copy)
