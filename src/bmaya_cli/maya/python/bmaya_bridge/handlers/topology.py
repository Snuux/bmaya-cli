from __future__ import annotations

import math
from typing import Any

import maya.api.OpenMaya as om

from bmaya_bridge import softrender, topology
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    float_param,
    int_param,
    listing,
    rnd,
    rnd_list,
    text_param,
)
from bmaya_bridge.handlers.mesh import dag_path, single_mesh, transform_of
from bmaya_bridge.handlers.views import get_model_views

LOOPS_SHOWN = 60
TUBES_SHOWN = 30
IDS_SHOWN = 64
LONE: softrender.Color = (140, 140, 140)
JOINT: softrender.Color = (15, 15, 15)
NO_LOOPS_HINT = (
    "Loops are walked across vertices with four edges, so they need quads around the tube. "
    "A triangulated mesh has none: `bmaya audit NAME` shows the faces by kind."
)


def mesh_data(shape: str) -> tuple[list[topology.Vec], list[list[int]]]:
    mesh = om.MFnMesh(dag_path(shape))
    points = [(point.x, point.y, point.z) for point in mesh.getPoints(om.MSpace.kWorld)]
    faces = [list(mesh.getPolygonVertices(face)) for face in range(mesh.numPolygons)]
    return points, faces


def tube_color(index: int) -> softrender.Color:
    return softrender.part_color(index + 1)


def loop_entries(
    found: list[topology.Loop], owners: dict[int, int], with_ids: bool
) -> list[dict[str, Any]]:
    entries = []
    for index, loop in enumerate(found):
        entry: dict[str, Any] = {
            "id": index,
            "at": rnd_list(loop.center),
            "radius": rnd(loop.radius),
            "normal": rnd_list(loop.normal),
            "verts": len(loop.ids),
        }
        if index in owners:
            entry["tube"] = owners[index]
        if with_ids:
            entry["ids"] = loop.ids[:IDS_SHOWN]
            if len(loop.ids) > IDS_SHOWN:
                entry["ids_total"] = len(loop.ids)
        entries.append(entry)
    return entries


def tube_entry(index: int, tube: topology.Tube, found: list[topology.Loop]) -> dict[str, Any]:
    radii = [found[loop].radius for loop in tube.loops]
    joints = topology.joint_loops(tube)
    return {
        "id": index,
        "loops": tube.loops,
        "radius": [rnd(radii[0]), rnd(radii[-1])],
        "length": rnd(sum(tube.gaps)),
        "axis": rnd_list(tube.axis),
        "gaps": [rnd(gap) for gap in tube.gaps],
        "runs": tube.runs,
        "joints": [{"loop": loop, "at": rnd_list(found[loop].center)} for loop in joints],
    }


def overlay(
    found: list[topology.Loop],
    tubes: list[topology.Tube],
    owners: dict[int, int],
    points: list[topology.Vec],
    picked: list[int] | None,
) -> tuple[list[softrender.Mark], list[tuple[topology.Vec, str, softrender.Color]]]:
    marks = []
    for index, loop in enumerate(found):
        if picked is not None and owners.get(index) not in picked:
            continue
        color = tube_color(owners[index]) if index in owners else LONE
        ring = order_ring(loop, points)
        marks.append(softrender.Mark(ring, color, closed=True))
    centers = []
    labels = []
    for index, tube in enumerate(tubes):
        if picked is not None and index not in picked:
            continue
        for loop in topology.joint_loops(tube):
            centers.append(found[loop].center)
        labels.append((found[tube.loops[-1]].center, f"tube {index}", tube_color(index)))
    marks.append(softrender.Mark(centers, JOINT, dots=True))
    return marks, labels


def parse_tubes(raw: str, count: int) -> list[int] | None:
    if not raw:
        return None
    picked = []
    for item in raw.replace(" ", "").split(","):
        if not item:
            continue
        try:
            index = int(item)
        except ValueError:
            raise BridgeError(
                f"Bad tube '{item}'.", hint="--tubes takes tube ids from the report, like 0,3."
            ) from None
        if not 0 <= index < count:
            raise BridgeError(
                f"There is no tube {index}; the mesh has {count}.",
                hint="`bmaya topo NAME` lists the tubes with their ids.",
            )
        picked.append(index)
    return picked


def tube_box(
    picked: list[int], tubes: list[topology.Tube], found: list[topology.Loop], points: list
) -> tuple[topology.Vec, topology.Vec]:
    chosen = [
        points[index] for tube in picked for loop in tubes[tube].loops for index in found[loop].ids
    ]
    low = (min(p[0] for p in chosen), min(p[1] for p in chosen), min(p[2] for p in chosen))
    high = (max(p[0] for p in chosen), max(p[1] for p in chosen), max(p[2] for p in chosen))
    return low, high


def order_ring(loop: topology.Loop, points: list[topology.Vec]) -> list[topology.Vec]:
    """The loop's points in order around its centre, so the drawn line goes round it."""
    ring = [points[index] for index in loop.ids]
    normal = loop.normal
    reference = topology.sub(ring[0], loop.center)
    side = (
        normal[1] * reference[2] - normal[2] * reference[1],
        normal[2] * reference[0] - normal[0] * reference[2],
        normal[0] * reference[1] - normal[1] * reference[0],
    )

    def angle(point: topology.Vec) -> float:
        offset = topology.sub(point, loop.center)
        along = sum(offset[k] * reference[k] for k in range(3))
        across = sum(offset[k] * side[k] for k in range(3))
        return math.atan2(across, along)

    return sorted(ring, key=angle)


def get_topology(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    shape = single_mesh(name)
    gap = float_param(params, "gap") or topology.RUN_GAP
    if gap < 1.0:
        raise BridgeError(
            f"--gap {rnd(gap)} is below 1.",
            hint="It is how many times the closest gap of a tube still counts as one run: "
            "1.2 by default, 1.5 to join looser loops.",
        )
    limit = int_param(params, "limit", LOOPS_SHOWN, 1, 1000)
    points, faces = mesh_data(shape)
    found = topology.loops(points, faces)
    tubes = topology.tubes(found, gap)
    owners = {loop: index for index, tube in enumerate(tubes) for loop in tube.loops}
    result: dict[str, Any] = {
        "name": display_name(transform_of(shape)),
        "verts": len(points),
        "faces": len(faces),
        "edges": om.MFnMesh(dag_path(shape)).numEdges,
        "shells": topology.shells(faces),
        "border_edges": topology.border_edges(faces),
        "valence": {str(count): verts for count, verts in topology.valence(faces).items()},
        "loops": listing(loop_entries(found, owners, bool_param(params, "ids")), limit),
        "tubes": listing(
            [tube_entry(index, tube, found) for index, tube in enumerate(tubes)], TUBES_SHOWN
        ),
        "joints": sum(len(topology.joint_loops(tube)) for tube in tubes),
        "gap": rnd(gap),
    }
    if not found:
        result["note"] = NO_LOOPS_HINT
    picked = parse_tubes(text_param(params, "tubes"), len(tubes))
    if bool_param(params, "image"):
        marks, labels = overlay(found, tubes, owners, points, picked)
        sheet = get_model_views(
            {
                "names": name,
                "views": text_param(params, "views"),
                "style": "parts",
                "max_size": params.get("max_size") or 0,
            },
            marks,
            labels,
            tube_box(picked, tubes, found, points) if picked else None,
        )
        for key in ("path", "width", "height", "views"):
            result[key] = sheet[key]
        if sheet.get("note"):
            result["note"] = sheet["note"]
    return result
