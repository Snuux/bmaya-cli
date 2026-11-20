"""What a model file holds, the same shape for every format the CLI reads."""

from __future__ import annotations

from typing import NamedTuple

Vec = tuple[float, float, float]


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
