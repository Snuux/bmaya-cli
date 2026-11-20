import json
import struct
from pathlib import Path
from typing import Any

import pytest

from bmaya_cli.contents import Contents, ModelFileError
from bmaya_cli.gltfcontents import read_gltf


def identity_with(x: float, y: float, z: float) -> list[float]:
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, x, y, z, 1.0]


def packed(fmt: str, rows: list[list[float]]) -> bytes:
    return b"".join(struct.pack("<" + fmt, *row) for row in rows)


def model() -> tuple[dict[str, Any], bytes]:
    blobs = [
        packed("fff", [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]),
        packed("H", [[0], [1], [2], [2], [1], [3]]),
        packed("ffff", [[1, 0, 0, 0], [0.5, 0.5, 0, 0], [0, 0, 0, 0], [0, 1, 0, 0]]),
        packed("f" * 16, [identity_with(0, 0, 0), identity_with(0, -2, 0)]),
        packed("f", [[0.0], [1 / 30], [2 / 30]]),
        packed("ffff", [[0, 0, 0, 1]] * 3),
    ]
    views = []
    data = b""
    for blob in blobs:
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": len(blob)})
        data += blob
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
        {"bufferView": 1, "componentType": 5123, "count": 6, "type": "SCALAR"},
        {"bufferView": 2, "componentType": 5126, "count": 4, "type": "VEC4"},
        {"bufferView": 3, "componentType": 5126, "count": 2, "type": "MAT4"},
        {
            "bufferView": 4,
            "componentType": 5126,
            "count": 3,
            "type": "SCALAR",
            "min": [0.0],
            "max": [2 / 30],
        },
        {"bufferView": 5, "componentType": 5126, "count": 3, "type": "VEC4"},
    ]
    doc = {
        "asset": {"version": "2.0"},
        "nodes": [
            {"name": "card", "mesh": 0, "skin": 0},
            {"name": "root", "children": [2], "translation": [0, 0, 0]},
            {"name": "tip", "translation": [0, 2, 0]},
        ],
        "meshes": [
            {
                "name": "card_mesh",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "TEXCOORD_0": 0, "WEIGHTS_0": 2},
                        "indices": 1,
                        "material": 0,
                    }
                ],
            }
        ],
        "skins": [{"joints": [1, 2], "inverseBindMatrices": 3}],
        "animations": [
            {
                "name": "wave",
                "samplers": [{"input": 4, "output": 5}],
                "channels": [{"sampler": 0, "target": {"node": 2, "path": "rotation"}}],
            }
        ],
        "materials": [
            {"name": "paint", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
        ],
        "textures": [{"source": 0}],
        "images": [{"uri": "paint.png"}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(data)}],
    }
    return doc, data


def glb(doc: dict[str, Any], data: bytes) -> bytes:
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    data += b"\x00" * (-len(data) % 4)
    body = (
        struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(data), 0x004E4942)
        + data
    )
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def check(contents: Contents) -> None:
    (mesh,) = contents.meshes
    assert (mesh.name, mesh.verts, mesh.tris, mesh.uv_sets) == ("card", 4, 2, ["TEXCOORD_0"])
    assert (mesh.materials, mesh.influences, mesh.unweighted) == (["paint"], 2, 1)
    assert [(j.name, j.parent, j.at) for j in contents.joints] == [
        ("root", "", (0.0, 0.0, 0.0)),
        ("tip", "root", (0.0, 2.0, 0.0)),
    ]
    assert contents.joints_from == "bind_pose"
    (take,) = contents.takes
    assert (take.name, take.curves, take.animated) == ("wave", 1, 1)
    assert take.seconds == pytest.approx((0.0, 2 / 30))
    assert contents.fps == 30.0
    assert (contents.unit, contents.up) == ("m", "y")


def test_reads_a_glb(tmp_path: Path) -> None:
    doc, data = model()
    path = tmp_path / "card.glb"
    path.write_bytes(glb(doc, data))
    contents = read_gltf(path)
    check(contents)
    assert contents.format == "glb"
    assert contents.textures[0].found == ""
    (tmp_path / "paint.png").write_bytes(b"png")
    assert read_gltf(path).textures[0].found == "relative"


def test_reads_a_gltf_with_an_outside_buffer(tmp_path: Path) -> None:
    doc, data = model()
    doc["buffers"][0]["uri"] = "card.bin"
    (tmp_path / "card.bin").write_bytes(data)
    path = tmp_path / "card.gltf"
    path.write_text(json.dumps(doc))
    contents = read_gltf(path)
    check(contents)
    assert contents.format == "gltf"


def test_joint_positions_fall_back_to_nodes_without_a_buffer(tmp_path: Path) -> None:
    doc, _ = model()
    doc["buffers"][0]["uri"] = "missing.bin"
    path = tmp_path / "card.gltf"
    path.write_text(json.dumps(doc))
    contents = read_gltf(path)
    assert contents.joints_from == "nodes"
    assert contents.joints[1].at == (0.0, 2.0, 0.0)


def test_refuses_gltf_1(tmp_path: Path) -> None:
    path = tmp_path / "old.gltf"
    path.write_text(json.dumps({"asset": {"version": "1.0"}}))
    with pytest.raises(ModelFileError, match="2.0"):
        read_gltf(path)
