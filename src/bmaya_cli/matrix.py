"""4x4 matrices for column vectors: a point is transformed as M @ p, translation in column 3."""

from __future__ import annotations

import math

Vec = tuple[float, float, float]
Matrix = list[list[float]]


def identity() -> Matrix:
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def multiply(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def translation(v: Vec) -> Matrix:
    m = identity()
    for i in range(3):
        m[i][3] = v[i]
    return m


def scaling(v: Vec) -> Matrix:
    m = identity()
    for i in range(3):
        m[i][i] = v[i]
    return m


def turn(axis: str, degrees: float) -> Matrix:
    c = math.cos(math.radians(degrees))
    s = math.sin(math.radians(degrees))
    m = identity()
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    m[i][i] = c
    m[i][j] = -s
    m[j][i] = s
    m[j][j] = c
    return m


def euler(angles: Vec, order: str) -> Matrix:
    """Rotation that applies the axes in `order`, first letter first."""
    m = identity()
    for axis in order:
        m = multiply(turn(axis, angles["xyz".index(axis)]), m)
    return m


def quaternion(x: float, y: float, z: float, w: float) -> Matrix:
    m = identity()
    m[0][:3] = [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)]
    m[1][:3] = [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)]
    m[2][:3] = [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]
    return m


def transposed_rotation(m: Matrix) -> Matrix:
    out = identity()
    for r in range(3):
        for c in range(3):
            out[r][c] = m[c][r]
    return out


def column_major(values: list[float]) -> Matrix:
    """A matrix stored column by column, translation in 12-14, as FBX and glTF store them."""
    return [[values[c * 4 + r] for c in range(4)] for r in range(4)]


def inverse_affine(m: Matrix) -> Matrix:
    a = [row[:3] for row in m[:3]]
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    if abs(det) < 1e-12:
        return identity()
    inv = [
        [
            (
                a[(c + 1) % 3][(r + 1) % 3] * a[(c + 2) % 3][(r + 2) % 3]
                - a[(c + 1) % 3][(r + 2) % 3] * a[(c + 2) % 3][(r + 1) % 3]
            )
            / det
            for c in range(3)
        ]
        for r in range(3)
    ]
    out = identity()
    for r in range(3):
        out[r][:3] = inv[r]
        out[r][3] = -sum(inv[r][k] * m[k][3] for k in range(3))
    return out


def position_and_axes(m: Matrix) -> tuple[Vec, tuple[Vec, Vec, Vec]]:
    at = (m[0][3], m[1][3], m[2][3])
    axes = []
    for c in range(3):
        column = (m[0][c], m[1][c], m[2][c])
        length = math.sqrt(sum(v * v for v in column)) or 1.0
        axes.append((column[0] / length, column[1] / length, column[2] / length))
    return at, (axes[0], axes[1], axes[2])
