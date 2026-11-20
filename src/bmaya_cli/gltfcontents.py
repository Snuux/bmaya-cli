"""Meshes, skeleton, animations and textures of a glTF 2.0 file (.gltf or .glb)."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

from bmaya_cli.contents import Contents, Joint, Mesh, ModelFileError, Take, Texture, usual_gap
from bmaya_cli.matrix import (
    Matrix,
    column_major,
    identity,
    inverse_affine,
    multiply,
    position_and_axes,
    quaternion,
    scaling,
    translation,
)

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
COMPONENT_TYPES = {
    5120: ("b", 127.0),
    5121: ("B", 255.0),
    5122: ("h", 32767.0),
    5123: ("H", 65535.0),
    5125: ("I", 0.0),
    5126: ("f", 0.0),
}
TRIANGLES = 4
TEXTURE_SLOTS = (
    ("pbrMetallicRoughness", "baseColorTexture"),
    ("pbrMetallicRoughness", "metallicRoughnessTexture"),
    ("", "normalTexture"),
    ("", "occlusionTexture"),
    ("", "emissiveTexture"),
)
WEIGHT_EPSILON = 1e-6


class GltfError(ModelFileError):
    pass


class Buffers:
    def __init__(self, doc: dict[str, Any], folder: Path, glb_bin: bytes | None) -> None:
        self.doc = doc
        self.folder = folder
        self.glb_bin = glb_bin
        self.loaded: dict[int, bytes | None] = {}

    def buffer(self, index: int) -> bytes | None:
        if index not in self.loaded:
            self.loaded[index] = self.load(index)
        return self.loaded[index]

    def load(self, index: int) -> bytes | None:
        entry = self.doc.get("buffers", [])[index]
        uri = entry.get("uri")
        if uri is None:
            return self.glb_bin
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        path = self.folder / uri
        return path.read_bytes() if path.is_file() else None

    def read(self, accessor_index: int) -> list[list[float]] | None:
        accessors = self.doc.get("accessors", [])
        if not 0 <= accessor_index < len(accessors):
            return None
        accessor = accessors[accessor_index]
        if "bufferView" not in accessor or "sparse" in accessor:
            return None
        view = self.doc["bufferViews"][accessor["bufferView"]]
        data = self.buffer(view["buffer"])
        if data is None:
            return None
        code, scale = COMPONENT_TYPES[accessor["componentType"]]
        width = COMPONENTS[accessor["type"]]
        item = struct.Struct("<" + code * width)
        stride = view.get("byteStride") or item.size
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        normalized = accessor.get("normalized", False) and scale
        out = []
        for i in range(accessor["count"]):
            values = item.unpack_from(data, start + i * stride)
            out.append([v / scale for v in values] if normalized else list(values))
        return out


def split_glb(data: bytes) -> tuple[dict[str, Any], bytes | None]:
    if len(data) < 20:
        raise GltfError("The GLB file ends inside its header.", "Export it again.")
    _, version, _ = struct.unpack_from("<4sII", data, 0)
    if version != 2:
        raise GltfError(f"GLB version {version} is not read; only glTF 2.0 is.")
    doc: dict[str, Any] | None = None
    binary = None
    at = 12
    while at + 8 <= len(data):
        size, kind = struct.unpack_from("<II", data, at)
        chunk = data[at + 8 : at + 8 + size]
        if kind == JSON_CHUNK:
            doc = json.loads(chunk)
        elif kind == BIN_CHUNK:
            binary = chunk
        at += 8 + size
    if doc is None:
        raise GltfError("The GLB file has no JSON chunk.", "Export it again.")
    return doc, binary


def node_matrix(node: dict[str, Any]) -> Matrix:
    if "matrix" in node:
        return column_major(node["matrix"])
    t = node.get("translation", [0.0, 0.0, 0.0])
    r = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    s = node.get("scale", [1.0, 1.0, 1.0])
    m = multiply(translation((t[0], t[1], t[2])), quaternion(r[0], r[1], r[2], r[3]))
    return multiply(m, scaling((s[0], s[1], s[2])))


def world_matrices(doc: dict[str, Any]) -> dict[int, Matrix]:
    nodes = doc.get("nodes", [])
    parent = {}
    for index, node in enumerate(nodes):
        for kid in node.get("children", []):
            parent[kid] = index
    out: dict[int, Matrix] = {}

    def world(index: int) -> Matrix:
        if index not in out:
            base = world(parent[index]) if index in parent else identity()
            out[index] = multiply(base, node_matrix(nodes[index]))
        return out[index]

    for index in range(len(nodes)):
        world(index)
    return out


def node_name(doc: dict[str, Any], index: int) -> str:
    return doc["nodes"][index].get("name", f"node{index}")


def read_meshes(doc: dict[str, Any], buffers: Buffers) -> list[Mesh]:
    out = []
    materials = doc.get("materials", [])
    skins = doc.get("skins", [])
    for index, node in enumerate(doc.get("nodes", [])):
        if "mesh" not in node:
            continue
        mesh = doc["meshes"][node["mesh"]]
        verts = 0
        tris = 0
        uv_sets: list[str] = []
        names: list[str] = []
        unweighted = 0
        for primitive in mesh.get("primitives", []):
            attributes = primitive.get("attributes", {})
            count = (
                doc["accessors"][attributes["POSITION"]]["count"] if "POSITION" in attributes else 0
            )
            verts += count
            if primitive.get("mode", TRIANGLES) == TRIANGLES:
                if "indices" in primitive:
                    tris += doc["accessors"][primitive["indices"]]["count"] // 3
                else:
                    tris += count // 3
            for key in sorted(attributes):
                if key.startswith("TEXCOORD_") and key not in uv_sets:
                    uv_sets.append(key)
            material = primitive.get("material")
            if material is not None and 0 <= material < len(materials):
                name = materials[material].get("name", f"material{material}")
                if name not in names:
                    names.append(name)
            if "skin" in node and "WEIGHTS_0" in attributes:
                weights = buffers.read(attributes["WEIGHTS_0"])
                if weights is not None:
                    unweighted += sum(1 for row in weights if sum(row) < WEIGHT_EPSILON)
        influences = len(skins[node["skin"]].get("joints", [])) if "skin" in node else 0
        name = node.get("name") or mesh.get("name") or f"node{index}"
        out.append(Mesh(name, verts, tris, tris, uv_sets, names, influences, unweighted))
    return out


def read_skeleton(doc: dict[str, Any], buffers: Buffers) -> tuple[list[Joint], str]:
    skins = doc.get("skins", [])
    if not skins:
        return [], ""
    worlds = world_matrices(doc)
    bind: dict[int, Matrix] = {}
    for skin in skins:
        if "inverseBindMatrices" not in skin:
            continue
        rows = buffers.read(skin["inverseBindMatrices"])
        if rows is None:
            continue
        for joint, values in zip(skin["joints"], rows, strict=False):
            bind.setdefault(joint, inverse_affine(column_major(values)))
    members: list[int] = []
    for skin in skins:
        for joint in skin.get("joints", []):
            if joint not in members:
                members.append(joint)
    parent = {}
    for index, node in enumerate(doc.get("nodes", [])):
        for kid in node.get("children", []):
            parent[kid] = index
    order: list[int] = []

    def visit(index: int) -> None:
        order.append(index)
        for kid in doc["nodes"][index].get("children", []):
            if kid in members:
                visit(kid)

    for joint in members:
        if parent.get(joint) not in members:
            visit(joint)
    out = []
    for joint in order:
        at, axes = position_and_axes(bind.get(joint, worlds[joint]))
        up = parent.get(joint)
        out.append(
            Joint(node_name(doc, joint), node_name(doc, up) if up in members else "", at, axes)
        )
    if all(joint in bind for joint in order):
        return out, "bind_pose"
    return out, "mixed" if bind else "nodes"


def read_takes(doc: dict[str, Any], buffers: Buffers) -> list[Take]:
    out = []
    for index, animation in enumerate(doc.get("animations", [])):
        samplers = animation.get("samplers", [])
        first: float | None = None
        last: float | None = None
        gaps: dict[float, int] = {}
        targets = set()
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            if "node" in target:
                targets.add(target["node"])
            sampler = samplers[channel["sampler"]]
            accessor = doc["accessors"][sampler["input"]]
            if "min" in accessor and "max" in accessor:
                low, high = accessor["min"][0], accessor["max"][0]
                first = low if first is None else min(first, low)
                last = high if last is None else max(last, high)
            times = buffers.read(sampler["input"])
            if times:
                for a, b in zip(times, times[1:], strict=False):
                    gap = round(b[0] - a[0], 6)
                    if gap > 0:
                        gaps[gap] = gaps.get(gap, 0) + 1
        step = usual_gap(gaps)
        keys = (first, last) if first is not None and last is not None else None
        name = animation.get("name", f"animation{index}")
        seconds = keys or (0.0, 0.0)
        out.append(
            Take(name, seconds, len(animation.get("channels", [])), len(targets), keys, step)
        )
    return out


def read_textures(doc: dict[str, Any], folder: Path) -> list[Texture]:
    textures = doc.get("textures", [])
    images = doc.get("images", [])
    out = []
    for material in doc.get("materials", []):
        for group, slot in TEXTURE_SLOTS:
            holder = material.get(group, {}) if group else material
            info = holder.get(slot)
            if not info or not 0 <= info.get("index", -1) < len(textures):
                continue
            source = textures[info["index"]].get("source")
            if source is None or not 0 <= source < len(images):
                continue
            image = images[source]
            uri = image.get("uri", "")
            if not uri or uri.startswith("data:"):
                path = image.get("name", "")
                found = "embedded"
            else:
                path = uri
                found = "relative" if (folder / uri).is_file() else ""
            out.append(Texture(material.get("name", ""), slot, path, found))
    return out


def read_gltf(path: str | Path) -> Contents:
    file = Path(path)
    data = file.read_bytes()
    if data[:4] == GLB_MAGIC:
        doc, binary = split_glb(data)
    else:
        try:
            doc = json.loads(data)
        except ValueError as error:
            raise GltfError(f"This .gltf file is not valid JSON: {error}") from error
        binary = None
    version = str(doc.get("asset", {}).get("version", ""))
    if not version.startswith("2"):
        raise GltfError(f"glTF version {version or '?'} is not read; only glTF 2.0 is.")
    buffers = Buffers(doc, file.parent, binary)
    joints, joints_from = read_skeleton(doc, buffers)
    takes = read_takes(doc, buffers)
    steps = [take.key_step for take in takes if take.key_step]
    fps = round(1.0 / min(steps), 3) if steps else None
    return Contents(
        path=file.resolve().as_posix(),
        format="glb" if data[:4] == GLB_MAGIC else "gltf",
        version=version,
        size=len(data),
        unit="m",
        unit_scale=100.0,
        up="y",
        fps=fps,
        fps_from="keys" if fps else "",
        meshes=read_meshes(doc, buffers),
        joints=joints,
        joints_from=joints_from,
        takes=takes,
        textures=read_textures(doc, file.parent),
    )
