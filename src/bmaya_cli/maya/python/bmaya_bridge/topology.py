"""Edge loops around tubes, the tubes they make, and joint places in them.

Pure Python: no maya import. A loop here is a closed edge loop that goes around a tube, like
the loops of a finger: it is walked across four-edge vertices, always taking the edge that
shares no face with the one it came along. Faces are lists of point indices.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

from bmaya_bridge.skeleton import largest_eigenvector

Vec = tuple[float, float, float]
Edge = tuple[int, int]

RADIUS_RATIO = 1.3
NEAR_SHARE = 0.5
RUN_GAP = 1.2
RUN_LENGTH = 3


class Loop(NamedTuple):
    ids: list[int]
    center: Vec
    radius: float
    normal: Vec


class Tube(NamedTuple):
    loops: list[int]
    gaps: list[float]
    runs: list[list[int]]
    axis: Vec


def edge_key(a: int, b: int) -> Edge:
    return (a, b) if a < b else (b, a)


def edge_faces(faces: Sequence[Sequence[int]]) -> dict[Edge, set[int]]:
    found: dict[Edge, set[int]] = {}
    for index, face in enumerate(faces):
        count = len(face)
        for corner in range(count):
            key = edge_key(face[corner], face[(corner + 1) % count])
            found.setdefault(key, set()).add(index)
    return found


def vertex_edges(edges: dict[Edge, set[int]]) -> dict[int, list[Edge]]:
    found: dict[int, list[Edge]] = {}
    for edge in edges:
        for vertex in edge:
            found.setdefault(vertex, []).append(edge)
    return found


def valence(faces: Sequence[Sequence[int]]) -> dict[int, int]:
    """How many vertices have each number of edges."""
    counts: dict[int, int] = {}
    for edges in vertex_edges(edge_faces(faces)).values():
        counts[len(edges)] = counts.get(len(edges), 0) + 1
    return dict(sorted(counts.items()))


def border_edges(faces: Sequence[Sequence[int]]) -> int:
    return sum(1 for owners in edge_faces(faces).values() if len(owners) == 1)


def shells(faces: Sequence[Sequence[int]]) -> int:
    parent: dict[int, int] = {}

    def root(vertex: int) -> int:
        while parent.setdefault(vertex, vertex) != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    for face in faces:
        first = root(face[0])
        for vertex in face[1:]:
            other = root(vertex)
            if other != first:
                parent[other] = first
    return len({root(vertex) for vertex in parent})


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def length(a: Vec) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def mean(points: Sequence[Vec]) -> Vec:
    count = float(len(points))
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
        sum(point[2] for point in points) / count,
    )


def main_axis(points: Sequence[Vec]) -> Vec:
    """The direction the points spread along most (first principal component)."""
    middle = mean(points)
    offsets = [sub(point, middle) for point in points]
    spread = [
        [sum(offset[row] * offset[column] for offset in offsets) for column in range(3)]
        for row in range(3)
    ]
    x, y, z = largest_eigenvector(spread)
    size = length((x, y, z)) or 1.0
    return (x / size, y / size, z / size)


def ring_normal(points: Sequence[Vec], center: Vec) -> Vec:
    """The normal of the plane a loop lies in: the axis the points spread along least."""
    offsets = [sub(point, center) for point in points]
    spread = [
        [sum(offset[row] * offset[column] for offset in offsets) for column in range(3)]
        for row in range(3)
    ]
    biggest = max(spread[index][index] for index in range(3)) or 1.0
    flipped = [
        [(biggest if row == column else 0.0) * 1.0001 - spread[row][column] for column in range(3)]
        for row in range(3)
    ]
    x, y, z = largest_eigenvector(flipped)
    size = length((x, y, z)) or 1.0
    return (x / size, y / size, z / size)


def loops(points: Sequence[Vec], faces: Sequence[Sequence[int]]) -> list[Loop]:
    """Closed edge loops through four-edge vertices, each found once."""
    owners = edge_faces(faces)
    around = vertex_edges(owners)

    def across(edge: Edge, vertex: int) -> Edge | None:
        edges = around[vertex]
        if len(edges) != 4:
            return None
        out = [other for other in edges if other != edge and not owners[other] & owners[edge]]
        return out[0] if len(out) == 1 else None

    seen: set[Edge] = set()
    found = []
    for start in owners:
        if start in seen:
            continue
        walked = [start]
        edge: Edge | None = start
        vertex = max(start)
        closed = False
        while True:
            edge = across(edge, vertex) if edge is not None else None
            if edge is None or len(walked) > len(owners):
                break
            if edge == start:
                closed = True
                break
            walked.append(edge)
            vertex = edge[0] if edge[1] == vertex else edge[1]
        seen.update(walked)
        if not closed:
            continue
        ids = sorted({vertex for edge in walked for vertex in edge})
        ring = [points[index] for index in ids]
        center = mean(ring)
        radius = sum(length(sub(point, center)) for point in ring) / len(ring)
        found.append(Loop(ids, center, radius, ring_normal(ring, center)))
    return found


def joined(one: Loop, other: Loop) -> bool:
    """Neighbouring loops of one tube: of a size, closer than their radius."""
    small = min(one.radius, other.radius)
    big = max(one.radius, other.radius)
    if big >= RADIUS_RATIO * small:
        return False
    return length(sub(one.center, other.center)) < NEAR_SHARE * (one.radius + other.radius)


def order_tube(members: list[int], found: Sequence[Loop]) -> list[int]:
    """The loops of a tube from one end to the other, the thicker end first."""
    if len(members) < 3:
        ordered = list(members)
    else:
        near = {
            index: [
                other for other in members if other != index and joined(found[index], found[other])
            ]
            for index in members
        }
        ends = [index for index in members if len(near[index]) == 1]
        ordered = []
        if len(ends) == 2:
            ordered = [ends[0]]
            while len(ordered) < len(members):
                step = [other for other in near[ordered[-1]] if other not in ordered]
                if len(step) != 1:
                    ordered = []
                    break
                ordered.append(step[0])
        if not ordered:
            axis = main_axis([found[index].center for index in members])
            ordered = sorted(
                members,
                key=lambda index: sum(found[index].center[k] * axis[k] for k in range(3)),
            )
    if found[ordered[0]].radius < found[ordered[-1]].radius:
        ordered.reverse()
    return ordered


def tubes(found: Sequence[Loop], gap: float = RUN_GAP) -> list[Tube]:
    """Loops joined into tubes, thinnest tube first; a loop alone is not a tube."""
    parent = list(range(len(found)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for one in range(len(found)):
        for other in range(one + 1, len(found)):
            if joined(found[one], found[other]):
                parent[root(one)] = root(other)
    groups: dict[int, list[int]] = {}
    for index in range(len(found)):
        groups.setdefault(root(index), []).append(index)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = order_tube(members, found)
        centers = [found[index].center for index in ordered]
        gaps = [length(sub(centers[k + 1], centers[k])) for k in range(len(centers) - 1)]
        axis = main_axis(centers) if len(centers) > 2 else sub(centers[-1], centers[0])
        span = sub(centers[-1], centers[0])
        if sum(axis[k] * span[k] for k in range(3)) < 0:
            axis = (-axis[0], -axis[1], -axis[2])
        size = length(axis) or 1.0
        out.append(
            Tube(
                ordered,
                gaps,
                runs(ordered, gaps, gap),
                (axis[0] / size, axis[1] / size, axis[2] / size),
            )
        )
    out.sort(key=lambda tube: sum(found[index].radius for index in tube.loops) / len(tube.loops))
    return out


def runs(ordered: list[int], gaps: list[float], gap: float = RUN_GAP) -> list[list[int]]:
    """Runs of tightly spaced loops: a gap over `gap` times the closest one starts a new run."""
    if not gaps:
        return [list(ordered)]
    near = gap * min(gaps)
    out = [[ordered[0]]]
    for index, step in enumerate(gaps):
        if step <= near:
            out[-1].append(ordered[index + 1])
        else:
            out.append([ordered[index + 1]])
    return out


def joint_loops(tube: Tube, shortest: int = RUN_LENGTH) -> list[int]:
    """The middle loop of each run of at least `shortest` loops: where a joint goes."""
    return [run[len(run) // 2] for run in tube.runs if len(run) >= shortest]
