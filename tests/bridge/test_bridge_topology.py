import math

from bmaya_bridge import topology

SIDES = 8


def tube(
    heights: list[float], radius: float, taper: float = 0.0, x: float = 0.0
) -> tuple[list[topology.Vec], list[list[int]]]:
    """A capped quad tube along +y with a loop at each height, thinner by `taper` per unit."""
    points: list[topology.Vec] = []
    for height in heights:
        size = radius - taper * height
        for side in range(SIDES):
            angle = 2.0 * math.pi * side / SIDES
            points.append((x + size * math.cos(angle), height, size * math.sin(angle)))
    faces = []
    for ring in range(len(heights) - 1):
        for side in range(SIDES):
            a = ring * SIDES + side
            b = ring * SIDES + (side + 1) % SIDES
            faces.append([a, b, b + SIDES, a + SIDES])
    bottom = len(points)
    points.append((x, heights[0] - radius, 0.0))
    top = len(points)
    points.append((x, heights[-1] + radius, 0.0))
    last = (len(heights) - 1) * SIDES
    for side in range(SIDES):
        nxt = (side + 1) % SIDES
        faces.append([nxt, side, bottom])
        faces.append([last + side, last + nxt, top])
    return points, faces


def finger(x: float = 0.0, radius: float = 1.0) -> tuple[list[topology.Vec], list[list[int]]]:
    heights = [0.0, 0.25, 0.5, 1.1, 1.35, 1.6, 2.1]
    return tube(heights, radius, taper=0.05, x=x)


def test_loops_go_around_the_tube() -> None:
    points, faces = finger()
    found = topology.loops(points, faces)
    assert len(found) == 7
    assert all(len(loop.ids) == SIDES for loop in found)
    lowest = min(found, key=lambda loop: loop.center[1])
    assert abs(lowest.center[1]) < 1e-9 and abs(lowest.radius - 1.0) < 1e-9
    assert abs(abs(lowest.normal[1]) - 1.0) < 1e-6


def test_tubes_runs_and_joint_places() -> None:
    points, faces = finger()
    found = topology.loops(points, faces)
    tubes = topology.tubes(found)
    assert len(tubes) == 1
    one = tubes[0]
    heights = [round(found[index].center[1], 6) for index in one.loops]
    assert heights == [0.0, 0.25, 0.5, 1.1, 1.35, 1.6, 2.1]
    assert [len(run) for run in one.runs] == [3, 3, 1]
    places = [topology.joint_place(loops, found)[1] for loops in topology.joint_loops(one)]
    assert [round(place, 6) for place in places] == [0.25, 1.35]
    assert one.axis[1] > 0.999
    assert [round(gap, 6) for gap in one.gaps] == [0.25, 0.25, 0.6, 0.25, 0.25, 0.5]


def test_a_wider_gap_setting_merges_runs() -> None:
    points, faces = finger()
    tube_one = topology.tubes(topology.loops(points, faces), gap=3.5)[0]
    assert [len(run) for run in tube_one.runs] == [7]


def test_separate_tubes_and_lone_loops() -> None:
    first_points, first_faces = finger()
    second_points, second_faces = finger(x=10.0, radius=2.0)
    shift = len(first_points)
    points = first_points + second_points
    faces = first_faces + [[index + shift for index in face] for face in second_faces]
    tubes = topology.tubes(topology.loops(points, faces))
    assert len(tubes) == 2
    found = topology.loops(points, faces)
    assert found[tubes[0].loops[0]].radius < found[tubes[1].loops[0]].radius
    assert topology.shells(faces) == 2


def test_thick_end_comes_first() -> None:
    points, faces = tube([0.0, 1.0, 2.0, 3.0], 1.0, taper=-0.1)
    found = topology.loops(points, faces)
    one = topology.tubes(found)[0]
    assert found[one.loops[0]].center[1] == 3.0
    assert one.axis[1] < -0.999


def test_counts_of_a_mesh() -> None:
    points, faces = finger()
    assert topology.valence(faces) == {4: 56, 8: 2}
    assert topology.border_edges(faces) == 0
    assert topology.border_edges(faces[:-1]) == 3
    assert topology.loops(points, [[0, 1, 2]]) == []


def test_evenly_spaced_loops_mark_no_joint() -> None:
    points, faces = tube([0.0, 0.3, 0.6, 0.9, 1.2], 1.0)
    one = topology.tubes(topology.loops(points, faces))[0]
    assert [len(run) for run in one.runs] == [5]
    assert topology.joint_loops(one) == []


def test_an_even_run_puts_the_joint_between_its_two_middle_loops() -> None:
    points, faces = tube([0.0, 0.2, 0.4, 0.6, 1.2, 1.4, 1.6, 2.4], 1.0, taper=0.02)
    found = topology.loops(points, faces)
    one = topology.tubes(found)[0]
    assert [len(run) for run in one.runs] == [4, 3, 1]
    places = [topology.joint_place(loops, found)[1] for loops in topology.joint_loops(one)]
    assert [round(place, 6) for place in places] == [0.3, 1.4]


def test_loops_side_by_side_are_not_one_tube() -> None:
    first_points, first_faces = tube([0.0, 0.25], 1.0)
    second_points, second_faces = tube([0.0, 0.25], 1.0, x=0.8)
    shift = len(first_points)
    points = first_points + second_points
    faces = first_faces + [[index + shift for index in face] for face in second_faces]
    assert len(topology.tubes(topology.loops(points, faces))) == 2


def test_a_bent_tube_keeps_its_order() -> None:
    rings = []
    for step in range(50):
        angle = math.radians(step * 4.0)
        rings.append((10.0 * math.cos(angle), 10.0 * math.sin(angle), angle))
    points: list[topology.Vec] = []
    for cx, cy, angle in rings:
        for side in range(SIDES):
            turn = 2.0 * math.pi * side / SIDES
            radial = (math.cos(angle), math.sin(angle), 0.0)
            px = cx + 1.0 * math.cos(turn) * radial[0]
            py = cy + 1.0 * math.cos(turn) * radial[1]
            points.append((px, py, 1.0 * math.sin(turn)))
    faces = []
    for ring in range(len(rings) - 1):
        for side in range(SIDES):
            a = ring * SIDES + side
            b = ring * SIDES + (side + 1) % SIDES
            faces.append([a, b, b + SIDES, a + SIDES])
    found = topology.loops(points, faces)
    one = topology.tubes(found)[0]
    angles = [
        math.atan2(found[index].center[1], found[index].center[0]) % (2.0 * math.pi)
        for index in one.loops
    ]
    assert angles == sorted(angles) or angles == sorted(angles, reverse=True)
    assert len(one.loops) == 48


def test_a_finger_bent_at_its_knuckles_keeps_both_joints() -> None:
    heights = [-0.6, 0.0, 0.25, 0.5, 1.1, 1.35, 1.6, 2.1, 2.7]
    for bend in (45.0, 90.0):
        half = math.radians(bend / 2.0)
        turns = {2: half, 3: half, 5: half, 6: half}
        heading = 0.0
        previous = 0.0
        x = y = 0.0
        points: list[topology.Vec] = []
        for index, height in enumerate(heights):
            heading += turns.get(index, 0.0)
            step = height - previous
            previous = height
            x += step * math.sin(heading)
            y += step * math.cos(heading)
            for side in range(SIDES):
                angle = 2.0 * math.pi * side / SIDES
                points.append(
                    (
                        x + math.cos(angle) * math.cos(heading),
                        y - math.cos(angle) * math.sin(heading),
                        math.sin(angle),
                    )
                )
        faces = []
        for ring in range(len(heights) - 1):
            for side in range(SIDES):
                a = ring * SIDES + side
                b = ring * SIDES + (side + 1) % SIDES
                faces.append([a, b, b + SIDES, a + SIDES])
        found = topology.loops(points, faces)
        tubes = topology.tubes(found)
        assert len(tubes) == 1, bend
        assert len(topology.joint_loops(tubes[0])) == 2, (bend, tubes[0].runs)
