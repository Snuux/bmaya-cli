"""Meshes, skeleton, takes and textures of a binary FBX file."""

from __future__ import annotations

import math
from pathlib import Path

from bmaya_cli import fbxfile
from bmaya_cli.contents import Contents, Joint, Mesh, Take, Texture, Vec, usual_gap
from bmaya_cli.fbxfile import Document, Node, Value, child, children, props70, split_name
from bmaya_cli.matrix import (
    Matrix,
    column_major,
    euler,
    identity,
    multiply,
    position_and_axes,
    scaling,
    translation,
    transposed_rotation,
)

KTIME_PER_SECOND = 46186158000
CUSTOM_TIME_MODE = 14
# FbxTime::EMode. Maya writes 0 for rates it has no mode for (15 fps among them), and the
# rate then comes from the spacing of the baked keys.
TIME_MODES = {
    1: 120.0,
    2: 100.0,
    3: 60.0,
    4: 50.0,
    5: 48.0,
    6: 30.0,
    7: 30.0,
    8: 29.97,
    9: 29.97,
    10: 25.0,
    11: 24.0,
    12: 1000.0,
    13: 23.976,
    15: 96.0,
    16: 72.0,
    17: 59.94,
    18: 119.88,
}
# UnitScaleFactor is centimetres per file unit.
UNITS = {
    0.1: "mm",
    1.0: "cm",
    10.0: "dm",
    100.0: "m",
    100000.0: "km",
    2.54: "in",
    30.48: "ft",
    91.44: "yd",
}
ROTATION_ORDERS = ("xyz", "xzy", "yzx", "yxz", "zxy", "zyx")
JOINT_TYPES = frozenset({"LimbNode", "Limb", "Root"})
WEIGHT_EPSILON = 1e-6


def vec(values: list[Value] | None, default: Vec) -> Vec:
    if not values or len(values) < 3:
        return default
    return (float(values[0]), float(values[1]), float(values[2]))


def number(values: list[Value] | None, default: float) -> float:
    if not values:
        return default
    return float(values[0])


class Scene:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        objects = child(doc, "Objects")
        self.objects: dict[int, Node] = {}
        for node in objects.children if objects is not None else []:
            if node.props and isinstance(node.props[0], int):
                self.objects[node.props[0]] = node
        self.up_links: dict[int, list[tuple[int, str]]] = {}
        self.down_links: dict[int, list[tuple[int, str]]] = {}
        connections = child(doc, "Connections")
        for link in children(connections, "C") if connections is not None else []:
            if len(link.props) < 3:
                continue
            low, high = link.props[1], link.props[2]
            prop = str(link.props[3]) if len(link.props) > 3 else ""
            if isinstance(low, int) and isinstance(high, int):
                self.up_links.setdefault(low, []).append((high, prop))
                self.down_links.setdefault(high, []).append((low, prop))
        self.templates = self.read_templates()

    def read_templates(self) -> dict[str, dict[str, list[Value]]]:
        out: dict[str, dict[str, list[Value]]] = {}
        definitions = child(self.doc, "Definitions")
        for kind in children(definitions, "ObjectType") if definitions is not None else []:
            template = child(kind, "PropertyTemplate")
            if kind.props and template is not None:
                out[str(kind.props[0])] = props70(template)
        return out

    def props(self, node: Node) -> dict[str, list[Value]]:
        return {**self.templates.get(node.name, {}), **props70(node)}

    def of(self, kind: str, subtype: str = "") -> list[tuple[int, Node]]:
        return [
            (key, node)
            for key, node in self.objects.items()
            if node.name == kind and (not subtype or subtype_of(node) == subtype)
        ]

    def above(self, key: int, kind: str) -> list[int]:
        return [
            high
            for high, _ in self.up_links.get(key, [])
            if high in self.objects and self.objects[high].name == kind
        ]

    def below(self, key: int, kind: str) -> list[int]:
        return [
            low
            for low, _ in self.down_links.get(key, [])
            if low in self.objects and self.objects[low].name == kind
        ]


def name_of(node: Node) -> str:
    return split_name(node.props[1])[0] if len(node.props) > 1 else ""


def subtype_of(node: Node) -> str:
    return str(node.props[2]) if len(node.props) > 2 else ""


def array_of(node: Node, name: str) -> fbxfile.Array | None:
    found = child(node, name)
    if found is None or not found.props or not isinstance(found.props[0], fbxfile.Array):
        return None
    return found.props[0]


def settings(scene: Scene) -> tuple[str, float, str, float | None]:
    values = props70(child(scene.doc, "GlobalSettings"))
    scale = number(values.get("UnitScaleFactor"), 1.0)
    unit = next((name for size, name in UNITS.items() if math.isclose(size, scale)), "")
    axis = int(number(values.get("UpAxis"), 1))
    sign = int(number(values.get("UpAxisSign"), 1))
    up = ("-" if sign < 0 else "") + "xyz"[axis if 0 <= axis <= 2 else 1]
    mode = int(number(values.get("TimeMode"), 0))
    fps = TIME_MODES.get(mode)
    if mode == CUSTOM_TIME_MODE:
        custom = number(values.get("CustomFrameRate"), -1.0)
        fps = custom if custom > 0 else None
    return unit or f"{scale:g} cm", scale, up, fps


def read_mesh(scene: Scene, key: int, model: Node) -> Mesh:
    geometries = scene.below(key, "Geometry")
    materials = [name_of(scene.objects[m]) for m in scene.below(key, "Material")]
    if not geometries:
        return Mesh(name_of(model), 0, 0, 0, [], materials, 0, 0)
    geometry = scene.objects[geometries[0]]
    points = array_of(geometry, "Vertices")
    verts = points.count // 3 if points is not None else 0
    polys = 0
    tris = 0
    indices = array_of(geometry, "PolygonVertexIndex")
    if indices is not None:
        size = 0
        for index in indices.values():
            size += 1
            if index < 0:
                polys += 1
                tris += max(size - 2, 0)
                size = 0
    uv_sets = []
    for layer in children(geometry, "LayerElementUV"):
        name = child(layer, "Name")
        uv_sets.append(str(name.props[0]) if name is not None and name.props else "")
    influences, unweighted = skin_of(scene, geometries[0], verts)
    return Mesh(name_of(model), verts, polys, tris, uv_sets, materials, influences, unweighted)


def skin_of(scene: Scene, geometry: int, verts: int) -> tuple[int, int]:
    skins = [s for s in scene.below(geometry, "Deformer") if subtype_of(scene.objects[s]) == "Skin"]
    if not skins:
        return 0, 0
    totals = [0.0] * verts
    influences = 0
    for skin in skins:
        for cluster in scene.below(skin, "Deformer"):
            node = scene.objects[cluster]
            if subtype_of(node) != "Cluster":
                continue
            if any(
                subtype_of(scene.objects[m]) in JOINT_TYPES for m in scene.below(cluster, "Model")
            ):
                influences += 1
            indexes = array_of(node, "Indexes")
            weights = array_of(node, "Weights")
            if indexes is None or weights is None:
                continue
            for index, weight in zip(indexes.values(), weights.values(), strict=False):
                if 0 <= index < verts:
                    totals[index] += weight
    return influences, sum(1 for total in totals if total < WEIGHT_EPSILON)


def bind_matrices(scene: Scene) -> dict[int, Matrix]:
    """Joint world matrices at bind time: cluster links first, then bind poses."""
    out: dict[int, Matrix] = {}
    for _, pose in scene.of("Pose", "BindPose"):
        for entry in children(pose, "PoseNode"):
            node = child(entry, "Node")
            matrix = array_of(entry, "Matrix")
            if node is not None and node.props and matrix is not None and matrix.count == 16:
                out.setdefault(int(node.props[0]), column_major(matrix.values()))
    for key, cluster in scene.of("Deformer", "Cluster"):
        link = array_of(cluster, "TransformLink")
        if link is None or link.count != 16:
            continue
        for model in scene.below(key, "Model"):
            out[model] = column_major(link.values())
    return out


def local_matrix(scene: Scene, node: Node) -> Matrix:
    p = scene.props(node)
    zero = (0.0, 0.0, 0.0)
    order = ROTATION_ORDERS[int(number(p.get("RotationOrder"), 0)) % len(ROTATION_ORDERS)]
    rotation_pivot = translation(vec(p.get("RotationPivot"), zero))
    scaling_pivot = translation(vec(p.get("ScalingPivot"), zero))
    steps = [
        translation(vec(p.get("Lcl Translation"), zero)),
        translation(vec(p.get("RotationOffset"), zero)),
        rotation_pivot,
        euler(vec(p.get("PreRotation"), zero), "xyz"),
        euler(vec(p.get("Lcl Rotation"), zero), order),
        transposed_rotation(euler(vec(p.get("PostRotation"), zero), "xyz")),
        translation(tuple(-v for v in vec(p.get("RotationPivot"), zero))),
        translation(vec(p.get("ScalingOffset"), zero)),
        scaling_pivot,
        scaling(vec(p.get("Lcl Scaling"), (1.0, 1.0, 1.0))),
        translation(tuple(-v for v in vec(p.get("ScalingPivot"), zero))),
    ]
    m = identity()
    for step in steps:
        m = multiply(m, step)
    return m


def read_skeleton(scene: Scene) -> tuple[list[Joint], str]:
    joints = {key: node for key, node in scene.of("Model") if subtype_of(node) in JOINT_TYPES}
    if not joints:
        return [], ""

    def parent_model(key: int) -> int:
        models = scene.above(key, "Model")
        return models[0] if models else 0

    parent_of = {key: parent_model(key) for key in joints}
    bound = bind_matrices(scene)
    world: dict[int, Matrix] = {}

    def world_of(key: int) -> Matrix:
        if key not in world:
            parent = parent_model(key)
            base = world_of(parent) if parent in scene.objects else identity()
            world[key] = multiply(base, local_matrix(scene, scene.objects[key]))
        return world[key]

    order = []

    def visit(key: int) -> None:
        order.append(key)
        for low in scene.below(key, "Model"):
            if low in joints:
                visit(low)

    for key in joints:
        if parent_of[key] not in joints:
            visit(key)
    out = []
    used_bind = 0
    for key in order:
        if key in bound:
            matrix = bound[key]
            used_bind += 1
        else:
            matrix = world_of(key)
        at, axes = position_and_axes(matrix)
        parent = parent_of[key]
        parent_name = name_of(joints[parent]) if parent in joints else ""
        out.append(Joint(name_of(joints[key]), parent_name, at, axes))
    if used_bind == len(order):
        source = "bind_pose"
    elif used_bind:
        source = "mixed"
    else:
        source = "local"
    return out, source


def read_takes(scene: Scene) -> list[Take]:
    out = []
    for key, stack in scene.of("AnimationStack"):
        p = scene.props(stack)
        start = number(p.get("LocalStart"), 0.0) / KTIME_PER_SECOND
        stop = number(p.get("LocalStop"), 0.0) / KTIME_PER_SECOND
        curves = 0
        animated: set[int] = set()
        first: int | None = None
        last: int | None = None
        gaps: dict[float, int] = {}
        for layer in scene.below(key, "AnimationLayer"):
            for curve_node in scene.below(layer, "AnimationCurveNode"):
                for curve in scene.below(curve_node, "AnimationCurve"):
                    times = array_of(scene.objects[curve], "KeyTime")
                    if times is None or times.count == 0:
                        continue
                    curves += 1
                    animated.update(scene.above(curve_node, "Model"))
                    values = times.values()
                    first = values[0] if first is None else min(first, values[0])
                    last = values[-1] if last is None else max(last, values[-1])
                    for a, b in zip(values, values[1:], strict=False):
                        if b > a:
                            gaps[b - a] = gaps.get(b - a, 0) + 1
        keys = None
        if first is not None and last is not None:
            keys = (first / KTIME_PER_SECOND, last / KTIME_PER_SECOND)
        step = usual_gap(gaps)
        key_step = step / KTIME_PER_SECOND if step else None
        out.append(Take(name_of(stack), (start, stop), curves, len(animated), keys, key_step))
    return out


def found_texture(path: str, relative: str, folder: Path, embedded: bool) -> str:
    if embedded:
        return "embedded"
    if path and Path(path).is_file():
        return "absolute"
    if relative and (folder / relative).is_file():
        return "relative"
    name = Path((path or relative).replace("\\", "/")).name
    if name and (folder / name).is_file():
        return "beside"
    return ""


def read_textures(scene: Scene, folder: Path) -> list[Texture]:
    out = []
    for key, texture in scene.of("Texture"):
        file_name = child(texture, "FileName")
        relative = child(texture, "RelativeFilename")
        path = str(file_name.props[0]) if file_name is not None and file_name.props else ""
        rel = str(relative.props[0]) if relative is not None and relative.props else ""
        embedded = False
        for video in scene.below(key, "Video"):
            content = child(scene.objects[video], "Content")
            if content is not None and content.props and content.props[0]:
                embedded = True
        found = found_texture(path, rel, folder, embedded)
        materials = [
            (scene.objects[high], prop)
            for high, prop in scene.up_links.get(key, [])
            if high in scene.objects and scene.objects[high].name == "Material"
        ]
        shown = path.replace("\\", "/") or rel.replace("\\", "/")
        if not materials:
            out.append(Texture("", "", shown, found))
        for material, prop in materials:
            out.append(Texture(name_of(material), prop, shown, found))
    return out


def read_fbx(path: str | Path) -> Contents:
    file = Path(path)
    data = file.read_bytes()
    doc = fbxfile.parse(data)
    scene = Scene(doc)
    unit, scale, up, fps = settings(scene)
    meshes = [read_mesh(scene, key, node) for key, node in scene.of("Model", "Mesh")]
    joints, joints_from = read_skeleton(scene)
    takes = read_takes(scene)
    fps_from = "file" if fps else ""
    if fps is None:
        steps = [take.key_step for take in takes if take.key_step]
        if steps:
            fps = round(1.0 / min(steps), 3)
            fps_from = "keys"
    return Contents(
        path=file.resolve().as_posix(),
        format="fbx",
        version=doc.version,
        size=len(data),
        unit=unit,
        unit_scale=scale,
        up=up,
        fps=fps,
        fps_from=fps_from,
        meshes=meshes,
        joints=joints,
        joints_from=joints_from,
        takes=takes,
        textures=read_textures(scene, file.parent),
    )
