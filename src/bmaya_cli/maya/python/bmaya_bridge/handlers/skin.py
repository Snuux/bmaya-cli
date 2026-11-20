from __future__ import annotations

import json
import math
import os
from typing import Any, NamedTuple

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import skinweights
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    int_param,
    listing,
    number_text,
    resolve,
    rnd,
    short_name,
    text_param,
)
from bmaya_bridge.handlers._skin import (
    BUDGET,
    METHODS,
    bind_pose,
    clusters_of,
    dag_path,
    influence_names,
    read_skin,
    skin_fn,
    skin_issues,
    vertex_count,
    write_undoable,
)
from bmaya_bridge.handlers.mesh import meshes_under, single_mesh, transform_of
from bmaya_bridge.skinweights import Row

MOST_BUDGET = 32
INFLUENCES_SHOWN = 30
VERTS_SHOWN = 200
NAMES_SHOWN = 10
MESHES_SHOWN = 50
PLANE_SHARE = 1e-4
SEARCH_SHARE = 0.05
NORMALIZE = {0: "none", 1: "interactive", 2: "post"}
AXES = "xyz"
MIRROR_HINT = (
    "Pass a world axis with the side the weights come from: --mirror +x copies the +x half "
    "onto the -x half. The plane goes through 0; --mirror +x=12.5 moves it."
)


class Mirror(NamedTuple):
    axis: int
    sign: int
    at: float


def mesh_report(shape: str, budget: int, limit: int, verts_spec: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": display_name(transform_of(shape))}
    clusters = clusters_of(shape)
    if not clusters:
        entry["skin"] = None
        entry["verts"] = vertex_count(shape)
        return entry
    skin = read_skin(clusters[0], shape)
    found = skinweights.survey(skin.rows, skin.names, budget)
    moved = bind_pose(skin.cluster)
    entry.update(
        {
            "skin": skin.cluster,
            "verts": len(skin.rows),
            "influences": listing(found.influences, limit),
            "most_per_vertex": found.most,
            "max_influences": cmds.getAttr(skin.cluster + ".maxInfluences"),
            "method": METHODS.get(cmds.getAttr(skin.cluster + ".skinningMethod"), "other"),
            "normalize": NORMALIZE.get(cmds.getAttr(skin.cluster + ".normalizeWeights"), "other"),
            "bind_pose": {"at": not moved, "moved": listing(moved, NAMES_SHOWN)},
        }
    )
    if found.unused:
        entry["unused"] = listing(found.unused, NAMES_SHOWN)
    entry["issues"] = skin_issues(skin.cluster, shape, found, budget)
    picked = skinweights.samples(found)
    if picked:
        entry["sample"] = picked
    if verts_spec:
        ids = skinweights.vertex_ids(verts_spec, len(skin.rows))
        rows = [
            {"vertex": vertex, "weights": skinweights.rounded_row(skin.rows[vertex], 4)}
            for vertex in ids
        ]
        entry["rows"] = listing(rows, VERTS_SHOWN)
    return entry


def report_meshes(names: str) -> tuple[list[str], str]:
    if names:
        shapes = []
        for name in [name.strip() for name in names.split(",") if name.strip()]:
            for shape in meshes_under(resolve(name)):
                if shape not in shapes:
                    shapes.append(shape)
        if not shapes:
            raise BridgeError(
                "No meshes found under the given names.",
                hint="Pass mesh or group names; `bmaya scene --type mesh` lists them.",
            )
        return shapes, "names"
    selected = cmds.ls(selection=True, objectsOnly=True, long=True) or []
    shapes = []
    for node in selected:
        for shape in meshes_under(node):
            if shape not in shapes and clusters_of(shape):
                shapes.append(shape)
    if shapes:
        return shapes, "selection"
    skinned = []
    for cluster in cmds.ls(type="skinCluster") or []:
        for item in cmds.skinCluster(cluster, query=True, geometry=True) or []:
            for shape in cmds.ls(item, long=True, type="mesh") or []:
                if shape not in skinned:
                    skinned.append(shape)
    if not skinned:
        raise BridgeError(
            "No skinned meshes in the scene.",
            hint="Bind one with weights from a file: bmaya skin MESH --load weights.json, or "
            "from another skinned mesh: bmaya skin MESH --copy-from SKINNED.",
        )
    return skinned, "scene"


def get_skin_info(params: dict[str, Any]) -> dict[str, Any]:
    budget = int_param(params, "max", BUDGET, 1, MOST_BUDGET)
    limit = int_param(params, "limit", INFLUENCES_SHOWN, 1, 1000)
    verts_spec = text_param(params, "verts")
    shapes, source = report_meshes(text_param(params, "names"))
    if verts_spec and len(shapes) > 1:
        raise BridgeError(
            f"--verts reads one mesh; {len(shapes)} were given.",
            hint="Name one mesh: bmaya skin MESH --verts 0:20.",
        )
    meshes = [mesh_report(shape, budget, limit, verts_spec) for shape in shapes[:MESHES_SHOWN]]
    return {
        "source": source,
        "max": budget,
        "meshes": meshes,
        "shown": len(meshes),
        "total": len(shapes),
    }


def skinned_mesh(name: str) -> tuple[str, str]:
    shape = single_mesh(name)
    clusters = clusters_of(shape)
    if not clusters:
        raise BridgeError(
            f"'{name}' has no skin.",
            hint=f"Bind it with weights from a file: bmaya skin {name} --load weights.json, or "
            f"from another skinned mesh: bmaya skin {name} --copy-from SKINNED.",
        )
    if len(clusters) > 1:
        raise BridgeError(
            f"'{name}' has {len(clusters)} skin clusters: {', '.join(clusters)}.",
            hint="Game engines take one skin per mesh. Keep one and delete the others with "
            "`bmaya exec`.",
        )
    return shape, clusters[0]


def save_skin_weights(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    path = text_param(params, "path")
    if not path:
        raise BridgeError("No file to save to.", hint=f"bmaya skin {name} --save weights.json")
    shape, cluster = skinned_mesh(name)
    skin = read_skin(cluster, shape)
    data = {
        "mesh": display_name(transform_of(shape)),
        "skin": cluster,
        "verts": len(skin.rows),
        "joints": skin.names,
        "max_influences": cmds.getAttr(cluster + ".maxInfluences"),
        "weights": [skinweights.rounded_row(row) for row in skin.rows],
    }
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    os.replace(partial, path)
    found = skinweights.survey(skin.rows, skin.names, BUDGET)
    return {
        "path": path,
        "size": os.path.getsize(path),
        "mesh": data["mesh"],
        "skin": cluster,
        "verts": len(skin.rows),
        "joints": len(skin.names),
        "most_per_vertex": found.most,
    }


def parse_mirror(raw: str) -> Mirror | None:
    if not raw:
        return None
    text = raw.lower().replace(" ", "")
    head, _, position = text.partition("=")
    sign = 0
    if head[:1] in "+-" and head:
        sign = 1 if head[0] == "+" else -1
        head = head[1:]
    if head not in ("x", "y", "z"):
        raise BridgeError(f"Bad mirror '{raw}'.", hint=MIRROR_HINT)
    at = 0.0
    if position:
        try:
            at = float(position)
        except ValueError:
            raise BridgeError(f"Bad mirror plane '{position}'.", hint=MIRROR_HINT) from None
        if not math.isfinite(at):
            raise BridgeError(f"Bad mirror plane '{position}'.", hint=MIRROR_HINT)
    return Mirror(AXES.index(head), sign, at)


def reflected(point: om.MPoint, mirror: Mirror | None) -> om.MPoint:
    if mirror is None:
        return point
    out = om.MPoint(point)
    out[mirror.axis] = 2.0 * mirror.at - point[mirror.axis]
    return out


def bind_points(cluster: str, shape: str) -> list[om.MPoint]:
    """World points of the mesh as it was bound, whatever pose the skeleton is in now."""
    fn = skin_fn(cluster)
    path = dag_path(shape)
    geometry = fn.inputShapeAtIndex(fn.indexForOutputShape(path.node()))
    world = path.inclusiveMatrix()
    return [point * world for point in om.MFnMesh(geometry).getPoints()]


def box_of(points: list[om.MPoint]) -> om.MBoundingBox:
    box = om.MBoundingBox()
    for point in points:
        box.expand(point)
    return box


class Sampler:
    """Weights anywhere on a skinned mesh's bind-pose surface, blended across a triangle."""

    def __init__(self, cluster: str, shape: str) -> None:
        fn = skin_fn(cluster)
        path = dag_path(shape)
        geometry = fn.inputShapeAtIndex(fn.indexForOutputShape(path.node()))
        self.mesh = om.MFnMesh(geometry)
        self.rows = read_skin(cluster, shape).rows
        world = path.inclusiveMatrix()
        self.local = path.inclusiveMatrixInverse()
        self.intersector = om.MMeshIntersector()
        self.intersector.create(geometry, world)
        self.box = box_of([point * world for point in self.mesh.getPoints()])
        size = max(self.box.width, self.box.height, self.box.depth)
        self.radius = max(1e-6, SEARCH_SHARE * size)
        self.far = 0

    def weights_at(self, point: om.MPoint) -> Row:
        hit = self.intersector.getClosestPoint(point, self.radius)
        if hit is None:
            # The bounded search now and then misses a point that lies on the surface.
            hit = self.intersector.getClosestPoint(point, 1e30)
            gap = (om.MPoint(hit.point) - point * self.local).length()
            if gap > self.radius:
                self.far += 1
        first, second, third = self.mesh.getPolygonTriangleVertices(hit.face, hit.triangle)
        along, across = hit.barycentricCoords
        return skinweights.blend(
            [
                (self.rows[first], along),
                (self.rows[second], across),
                (self.rows[third], 1.0 - along - across),
            ]
        )


def overlap_or_fail(points: list[om.MPoint], sampler: Sampler, what: str) -> None:
    box = box_of(points)
    grown = om.MBoundingBox(sampler.box)
    reach = om.MVector(sampler.radius, sampler.radius, sampler.radius)
    grown.expand(sampler.box.min - reach)
    grown.expand(sampler.box.max + reach)
    if not grown.intersects(box):
        raise BridgeError(
            f"{what} does not overlap the source mesh, so no vertex has weights to take.",
            hint="Weights are copied by closest point in world space: put the meshes on top of "
            "each other first. With --mirror check the axis and the plane.",
        )


def joint_paths(names: list[str]) -> list[str]:
    missing = []
    found = []
    for name in names:
        matches = cmds.ls(name, long=True, type="joint") or []
        if len(matches) == 1:
            found.append(matches[0])
        elif not matches:
            missing.append(name)
        else:
            raise BridgeError(
                f"Joint '{name}' matches {len(matches)} joints.",
                hint="Name it with a parent or namespace in the weights, or map it with "
                f"--rename {name}=PATH: " + ", ".join(matches[:3]),
            )
    if missing:
        shown = ", ".join(missing[:NAMES_SHOWN])
        more = f" and {len(missing) - NAMES_SHOWN} more" if len(missing) > NAMES_SHOWN else ""
        raise BridgeError(
            f"{len(missing)} joints in the weights are not in the scene: {shown}{more}.",
            hint="Map the names with --rename: {}_r adds a suffix, rig:{} a namespace, "
            "arm=forearm_r one name. `bmaya scene --type joint` lists the joints.",
        )
    return found


def read_file(path: str) -> object:
    if not path or not os.path.isfile(path):
        raise BridgeError(
            f"No weights file at {path}.",
            hint="`bmaya skin MESH --save FILE` writes one; any JSON list of "
            '{"joint": weight} objects per vertex works.',
        )
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return json.load(handle)
    except ValueError as exc:
        raise BridgeError(f"{path} is not valid JSON: {exc}.", hint=skinweights.FILE_HINT) from None


def needed_names(rows: list[Row], touched: list[int]) -> list[str]:
    names: dict[str, None] = {}
    for vertex in touched:
        names.update(dict.fromkeys(rows[vertex]))
    return list(names)


def edit_skin(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    load = text_param(params, "load")
    copy_from = text_param(params, "copy_from")
    mirror = parse_mirror(text_param(params, "mirror"))
    rename = skinweights.renamer(text_param(params, "rename"))
    prune = number_text(params, "prune")
    normalize = bool_param(params, "normalize")
    most = int_param(params, "max", 0, 1, MOST_BUDGET) or None
    if load and copy_from:
        raise BridgeError(
            "--load and --copy-from do not go together.",
            hint="Take the weights from a file or from another mesh, one of them.",
        )
    if load and mirror:
        raise BridgeError(
            "--mirror does not go with --load.",
            hint="Load the file first, then mirror in a second call: "
            f"bmaya skin {name or 'MESH'} --mirror +x.",
        )
    if not (load or copy_from or mirror or prune is not None or normalize or most):
        raise BridgeError(
            "Nothing to change.",
            hint=f"bmaya skin {name or 'MESH'} reports the skin; to change it pass --load, "
            "--copy-from, --mirror, --prune, --normalize or --max.",
        )
    if prune is not None and not 0 < prune < 1:
        raise BridgeError(
            f"--prune {rnd(prune)} is not between 0 and 1.",
            hint="It is the smallest weight to keep, like --prune 0.01.",
        )

    shape = single_mesh(name)
    clusters = clusters_of(shape)
    if len(clusters) > 1:
        skinned_mesh(name)
    if not clusters and not (load or copy_from):
        skinned_mesh(name)
    cluster = clusters[0] if clusters else ""
    verts = vertex_count(shape)
    before = read_skin(cluster, shape).rows if cluster else [{} for _ in range(verts)]
    after = list(before)
    touched: list[int] = list(range(verts))
    result: dict[str, Any] = {"name": display_name(transform_of(shape))}

    if load:
        picked = skinweights.read_rows(read_file(load), verts)
        for vertex, row in picked.items():
            after[vertex] = skinweights.renamed_row(row, rename)
        touched = sorted(picked)
        result["source"] = {"file": load, "verts": len(picked)}
    elif copy_from:
        source_shape, source_cluster = skinned_mesh(copy_from)
        if source_shape == shape:
            raise BridgeError(
                "A mesh cannot copy weights from itself.",
                hint=f"To mirror one mesh's weights: bmaya skin {name} --mirror +x.",
            )
        if cluster:
            points = bind_points(cluster, shape)
        else:
            moved = bind_pose(source_cluster)
            if moved:
                raise BridgeError(
                    f"The skeleton of '{copy_from}' is not at its bind pose "
                    f"({len(moved)} joints moved: {', '.join(moved[:5])}), so '{name}' would "
                    "be bound in a different pose.",
                    hint="Put the skeleton back at the bind pose first, for example "
                    f"bmaya exec \"cmds.dagPose(cmds.listConnections('{source_cluster}"
                    ".bindPose'), restore=True)\", or go to a frame where it stands in it.",
                )
            # Copies: the points of a dropped MPointArray dangle.
            current = om.MFnMesh(dag_path(shape)).getPoints(om.MSpace.kWorld)
            points = [om.MPoint(point) for point in current]
        sampler = Sampler(source_cluster, source_shape)
        queries = [reflected(point, mirror) for point in points]
        overlap_or_fail(queries, sampler, f"'{name}'" + (" mirrored" if mirror else ""))
        after = []
        for point in queries:
            row = skinweights.renamed_row(sampler.weights_at(point), rename)
            after.append(skinweights.mirrored_row(row) if mirror else row)
        result["source"] = {"mesh": display_name(transform_of(source_shape))}
        if sampler.far:
            result["far"] = sampler.far
    elif mirror:
        if not mirror.sign:
            raise BridgeError(
                "Say which half the weights come from.",
                hint=f"bmaya skin {name} --mirror +x copies the +x half onto the -x half; "
                "-x the other way.",
            )
        points = bind_points(cluster, shape)
        box = box_of(points)
        size = max(box.width, box.height, box.depth)
        tolerance = max(1e-9, PLANE_SHARE * size)
        touched = [
            vertex
            for vertex, point in enumerate(points)
            if mirror.sign * (point[mirror.axis] - mirror.at) < -tolerance
        ]
        if not touched:
            raise BridgeError(
                f"No vertices on the {'-' if mirror.sign > 0 else '+'}{AXES[mirror.axis]} side "
                f"of the plane {AXES[mirror.axis]}={rnd(mirror.at)}.",
                hint="The plane goes through 0 by default; --mirror +x=12.5 moves it.",
            )
        sampler = Sampler(cluster, shape)
        for vertex in touched:
            row = sampler.weights_at(reflected(points[vertex], mirror))
            after[vertex] = skinweights.mirrored_row(row)
        result["source"] = {"half": ("+" if mirror.sign > 0 else "-") + AXES[mirror.axis]}
        if sampler.far:
            result["far"] = sampler.far
    if mirror:
        used = needed_names(after, touched)
        unpaired = [joint for joint in used if skinweights.other_side(joint) == joint]
        result["mirror"] = {
            "axis": AXES[mirror.axis],
            "at": rnd(mirror.at),
            "paired": len(used) - len(unpaired),
            "unpaired": listing(unpaired, NAMES_SHOWN),
        }

    wanted = needed_names(after, touched)
    paths = joint_paths(wanted)
    if not cluster:
        if not wanted:
            raise BridgeError(
                "The weights name no joints.", hint="Every vertex needs at least one weight."
            )
        transform = transform_of(shape)
        cluster = cmds.skinCluster(
            paths,
            transform,
            toSelectedBones=True,
            maximumInfluences=most or BUDGET,
            normalizeWeights=1,
            name=f"{short_name(transform).rsplit(':', 1)[-1]}_skin",
        )[0]
        result["bound"] = cluster
    else:
        have = {cmds.ls(joint, long=True)[0] for joint in influence_names(cluster)}
        extra = [path for path in paths if path not in have]
        if extra:
            cmds.skinCluster(cluster, edit=True, addInfluence=extra, weight=0.0, lockWeights=False)
            result["added"] = listing([short_name(path) for path in extra], NAMES_SHOWN)

    finished = skinweights.finish(before, after, touched, prune, most)
    write_undoable(cluster, shape, before, finished.rows)
    names = influence_names(cluster)
    found = skinweights.survey(finished.rows, names, most or BUDGET)
    result["skin"] = cluster
    result["changed"] = finished.changed
    for key, count in (
        ("pruned", finished.pruned),
        ("capped", finished.capped),
        ("normalized", finished.normalized),
    ):
        if count:
            result[key] = count
    result["after"] = {"influences": len(names), "most_per_vertex": found.most}
    result["issues"] = skin_issues(cluster, shape, found, most or BUDGET)
    top = found.influences[0]["name"] if found.influences else "JOINT"
    result["next"] = (
        f"`bmaya views {name} --style weights --joint {top}` shows the weights, "
        "`bmaya undo` takes this edit back"
    )
    return result
