from __future__ import annotations

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import meshcheck
from bmaya_bridge.handlers._common import display_name
from bmaya_bridge.handlers._skin import audit_skin
from bmaya_bridge.shapecheck import Problem, ShapeFacts, classify


def stored_inside_out(shape: str) -> bool:
    selection = om.MSelectionList()
    selection.add(shape)
    mesh = om.MFnMesh(selection.getDagPath(0))
    points = [(p.x, p.y, p.z) for p in mesh.getPoints(om.MSpace.kObject)]
    counts, indices = mesh.getVertices()
    faces = []
    start = 0
    for count in counts:
        faces.append(list(indices[start : start + count]))
        start += count
    wound = meshcheck.winding(points, faces)
    return wound.inward * 2 > wound.shells


def facts_of(shape: str) -> ShapeFacts:
    intermediate = bool(cmds.getAttr(shape + ".intermediateObject"))
    selection = om.MSelectionList()
    selection.add(shape)
    try:
        faces = om.MFnMesh(selection.getDagPath(0)).numPolygons
    except ValueError:
        faces = 0
    feeds = False
    if intermediate:
        readers = cmds.listConnections(
            [shape + ".outMesh", shape + ".worldMesh"], source=False, destination=True
        )
        feeds = bool(readers)
    opposite = bool(cmds.getAttr(shape + ".opposite"))
    inward = opposite and not intermediate and faces > 0 and stored_inside_out(shape)
    parents = cmds.listRelatives(shape, allParents=True, fullPath=True) or []
    return ShapeFacts(
        name=shape.rsplit("|", 1)[-1],
        intermediate=intermediate,
        faces=faces,
        feeds=feeds,
        instances=len(parents),
        opposite=opposite,
        inward=inward,
    )


def shape_problems(transform: str) -> list[Problem]:
    shapes = cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or []
    return classify([facts_of(shape) for shape in shapes])


def mesh_transforms(nodes: list[str]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        shapes = cmds.listRelatives(node, allDescendents=True, type="mesh", fullPath=True) or []
        if cmds.nodeType(node) == "mesh":
            shapes.append(node)
        for shape in shapes:
            parent = cmds.listRelatives(shape, parent=True, fullPath=True)
            if parent and parent[0] not in out:
                out.append(parent[0])
    return out


def preflight(nodes: list[str]) -> tuple[list[str], list[str]]:
    """Problems of every mesh under the nodes, skin ones too: (blocking, the rest), each led by
    the mesh name."""
    blocking = []
    issues = []
    for transform in mesh_transforms(nodes):
        name = display_name(transform)
        for problem in shape_problems(transform):
            (blocking if problem.blocking else issues).append(f"{name}: {problem.text}")
        shown = cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or []
        for shape in shown:
            if not cmds.getAttr(shape + ".intermediateObject"):
                issues.extend(f"{name}: {line}" for line in audit_skin(shape)[1])
    return blocking, issues
