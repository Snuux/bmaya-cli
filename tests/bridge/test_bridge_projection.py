from __future__ import annotations

from bmaya_bridge.projection import View, inside, project

IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


def test_perspective_camera_at_the_origin() -> None:
    view = View(IDENTITY, -1.0, 1.0, -0.5, 0.5, False, 0.1)
    assert project((0, 0, -5), view, 400, 200) == (200, 100)
    assert project((5, 0, -5), view, 400, 200) == (400, 100)
    assert project((0, 2.5, -5), view, 400, 200) == (200, 0)
    assert project((0, 0, 5), view, 400, 200) is None
    assert project((0, 0, -0.05), view, 400, 200) is None


def test_moved_camera_uses_its_inverse_matrix() -> None:
    moved_back = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, -10.0, 1.0)
    view = View(moved_back, -1.0, 1.0, -1.0, 1.0, False, 0.1)
    assert project((5, 0, 5), view, 100, 100) == (100, 50)


def test_orthographic_camera_ignores_depth() -> None:
    view = View(IDENTITY, -6.0, 6.0, -3.0, 3.0, True, 0.1)
    assert project((3, 0, -100), view, 1200, 600) == (900, 300)
    assert project((3, 0, -1), view, 1200, 600) == (900, 300)


def test_inside_the_frame() -> None:
    assert inside((0, 0), 10, 10) and inside((10, 10), 10, 10)
    assert not inside((-1, 5), 10, 10) and not inside((5, 11), 10, 10)
