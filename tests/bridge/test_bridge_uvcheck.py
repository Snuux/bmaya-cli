import math
import struct
from array import array

from bmaya_bridge import uvcheck

# Two unit quads side by side in 3D, with their UVs in two shells: the left one fills
# u 0.1-0.4, the right one u 0.6-0.8 (half as wide: stretched).
POINTS = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)] + [
    (2.0, 0.0, 0.0),
    (3.0, 0.0, 0.0),
    (3.0, 1.0, 0.0),
    (2.0, 1.0, 0.0),
]
FACE_POINTS = [[0, 1, 2, 3], [4, 5, 6, 7]]
UVS = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4)] + [
    (0.6, 0.1),
    (0.75, 0.1),
    (0.75, 0.4),
    (0.6, 0.4),
]
FACE_UVS = [[0, 1, 2, 3], [4, 5, 6, 7]]


def test_shells_areas_and_density() -> None:
    found = uvcheck.shells(UVS, FACE_UVS, POINTS, FACE_POINTS)
    assert [shell.faces for shell in found] == [[0], [1]]
    assert abs(found[0].uv_area - 0.09) < 1e-12 and abs(found[0].area - 1.0) < 1e-12
    assert found[0].box == (0.1, 0.1, 0.4, 0.4)
    assert abs(uvcheck.density(found[0].uv_area, found[0].area, 1024) - 0.3 * 1024) < 1e-6


def test_stretch_compares_each_face_with_the_average() -> None:
    ratios = uvcheck.stretch(UVS, FACE_UVS, POINTS, FACE_POINTS)
    assert ratios[0] > 1.0 > ratios[1]
    assert abs(ratios[0] / ratios[1] - math.sqrt(2.0)) < 1e-9


def test_fingerprint_sees_a_moved_uv() -> None:
    same = uvcheck.fingerprint(UVS, FACE_UVS)
    assert same == uvcheck.fingerprint(list(UVS), [list(face) for face in FACE_UVS])
    moved = list(UVS)
    moved[2] = (0.41, 0.4)
    assert uvcheck.fingerprint(moved, FACE_UVS) != same
    tiny = list(UVS)
    tiny[2] = (0.400001, 0.4)
    assert uvcheck.fingerprint(tiny, FACE_UVS) == same


def test_raster_counts_overlaps_between_shells() -> None:
    overlapping = list(UVS)
    overlapping[4:] = [(0.3, 0.1), (0.6, 0.1), (0.6, 0.4), (0.3, 0.4)]
    clean = uvcheck.raster(UVS, FACE_UVS, [0, 1], 100)
    assert clean.overlap == 0 and not clean.pairs
    assert sum(1 for owner in clean.owner if owner >= 0) == 900 + 450
    folded = uvcheck.raster(overlapping, FACE_UVS, [0, 1], 100)
    assert folded.overlap == 300 and folded.pairs == {(0, 1)}


def test_padding_between_shells_and_to_the_edge() -> None:
    shell_of_uv = {uv: (0 if uv < 4 else 1) for uv in range(8)}
    between, edge = uvcheck.padding(UVS, FACE_UVS, shell_of_uv, 100, cap=40.0)
    assert abs(between - 20.0) < 1e-9 and abs(edge - 10.0) < 1e-9
    assert uvcheck.padding(UVS, FACE_UVS, shell_of_uv, 100, cap=5.0) == (None, None)


def test_texture_fit_finds_edges_on_the_outlines() -> None:
    size = 64
    square = [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)]
    faces = [[0, 1, 2, 3]]
    painted = [0.0] * (size * size)
    for y in range(16, 48):
        for x in range(16, 48):
            painted[y * size + x] = 200.0
    band = uvcheck.band_mask(square, faces, size)
    fits = uvcheck.texture_fit(painted, size, band)
    assert fits.verdict == "fits" and fits.lift > 5.0
    shifted = [(u + 0.2, v + 0.2) for u, v in square]
    moved = uvcheck.band_mask(shifted, faces, size)
    assert uvcheck.texture_fit(painted, size, moved).verdict == "does not fit"
    flat = uvcheck.texture_fit([100.0] * (size * size), size, band)
    assert flat.verdict == "unclear" and flat.lift is None


def test_bake_blends_positions_and_normals() -> None:
    normals = [(0.0, 0.0, 1.0)] * 8
    baked = uvcheck.bake(UVS, FACE_UVS, POINTS, FACE_POINTS, normals, FACE_POINTS, 10)
    assert baked.covered == 9 + 6
    texel = 7 * 10 + 1
    assert baked.mask[texel] == 1
    x, y, z = baked.position[texel * 3 : texel * 3 + 3]
    assert abs(x - (1.5 - 1.0) / 3.0) < 1e-6 and abs(y - (1.0 - (7.5 - 6.0) / 3.0)) < 1e-6
    assert z == 0.0 and tuple(baked.normal[texel * 3 : texel * 3 + 3]) == (0.0, 0.0, 1.0)


def test_npy_bytes_follow_the_format() -> None:
    values = array("f", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    data = uvcheck.npy_bytes(values, (1, 2, 3))
    assert data[:8] == b"\x93NUMPY\x01\x00"
    length = struct.unpack("<H", data[8:10])[0]
    header = data[10 : 10 + length].decode("latin1")
    assert (10 + length) % 64 == 0 and header.endswith("\n")
    assert "'shape': (1, 2, 3, )" in header and "'<f4'" in header
    assert array("f", data[10 + length :]).tolist() == values.tolist()


def test_unmatched_counts_uvs_with_nothing_near() -> None:
    first = [(0.0, 0.0), (0.5, 0.5), (0.99995, 1.0)]
    second = [(0.00005, 0.0), (1.0, 1.0)]
    assert uvcheck.unmatched(first, second) == 1
    assert uvcheck.unmatched(second, first) == 0
    assert uvcheck.unmatched([], second) == 0


def test_a_concave_face_covers_only_itself() -> None:
    ell = [(2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    assert uvcheck.face_area_3d([(x, y, 0.0) for x, y in ell]) == 3.0
    pieces = uvcheck.triangles(ell)
    assert len(pieces) == 4
    area = sum(abs(uvcheck.signed_area([ell[a], ell[b], ell[c]])) for a, b, c in pieces)
    assert abs(area - 3.0) < 1e-12
    uvs = [(x / 4.0, y / 4.0) for x, y in ell] + [
        (0.25, 0.25),
        (0.5, 0.25),
        (0.5, 0.5),
        (0.25, 0.5),
    ]
    notch = uvcheck.raster(uvs, [list(range(6)), [6, 7, 8, 9]], [0, 1], 64)
    assert notch.overlap == 0, notch.overlap


def test_shells_touching_on_texel_centers_do_not_overlap() -> None:
    edge = 1025 / 2048
    uvs = [(0.1, 0.1), (edge, 0.1), (edge, 0.4), (0.1, 0.4)]
    uvs += [(edge, 0.1), (0.9, 0.1), (0.9, 0.4), (edge, 0.4)]
    found = uvcheck.raster(uvs, [[0, 1, 2, 3], [4, 5, 6, 7]], [0, 1], 1024)
    assert found.overlap == 0 and not found.pairs


def test_folds_and_stacks_count_each_texel_once() -> None:
    fan = [(0.5, 0.5)]
    faces = []
    for step in range(12):
        angle = step * 4.0 * math.pi / 12.0
        fan.append((0.5 + 0.4 * math.cos(angle), 0.5 + 0.4 * math.sin(angle)))
    for step in range(12):
        faces.append([0, 1 + step, 1 + (step + 1) % 12])
    folded = uvcheck.raster(fan, faces, [0] * 12, 100)
    assert folded.overlap > 0.3 * sum(1 for owner in folded.owner if owner >= 0)
    stack = [list(range(4))] * 10
    stacked = uvcheck.raster(UVS[:4], stack, list(range(10)), 100)
    assert stacked.overlap == 900 and len(stacked.pairs) == 9


def test_padding_finds_gaps_across_grid_lines_and_skips_other_tiles() -> None:
    size = 1024
    left = 510.9 / size
    right = 512.4 / size
    uvs = [(0.4, 0.4), (left, 0.4), (left, 0.6), (0.4, 0.6)]
    uvs += [(right, 0.4), (0.6, 0.4), (0.6, 0.6), (right, 0.6)]
    shell_of_uv = {uv: (0 if uv < 4 else 1) for uv in range(8)}
    between, _ = uvcheck.padding(uvs, [[0, 1, 2, 3], [4, 5, 6, 7]], shell_of_uv, size)
    assert abs(between - 1.5) < 1e-6
    far = [(u + 1.0, v) for u, v in UVS]
    shell_of_far = {uv: (0 if uv < 4 else 1) for uv in range(8)}
    assert uvcheck.padding(far, FACE_UVS, shell_of_far, 100, cap=40.0)[1] is None


def test_texture_fit_allows_for_a_crowded_band() -> None:
    size = 64
    painted = [
        float((x // 8 + y // 8) % 2) * 200.0 + (x * 7 + y * 13) % 5
        for y in range(size)
        for x in range(size)
    ]
    band = bytearray(size * size)
    for y in range(size):
        for x in range(size):
            if x % 8 in (0, 1, 6, 7) or y % 8 in (0, 1, 6, 7):
                band[y * size + x] = 1
    found = uvcheck.texture_fit(painted, size, band)
    assert found.verdict == "unclear" and found.lift is not None
