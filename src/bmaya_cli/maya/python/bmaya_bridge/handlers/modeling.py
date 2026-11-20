from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import maya.api.OpenMaya as om
from maya import cmds, mel

from bmaya_bridge import meshcheck
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    float_param,
    int_param,
    rnd,
    select_only,
    text_param,
)
from bmaya_bridge.handlers.mesh import (
    audit_one,
    dag_path,
    mark_stats,
    read_faces,
    read_mark,
    read_points,
    single_mesh,
    size_of,
    take_mark,
    transform_of,
)

REMESH_TARGETS = ("tris", "quads")
RETOPO_MAX_TRIS = 200000
RETOPO_MIN_FACES = 50
REMESH_MAX_TRIS = 500000
# polyRemesh makes about this many triangles per unit of area over the edge length squared.
REMESH_DENSITY = 8.5
MERGE_SHARE = 1e-5
CLEANUP_TOLERANCE = "1e-05"
REDUCE_BY_PERCENT = 0
REDUCE_BY_TRIANGLES = 2
CONFORM = 2
REVERSE = 0


def edited(name: str, command: str, edit: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """Runs one mesh edit and reports the mesh before and after it.

    The mesh is marked first, so `bmaya diff NAME` still answers when the reply is lost.
    """
    shape = single_mesh(name)
    transform = transform_of(shape)
    before = take_mark(shape)
    selection = cmds.ls(selection=True, long=True) or []
    try:
        used = edit(shape)
    finally:
        select_only([node for node in selection if cmds.objExists(node)])
    shape = single_mesh(transform)
    result: dict[str, Any] = {"name": before.name}
    result.update(used)
    result["before"] = mark_stats(before)
    result["after"] = mark_stats(read_mark(shape))
    result["issues"] = audit_one(shape)["issues"]
    result["next"] = (
        f"`bmaya views {name}` shows the result, `bmaya undo` takes this {command} back"
    )
    return result


def triangle_count(shape: str) -> int:
    return int(cmds.polyEvaluate(shape, triangle=True))


def remesh_mesh(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    target = text_param(params, "to").lower() or "tris"
    if target not in REMESH_TARGETS:
        raise BridgeError(
            f"Unknown remesh target '{target}'.",
            hint="--to tris gives even triangles, --to quads a clean quad mesh.",
        )
    edge = float_param(params, "edge")
    faces = int_param(params, "faces", 0, RETOPO_MIN_FACES, 1000000)
    if target == "tris" and faces:
        raise BridgeError(
            "--faces goes with --to quads.", hint="For triangles give the edge length: --edge 0.5."
        )
    if target == "quads" and edge is not None:
        raise BridgeError(
            "--edge goes with --to tris.", hint="For quads give the face count: --faces 800."
        )

    def to_tris(shape: str) -> dict[str, Any]:
        mesh = om.MFnMesh(dag_path(shape))
        area, _ = meshcheck.measure(read_points(mesh, om.MSpace.kWorld), read_faces(mesh))
        if edge is not None and edge <= 0:
            raise BridgeError(
                "The edge length must be above zero.", hint="Like --edge 0.5, in scene units."
            )
        if area <= 0:
            raise BridgeError("The mesh has no surface to remesh.", hint="`bmaya audit` shows why.")
        # Without --edge the mesh keeps about the triangle count it has.
        same_density = math.sqrt(REMESH_DENSITY * area / max(1, triangle_count(shape)))
        length = edge if edge is not None else same_density
        expected = int(REMESH_DENSITY * area / (length * length))
        if expected > REMESH_MAX_TRIS:
            shortest = math.sqrt(REMESH_DENSITY * area / REMESH_MAX_TRIS)
            raise BridgeError(
                f"An edge of {rnd(length, 6)} would make about {expected} triangles; "
                f"the most is {REMESH_MAX_TRIS}.",
                hint=f"The mesh's surface is {rnd(area)} square units: pass --edge "
                f"{rnd(shortest, 4)} or more. `bmaya object {name}` gives its size.",
            )
        cmds.polyRemesh(
            shape,
            maxEdgeLength=length,
            useRelativeValues=False,
            worldSpace=True,
            constructionHistory=False,
        )
        return {"to": "tris", "edge": rnd(length)}

    def to_quads(shape: str) -> dict[str, Any]:
        tris = triangle_count(shape)
        if tris > RETOPO_MAX_TRIS:
            raise BridgeError(
                f"{tris} triangles are too many to retopologize in time; "
                f"the most is {RETOPO_MAX_TRIS}.",
                hint=f"Thin it first: bmaya reduce {name} --tris {RETOPO_MAX_TRIS // 2}.",
            )
        wanted = faces or max(RETOPO_MIN_FACES, tris // 2)
        cmds.polyRetopo(
            shape,
            targetFaceCount=wanted,
            preserveHardEdges=bool_param(params, "keep_hard_edges"),
            replaceOriginal=True,
            constructionHistory=False,
        )
        return {"to": "quads", "faces_asked": wanted}

    return edited(name, "remesh", to_tris if target == "tris" else to_quads)


def reduce_mesh(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    tris = int_param(params, "tris", 0, 4, 100000000)
    percent = float_param(params, "percent")
    if bool(tris) == (percent is not None):
        raise BridgeError(
            "Give the target as --tris or as --percent, one of them.",
            hint=f"bmaya reduce {name or 'NAME'} --tris 2000 for a triangle budget, or "
            "--percent 50 to keep half of the triangles.",
        )
    if percent is not None and not 0 < percent < 100:
        raise BridgeError(
            f"--percent {rnd(percent)} is not between 0 and 100.",
            hint="It is the share of triangles to keep: --percent 50 halves the mesh.",
        )

    def reduce(shape: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "version": 1,
            "keepBorder": bool_param(params, "keep_borders"),
            "keepMapBorder": bool_param(params, "keep_uvs"),
            "keepHardEdge": bool_param(params, "keep_hard_edges"),
            "keepQuadsWeight": 1.0 if bool_param(params, "keep_quads") else 0.0,
            "replaceOriginal": True,
            "constructionHistory": False,
        }
        if tris:
            have = triangle_count(shape)
            if tris >= have:
                raise BridgeError(
                    f"The mesh already has {have} triangles, not more than the {tris} asked.",
                    hint="Pass a lower --tris, or --percent.",
                )
            cmds.polyReduce(shape, termination=REDUCE_BY_TRIANGLES, triangleCount=tris, **options)
            return {"tris_asked": tris}
        cmds.polyReduce(shape, termination=REDUCE_BY_PERCENT, percentage=100.0 - percent, **options)
        return {"percent": rnd(percent)}

    return edited(name, "reduce", reduce)


def is_inside_out(shape: str) -> bool:
    mesh = om.MFnMesh(dag_path(shape))
    wound = meshcheck.winding(read_points(mesh, om.MSpace.kObject), read_faces(mesh))
    return wound.inward * 2 > wound.shells


def cleanup_mesh(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    merge = float_param(params, "merge")
    if merge is not None and merge < 0:
        raise BridgeError(
            "--merge must be a distance above zero.", hint="Like --merge 0.001, in scene units."
        )
    ngons = "1" if bool_param(params, "ngons") else "0"

    def cleanup(shape: str) -> dict[str, Any]:
        # The flag only changes how Maya draws the faces; files get them as stored.
        opposite = bool(cmds.getAttr(shape + ".opposite"))
        if opposite:
            cmds.setAttr(shape + ".opposite", False)
        mesh = om.MFnMesh(dag_path(shape))
        size = max(size_of(read_points(mesh, om.MSpace.kWorld)))
        distance = merge if merge is not None else max(1e-9, size * MERGE_SHARE)
        cmds.polyMergeVertex(shape, distance=distance, texture=False, constructionHistory=False)
        transform = transform_of(shape)
        # Maya's Cleanup works on the selection; `edited` puts the caller's selection back.
        cmds.select(transform, replace=True)
        # all meshes, select only, history, quads, n-gons, concave, holed, non-planar,
        # zero faces + tolerance, zero edges + tolerance, zero UV faces + tolerance,
        # shared UVs, non-manifold (1 = also conform normals), lamina, invalid components
        arguments = [
            "0", "0", "0", "0", ngons, "0", "0", "0",
            "1", CLEANUP_TOLERANCE, "1", CLEANUP_TOLERANCE, "0", CLEANUP_TOLERANCE,
            "0", "1", "1", "1",
        ]  # fmt: skip
        quoted = ",".join(f'"{value}"' for value in arguments)
        mel.eval(f"polyCleanupArgList 4 {{{quoted}}}")
        shape = single_mesh(transform)
        cmds.polyNormal(shape, normalMode=CONFORM, userNormalMode=0, constructionHistory=False)
        reversed_normals = is_inside_out(shape)
        if reversed_normals:
            cmds.polyNormal(shape, normalMode=REVERSE, userNormalMode=0, constructionHistory=False)
        result = {"merge": rnd(distance, 6), "normals_reversed": reversed_normals}
        if opposite:
            result["opposite_cleared"] = True
        return result

    return edited(name, "cleanup", cleanup)
