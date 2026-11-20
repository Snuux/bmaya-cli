from __future__ import annotations

import re
import time
from typing import Any, NamedTuple

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import clients, meshcheck, skinweights
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    cm_per_unit,
    display_name,
    int_param,
    resolve,
    rnd,
    rnd_list,
    text_param,
)
from bmaya_bridge.handlers._shapes import shape_problems
from bmaya_bridge.handlers._skin import audit_skin
from bmaya_bridge.meshcheck import Face, Vec

MAX_MESHES = 50
AREA_EPSILON = 1e-8
LENGTH_EPSILON = 1e-5
TRANSFORM_EPSILON = 1e-4
UV_EPSILON = 1e-6
OVERLAP_FACES = 50000
SYMMETRY_TOLERANCE = 1e-3
MOVE_TOLERANCE = 1e-5
UV_MOVE_TOLERANCE = 1e-6
MARKS_KEPT = 20
AUDIT_SECONDS = 30.0
AXES = "xyz"

DEFAULT_NAME = re.compile(
    r"^(pCube|pSphere|pCylinder|pCone|pPlane|pTorus|pPrism|pPyramid|pPipe|pHelix|pSolid"
    r"|pDisc|pSuperShape|polySurface|group|null|transform|mesh)\d*$"
)


def meshes_under(node: str) -> list[str]:
    if cmds.nodeType(node) == "mesh":
        if cmds.getAttr(node + ".intermediateObject"):
            return []
        return [node]
    return (
        cmds.listRelatives(
            node, allDescendents=True, type="mesh", fullPath=True, noIntermediate=True
        )
        or []
    )


def target_meshes(names: str) -> tuple[list[str], str]:
    if names:
        nodes = [resolve(name.strip()) for name in names.split(",") if name.strip()]
        source = "names"
    else:
        nodes = cmds.ls(selection=True, objectsOnly=True, long=True) or []
        source = "selection"
    if not nodes:
        meshes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        return meshes, "scene"

    meshes = []
    for node in nodes:
        for mesh in meshes_under(node):
            if mesh not in meshes:
                meshes.append(mesh)
    if not meshes:
        raise BridgeError(
            f"No meshes found in the given {source}.",
            hint="Pass mesh or group names, or none to audit every mesh in the scene.",
        )
    return meshes, source


def dag_path(node: str) -> om.MDagPath:
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def face_stats(path: om.MDagPath) -> dict[str, int]:
    stats = {"ngons": 0, "lamina": 0, "zero_area": 0, "no_uv": 0}
    faces = om.MItMeshPolygon(path)
    while not faces.isDone():
        if faces.polygonVertexCount() > 4:
            stats["ngons"] += 1
        if faces.isLamina():
            stats["lamina"] += 1
        if faces.getArea() < AREA_EPSILON:
            stats["zero_area"] += 1
        if not faces.hasUVs():
            stats["no_uv"] += 1
        faces.next()
    return stats


def edge_stats(path: om.MDagPath) -> dict[str, int]:
    stats = {"non_manifold": 0, "border": 0, "zero_length": 0, "hard": 0}
    edges = om.MItMeshEdge(path)
    while not edges.isDone():
        connected = edges.numConnectedFaces()
        if connected > 2:
            stats["non_manifold"] += 1
        elif connected == 1:
            stats["border"] += 1
        if edges.length() < LENGTH_EPSILON:
            stats["zero_length"] += 1
        if not edges.isSmooth:
            stats["hard"] += 1
        edges.next()
    return stats


def non_manifold_verts(shape: str) -> int:
    found = cmds.polyInfo(shape, nonManifoldVertices=True) or []
    return len(cmds.ls(found, flatten=True) or [])


def material_count(shape: str) -> tuple[int, bool]:
    engines = list(dict.fromkeys(cmds.listConnections(shape, type="shadingEngine") or []))
    return len(engines), engines == ["initialShadingGroup"]


def creation_history(shape: str) -> list[str]:
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    kept = []
    for node in history:
        if cmds.objectType(node, isAType="geometryFilter"):
            continue
        node_type = cmds.nodeType(node)
        if node_type in ("groupId", "groupParts") or node_type in kept:
            continue
        kept.append(node_type)
    return kept


def transform_issues(transform: str) -> list[str]:
    issues = []
    translate = cmds.getAttr(transform + ".translate")[0]
    rotate = cmds.getAttr(transform + ".rotate")[0]
    scale = cmds.getAttr(transform + ".scale")[0]
    unfrozen = any(abs(v) > TRANSFORM_EPSILON for v in translate + rotate) or any(
        abs(v - 1.0) > TRANSFORM_EPSILON for v in scale
    )
    if unfrozen:
        issues.append("unfrozen transform")
    if any(v < 0 for v in scale):
        issues.append("negative scale")
    pivots = (
        cmds.getAttr(transform + ".rotatePivot")[0] + cmds.getAttr(transform + ".scalePivot")[0]
    )
    if any(abs(v) > TRANSFORM_EPSILON for v in pivots):
        issues.append("pivot not at origin")
    return issues


def name_issues(transform: str) -> list[str]:
    name = transform.rsplit("|", 1)[-1]
    issues = []
    if ":" in name:
        issues.append("namespace in name")
    leaf = name.rsplit(":", 1)[-1]
    if DEFAULT_NAME.match(leaf):
        issues.append(f"default name '{leaf}'")
    if not name.isascii():
        issues.append("non-ASCII name")
    return issues


def read_points(mesh: om.MFnMesh, space: int) -> list[Vec]:
    """Points in the scene unit: the API gives centimeters whatever the scene is set to."""
    factor = 1.0 / cm_per_unit()
    return [
        (point.x * factor, point.y * factor, point.z * factor) for point in mesh.getPoints(space)
    ]


def read_faces(mesh: om.MFnMesh) -> list[Face]:
    counts, indices = mesh.getVertices()
    faces = []
    start = 0
    for count in counts:
        faces.append(list(indices[start : start + count]))
        start += count
    return faces


def size_of(points: list[Vec]) -> list[float]:
    if not points:
        return [0.0, 0.0, 0.0]
    return [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]


def face_kinds(faces: list[Face]) -> dict[str, int]:
    kinds = {"tris": 0, "quads": 0, "ngons": 0}
    for face in faces:
        kinds["tris" if len(face) == 3 else "quads" if len(face) == 4 else "ngons"] += 1
    return kinds


def uv_stats(mesh: om.MFnMesh, shape: str, face_count: int) -> dict[str, int]:
    us, vs = mesh.getUVs()
    low, high = -UV_EPSILON, 1.0 + UV_EPSILON
    outside = 0
    for index in range(len(us)):
        if not (low <= us[index] <= high and low <= vs[index] <= high):
            outside += 1
    stats = {"outside": outside}
    counts, indices = mesh.getAssignedUVs()
    face_uvs = []
    start = 0
    for count in counts:
        face_uvs.append([(us[index], vs[index]) for index in indices[start : start + count]])
        start += count
    stats["flipped"] = meshcheck.flipped_uv_faces(face_uvs)
    if face_count <= OVERLAP_FACES:
        found = cmds.polyUVOverlap(shape + ".f[*]", overlappingComponents=True) or []
        stats["overlapping"] = len(cmds.ls(found, flatten=True) or [])
    return stats


def locked_normals(mesh: om.MFnMesh) -> int:
    return sum(1 for index in range(mesh.numNormals) if mesh.isNormalLocked(index))


def parse_axis(raw: str, flag: str) -> int | None:
    if not raw:
        return None
    axis = raw.lower()
    if axis not in ("x", "y", "z"):
        raise BridgeError(
            f"Bad axis '{raw}' for {flag}.", hint=f"Pass a world axis: {flag} x, y or z."
        )
    return AXES.index(axis)


def symmetry_of(mesh: om.MFnMesh, transform: str, axis: int) -> dict[str, Any]:
    points = read_points(mesh, om.MSpace.kWorld)
    at = cmds.xform(transform, query=True, worldSpace=True, rotatePivot=True)[axis]
    size = size_of(points)
    tolerance = max(1e-6, SYMMETRY_TOLERANCE * max(size))
    found = meshcheck.symmetry(points, axis, at, tolerance)
    result: dict[str, Any] = {
        "axis": AXES[axis],
        "at": rnd(at),
        "matched": found.matched,
        "unmatched": found.unmatched,
    }
    if found.sample:
        result["sample"] = [rnd_list(point) for point in found.sample]
    return result


def empty_entry(transform: str) -> dict[str, Any]:
    issues = transform_issues(transform) + name_issues(transform)
    issues += [problem.text for problem in shape_problems(transform)]
    return {
        "name": display_name(transform),
        "verts": 0,
        "tris": 0,
        "faces": {"tris": 0, "quads": 0, "ngons": 0},
        "size": [0, 0, 0],
        "uv_sets": 0,
        "materials": 0,
        "issues": issues,
    }


def audit_one(shape: str, symmetry_axis: int | None = None) -> dict[str, Any]:
    path = dag_path(shape)
    transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
    try:
        mesh = om.MFnMesh(path)
    except ValueError:
        return empty_entry(transform)

    tri_counts, _ = mesh.getTriangles()
    tris = int(sum(tri_counts))
    faces = face_stats(path)
    edges = edge_stats(path)
    uv_sets = mesh.getUVSetNames()
    uv_count = mesh.numUVs() if uv_sets else 0
    materials, default_material = material_count(shape)
    polygons = read_faces(mesh)
    wound = meshcheck.winding(read_points(mesh, om.MSpace.kObject), polygons)
    uvs = uv_stats(mesh, shape, len(polygons)) if uv_count else {}

    issues = []
    checks = [
        (faces["ngons"], "n-gons"),
        (edges["non_manifold"], "non-manifold edges"),
        (non_manifold_verts(shape), "non-manifold verts"),
        (faces["lamina"], "lamina faces"),
        (faces["zero_area"], "zero-area faces"),
        (edges["zero_length"], "zero-length edges"),
        (wound.flipped, "faces with flipped winding"),
    ]
    for count, label in checks:
        if count:
            issues.append(f"{count} {label}")
    # With the opposite flag set, shape_problems explains the stored winding instead.
    if wound.inward and not cmds.getAttr(shape + ".opposite"):
        if wound.shells == 1:
            issues.append("normals point inward")
        else:
            issues.append(f"{wound.inward} of {wound.shells} shells have normals pointing inward")
    if not uv_sets or uv_count == 0:
        issues.append("no UVs")
    elif faces["no_uv"]:
        issues.append(f"{faces['no_uv']} faces without UVs")
    if uvs.get("overlapping"):
        issues.append(f"{uvs['overlapping']} overlapping UV faces")
    if len(uv_sets) > 1:
        issues.append(f"{len(uv_sets)} UV sets")
    if materials == 0:
        issues.append("no material")
    elif default_material:
        issues.append("default material only")
    elif materials > 1:
        issues.append(f"{materials} materials")
    history = creation_history(shape)
    if history:
        issues.append("construction history: " + ", ".join(history[:5]))
    issues.extend(transform_issues(transform))
    issues.extend(name_issues(transform))
    issues.extend(problem.text for problem in shape_problems(transform))
    skin, skin_issues = audit_skin(shape)
    issues.extend(skin_issues)

    entry: dict[str, Any] = {
        "name": display_name(transform),
        "verts": mesh.numVertices,
        "tris": tris,
        "faces": face_kinds(polygons),
        "size": rnd_list(size_of(read_points(mesh, om.MSpace.kWorld))),
        "uv_sets": len(uv_sets),
        "materials": materials,
    }
    if edges["border"]:
        entry["open_edges"] = edges["border"]
    if edges["hard"]:
        entry["hard_edges"] = edges["hard"]
    locked = locked_normals(mesh)
    if locked:
        entry["locked_normals"] = locked
    if uvs.get("outside"):
        entry["uvs_outside_01"] = uvs["outside"]
    if uvs.get("flipped"):
        entry["flipped_uv_faces"] = uvs["flipped"]
    color_sets = mesh.getColorSetNames()
    if color_sets:
        entry["color_sets"] = list(color_sets)
    if skin:
        entry["skin"] = skin
    if symmetry_axis is not None:
        entry["symmetry"] = symmetry_of(mesh, transform, symmetry_axis)
        if entry["symmetry"]["unmatched"]:
            issues.append(
                f"{entry['symmetry']['unmatched']} verts without a mirror twin across "
                f"{AXES[symmetry_axis]}"
            )
    entry["issues"] = issues
    return entry


class Mark(NamedTuple):
    name: str
    points: list[Vec]
    faces: list[Face]
    tris: int
    uv_set: str
    corners: list[tuple[float, float] | None]
    unit_cm: float


MARKS: dict[tuple[str, str], Mark] = {}


def has_geometry(shape: str) -> bool:
    """False for a shape whose deformer lost its input, like a skinned mesh whose Orig
    shape was deleted: MFnMesh refuses it."""
    try:
        return om.MFnMesh(dag_path(shape)).numVertices > 0
    except (RuntimeError, ValueError):
        return False


def single_mesh(name: str) -> str:
    meshes = meshes_under(resolve(name))
    if not meshes:
        raise BridgeError(
            f"'{name}' has no mesh.", hint="`bmaya scene --type mesh` lists the meshes."
        )
    if len(meshes) > 1:
        parents = cmds.listRelatives(meshes[:8], parent=True, fullPath=True) or []
        raise BridgeError(
            f"'{name}' holds {len(meshes)} meshes; this works on one mesh at a time.",
            hint="Name one of them: " + ", ".join(display_name(parent) for parent in parents) + ".",
        )
    if not has_geometry(meshes[0]):
        raise BridgeError(
            f"'{name}' has no geometry.",
            hint="A skinned mesh whose Orig shape was deleted has none: undo that delete. "
            f"`bmaya audit {name}` shows what is left.",
        )
    return meshes[0]


def transform_of(shape: str) -> str:
    return cmds.listRelatives(shape, parent=True, fullPath=True)[0]


def corner_uvs(mesh: om.MFnMesh, uv_set: str) -> list[tuple[float, float] | None]:
    """The UV of every face corner in face order; None for the corners of a face without UVs."""
    if not uv_set:
        return []
    us, vs = mesh.getUVs(uv_set)
    counts, ids = mesh.getAssignedUVs(uv_set)
    sizes = mesh.getVertices()[0]
    out: list[tuple[float, float] | None] = []
    start = 0
    for face, count in enumerate(counts):
        if count:
            out.extend((us[uv], vs[uv]) for uv in ids[start : start + count])
        else:
            out.extend([None] * sizes[face])
        start += count
    return out


def read_mark(shape: str) -> Mark:
    mesh = om.MFnMesh(dag_path(shape))
    tri_counts, _ = mesh.getTriangles()
    uv_set = mesh.currentUVSetName() if mesh.numUVs() else ""
    return Mark(
        display_name(transform_of(shape)),
        read_points(mesh, om.MSpace.kWorld),
        read_faces(mesh),
        int(sum(tri_counts)),
        uv_set,
        corner_uvs(mesh, uv_set),
        cm_per_unit(),
    )


def in_current_unit(mark: Mark) -> Mark:
    """A mark read before the scene's unit changed, in the unit it has now."""
    factor = mark.unit_cm / cm_per_unit()
    if abs(factor - 1.0) < 1e-12:
        return mark
    points = [(x * factor, y * factor, z * factor) for x, y, z in mark.points]
    return mark._replace(points=points, unit_cm=cm_per_unit())


def uv_moves(before: Mark, after: Mark) -> dict[str, Any]:
    """How the UV layout changed between two marks of the same topology."""
    out: dict[str, Any] = {}
    if before.uv_set != after.uv_set:
        out["set"] = [before.uv_set or None, after.uv_set or None]
    moved = 0
    worst = 0.0
    for index in range(min(len(before.corners), len(after.corners))):
        one = before.corners[index]
        two = after.corners[index]
        if one is None or two is None:
            if one != two:
                moved += 1
            continue
        gap = ((one[0] - two[0]) ** 2 + (one[1] - two[1]) ** 2) ** 0.5
        if gap > UV_MOVE_TOLERANCE:
            moved += 1
            worst = max(worst, gap)
    out["moved"] = moved
    if worst:
        out["max_move"] = rnd(worst)
    return out


def mark_key(shape: str) -> tuple[str, str]:
    return clients.acting_client(), cmds.ls(transform_of(shape), uuid=True)[0]


def take_mark(shape: str) -> Mark:
    """Remembers the mesh as it is now, for this client, so a later `diff` has a before."""
    key = mark_key(shape)
    MARKS.pop(key, None)
    MARKS[key] = read_mark(shape)
    for old in list(MARKS)[: max(0, len(MARKS) - MARKS_KEPT)]:
        del MARKS[old]
    return MARKS[key]


def mark_stats(mark: Mark) -> dict[str, Any]:
    area, volume = meshcheck.measure(mark.points, mark.faces)
    return {
        "verts": len(mark.points),
        "tris": mark.tris,
        "faces": face_kinds(mark.faces),
        "size": rnd_list(size_of(mark.points)),
        "area": rnd(area),
        "volume": rnd(volume),
    }


def compare_marks(before: Mark, after: Mark) -> dict[str, Any]:
    result: dict[str, Any] = {"before": mark_stats(before), "after": mark_stats(after)}
    if before.faces != after.faces or len(before.points) != len(after.points):
        result["topology_changed"] = True
        return result
    result["topology_changed"] = False
    size = max(size_of(after.points) + [0.0])
    found = meshcheck.moves(before.points, after.points, max(1e-9, MOVE_TOLERANCE * size))
    result["moved"] = found.moved
    if found.box:
        result["max_move"] = rnd(found.max_move)
        result["mean_move"] = rnd(found.mean_move)
        result["moved_box"] = rnd_list(found.box[0] + found.box[1])
    uv = uv_moves(before, after)
    result["uv_changed"] = bool(uv.get("moved") or "set" in uv)
    if result["uv_changed"]:
        result["uv"] = uv
    return result


def diff_mesh(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    shape = single_mesh(name)
    against = text_param(params, "against")
    if bool_param(params, "mark"):
        if against:
            raise BridgeError(
                "--mark and --against do not go together.",
                hint="--mark remembers the mesh as it is; --against compares two meshes now.",
            )
        taken = take_mark(shape)
        return {
            "name": taken.name,
            "marked": mark_stats(taken),
            "next": f"edit the mesh, then `bmaya diff {name}` shows what changed",
        }
    after = read_mark(shape)
    if against:
        before = read_mark(single_mesh(against))
    else:
        found = MARKS.get(mark_key(shape))
        if found is None:
            raise BridgeError(
                f"'{name}' has no mark to compare with.",
                hint=f"Take one before the edit: bmaya diff {name} --mark. To compare two "
                f"meshes as they are: bmaya diff {name} --against OTHER.",
            )
        before = in_current_unit(found)
    result = {"name": after.name, "against": before.name if against else "mark"}
    result.update(compare_marks(before, after))
    if bool_param(params, "image"):
        draw_moves(shape, before, after, result, params)
    return result


def draw_moves(
    shape: str, before: Mark, after: Mark, result: dict[str, Any], params: dict[str, Any]
) -> None:
    """The views sheet of the mesh with each vertex colored by how far it moved."""
    from bmaya_bridge.handlers.views import get_model_views  # views imports this module

    name = result["name"]
    if result["topology_changed"]:
        result["note"] = (
            f"no picture: the topology changed, so vertices do not pair up. "
            f"bmaya views {name} --compare mark -o diff.png draws both shapes"
        )
        return
    size = max(size_of(after.points) + [0.0])
    shares = meshcheck.move_shares(before.points, after.points, max(1e-9, MOVE_TOLERANCE * size))
    unit = cmds.currentUnit(query=True, linear=True)
    longest = result.get("max_move", 0.0)
    legend = [{"name": "did not move", "color": hex_of(skinweights.heat(0.0))}]
    if longest:
        legend += [
            {"name": f"{rnd(longest / 2.0)} {unit}", "color": hex_of(skinweights.heat(0.5))},
            {"name": f"{rnd(longest)} {unit}", "color": hex_of(skinweights.heat(1.0))},
        ]
    sheet = get_model_views(
        {
            "names": shape,
            "views": text_param(params, "views"),
            "style": "parts",
            "max_size": int_param(params, "max_size", 0, 0, 4000),
        },
        paint={shape: [skinweights.heat(share) for share in shares]},
        paint_legend=legend,
    )
    for key in ("path", "width", "height", "views"):
        result[key] = sheet[key]
    if sheet.get("note"):
        result["note"] = sheet["note"]


def hex_of(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def audit_mesh(params: dict[str, Any]) -> dict[str, Any]:
    meshes, source = target_meshes(text_param(params, "names"))
    axis = parse_axis(text_param(params, "symmetry"), "--symmetry")
    deadline = time.perf_counter() + AUDIT_SECONDS
    audited = []
    for shape in meshes[:MAX_MESHES]:
        if audited and time.perf_counter() > deadline:
            break
        audited.append(audit_one(shape, axis))
    return {
        "source": source,
        "meshes": audited,
        "shown": len(audited),
        "total": len(meshes),
        "totals": {
            "verts": sum(entry["verts"] for entry in audited),
            "tris": sum(entry["tris"] for entry in audited),
            "with_issues": sum(1 for entry in audited if entry["issues"]),
        },
    }
