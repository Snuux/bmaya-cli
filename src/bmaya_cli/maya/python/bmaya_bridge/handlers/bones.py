from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator
from typing import Any, NamedTuple

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import skeleton, skinweights
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    bool_param,
    display_name,
    int_param,
    listing,
    resolve,
    rnd,
    rnd_list,
    text_param,
)
from bmaya_bridge.handlers._skin import (
    AXES,
    Mirror,
    bind_pose,
    clusters_of,
    influence_names,
    parse_mirror,
    read_skin,
    skin_fn,
)
from bmaya_bridge.handlers.mesh import meshes_under
from bmaya_bridge.handlers.rig import ik_chains, is_driven
from bmaya_bridge.skeleton import Bone

JOINTS_SHOWN = 50
NAMES_SHOWN = 20
ZERO = 1e-4
AIM_COSINE = math.cos(math.radians(10.0))
CM_PER_UNIT = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "km": 100000.0,
    "in": 2.54,
    "ft": 30.48,
    "yd": 91.44,
    "mi": 160934.4,
}
AXIS_NAMES = ("+x", "+y", "+z", "-x", "-y", "-z")
POSE_ATTRS = ("translate", "rotate")
KEPT_ATTRS = ("translate", "rotate", "scale", "shear")
JOINT_VALUES = (("orient", "jointOrient"), ("rotate", "rotate"), ("rotate_axis", "rotateAxis"))
ORIENT_HINT = (
    "--orient world gives every joint the world's axes. Otherwise name the axis that points "
    "at the child and the one that points toward --up: xy (+x at the child, +y up), -zy, "
    "yz. --up takes x, y, z or -x, -y, -z (default y)."
)
BONES_MIRROR_HINT = (
    "--mirror names the plane twins should mirror across: x, y or z through 0, or x=12.5."
)


class Skeleton(NamedTuple):
    """Joints in hierarchy order, with their world matrices at the bind pose where known."""

    joints: list[str]
    bind: dict[str, om.MMatrix]
    from_bind: bool
    clusters: list[str]


def joints_below(node: str) -> list[str]:
    below = cmds.listRelatives(node, allDescendents=True, type="joint", fullPath=True) or []
    if cmds.nodeType(node) == "joint":
        below.append(node)
    return below


def skinned_joints(node: str) -> list[str]:
    found = []
    for shape in meshes_under(node):
        for cluster in clusters_of(shape):
            for path in skin_fn(cluster).influenceObjects():
                if path.hasFn(om.MFn.kJoint):
                    found.append(path.fullPathName())
    return found


def pick_joints(names: str) -> tuple[list[str], str]:
    """Full paths of the joints asked for: under a joint or group, or the skeleton of a mesh."""
    if names:
        nodes = [resolve(name.strip()) for name in names.split(",") if name.strip()]
        source = "names"
    else:
        nodes = cmds.ls(selection=True, objectsOnly=True, long=True) or []
        source = "selection"
    if not nodes:
        joints = cmds.ls(type="joint", long=True) or []
        if not joints:
            raise BridgeError(
                "No joints in the scene.",
                hint="Build a skeleton with cmds.joint, or import one with `bmaya file import`.",
            )
        return sorted(joints), "scene"
    found: dict[str, None] = {}
    for node in nodes:
        here = joints_below(node) if cmds.nodeType(node) == "joint" else skinned_joints(node)
        if not here:
            here = joints_below(node)
        found.update(dict.fromkeys(here))
    if not found:
        raise BridgeError(
            f"No joints under the given {source}.",
            hint="Name a joint (its whole chain is taken), a group, or a skinned mesh. "
            "`bmaya scene --type joint` lists the joints.",
        )
    return sorted(found), source


def world_matrix(node: str) -> om.MMatrix:
    return om.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))


def bind_matrices(joints: list[str]) -> tuple[dict[str, om.MMatrix], list[str]]:
    """World matrices at bind time of the joints that skin something, by the skin clusters."""
    bind: dict[str, om.MMatrix] = {}
    clusters: dict[str, None] = {}
    wanted = set(joints)
    for joint in joints:
        for cluster in cmds.listConnections(joint + ".worldMatrix", type="skinCluster") or []:
            clusters[cluster] = None
    for cluster in clusters:
        fn = skin_fn(cluster)
        for path in fn.influenceObjects():
            full = path.fullPathName()
            if full not in wanted or full in bind:
                continue
            index = fn.indexForInfluenceObject(path)
            pre = om.MMatrix(cmds.getAttr(f"{cluster}.bindPreMatrix[{index}]"))
            bind[full] = pre.inverse()
    return bind, list(clusters)


def read_skeleton(joints: list[str]) -> Skeleton:
    """World matrices at the bind pose: a skinned joint's from its skin, the others carried
    along from their nearest skinned parent by their own local matrices."""
    bind, clusters = bind_matrices(joints)
    if not bind:
        return Skeleton(joints, {joint: world_matrix(joint) for joint in joints}, False, [])
    out: dict[str, om.MMatrix] = {}
    for joint in joints:
        if joint in bind:
            out[joint] = bind[joint]
            continue
        parent = joint_parent(joint)
        if parent in out:
            local = world_matrix(joint) * world_matrix(parent).inverse()
            out[joint] = local * out[parent]
        else:
            out[joint] = world_matrix(joint)
    return Skeleton(joints, out, True, clusters)


def joint_parent(node: str) -> str:
    """The nearest joint above the node, past any plain transforms in between; '' for none."""
    parent = cmds.listRelatives(node, parent=True, fullPath=True)
    while parent:
        if cmds.nodeType(parent[0]) == "joint":
            return parent[0]
        parent = cmds.listRelatives(parent[0], parent=True, fullPath=True)
    return ""


def between(joints: list[str]) -> list[str]:
    """Plain transforms that sit between a joint and its nearest joint above."""
    found: dict[str, None] = {}
    for joint in joints:
        parent = cmds.listRelatives(joint, parent=True, fullPath=True)
        while parent and cmds.nodeType(parent[0]) != "joint" and joint_parent(joint):
            found[parent[0]] = None
            parent = cmds.listRelatives(parent[0], parent=True, fullPath=True)
    return list(found)


def point_of(matrix: om.MMatrix) -> skeleton.Vec:
    return (matrix.getElement(3, 0), matrix.getElement(3, 1), matrix.getElement(3, 2))


def axes_of(matrix: om.MMatrix) -> skeleton.Axes:
    rows = []
    for row in range(3):
        vector = om.MVector(
            matrix.getElement(row, 0), matrix.getElement(row, 1), matrix.getElement(row, 2)
        )
        if vector.length() > 1e-12:
            vector.normalize()
        rows.append((vector.x, vector.y, vector.z))
    return (rows[0], rows[1], rows[2])


def aim_of(axes: skeleton.Axes, direction: skeleton.Vec) -> str:
    """Which of the joint's own axes points at its child, like +x or -z; '' for none."""
    reach = skeleton.length(direction)
    if reach < 1e-9:
        return ""
    unit = (direction[0] / reach, direction[1] / reach, direction[2] / reach)
    for index, name in enumerate(AXIS_NAMES):
        sign = 1.0 if index < 3 else -1.0
        if sign * skeleton.dot(axes[index % 3], unit) >= AIM_COSINE:
            return name
    return ""


def nonzero(values: tuple[float, float, float]) -> bool:
    return any(abs(value) > ZERO for value in values)


def weighted_counts(clusters: list[str]) -> dict[str, int]:
    """How many vertices each influence moves, over every given skin cluster."""
    counts: dict[str, int] = {}
    for cluster in clusters:
        for shape in cmds.ls(cmds.skinCluster(cluster, query=True, geometry=True) or [], long=True):
            if cmds.nodeType(shape) != "mesh":
                continue
            found = skinweights.survey(read_skin(cluster, shape).rows, influence_names(cluster), 4)
            for entry in found.influences:
                counts[entry["name"]] = counts.get(entry["name"], 0) + entry["verts"]
    return counts


def scale_issue(joint: str) -> str:
    scale = cmds.getAttr(joint + ".scale")[0]
    if min(scale) < 0:
        return "negative"
    if max(scale) - min(scale) > ZERO:
        return "uneven"
    if abs(scale[0] - 1.0) > ZERO:
        return "scaled"
    return ""


def cm_per_unit() -> float:
    return CM_PER_UNIT.get(cmds.currentUnit(query=True, linear=True), 1.0)


def reference_bones(against: dict[str, Any]) -> list[Bone]:
    """The joints of a model file, sent by the command line, in this scene's unit."""
    factor = float(against.get("unit_scale") or 1.0) / cm_per_unit()
    bones = []
    for entry in against.get("joints") or []:
        at = entry["at"]
        axes = entry.get("axes") or skeleton_axes_identity()
        bones.append(
            Bone(
                str(entry["name"]),
                str(entry.get("parent") or ""),
                (at[0] * factor, at[1] * factor, at[2] * factor),
                (tuple(axes[0]), tuple(axes[1]), tuple(axes[2])),
            )
        )
    return bones


def skeleton_axes_identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def joint_entry(
    joint: str, found: Skeleton, names: dict[str, str], with_axes: bool
) -> dict[str, Any]:
    matrix = found.bind[joint]
    at = point_of(matrix)
    axes = axes_of(matrix)
    parent = joint_parent(joint)
    if not parent:
        parent = (cmds.listRelatives(joint, parent=True, fullPath=True) or [""])[0]
    entry: dict[str, Any] = {"name": names[joint]}
    if parent:
        entry["parent"] = display_name(parent)
    entry["at"] = rnd_list(at)
    if parent in found.bind:
        entry["length"] = rnd(skeleton.length(skeleton.sub(at, point_of(found.bind[parent]))))
    for key, attr in JOINT_VALUES:
        values = cmds.getAttr(f"{joint}.{attr}")[0]
        if nonzero(values):
            entry[key] = rnd_list(values, 3)
    children = [child for child in joint_children_of(joint) if child in found.bind]
    if len(children) == 1:
        entry["aim"] = aim_of(axes, skeleton.sub(point_of(found.bind[children[0]]), at)) or "none"
    if with_axes:
        entry["axes"] = [rnd_list(axis) for axis in axes]
    if scale_issue(joint):
        entry["scale"] = rnd_list(cmds.getAttr(joint + ".scale")[0])
    return entry


def scene_bones(joints: list[str], found: Skeleton, names: dict[str, str]) -> list[Bone]:
    bones = []
    for joint in joints:
        parent = joint_parent(joint)
        matrix = found.bind[joint]
        bones.append(Bone(names[joint], names.get(parent, ""), point_of(matrix), axes_of(matrix)))
    return bones


def get_bones(params: dict[str, Any]) -> dict[str, Any]:
    joints, source = pick_joints(text_param(params, "names"))
    limit = int_param(params, "limit", JOINTS_SHOWN, 1, 1000)
    mirror = parse_mirror(text_param(params, "mirror") or "x", BONES_MIRROR_HINT)
    found = read_skeleton(joints)
    names = {joint: display_name(joint) for joint in joints}
    entries = [joint_entry(joint, found, names, bool_param(params, "axes")) for joint in joints]
    bones = scene_bones(joints, found, names)
    by_name = {bone.name: bone for bone in bones}
    for entry in entries:
        twin = skinweights.other_side(entry["name"])
        if twin != entry["name"] and twin in by_name:
            entry["twin"] = twin
    counts = weighted_counts(found.clusters)
    if found.clusters:
        for entry in entries:
            entry["verts"] = counts.get(entry["name"], 0)

    result: dict[str, Any] = {
        "source": source,
        "unit": cmds.currentUnit(query=True, linear=True),
        "up": cmds.upAxis(query=True, axis=True),
        "from": "bind" if found.from_bind else "scene",
        "roots": [names[joint] for joint in joints if not parent_in(joint, found.bind)],
        "joints": listing(entries, limit),
    }
    issues = []
    clashing = skeleton.clashes(list(names.values()))
    if clashing:
        result["clashes"] = listing(clashing, NAMES_SHOWN)
        issues.append(
            f"{len(clashing)} joint names are used twice; FBX and game engines keep one: "
            + skeleton.some(clashing)
        )
    scaled = [entry["name"] for entry in entries if "scale" in entry]
    if scaled:
        result["scaled"] = listing(scaled, NAMES_SHOWN)
        bad = [joint for joint in joints if scale_issue(joint) in ("negative", "uneven")]
        if bad:
            issues.append(
                f"{len(bad)} joints have a negative or uneven scale, which engines skin "
                "differently: " + skeleton.some([names[joint] for joint in bad])
            )
    extra = between(joints)
    if extra:
        result["between"] = listing([display_name(node) for node in extra], NAMES_SHOWN)
        issues.append(
            f"{len(extra)} plain transforms sit between joints, and FBX writes each as a node "
            "of its own: " + skeleton.some([display_name(node) for node in extra])
        )
    flat = [entry["name"] for entry in entries if entry.get("length", 1) == 0]
    if flat:
        result["zero_length"] = listing(flat, NAMES_SHOWN)
    result["pairs"], mirror_issue = pair_report(bones, mirror)
    if mirror_issue:
        issues.append(mirror_issue)
    if found.clusters:
        result["skins"] = skin_states(found.clusters)
        unweighted = [entry["name"] for entry in entries if entry.get("verts") == 0]
        result["unweighted"] = listing(unweighted, NAMES_SHOWN)
    against = params.get("against")
    if isinstance(against, dict) and against.get("joints"):
        result["against"], more = against_file(params, against, bones, clashing)
        issues.extend(more)
    result["issues"] = issues
    return result


def skin_states(clusters: list[str]) -> list[dict[str, Any]]:
    states = []
    for cluster in clusters:
        moved = bind_pose(cluster)
        entry: dict[str, Any] = {"name": cluster, "at_bind": not moved}
        if moved:
            entry["moved"] = listing(moved, NAMES_SHOWN)
        states.append(entry)
    return states


def against_file(
    params: dict[str, Any], against: dict[str, Any], bones: list[Bone], clashing: list[str]
) -> tuple[dict[str, Any], list[str]]:
    if clashing:
        raise BridgeError(
            f"{len(clashing)} joint names appear twice in what you named ("
            + skeleton.some(clashing)
            + "), so they cannot be matched with the file by name.",
            hint="Name the top joint of one skeleton: bmaya bones |rig|hand --against FILE.",
        )
    rename = skinweights.renamer(text_param(params, "rename"))
    by_leaf = [
        Bone(skeleton.leaf(bone.name), skeleton.leaf(bone.parent), bone.at, bone.axes)
        for bone in bones
    ]
    compared, issues = skeleton.compare(by_leaf, reference_bones(against), rename)
    return {"path": against.get("path", ""), **compared}, issues


def parent_in(joint: str, known: dict[str, om.MMatrix]) -> bool:
    return joint_parent(joint) in known


def pair_report(bones: list[Bone], mirror: Mirror) -> tuple[dict[str, Any], str]:
    by_name = {bone.name: bone for bone in bones}
    found, lonely = skeleton.pairs(list(by_name))
    report: dict[str, Any] = {"paired": len(found)}
    if lonely:
        report["lonely"] = listing(lonely, NAMES_SHOWN)
    if not found:
        return report, ""
    gaps = skeleton.mirror_gaps(bones, mirror.axis, mirror.at)
    tolerance = max(1e-6, skeleton.PLACE_SHARE * skeleton.box_size([bone.at for bone in bones]))
    off = [gap for gap in gaps if gap["off"] > tolerance]
    kinds: dict[str, int] = {}
    for name, twin in found:
        kind = skeleton.mirror_kind(by_name[name], by_name[twin], mirror.axis)
        kinds[kind] = kinds.get(kind, 0) + 1
    report["mirror"] = {
        "axis": AXES[mirror.axis],
        "at": rnd(mirror.at),
        "kinds": kinds,
        "off": listing([{**gap, "off": rnd(gap["off"])} for gap in off], NAMES_SHOWN),
    }
    if off and len(off) < len(gaps):
        plane = f"{AXES[mirror.axis]}={rnd(mirror.at)}"
        return report, (
            f"{len(off)} of {len(gaps)} twins stand off their mirror image across {plane}: "
            + skeleton.some([gap["name"] for gap in off])
        )
    return report, ""


def pose_targets(joints: list[str]) -> dict[str, om.MMatrix]:
    bind, _ = bind_matrices(joints)
    if not bind:
        raise BridgeError(
            "None of these joints skins a mesh, so there is no bind pose to go to.",
            hint="`bmaya skin` lists the skinned meshes; name a joint of their skeleton.",
        )
    return bind


def held_by(joint: str, chains: dict[str, str]) -> str:
    """Why the joint cannot be posed by setting its values, or '' when it can."""
    if joint in chains:
        return f"IK handle {chains[joint]} turns it"
    for attr in POSE_ATTRS:
        for plug in (attr, attr + "X", attr + "Y", attr + "Z"):
            if cmds.getAttr(f"{joint}.{plug}", lock=True):
                return f"{plug} is locked"
            if is_driven(joint, plug):
                source = cmds.listConnections(f"{joint}.{plug}", source=True, destination=False)
                return f"{plug} is driven by {display_name(source[0])}"
    return ""


def keyed(joint: str) -> bool:
    for attr in POSE_ATTRS:
        for axis in ("", "X", "Y", "Z"):
            if cmds.listConnections(f"{joint}.{attr}{axis}", type="animCurve", destination=False):
                return True
    return False


def set_pose(params: dict[str, Any]) -> dict[str, Any]:
    if not bool_param(params, "bind"):
        raise BridgeError(
            "Say which pose.", hint="bmaya pose ROOT --bind puts the skeleton at its bind pose."
        )
    joints, source = pick_joints(text_param(params, "names"))
    targets = pose_targets(joints)
    chains = ik_chains()
    posed = []
    skipped = []
    with_keys = []
    for joint in joints:
        if joint not in targets:
            continue
        why = held_by(joint, chains)
        if why:
            skipped.append({"name": display_name(joint), "why": why})
            continue
        if world_matrix(joint).isEquivalent(targets[joint], ZERO):
            continue
        cmds.xform(joint, worldSpace=True, matrix=list(targets[joint]))
        posed.append(display_name(joint))
        if keyed(joint):
            with_keys.append(display_name(joint))
    result: dict[str, Any] = {
        "source": source,
        "pose": "bind",
        "posed": listing(posed, NAMES_SHOWN),
        "joints": len(targets),
    }
    if skipped:
        result["skipped"] = listing(skipped, NAMES_SHOWN)
    if with_keys:
        result["keyed"] = listing(with_keys, NAMES_SHOWN)
        result["note"] = (
            "these joints have keys: the next frame change plays the keys again. Key the pose "
            "or cut the keys to keep it"
        )
    left = []
    for cluster in bind_matrices(joints)[1]:
        left.extend(bind_pose(cluster))
    result["at_bind"] = not left
    return result


@contextlib.contextmanager
def at_bind(joints: list[str]) -> Iterator[list[str]]:
    """Stands the skinned joints at their bind pose inside the `with`, then puts back their
    values; yields the joints that could not be moved there."""
    targets, _ = bind_matrices(joints)
    chains = ik_chains()
    saved: dict[str, dict[str, tuple[float, ...]]] = {}
    stuck = []
    for joint in sorted(joints):
        if joint not in targets or world_matrix(joint).isEquivalent(targets[joint], ZERO):
            continue
        if held_by(joint, chains):
            stuck.append(display_name(joint))
            continue
        saved[joint] = {attr: cmds.getAttr(f"{joint}.{attr}")[0] for attr in KEPT_ATTRS}
        cmds.xform(joint, worldSpace=True, matrix=list(targets[joint]))
    try:
        yield stuck
    finally:
        for joint, values in saved.items():
            for attr, value in values.items():
                cmds.setAttr(f"{joint}.{attr}", *value)


class Orient(NamedTuple):
    """`world`, or which of the joint's axes points at its child and which toward `up`."""

    world: bool
    aim: int
    aim_sign: float
    side: int
    side_sign: float
    up: skeleton.Vec


def signed_axis(text: str) -> tuple[int, float, str]:
    sign = -1.0 if text.startswith("-") else 1.0
    letter = text.lstrip("+-")[:1]
    if letter not in AXES:
        return -1, sign, text
    return AXES.index(letter), sign, text[len(text) - len(text.lstrip("+-")) + 1 :]


def parse_orient(raw: str, up_raw: str) -> Orient:
    text = raw.strip().lower().replace(" ", "")
    up_text = (up_raw or "+y").strip().lower()
    up_axis, up_sign, rest = signed_axis(up_text)
    if up_axis < 0 or rest:
        raise BridgeError(f"Bad --up '{up_raw}'.", hint=ORIENT_HINT)
    up = [0.0, 0.0, 0.0]
    up[up_axis] = up_sign
    if text == "world":
        return Orient(True, 0, 1.0, 1, 1.0, (up[0], up[1], up[2]))
    aim, aim_sign, rest = signed_axis(text)
    side, side_sign, rest = signed_axis(rest)
    if aim < 0 or side < 0 or aim == side or rest not in ("", AXES[3 - aim - side]):
        raise BridgeError(f"Bad --orient '{raw}'.", hint=ORIENT_HINT)
    return Orient(False, aim, aim_sign, side, side_sign, (up[0], up[1], up[2]))


def normalized(vector: skeleton.Vec) -> skeleton.Vec:
    size = skeleton.length(vector)
    return (vector[0] / size, vector[1] / size, vector[2] / size)


def cross(a: skeleton.Vec, b: skeleton.Vec) -> skeleton.Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def aimed_axes(direction: skeleton.Vec, orient: Orient) -> skeleton.Axes:
    """Axes with `orient.aim` along `direction` and `orient.side` as near `orient.up` as it goes."""
    first = normalized(direction)
    first = (first[0] * orient.aim_sign, first[1] * orient.aim_sign, first[2] * orient.aim_sign)
    along = skeleton.dot(orient.up, first)
    flat = skeleton.sub(orient.up, (first[0] * along, first[1] * along, first[2] * along))
    if skeleton.length(flat) < 1e-6:
        spare = (orient.up[1], orient.up[2], orient.up[0])
        along = skeleton.dot(spare, first)
        flat = skeleton.sub(spare, (first[0] * along, first[1] * along, first[2] * along))
    unit = normalized(flat)
    sign = orient.side_sign
    second = (unit[0] * sign, unit[1] * sign, unit[2] * sign)
    axes: list[skeleton.Vec] = [(0.0, 0.0, 0.0)] * 3
    axes[orient.aim] = first
    axes[orient.side] = second
    third = 3 - orient.aim - orient.side
    if (orient.side - orient.aim) % 3 == 1:
        axes[third] = cross(first, second)
    else:
        axes[third] = cross(second, first)
    return (axes[0], axes[1], axes[2])


def aim_child(joint: str, at: skeleton.Vec) -> str:
    """The first child joint that stands away from the joint, as Maya's orientJoint takes it."""
    for child in joint_children_of(joint):
        if skeleton.length(skeleton.sub(point_of(world_matrix(child)), at)) > ZERO:
            return child
    return ""


def joint_children_of(joint: str) -> list[str]:
    """The joints right below, past any plain transforms in between, in their order."""
    found = []
    for child in cmds.listRelatives(joint, children=True, type="transform", fullPath=True) or []:
        kind = cmds.nodeType(child)
        if kind == "joint":
            found.append(child)
        elif kind == "transform":
            found.extend(joint_children_of(child))
    return found


def axes_matrix(axes: skeleton.Axes, at: skeleton.Vec) -> om.MMatrix:
    return om.MMatrix([*axes[0], 0.0, *axes[1], 0.0, *axes[2], 0.0, *at, 1.0])


def keyed_turns(joints: list[str]) -> list[str]:
    found = []
    for joint in joints:
        for attr in ("rotate", "jointOrient", "rotateAxis"):
            plugs = [attr] + [attr + axis for axis in "XYZ"]
            if any(
                cmds.listConnections(f"{joint}.{plug}", source=True, destination=False)
                for plug in plugs
            ):
                found.append(display_name(joint))
                break
    return found


def skinned_points(clusters: list[str]) -> dict[str, list[om.MPoint]]:
    out = {}
    for cluster in clusters:
        for shape in cmds.ls(cmds.skinCluster(cluster, query=True, geometry=True) or [], long=True):
            if cmds.nodeType(shape) == "mesh":
                path = om.MSelectionList().add(shape).getDagPath(0)
                out[shape] = [om.MPoint(point) for point in om.MFnMesh(path).getPoints()]
    return out


def orient_joints(params: dict[str, Any]) -> dict[str, Any]:
    orient = parse_orient(text_param(params, "orient"), text_param(params, "up"))
    joints, source = pick_joints(text_param(params, "names"))
    held = keyed_turns(joints)
    if held:
        raise BridgeError(
            f"{len(held)} joints have keys or inputs on their rotations: "
            + skeleton.some(held)
            + ". New axes would change what those keys mean.",
            hint="Orient a skeleton before animating it, or cut its rotation keys first.",
        )
    _, clusters = bind_matrices(joints)
    before_points = skinned_points(clusters)
    old = {joint: world_matrix(joint) for joint in joints}
    others = {}
    for joint in joints:
        children = cmds.listRelatives(joint, children=True, type="transform", fullPath=True)
        for child in children or []:
            if cmds.nodeType(child) == "transform":
                others[child] = world_matrix(child)
    new_axes: dict[str, skeleton.Axes] = {}
    aimed = []
    for joint in sorted(joints):
        at = point_of(old[joint])
        parent = (cmds.listRelatives(joint, parent=True, fullPath=True) or [""])[0]
        if orient.world:
            axes: skeleton.Axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        else:
            child = aim_child(joint, at)
            if child:
                axes = aimed_axes(skeleton.sub(point_of(old[child]), at), orient)
                if len(joint_children_of(joint)) > 1:
                    aimed.append({"name": display_name(joint), "at": display_name(child)})
            elif parent in new_axes:
                axes = new_axes[parent]
            else:
                axes = axes_of(old[joint])
        new_axes[joint] = axes
        target = axes_matrix(axes, at)
        parent_now = world_matrix(parent) if parent else om.MMatrix()
        local = om.MTransformationMatrix(target * parent_now.inverse())
        turn = local.rotation()
        cmds.setAttr(joint + ".rotate", 0.0, 0.0, 0.0)
        cmds.setAttr(joint + ".rotateAxis", 0.0, 0.0, 0.0)
        cmds.setAttr(
            joint + ".jointOrient",
            math.degrees(turn.x),
            math.degrees(turn.y),
            math.degrees(turn.z),
        )
        cmds.xform(joint, worldSpace=True, translation=at)
    for child, matrix in others.items():
        cmds.xform(child, worldSpace=True, matrix=list(matrix))
    for cluster in clusters:
        fn = skin_fn(cluster)
        for path in fn.influenceObjects():
            full = path.fullPathName()
            if full not in old:
                continue
            index = fn.indexForInfluenceObject(path)
            pre = om.MMatrix(cmds.getAttr(f"{cluster}.bindPreMatrix[{index}]"))
            fixed = pre * old[full] * world_matrix(full).inverse()
            cmds.setAttr(f"{cluster}.bindPreMatrix[{index}]", list(fixed), type="matrix")
    worst = 0.0
    for shape, points in skinned_points(clusters).items():
        for index, point in enumerate(points):
            worst = max(worst, point.distanceTo(before_points[shape][index]))
    turned = [
        display_name(joint)
        for joint in joints
        if skeleton.angle_between(axes_of(old[joint]), axes_of(world_matrix(joint)))
        > skeleton.TURN_TOLERANCE
    ]
    result: dict[str, Any] = {
        "source": source,
        "orient": "world" if orient.world else text_param(params, "orient"),
        "joints": len(joints),
        "turned": listing(turned, NAMES_SHOWN),
    }
    if aimed:
        result["aimed"] = listing(aimed, NAMES_SHOWN)
    if clusters:
        result["skins"] = len(clusters)
        result["mesh_moved"] = rnd(worst)
    result["next"] = "`bmaya bones ROOT --axes` shows the new axes, `bmaya undo` takes them back"
    return result
