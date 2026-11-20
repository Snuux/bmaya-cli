import json
import math
from pathlib import Path
from typing import Any

import pytest

from bmaya_cli.fbxcontents import KTIME_PER_SECOND, read_fbx

from .fbx_writer import Arr, N, name, p70, write

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TRUTH = json.loads((FIXTURES / "fixtures.json").read_text())


def angle(a: tuple[float, ...], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


@pytest.mark.parametrize("file", sorted(TRUTH))
def test_matches_what_maya_wrote(file: str) -> None:
    truth = TRUTH[file]
    contents = read_fbx(FIXTURES / file)
    assert contents.unit == "cm"
    assert contents.up == "y"
    assert contents.fps == truth["fps"]
    meshes = {mesh.name: mesh for mesh in contents.meshes}
    for mesh_name, expected in truth.get("meshes", {}).items():
        mesh = meshes[mesh_name]
        assert (mesh.verts, mesh.polys, mesh.tris) == (
            expected["verts"],
            expected["polys"],
            expected["tris"],
        )
        assert mesh.influences == expected.get("influences", 0)
        assert mesh.unweighted == 0
    joints = {joint.name: joint for joint in contents.joints}
    assert set(joints) == set(truth.get("joints", {}))
    for joint_name, expected in truth.get("joints", {}).items():
        joint = joints[joint_name]
        assert joint.parent == expected["parent"]
        assert max(abs(a - b) for a, b in zip(joint.at, expected["at"], strict=True)) < 1e-6
        for axis, want in zip(joint.axes, expected["axes"], strict=True):
            assert angle(axis, want) < 1e-4
    takes = {take.name: take for take in contents.takes}
    for take_name, (first, last) in truth.get("takes", {}).items():
        take = takes[take_name] if take_name else contents.takes[0]
        fps = contents.fps
        assert [round(s * fps, 3) for s in take.seconds] == [first, last]
        assert take.curves > 0


def test_joint_positions_come_from_the_bind_pose_or_local_transforms() -> None:
    assert read_fbx(FIXTURES / "walker.fbx").joints_from == "bind_pose"
    assert read_fbx(FIXTURES / "bones.fbx").joints_from == "local"


def test_textures_are_found_by_path_or_beside_the_file(tmp_path: Path) -> None:
    contents = read_fbx(FIXTURES / "crate.fbx")
    assert [(t.material, t.slot) for t in contents.textures] == [("crate_mat", "DiffuseColor")]
    assert contents.textures[0].path.endswith("crate.png")
    assert contents.textures[0].found in ("absolute", "beside", "relative")
    lonely = tmp_path / "crate.fbx"
    lonely.write_bytes((FIXTURES / "crate.fbx").read_bytes())
    found = read_fbx(lonely).textures[0].found
    assert found in ("absolute", "")
    (tmp_path / "crate.png").write_bytes((FIXTURES / "crate.png").read_bytes())
    assert read_fbx(lonely).textures[0].found in ("absolute", "beside", "relative")


def test_fixtures_hold_no_home_folder() -> None:
    for file in FIXTURES.iterdir():
        data = file.read_bytes().lower()
        assert b"\\users\\" not in data and b"/users/" not in data, file.name


def settings(time_mode: int = 6, scale: float = 1.0) -> N:
    return N(
        "GlobalSettings",
        [],
        [
            p70(
                ("UpAxis", "int", "Integer", "", 1),
                ("UnitScaleFactor", "double", "Number", "", scale),
                ("TimeMode", "enum", "", "", time_mode),
            )
        ],
    )


def matrix_at(x: float, y: float, z: float) -> Arr:
    return Arr("d", [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, x, y, z, 1.0])


def build(
    objects: list[N], links: list[tuple[Any, ...]], time_mode: int = 6, scale: float = 1.0
) -> list[N]:
    definitions = N(
        "Definitions",
        [],
        [
            N(
                "ObjectType",
                ["AnimationStack"],
                [
                    N(
                        "PropertyTemplate",
                        ["FbxAnimStack"],
                        [p70(("LocalStart", "KTime", "", "", 0))],
                    )
                ],
            )
        ],
    )
    return [
        settings(time_mode, scale),
        definitions,
        N("Objects", [], objects),
        N("Connections", [], [N("C", list(link)) for link in links]),
    ]


def geometry(key: int, polygons: list[list[int]]) -> N:
    indices = []
    for poly in polygons:
        indices += poly[:-1] + [-poly[-1] - 1]
    return N(
        "Geometry",
        [key, name("", "Geometry"), "Mesh"],
        [N("Vertices", [Arr("d", [0.0] * 12)]), N("PolygonVertexIndex", [Arr("i", indices)])],
    )


def test_mesh_without_geometry_and_partial_weights(tmp_path: Path) -> None:
    objects = [
        N("Model", [10, name("ghost", "Model"), "Mesh"]),
        N("Model", [11, name("quad", "Model"), "Mesh"]),
        geometry(12, [[0, 1, 2, 3]]),
        N("Deformer", [13, name("skin", "Deformer"), "Skin"]),
        N(
            "Deformer",
            [14, name("", "SubDeformer"), "Cluster"],
            [
                N("Indexes", [Arr("i", [0, 1])]),
                N("Weights", [Arr("d", [1.0, 0.5])]),
                N("TransformLink", [matrix_at(1, 2, 3)]),
            ],
        ),
        N(
            "Deformer",
            [15, name("", "SubDeformer"), "Cluster"],
            [N("TransformLink", [matrix_at(0, 5, 0)])],
        ),
        N("Model", [16, name("bone", "Model"), "LimbNode"]),
        N("Model", [17, name("tip", "Model"), "LimbNode"]),
    ]
    links = [
        ("OO", 10, 0),
        ("OO", 11, 0),
        ("OO", 12, 11),
        ("OO", 13, 12),
        ("OO", 14, 13),
        ("OO", 15, 13),
        ("OO", 16, 14),
        ("OO", 17, 15),
        ("OO", 16, 0),
        ("OO", 17, 16),
    ]
    path = tmp_path / "skin.fbx"
    path.write_bytes(write(build(objects, links)))
    contents = read_fbx(path)
    ghost, quad = contents.meshes
    assert (ghost.name, ghost.polys, ghost.verts) == ("ghost", 0, 0)
    assert (quad.verts, quad.polys, quad.tris) == (4, 1, 2)
    assert (quad.influences, quad.unweighted) == (2, 2)
    assert [(j.name, j.parent, j.at) for j in contents.joints] == [
        ("bone", "", (1.0, 2.0, 3.0)),
        ("tip", "bone", (0.0, 5.0, 0.0)),
    ]


def test_fps_from_keys_when_the_file_has_no_rate_and_template_start(tmp_path: Path) -> None:
    step = KTIME_PER_SECOND // 15
    objects = [
        N(
            "AnimationStack",
            [20, name("idle", "AnimStack"), ""],
            [p70(("LocalStop", "KTime", "Time", "", step * 10))],
        ),
        N("AnimationLayer", [21, name("base", "AnimLayer"), ""]),
        N("AnimationCurveNode", [22, name("T", "AnimCurveNode"), ""]),
        N(
            "AnimationCurve",
            [23, name("", "AnimCurve"), ""],
            [N("KeyTime", [Arr("l", [0, step, step * 2])])],
        ),
        N("AnimationCurve", [24, name("", "AnimCurve"), ""], [N("KeyTime", [Arr("l", [])])]),
        N("Model", [25, name("box", "Model"), "Null"]),
        N("AnimationStack", [26, name("empty", "AnimStack"), ""]),
    ]
    links = [
        ("OO", 21, 20),
        ("OO", 22, 21),
        ("OP", 23, 22, "d|X"),
        ("OP", 24, 22, "d|Y"),
        ("OP", 22, 25, "Lcl Translation"),
    ]
    path = tmp_path / "idle.fbx"
    path.write_bytes(write(build(objects, links, time_mode=0)))
    contents = read_fbx(path)
    assert (contents.fps, contents.fps_from) == (15.0, "keys")
    idle, empty = contents.takes
    assert idle.seconds == (0.0, pytest.approx(10 / 15))
    assert (idle.curves, idle.animated) == (1, 1)
    assert idle.keys == (0.0, pytest.approx(2 / 15))
    assert (empty.curves, empty.keys) == (0, None)


def test_units_and_up_axis(tmp_path: Path) -> None:
    path = tmp_path / "m.fbx"
    path.write_bytes(write(build([], [], scale=100.0)))
    contents = read_fbx(path)
    assert (contents.unit, contents.unit_scale, contents.up, contents.fps) == (
        "m",
        100.0,
        "y",
        30.0,
    )


def test_a_joint_under_a_null_hangs_under_the_joint_above(tmp_path: Path) -> None:
    objects = [
        N("Model", [20, name("hand", "Model"), "LimbNode"]),
        N("Model", [21, name("transform1", "Model"), "Null"]),
        N("Model", [22, name("finger", "Model"), "LimbNode"]),
    ]
    links = [("OO", 20, 0), ("OO", 21, 20), ("OO", 22, 21)]
    path = tmp_path / "gap.fbx"
    path.write_bytes(write(build(objects, links)))
    joints = read_fbx(path).joints
    assert [(joint.name, joint.parent) for joint in joints] == [
        ("hand", ""),
        ("finger", "hand"),
    ]
