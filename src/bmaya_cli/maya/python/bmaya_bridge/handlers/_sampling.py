from __future__ import annotations

import time
from typing import NamedTuple

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import frame
from bmaya_bridge.projection import View

SAMPLE_SECONDS = 30.0

Vec = tuple[float, float, float]
Quat = tuple[float, float, float, float]


class Pose(NamedTuple):
    position: Vec
    rotation: Quat


def dag_path(node: str) -> om.MDagPath:
    selection = om.MSelectionList()
    try:
        selection.add(node)
        return selection.getDagPath(0)
    except RuntimeError as exc:
        raise BridgeError(
            f"'{node}' is not a DAG node, so it has no place in the world.",
            hint="Name transforms or joints; `bmaya anim NAME` shows the keys of other nodes.",
        ) from exc


def pose_of(matrix: om.MMatrix) -> Pose:
    transform = om.MTransformationMatrix(matrix)
    position = transform.translation(om.MSpace.kWorld)
    rotation = transform.rotation(asQuaternion=True)
    return Pose(
        (position.x, position.y, position.z),
        (rotation.x, rotation.y, rotation.z, rotation.w),
    )


def deadline_after(seconds: float = SAMPLE_SECONDS) -> float:
    return time.perf_counter() + seconds


def check_deadline(deadline: float, at: float, frames: list[float]) -> None:
    if time.perf_counter() <= deadline:
        return
    raise BridgeError(
        f"Sampling stopped at frame {frame(at)} of {frame(frames[0])}:{frame(frames[-1])} "
        f"after {SAMPLE_SECONDS:g} seconds.",
        hint="Use a step (--frames A:B:2), a shorter range or a clip, or name fewer nodes.",
    )


def sample_poses(nodes: list[str], frames: list[float], deadline: float) -> list[list[Pose]]:
    """World poses per node and frame, read the way playback shows them (IK, constraints).

    The caller restores the current time, normally through `scene_kept`.
    """
    paths = [dag_path(node) for node in nodes]
    poses: list[list[Pose]] = [[] for _ in nodes]
    for at in frames:
        check_deadline(deadline, at, frames)
        cmds.currentTime(at, update=True)
        for index, path in enumerate(paths):
            poses[index].append(pose_of(path.inclusiveMatrix()))
    return poses


def sample_plugs(plugs: list[str], frames: list[float], deadline: float) -> list[list[float]]:
    values: list[list[float]] = [[] for _ in plugs]
    for at in frames:
        check_deadline(deadline, at, frames)
        cmds.currentTime(at, update=True)
        for index, plug in enumerate(plugs):
            value = cmds.getAttr(plug)
            if not isinstance(value, (int, float)):
                raise BridgeError(
                    f"{plug} is not a number, so it has no values to list.",
                    hint="Pass number attributes to --attrs, like rx, translateY or ik.",
                )
            values[index].append(float(value))
    return values


def camera_view(camera: str, width: int, height: int, overscan: bool = False) -> View:
    """How `camera` frames an image of width x height at the current time.

    An offscreen render ignores the camera's overscan; pass overscan=True for a view that
    draws it.
    """
    transform = dag_path(camera)
    shape = om.MDagPath(transform)
    shape.extendToShape()
    lens = om.MFnCamera(shape)
    left, right, bottom, top = lens.getViewingFrustum(width / float(height), overscan, False, True)
    near = lens.nearClippingPlane
    ortho = lens.isOrtho()
    if not ortho:
        left, right, bottom, top = left / near, right / near, bottom / near, top / near
    inverse = transform.inclusiveMatrixInverse()
    return View(tuple(inverse[i] for i in range(16)), left, right, bottom, top, ortho, near)


def linear_scale() -> float:
    """How many scene units one centimetre is."""
    return om.MDistance(1.0, om.MDistance.kCentimeters).asUnits(om.MDistance.uiUnit())


def up_index() -> int:
    return 2 if cmds.upAxis(query=True, axis=True) == "z" else 1
