from bmaya_bridge import softrender
from bmaya_bridge.projection import View
from bmaya_bridge.softrender import BACKGROUND, Canvas, Part

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
ORTHO = View(IDENTITY, -1.0, 1.0, -1.0, 1.0, True, 0.1)
PERSPECTIVE = View(IDENTITY, -1.0, 1.0, -1.0, 1.0, False, 0.1)
RED = (200, 0, 0)
BLUE = (0, 0, 200)
SIZE = 100


def quad(corners: list[tuple[float, float, float]], color: tuple[int, int, int]) -> Part:
    return Part(corners, [0, 1, 2, 0, 2, 3], [(0, 1), (1, 2), (2, 3), (3, 0)], color)


def facing_quad(low: float, high: float, z: float, color: tuple[int, int, int]) -> Part:
    return quad([(low, low, z), (high, low, z), (high, high, z), (low, high, z)], color)


def is_shade_of(color: tuple[int, int, int], base: tuple[int, int, int]) -> bool:
    """A lit or darkened `base`: the channels that are 0 in it stay 0."""
    dark = all(got == 0 for got, want in zip(color, base, strict=True) if want == 0)
    return color != BACKGROUND and dark


def test_a_triangle_covers_the_pixels_inside_it() -> None:
    canvas = Canvas(10, 10)
    softrender.fill_triangle(canvas, (0, 0, 1.0), (10, 0, 1.0), (0, 10, 1.0), softrender.pack(RED))
    assert canvas.color_at(1, 1) == RED
    assert canvas.color_at(8, 8) == BACKGROUND
    assert canvas.covered() == 45


def test_the_nearer_surface_wins_whatever_the_drawing_order() -> None:
    near = facing_quad(-0.5, 0.5, -1.0, RED)
    far = facing_quad(-0.9, 0.9, -2.0, BLUE)
    for parts in ([near, far], [far, near]):
        canvas = softrender.render(parts, ORTHO, SIZE, SIZE, wire=False)
        assert is_shade_of(canvas.color_at(50, 50), RED)
        assert is_shade_of(canvas.color_at(10, 50), BLUE)


def test_a_small_box_on_a_big_floor_is_not_painted_over() -> None:
    # The floor's center is nearer than the box, which breaks sorting faces by their center.
    floor = quad([(-0.9, -0.9, -1.0), (0.9, -0.9, -1.0), (0.9, 0.9, -9.0), (-0.9, 0.9, -9.0)], BLUE)
    box = facing_quad(0.5, 0.8, -6.0, RED)
    for view in (ORTHO, View(IDENTITY, -0.2, 0.2, -0.2, 0.2, False, 0.1)):
        canvas = softrender.render([box, floor], view, SIZE, SIZE, wire=False)
        if view.ortho:
            assert is_shade_of(canvas.color_at(82, 18), RED)
        else:
            assert is_shade_of(canvas.color_at(77, 23), RED)
        assert is_shade_of(canvas.color_at(20, 80), BLUE)


def test_two_surfaces_that_cross_each_show_their_near_half() -> None:
    rising = quad([(-0.8, -0.8, -3.0), (0.8, -0.8, -1.0), (0.8, 0.8, -1.0), (-0.8, 0.8, -3.0)], RED)
    falling = quad(
        [(-0.8, -0.8, -1.0), (0.8, -0.8, -3.0), (0.8, 0.8, -3.0), (-0.8, 0.8, -1.0)], BLUE
    )
    canvas = softrender.render([rising, falling], ORTHO, SIZE, SIZE, wire=False)
    assert is_shade_of(canvas.color_at(25, 50), BLUE)
    assert is_shade_of(canvas.color_at(75, 50), RED)


def test_a_face_seen_from_behind_is_darker() -> None:
    front = softrender.render([facing_quad(-0.5, 0.5, -1.0, RED)], ORTHO, SIZE, SIZE, wire=False)
    corners = [(-0.5, -0.5, -1.0), (-0.5, 0.5, -1.0), (0.5, 0.5, -1.0), (0.5, -0.5, -1.0)]
    back = softrender.render([quad(corners, RED)], ORTHO, SIZE, SIZE, wire=False)
    assert back.color_at(50, 50)[0] < front.color_at(50, 50)[0] * 0.6


def test_edges_are_drawn_on_top_and_hidden_behind_nearer_faces() -> None:
    near = facing_quad(-0.5, 0.5, -1.0, RED)
    far = facing_quad(-0.3, 0.3, -2.0, BLUE)
    canvas = softrender.render([near, far], ORTHO, SIZE, SIZE)
    assert canvas.color_at(50, 25) == softrender.EDGE
    assert is_shade_of(canvas.color_at(50, 35), RED)

    xray = softrender.render([near, far], ORTHO, SIZE, SIZE, style="xray")
    assert xray.color_at(50, 35) == softrender.mix(BLUE, BACKGROUND, 0.35)


def test_a_silhouette_is_one_color_without_edges() -> None:
    canvas = softrender.render(
        [facing_quad(-0.5, 0.5, -1.0, RED)], ORTHO, SIZE, SIZE, style="silhouette"
    )
    assert canvas.color_at(50, 50) == softrender.SILHOUETTE
    assert canvas.color_at(50, 25) == softrender.SILHOUETTE
    assert canvas.color_at(5, 5) == BACKGROUND


def test_a_point_behind_a_perspective_camera_has_no_place() -> None:
    assert softrender.place((0.0, 0.0, 1.0), PERSPECTIVE, SIZE, SIZE) is None
    spot, _ = softrender.place((0.0, 0.0, -2.0), PERSPECTIVE, SIZE, SIZE)
    assert spot == (50.0, 50.0, 0.5)


def test_a_cut_keeps_the_low_side_and_reports_the_outline() -> None:
    part = quad([(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)], RED)
    result = softrender.cut([part], 0, 0.25)
    kept = result.parts[0]
    assert max(kept.points[index][0] for index in kept.triangles) == 0.25
    assert min(kept.points[index][0] for index in kept.triangles) == -1.0
    assert max(max(kept.points[a][0], kept.points[b][0]) for a, b in kept.edges) == 0.25
    ends = sorted(point for segment in result.outline for point in segment)
    assert ends[0] == (0.25, 0.0, 0.0)
    assert ends[-1] == (0.25, 1.0, 0.0)
    area = 0.0
    for start in range(0, len(kept.triangles), 3):
        a, b, c = (kept.points[index] for index in kept.triangles[start : start + 3])
        area += abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0
    assert abs(area - 1.25) < 1e-9


def test_colors_by_point_blend_across_the_faces() -> None:
    plain = facing_quad(-0.9, 0.9, -1.0, RED)
    part = plain._replace(colors=[RED, BLUE, BLUE, RED])
    canvas = softrender.render([part], ORTHO, SIZE, SIZE, style="weights", wire=False)
    left, middle, right = (canvas.color_at(x, 50) for x in (8, 50, 92))
    assert left[0] > 150 and left[2] < 40, left
    assert right[2] > 150 and right[0] < 40, right
    assert 60 < middle[0] < 140 and 60 < middle[2] < 140, middle
    flat = softrender.render([plain], ORTHO, SIZE, SIZE, style="weights", wire=False)
    assert flat.color_at(50, 50) == softrender.mix(RED, BACKGROUND, 0.72)


def test_a_cut_blends_the_colors_of_the_points_it_adds() -> None:
    corners = [(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
    part = quad(corners, RED)._replace(
        colors=[(0, 0, 0), (200, 200, 200), (200, 200, 200), (0, 0, 0)]
    )
    kept = softrender.cut([part], 0, 0.0).parts[0]
    assert kept.colors is not None and len(kept.colors) == len(kept.points)
    assert kept.colors[len(corners)] == (100, 100, 100)


def test_bounds_and_counts() -> None:
    parts = [facing_quad(-0.5, 0.5, -1.0, RED), facing_quad(0.0, 2.0, 3.0, BLUE)]
    assert softrender.bounds(parts) == ((-0.5, -0.5, -1.0), (2.0, 2.0, 3.0))
    assert softrender.bounds([]) is None
    assert softrender.triangle_count(parts) == 4


def test_dense_meshes_lose_their_edges() -> None:
    assert softrender.wire_fits(5000, 400)
    assert not softrender.wire_fits(50000, 400)
    assert softrender.wire_fits(50000, 1000)


def test_marks_draw_over_the_mesh_without_a_depth_test() -> None:
    view = View((1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -5, 1), -2, 2, -2, 2, True, 0.1)
    wall = softrender.Part(
        [(-2.0, -2.0, 1.0), (2.0, -2.0, 1.0), (2.0, 2.0, 1.0), (-2.0, 2.0, 1.0)],
        [0, 1, 2, 0, 2, 3],
        [],
        (200, 200, 200),
    )
    red = (230, 20, 20)
    blue = (20, 20, 230)
    loop = softrender.Mark([(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0)], red, True)
    dot = softrender.Mark([(0.0, 0.0, -1.0)], blue, dots=True)
    canvas = softrender.render([wall], view, 40, 40, "parts", False, 1, None, [loop, dot])
    assert canvas.color_at(20, 30) == red
    assert canvas.color_at(20, 20) == blue
    assert canvas.color_at(15, 25) == red
    open_line = softrender.Mark(loop.points, red)
    plain = softrender.render([wall], view, 40, 40, "parts", False, 1, None, [open_line])
    assert plain.color_at(15, 25) != red
