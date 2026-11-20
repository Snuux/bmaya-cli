"""Checks on mesh data: winding, volume, symmetry, UVs, what an edit moved.

Pure Python: no maya import. A face is a list of point indices in winding order.
"""

from __future__ import annotations

import math
from typing import NamedTuple

Vec = tuple[float, float, float]
Face = list[int]

SAMPLES = 5


class Winding(NamedTuple):
    """`flipped` faces are wound against their neighbours; `inward` closed shells are inside out."""

    flipped: int
    inward: int
    shells: int


class Symmetry(NamedTuple):
    matched: int
    unmatched: int
    sample: list[Vec]


class Moves(NamedTuple):
    moved: int
    max_move: float
    mean_move: float
    box: tuple[Vec, Vec] | None


def face_volume(points: list[Vec], face: Face) -> float:
    """Six times the signed volume between the face and the origin."""
    ax, ay, az = points[face[0]]
    total = 0.0
    for index in range(1, len(face) - 1):
        bx, by, bz = points[face[index]]
        cx, cy, cz = points[face[index + 1]]
        total += ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)
    return total


def face_area(points: list[Vec], face: Face) -> float:
    ax, ay, az = points[face[0]]
    total = 0.0
    for index in range(1, len(face) - 1):
        bx, by, bz = points[face[index]]
        cx, cy, cz = points[face[index + 1]]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        total += math.sqrt(nx * nx + ny * ny + nz * nz) / 2.0
    return total


def measure(points: list[Vec], faces: list[Face]) -> tuple[float, float]:
    """Surface area and enclosed volume; the volume means something for a closed mesh only."""
    area = sum(face_area(points, face) for face in faces)
    volume = sum(face_volume(points, face) for face in faces) / 6.0
    return area, volume


def winding(points: list[Vec], faces: list[Face]) -> Winding:
    # Two faces agree when they run along their shared edge in opposite directions.
    edge_faces: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for index, face in enumerate(faces):
        for corner in range(len(face)):
            one = face[corner]
            two = face[(corner + 1) % len(face)]
            key = (one, two) if one < two else (two, one)
            edge_faces.setdefault(key, []).append((index, one < two))

    neighbours: list[list[tuple[int, bool]]] = [[] for _ in faces]
    open_faces = set()
    for users in edge_faces.values():
        if len(users) == 2:
            (first, first_way), (second, second_way) = users
            agree = first_way != second_way
            neighbours[first].append((second, agree))
            neighbours[second].append((first, agree))
        else:
            open_faces.update(face for face, _ in users)

    side: list[int | None] = [None] * len(faces)
    flipped = inward = shells = 0
    for start in range(len(faces)):
        if side[start] is not None:
            continue
        shells += 1
        side[start] = 0
        shell = [start]
        queue = [start]
        while queue:
            current = queue.pop()
            for other, agree in neighbours[current]:
                if side[other] is None:
                    side[other] = side[current] if agree else 1 - side[current]
                    shell.append(other)
                    queue.append(other)
        turned = sum(1 for face in shell if side[face] == 1)
        flipped += min(turned, len(shell) - turned)
        common = 1 if turned * 2 > len(shell) else 0
        if any(face in open_faces for face in shell):
            continue
        volume = 0.0
        for face in shell:
            part = face_volume(points, faces[face])
            volume += part if side[face] == common else -part
        if volume < 0:
            inward += 1
    return Winding(flipped, inward, shells)


def cell_of(point: Vec, size: float) -> tuple[int, int, int]:
    return (
        math.floor(point[0] / size),
        math.floor(point[1] / size),
        math.floor(point[2] / size),
    )


def symmetry(points: list[Vec], axis: int, at: float, tolerance: float) -> Symmetry:
    """How many points have a twin mirrored across the plane `axis` = `at`."""
    cells: dict[tuple[int, int, int], list[Vec]] = {}
    for point in points:
        cells.setdefault(cell_of(point, tolerance), []).append(point)

    unmatched: list[Vec] = []
    limit = tolerance * tolerance
    for point in points:
        if abs(point[axis] - at) <= tolerance:
            continue
        twin = list(point)
        twin[axis] = 2.0 * at - point[axis]
        tx, ty, tz = twin
        cx, cy, cz = cell_of((tx, ty, tz), tolerance)
        found = False
        for x in (cx - 1, cx, cx + 1):
            for y in (cy - 1, cy, cy + 1):
                for z in (cz - 1, cz, cz + 1):
                    for ox, oy, oz in cells.get((x, y, z), ()):
                        if (ox - tx) ** 2 + (oy - ty) ** 2 + (oz - tz) ** 2 <= limit:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if not found:
            unmatched.append(point)
    return Symmetry(len(points) - len(unmatched), len(unmatched), unmatched[:SAMPLES])


def moves(before: list[Vec], after: list[Vec], tolerance: float) -> Moves:
    """What changed between two point lists of the same length and order."""
    moved = 0
    total = 0.0
    longest = 0.0
    low = [math.inf, math.inf, math.inf]
    high = [-math.inf, -math.inf, -math.inf]
    for index, old in enumerate(before):
        new = after[index]
        distance = math.dist(old, new)
        if distance <= tolerance:
            continue
        moved += 1
        total += distance
        longest = max(longest, distance)
        for axis in range(3):
            low[axis] = min(low[axis], new[axis])
            high[axis] = max(high[axis], new[axis])
    if not moved:
        return Moves(0, 0.0, 0.0, None)
    box = ((low[0], low[1], low[2]), (high[0], high[1], high[2]))
    return Moves(moved, longest, total / moved, box)


def move_shares(before: list[Vec], after: list[Vec], tolerance: float) -> list[float]:
    """Each point's move as a share of the longest move, 0 for a point that stayed."""
    distances = [math.dist(old, after[index]) for index, old in enumerate(before)]
    longest = max(distances + [0.0])
    if longest <= tolerance:
        return [0.0] * len(distances)
    return [distance / longest if distance > tolerance else 0.0 for distance in distances]


def flipped_uv_faces(face_uvs: list[list[tuple[float, float]]]) -> int:
    """Faces whose UVs run the other way round than the face: mirrored in the texture."""
    flipped = 0
    for uvs in face_uvs:
        area = 0.0
        for index in range(len(uvs)):
            u, v = uvs[index]
            next_u, next_v = uvs[(index + 1) % len(uvs)]
            area += u * next_v - next_u * v
        if area < -1e-12:
            flipped += 1
    return flipped
