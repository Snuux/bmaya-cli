"""UV layouts as data: shells, areas, stretch, texel density, padding, overlaps, a fingerprint,
how well a texture fits the layout, and baking points into texels.

Pure Python: no maya import. A face's UVs are listed corner by corner, in winding order, as
indices into a list of (u, v); its points the same way, as indices into a list of positions.
A canvas is a square of `size` texels; row 0 is the top of the texture (v = 1).
"""

from __future__ import annotations

import hashlib
import math
import struct
from array import array
from collections.abc import Sequence
from typing import NamedTuple

UV = tuple[float, float]
Vec = tuple[float, float, float]

FINGERPRINT_DIGITS = 4
STRONG_SHARE = 0.03
BAND = 2
FITS = 2.5
MISFITS = 1.5
FEW_EDGES = 0.002
PADDING_CAP = 16.0


class Shell(NamedTuple):
    faces: list[int]
    uv_area: float
    area: float
    box: tuple[float, float, float, float]


class Raster(NamedTuple):
    """Which face covers each texel (-1 for none), and the texels two faces cover."""

    owner: array
    overlap: int
    pairs: set[tuple[int, int]]


def signed_area(corners: Sequence[UV]) -> float:
    total = 0.0
    count = len(corners)
    for index in range(count):
        u0, v0 = corners[index]
        u1, v1 = corners[(index + 1) % count]
        total += u0 * v1 - u1 * v0
    return total / 2.0


def face_area_3d(points: Sequence[Vec]) -> float:
    ax, ay, az = points[0]
    total = 0.0
    for index in range(1, len(points) - 1):
        bx, by, bz = points[index]
        cx, cy, cz = points[index + 1]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        total += math.sqrt(nx * nx + ny * ny + nz * nz) / 2.0
    return total


def shell_faces(face_uvs: Sequence[Sequence[int]]) -> list[list[int]]:
    """Faces joined into shells where they share a UV, largest shell first."""
    parent: dict[int, int] = {}

    def root(item: int) -> int:
        while parent.setdefault(item, item) != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for ids in face_uvs:
        if not ids:
            continue
        first = root(ids[0])
        for uv in ids[1:]:
            other = root(uv)
            if other != first:
                parent[other] = first
    groups: dict[int, list[int]] = {}
    for face, ids in enumerate(face_uvs):
        if ids:
            groups.setdefault(root(ids[0]), []).append(face)
    return sorted(groups.values(), key=lambda faces: (-len(faces), faces[0]))


def shells(
    uvs: Sequence[UV],
    face_uvs: Sequence[Sequence[int]],
    points: Sequence[Vec],
    face_points: Sequence[Sequence[int]],
) -> list[Shell]:
    out = []
    for faces in shell_faces(face_uvs):
        uv_area = 0.0
        area = 0.0
        low_u = low_v = math.inf
        high_u = high_v = -math.inf
        for face in faces:
            corners = [uvs[uv] for uv in face_uvs[face]]
            uv_area += abs(signed_area(corners))
            area += face_area_3d([points[point] for point in face_points[face]])
            for u, v in corners:
                low_u, low_v = min(low_u, u), min(low_v, v)
                high_u, high_v = max(high_u, u), max(high_v, v)
        out.append(Shell(faces, uv_area, area, (low_u, low_v, high_u, high_v)))
    out.sort(key=lambda shell: -shell.uv_area)
    return out


def density(uv_area: float, area: float, size: int) -> float:
    """Texels per scene unit of length, for a texture `size` texels wide."""
    if area <= 0.0:
        return 0.0
    return math.sqrt(uv_area / area) * size


def stretch(
    uvs: Sequence[UV],
    face_uvs: Sequence[Sequence[int]],
    points: Sequence[Vec],
    face_points: Sequence[Sequence[int]],
) -> list[float]:
    """For each face, how much more texture it gets than the mesh's average, as a length
    ratio: 2 is twice as many texels along an edge, 0.5 half as many, 0 for a face with no UV
    area. Faces with no area of their own are left out (nan)."""
    ratios = []
    total_uv = 0.0
    total = 0.0
    areas = []
    for face, ids in enumerate(face_uvs):
        uv_area = abs(signed_area([uvs[uv] for uv in ids])) if ids else 0.0
        area = face_area_3d([points[point] for point in face_points[face]])
        areas.append((uv_area, area))
        total_uv += uv_area
        total += area
    if total_uv <= 0.0 or total <= 0.0:
        return [math.nan for _ in areas]
    for uv_area, area in areas:
        if area <= 1e-12 * total:
            ratios.append(math.nan)
        else:
            ratios.append(math.sqrt((uv_area / total_uv) / (area / total)))
    return ratios


def fingerprint(uvs: Sequence[UV], face_uvs: Sequence[Sequence[int]]) -> str:
    """Eight hex digits of the UVs corner by corner, rounded: equal layouts give equal prints."""
    flat = []
    for ids in face_uvs:
        for uv in ids:
            u, v = uvs[uv]
            flat.append(round(u, FINGERPRINT_DIGITS) + 0.0)
            flat.append(round(v, FINGERPRINT_DIGITS) + 0.0)
    return hashlib.md5(repr(flat).encode()).hexdigest()[:8]


def triangles(corners: Sequence[int]) -> list[tuple[int, int, int]]:
    return [(0, index, index + 1) for index in range(1, len(corners) - 1)]


def to_canvas(uv: UV, size: int) -> tuple[float, float]:
    return uv[0] * size, (1.0 - uv[1]) * size


def raster(
    uvs: Sequence[UV],
    face_uvs: Sequence[Sequence[int]],
    shell_of: Sequence[int],
    size: int,
) -> Raster:
    """Paints each face's texels with its index. A texel already painted by a face of another
    shell, or by a face of the same shell that shares no UV with this one (a fold), is an
    overlap; faces side by side may both reach a texel on their shared edge, which is not."""
    owner = array("i", [-1]) * (size * size)
    overlap = 0
    pairs: set[tuple[int, int]] = set()
    for face, ids in enumerate(face_uvs):
        if len(ids) < 3:
            continue
        spots = [to_canvas(uvs[uv], size) for uv in ids]
        texels: set[int] = set()
        for first, second, third in triangles(ids):
            texels.update(covered(spots[first], spots[second], spots[third], size))
        mine = set(ids)
        for texel in texels:
            other = owner[texel]
            if other == -1:
                owner[texel] = face
                continue
            if shell_of[other] == shell_of[face] and mine.intersection(face_uvs[other]):
                continue
            overlap += 1
            pair = (shell_of[other], shell_of[face])
            pairs.add((min(pair), max(pair)))
    return Raster(owner, overlap, pairs)


def covered(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], size: int
) -> list[int]:
    """Texels whose centers lie inside the triangle (one edge rule: left-top inclusive)."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(det) < 1e-12:
        return []
    x0 = max(0, int(math.floor(min(ax, bx, cx))))
    x1 = min(size - 1, int(math.ceil(max(ax, bx, cx))))
    y0 = max(0, int(math.floor(min(ay, by, cy))))
    y1 = min(size - 1, int(math.ceil(max(ay, by, cy))))
    out = []
    inverse = 1.0 / det
    for y in range(y0, y1 + 1):
        py = y + 0.5
        for x in range(x0, x1 + 1):
            px = x + 0.5
            one = ((px - ax) * (cy - ay) - (cx - ax) * (py - ay)) * inverse
            two = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) * inverse
            if one >= 0.0 and two >= 0.0 and one + two <= 1.0:
                out.append(y * size + x)
    return out


def border_segments(face_uvs: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """UV edges with a face on one side only: the outlines of the shells."""
    count: dict[tuple[int, int], int] = {}
    for ids in face_uvs:
        for index in range(len(ids)):
            one, two = ids[index], ids[(index + 1) % len(ids)]
            key = (min(one, two), max(one, two))
            count[key] = count.get(key, 0) + 1
    return [edge for edge, uses in count.items() if uses == 1]


def segment_gap(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]
) -> float:
    if segments_cross(a, b, c, d):
        return 0.0
    return min(point_gap(a, c, d), point_gap(b, c, d), point_gap(c, a, b), point_gap(d, a, b))


def point_gap(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    reach = dx * dx + dy * dy
    t = (
        0.0
        if reach <= 0.0
        else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / reach))
    )
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def segments_cross(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]
) -> bool:
    def side(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    one, two = side(a, b, c), side(a, b, d)
    three, four = side(c, d, a), side(c, d, b)
    return one * two < 0.0 and three * four < 0.0


def padding(
    uvs: Sequence[UV],
    face_uvs: Sequence[Sequence[int]],
    shell_of_uv: dict[int, int],
    size: int,
    cap: float = PADDING_CAP,
) -> tuple[float | None, float | None]:
    """The narrowest gap between two shells and between a shell and the edge of the 0-1
    square, in texels of a `size` texture. None when every gap is wider than `cap`."""
    segments = border_segments(face_uvs)
    spots = {uv: to_canvas(uvs[uv], size) for edge in segments for uv in edge}
    cell = max(cap, 1.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for index, (one, two) in enumerate(segments):
        (x0, y0), (x1, y1) = spots[one], spots[two]
        for gx in range(
            int(math.floor(min(x0, x1) / cell)), int(math.floor(max(x0, x1) / cell)) + 1
        ):
            for gy in range(
                int(math.floor(min(y0, y1) / cell)), int(math.floor(max(y0, y1) / cell)) + 1
            ):
                grid.setdefault((gx, gy), []).append(index)
    between = None
    seen: set[tuple[int, int]] = set()
    for members in grid.values():
        for first in range(len(members)):
            for second in range(first + 1, len(members)):
                one, two = members[first], members[second]
                key = (min(one, two), max(one, two))
                if key in seen:
                    continue
                seen.add(key)
                a, b = segments[one]
                c, d = segments[two]
                if shell_of_uv[a] == shell_of_uv[c]:
                    continue
                gap = segment_gap(spots[a], spots[b], spots[c], spots[d])
                if gap <= cap and (between is None or gap < between):
                    between = gap
    edge = None
    for one, two in segments:
        for x, y in (spots[one], spots[two]):
            gap = min(x, y, size - x, size - y)
            if gap <= cap and (edge is None or gap < edge):
                edge = gap
    return between, edge


def band_mask(uvs: Sequence[UV], face_uvs: Sequence[Sequence[int]], size: int) -> bytearray:
    """Texels within BAND of a shell outline. The bridge draws this with Qt, which is much
    faster; this is the same thing in plain Python."""
    band = bytearray(size * size)
    reach = BAND + 0.5
    for one, two in border_segments(face_uvs):
        a = to_canvas(uvs[one], size)
        b = to_canvas(uvs[two], size)
        x0 = max(0, int(math.floor(min(a[0], b[0]) - reach)))
        x1 = min(size - 1, int(math.ceil(max(a[0], b[0]) + reach)))
        y0 = max(0, int(math.floor(min(a[1], b[1]) - reach)))
        y1 = min(size - 1, int(math.ceil(max(a[1], b[1]) + reach)))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if point_gap((x + 0.5, y + 0.5), a, b) <= reach:
                    band[y * size + x] = 1
    return band


def blurred_rows(gray: Sequence[float], size: int) -> list[list[float]]:
    rows = [list(gray[y * size : (y + 1) * size]) for y in range(size)]
    rows = [
        [row[0]]
        + list(map(lambda a, b, c: 0.25 * a + 0.5 * b + 0.25 * c, row[:-2], row[1:-1], row[2:]))
        + [row[-1]]
        for row in rows
    ]
    middle = [
        list(map(lambda a, b, c: 0.25 * a + 0.5 * b + 0.25 * c, rows[y - 1], rows[y], rows[y + 1]))
        for y in range(1, size - 1)
    ]
    return [rows[0]] + middle + [rows[-1]]


def edge_strength(gray: Sequence[float], size: int) -> list[float]:
    """How sharply each texel differs from its neighbours (a Laplacian after a light blur)."""
    rows = blurred_rows(gray, size)
    out = [0.0] * (size * size)
    for y in range(1, size - 1):
        up, here, down = rows[y - 1], rows[y], rows[y + 1]
        out[y * size + 1 : (y + 1) * size - 1] = map(
            lambda left, right, top, bottom, middle: abs(
                left + right + top + bottom - 4.0 * middle
            ),
            here[:-2],
            here[2:],
            up[1:-1],
            down[1:-1],
            here[1:-1],
        )
    return out


class Fit(NamedTuple):
    lift: float | None
    verdict: str
    edges: float


def texture_fit(gray: Sequence[float], size: int, band: bytearray) -> Fit:
    """Whether a texture was painted for a layout: painted textures change sharply along the
    outlines of the UV shells (seams, padding colour, the end of the painted area), so the
    strongest texture edges crowd near them. `band` marks the texels near the outlines
    (`band_mask`); `lift` is how much more of the strong edges lie in it than its share of
    the image would give."""
    strength = edge_strength(gray, size)
    sample = sorted(strength[::7])
    cut = sample[int((1.0 - STRONG_SHARE) * (len(sample) - 1))]
    strong = [index for index, value in enumerate(strength) if value > cut and value > 0.0]
    edges = len(strong) / float(size * size)
    share = sum(band) / float(size * size)
    if not strong or share <= 0.0 or edges < FEW_EDGES:
        return Fit(None, "unclear", round(edges, 4))
    on = sum(1 for index in strong if band[index]) / float(len(strong))
    lift = on / share
    if lift >= FITS:
        verdict = "fits"
    elif lift <= MISFITS:
        verdict = "does not fit"
    else:
        verdict = "unclear"
    return Fit(lift, verdict, round(edges, 4))


class Baked(NamedTuple):
    position: array
    normal: array
    mask: bytearray
    covered: int


def bake(
    uvs: Sequence[UV],
    face_uvs: Sequence[Sequence[int]],
    points: Sequence[Vec],
    face_points: Sequence[Sequence[int]],
    normals: Sequence[Vec],
    face_normals: Sequence[Sequence[int]],
    size: int,
) -> Baked:
    """Each texel's point on the surface and its normal, blended across the triangle it falls
    in: the position and normal maps a painter or a shader needs. Texels outside every
    face stay 0 and their mask is 0."""
    position = array("f", [0.0]) * (size * size * 3)
    normal = array("f", [0.0]) * (size * size * 3)
    mask = bytearray(size * size)
    count = 0
    for face, ids in enumerate(face_uvs):
        if len(ids) < 3:
            continue
        spots = [to_canvas(uvs[uv], size) for uv in ids]
        corners = [points[point] for point in face_points[face]]
        turns = [normals[index] for index in face_normals[face]]
        for first, second, third in triangles(ids):
            (ax, ay), (bx, by), (cx, cy) = spots[first], spots[second], spots[third]
            det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
            if abs(det) < 1e-12:
                continue
            inverse = 1.0 / det
            pa, pb, pc = corners[first], corners[second], corners[third]
            na, nb, nc = turns[first], turns[second], turns[third]
            for texel in covered(spots[first], spots[second], spots[third], size):
                x = texel % size + 0.5
                y = texel // size + 0.5
                one = ((x - ax) * (cy - ay) - (cx - ax) * (y - ay)) * inverse
                two = ((bx - ax) * (y - ay) - (x - ax) * (by - ay)) * inverse
                zero = 1.0 - one - two
                at = texel * 3
                for k in range(3):
                    position[at + k] = zero * pa[k] + one * pb[k] + two * pc[k]
                nx = zero * na[0] + one * nb[0] + two * nc[0]
                ny = zero * na[1] + one * nb[1] + two * nc[1]
                nz = zero * na[2] + one * nb[2] + two * nc[2]
                reach = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                normal[at] = nx / reach
                normal[at + 1] = ny / reach
                normal[at + 2] = nz / reach
                if not mask[texel]:
                    count += 1
                mask[texel] = 1
    return Baked(position, normal, mask, count)


def npy_bytes(values: array, shape: tuple[int, ...]) -> bytes:
    """A float32 array in NumPy's .npy format, so numpy.load reads it."""
    header = "{'descr': '<f4', 'fortran_order': False, 'shape': (%s), }" % (
        "".join(f"{dim}, " for dim in shape) if len(shape) > 1 else f"{shape[0]},"
    )
    head = header.encode("latin1")
    pad = 64 - (10 + len(head) + 1) % 64
    head += b" " * pad + b"\n"
    raw = values.tobytes() if values.itemsize == 4 else array("f", values).tobytes()
    if struct.pack("=f", 1.0) != struct.pack("<f", 1.0):
        swapped = array("f", values)
        swapped.byteswap()
        raw = swapped.tobytes()
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(head)) + head + raw
