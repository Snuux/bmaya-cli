"""What a model file holds, the same shape for every format the CLI reads."""

from __future__ import annotations

from typing import NamedTuple

Vec = tuple[float, float, float]


MOST_PARTS = 8
STEP_TOLERANCE = 0.02


def usual_gap(gaps: dict[float, int]) -> float | None:
    """The spacing most keys have: baked curves end on a shorter gap at the clip's last frame."""
    if not gaps:
        return None
    return max(gaps, key=lambda gap: (gaps[gap], -gap))


def shared_gap(gaps: dict[float, int]) -> float | None:
    """The step every key sits on. Thinned keys are whole steps apart, a different number
    each time, so the usual gap would be several frames."""
    if not gaps:
        return None
    smallest = min(gaps)
    for parts in range(1, MOST_PARTS + 1):
        step = smallest / parts
        if all(abs(gap / step - round(gap / step)) < STEP_TOLERANCE for gap in gaps):
            return step
    return usual_gap(gaps)


class ModelFileError(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class Mesh(NamedTuple):
    name: str
    verts: int
    polys: int
    tris: int
    uv_sets: list[str]
    materials: list[str]
    influences: int
    unweighted: int


class Joint(NamedTuple):
    name: str
    parent: str
    at: Vec
    axes: tuple[Vec, Vec, Vec]


class Take(NamedTuple):
    name: str
    seconds: tuple[float, float]
    curves: int
    animated: int
    keys: tuple[float, float] | None
    key_step: float | None


class Texture(NamedTuple):
    material: str
    slot: str
    path: str
    found: str


class Contents(NamedTuple):
    path: str
    format: str
    version: int | str
    size: int
    unit: str
    unit_scale: float
    up: str
    fps: float | None
    fps_from: str
    meshes: list[Mesh]
    joints: list[Joint]
    joints_from: str
    takes: list[Take]
    textures: list[Texture]


class Layout(NamedTuple):
    """One mesh's UV set: the UVs, and each face's UV indices corner by corner (an empty
    list for a face without UVs)."""

    mesh: str
    uvs: list[tuple[float, float]]
    faces: list[list[int]]
    uv_set: str = ""
