from __future__ import annotations

from typing import Any, NamedTuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
from maya import cmds

from bmaya_bridge import skinweights, undoable
from bmaya_bridge.skinweights import Row

BUDGET = 4
BIND_TOLERANCE = 1e-3
METHODS = {0: "linear", 1: "dual quaternion", 2: "blended"}


def dag_path(node: str) -> om.MDagPath:
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


class Skin(NamedTuple):
    cluster: str
    shape: str
    names: list[str]
    rows: list[Row]


def clusters_of(shape: str) -> list[str]:
    found = []
    for cluster in cmds.ls(cmds.listHistory(shape) or [], type="skinCluster") or []:
        geometry = cmds.skinCluster(cluster, query=True, geometry=True) or []
        if any(cmds.ls(item, long=True) == [shape] for item in geometry):
            found.append(cluster)
    return found


def skin_fn(cluster: str) -> oma.MFnSkinCluster:
    selection = om.MSelectionList()
    selection.add(cluster)
    return oma.MFnSkinCluster(selection.getDependNode(0))


def all_verts(count: int) -> om.MObject:
    component = om.MFnSingleIndexedComponent()
    whole = component.create(om.MFn.kMeshVertComponent)
    component.setCompleteData(count)
    return whole


def influence_names(cluster: str) -> list[str]:
    return [path.partialPathName() for path in skin_fn(cluster).influenceObjects()]


def vertex_count(shape: str) -> int:
    return om.MFnMesh(dag_path(shape)).numVertices


def read_skin(cluster: str, shape: str) -> Skin:
    names = influence_names(cluster)
    verts = vertex_count(shape)
    if not names or not verts:
        return Skin(cluster, shape, names, [{} for _ in range(verts)])
    flat, _ = skin_fn(cluster).getWeights(dag_path(shape), all_verts(verts))
    return Skin(cluster, shape, names, skinweights.rows_from_flat(flat, names))


def write_rows(cluster: str, shape: str, rows: list[Row]) -> None:
    """Writes every vertex's weights, finding the influences by name at the time of writing."""
    names = influence_names(cluster)
    flat = skinweights.flat_from_rows(rows, names)
    skin_fn(cluster).setWeights(
        dag_path(shape),
        all_verts(len(rows)),
        om.MIntArray(list(range(len(names)))),
        om.MDoubleArray(flat),
        False,
    )


def write_undoable(cluster: str, shape: str, before: list[Row], after: list[Row]) -> None:
    path = str(undoable.plugin_path())
    if not cmds.pluginInfo(path, query=True, loaded=True):
        cmds.loadPlugin(path, quiet=True)
    undoable.put(
        undoable.Step(
            do=lambda: write_rows(cluster, shape, after),
            undo=lambda: write_rows(cluster, shape, before),
        )
    )
    getattr(cmds, undoable.COMMAND)()


def bind_pose(cluster: str) -> list[str]:
    """Influences that stand away from where they were when the mesh was bound."""
    fn = skin_fn(cluster)
    moved = []
    for path in fn.influenceObjects():
        index = fn.indexForInfluenceObject(path)
        pre = om.MMatrix(cmds.getAttr(f"{cluster}.bindPreMatrix[{index}]"))
        product = pre * path.inclusiveMatrix()
        off = max(
            abs(product.getElement(row, column) - (1.0 if row == column else 0.0))
            for row in range(4)
            for column in range(4)
        )
        if off > BIND_TOLERANCE:
            moved.append(path.partialPathName())
    return moved


def skin_issues(cluster: str, shape: str, found: skinweights.Survey, budget: int) -> list[str]:
    issues = skinweights.issue_lines(found, budget)
    method = cmds.getAttr(cluster + ".skinningMethod")
    if method != 0:
        issues.append(f"{METHODS.get(method, method)} skinning: game engines skin linearly")
    for path in skin_fn(cluster).influenceObjects():
        if not path.hasFn(om.MFn.kJoint):
            issues.append(f"influence '{path.partialPathName()}' is not a joint")
    clusters = clusters_of(shape)
    if len(clusters) > 1:
        issues.append(f"{len(clusters)} skin clusters on one mesh: {', '.join(clusters)}")
    return issues


def audit_skin(shape: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The skin summary and issues `bmaya audit` adds for a skinned mesh."""
    clusters = clusters_of(shape)
    if not clusters:
        return None, []
    skin = read_skin(clusters[0], shape)
    found = skinweights.survey(skin.rows, skin.names, BUDGET)
    summary = {"name": skin.cluster, "influences": len(skin.names), "most_per_vertex": found.most}
    return summary, skin_issues(skin.cluster, shape, found, BUDGET)
