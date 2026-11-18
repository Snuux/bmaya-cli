from bmaya_bridge import meshcheck

CUBE_POINTS = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (1.0, 0.0, 1.0),
    (1.0, 1.0, 1.0),
    (0.0, 1.0, 1.0),
]
CUBE_FACES = [
    [0, 3, 2, 1],
    [4, 5, 6, 7],
    [0, 1, 5, 4],
    [2, 3, 7, 6],
    [1, 2, 6, 5],
    [0, 4, 7, 3],
]


def flipped(faces: list[list[int]], *which: int) -> list[list[int]]:
    return [face[::-1] if index in which else face for index, face in enumerate(faces)]


def test_a_cube_measures_its_area_and_volume() -> None:
    area, volume = meshcheck.measure(CUBE_POINTS, CUBE_FACES)
    assert abs(area - 6.0) < 1e-9
    assert abs(volume - 1.0) < 1e-9


def test_a_closed_cube_winds_one_way_and_outward() -> None:
    assert meshcheck.winding(CUBE_POINTS, CUBE_FACES) == meshcheck.Winding(0, 0, 1)


def test_one_face_turned_over_is_counted_and_the_shell_still_points_out() -> None:
    assert meshcheck.winding(CUBE_POINTS, flipped(CUBE_FACES, 2)) == meshcheck.Winding(1, 0, 1)


def test_a_cube_turned_inside_out_points_inward() -> None:
    inside_out = flipped(CUBE_FACES, 0, 1, 2, 3, 4, 5)
    assert meshcheck.winding(CUBE_POINTS, inside_out) == meshcheck.Winding(0, 1, 1)


def test_an_open_shell_has_no_inside() -> None:
    lid_off = flipped(CUBE_FACES, 0, 1, 2, 3, 4, 5)[1:]
    assert meshcheck.winding(CUBE_POINTS, lid_off) == meshcheck.Winding(0, 0, 1)


def test_shells_are_judged_one_by_one() -> None:
    far = [(x + 5.0, y, z) for x, y, z in CUBE_POINTS]
    second = [[index + 8 for index in face[::-1]] for face in CUBE_FACES]
    found = meshcheck.winding(CUBE_POINTS + far, CUBE_FACES + second)
    assert found == meshcheck.Winding(0, 1, 2)


def test_a_cube_is_symmetric_across_its_middle() -> None:
    found = meshcheck.symmetry(CUBE_POINTS, 0, 0.5, 1e-3)
    assert (found.matched, found.unmatched, found.sample) == (8, 0, [])


def test_a_moved_point_and_its_twin_lose_their_match() -> None:
    points = list(CUBE_POINTS)
    points[6] = (1.0, 1.4, 1.0)
    found = meshcheck.symmetry(points, 0, 0.5, 1e-3)
    assert found.unmatched == 2
    assert sorted(found.sample) == [(0.0, 1.0, 1.0), (1.0, 1.4, 1.0)]


def test_points_on_the_plane_are_their_own_twins() -> None:
    points = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.5, 0.0), (-1.0, 0.5, 0.0)]
    assert meshcheck.symmetry(points, 0, 0.0, 1e-3).unmatched == 0
    assert meshcheck.symmetry(points, 0, 0.2, 1e-3).unmatched == 4


def test_moves_count_what_changed_and_where() -> None:
    after = list(CUBE_POINTS)
    after[6] = (1.0, 1.0, 3.0)
    after[7] = (0.0, 1.0, 2.0)
    found = meshcheck.moves(CUBE_POINTS, after, 1e-6)
    assert (found.moved, found.max_move, found.mean_move) == (2, 2.0, 1.5)
    assert found.box == ((0.0, 1.0, 2.0), (1.0, 1.0, 3.0))
    assert meshcheck.moves(CUBE_POINTS, CUBE_POINTS, 1e-6) == meshcheck.Moves(0, 0.0, 0.0, None)


def test_uv_faces_that_run_backwards_are_flipped() -> None:
    forward = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert meshcheck.flipped_uv_faces([forward, forward[::-1], forward]) == 1
    assert meshcheck.flipped_uv_faces([[(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)]]) == 0
