from __future__ import annotations

import re
from typing import Any

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import display_name, resolve, text_param

MAX_MESHES = 50
AREA_EPSILON = 1e-8
LENGTH_EPSILON = 1e-5
TRANSFORM_EPSILON = 1e-4

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


def audit_one(shape: str) -> dict[str, Any]:
    path = dag_path(shape)
    mesh = om.MFnMesh(path)
    transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]

    tri_counts, _ = mesh.getTriangles()
    tris = int(sum(tri_counts))
    faces = face_stats(path)
    edges = edge_stats(path)
    uv_sets = mesh.getUVSetNames()
    uv_count = mesh.numUVs() if uv_sets else 0
    materials, default_material = material_count(shape)

    issues = []
    checks = [
        (faces["ngons"], "n-gons"),
        (edges["non_manifold"], "non-manifold edges"),
        (non_manifold_verts(shape), "non-manifold verts"),
        (faces["lamina"], "lamina faces"),
        (faces["zero_area"], "zero-area faces"),
        (edges["zero_length"], "zero-length edges"),
    ]
    for count, label in checks:
        if count:
            issues.append(f"{count} {label}")
    if not uv_sets or uv_count == 0:
        issues.append("no UVs")
    elif faces["no_uv"]:
        issues.append(f"{faces['no_uv']} faces without UVs")
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

    entry: dict[str, Any] = {
        "name": display_name(transform),
        "verts": mesh.numVertices,
        "tris": tris,
        "uv_sets": len(uv_sets),
        "materials": materials,
    }
    if edges["border"]:
        entry["open_edges"] = edges["border"]
    if edges["hard"]:
        entry["hard_edges"] = edges["hard"]
    entry["issues"] = issues
    return entry


def audit_mesh(params: dict[str, Any]) -> dict[str, Any]:
    meshes, source = target_meshes(text_param(params, "names"))
    audited = [audit_one(shape) for shape in meshes[:MAX_MESHES]]
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
