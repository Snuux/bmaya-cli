from __future__ import annotations

from typing import Any

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import skeleton, skinweights
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import (
    display_name,
    listing,
    resolve,
    rnd,
    short_name,
    text_param,
)
from bmaya_bridge.handlers._skin import (
    AXES,
    Mirror,
    clusters_of,
    dag_path,
    influence_names,
    parse_mirror,
    read_skin,
    skin_fn,
    write_undoable,
)
from bmaya_bridge.handlers.bones import at_bind, point_of, world_matrix
from bmaya_bridge.handlers.mesh import audit_one, single_mesh, transform_of
from bmaya_bridge.handlers.skin import skin_input_points

NAMES_SHOWN = 20
PLANE_TOLERANCE = 1e-4
MIRROR_FLAGS = ("mirrorYZ", "mirrorXZ", "mirrorXY")
AXIS_HINT = "--axis takes x, y or z for the world plane through 0, or x=12.5 to move the plane."


def twin_path(joint: str) -> str:
    """The full path of the joint's other-side twin, the joint itself when it has no side,
    or '' when the twin is not in the scene."""
    name = short_name(joint)
    twin = skinweights.other_side(name)
    if twin == name:
        return joint
    found = cmds.ls(twin, long=True, type="joint") or []
    if len(found) > 1:
        raise BridgeError(
            f"'{twin}', the twin of '{name}', matches {len(found)} joints.",
            hint="Give the joints unique names first; `bmaya bones` lists the clashes.",
        )
    return found[0] if found else ""


def joint_children(joint: str) -> list[str]:
    return cmds.listRelatives(joint, children=True, type="joint", fullPath=True) or []


def subtree(joint: str) -> list[str]:
    out = [joint]
    for child in joint_children(joint):
        out.extend(subtree(child))
    return out


def chain_roots(joints: list[str]) -> list[str]:
    """The top joints of the one-sided chains whose twins are missing."""
    roots: list[str] = []
    for joint in joints:
        if twin_path(joint):
            continue
        top = joint
        while True:
            parent = cmds.listRelatives(top, parent=True, fullPath=True, type="joint")
            if not parent or twin_path(parent[0]) == parent[0]:
                break
            if twin_path(parent[0]):
                raise BridgeError(
                    f"'{display_name(parent[0])}' has a twin but '{display_name(top)}' under "
                    "it has none, so the other side is half built.",
                    hint="Delete the half-built side, or finish it, then mirror again.",
                )
            top = parent[0]
        if top not in roots:
            roots.append(top)
    for root in roots:
        built = [joint for joint in subtree(root) if twin_path(joint) not in ("", joint)]
        if built:
            raise BridgeError(
                f"Some joints under '{display_name(root)}' already have twins: "
                + skeleton.some([display_name(joint) for joint in built]),
                hint="Delete the half-built side, or finish it, then mirror again.",
            )
    return roots


def pair_copies(source: str, copy: str, pairs: list[tuple[str, str]]) -> None:
    pairs.append((source, copy))
    ours = joint_children(source)
    theirs = joint_children(copy)
    for index in range(min(len(ours), len(theirs))):
        pair_copies(ours[index], theirs[index], pairs)


def mirror_chain(root: str, mirror: Mirror) -> list[str]:
    """Mirrors one chain with mirrorJoint's behavior mode, names each copy after its twin,
    and moves it onto the plane: mirrorJoint mirrors across the chain's parent."""
    parent = (cmds.listRelatives(root, parent=True, fullPath=True) or [""])[0]
    made = cmds.mirrorJoint(root, mirrorBehavior=True, **{MIRROR_FLAGS[mirror.axis]: True})
    copy_root = f"{parent}|{made[0]}"
    pairs: list[tuple[str, str]] = []
    pair_copies(root, copy_root, pairs)
    for source, copy in sorted(pairs, key=lambda pair: -pair[1].count("|")):
        cmds.rename(copy, skinweights.other_side(short_name(source)))
    copy_root = f"{parent}|{skinweights.other_side(short_name(root))}"
    used = point_of(world_matrix(parent))[mirror.axis] if parent else 0.0
    if abs(used - mirror.at) > PLANE_TOLERANCE:
        at = point_of(world_matrix(root))
        cmds.xform(
            copy_root, worldSpace=True, translation=skeleton.reflect(at, mirror.axis, mirror.at)
        )
    renamed: list[tuple[str, str]] = []
    pair_copies(root, copy_root, renamed)
    return [copy for _, copy in renamed]


def mirror_joints(joints: list[str], mirror: Mirror) -> tuple[list[str], dict[str, str]]:
    """Twins for the joints, made where they are missing; returns the made ones and the map."""
    made = []
    for root in chain_roots(joints):
        made.extend(mirror_chain(root, mirror))
    twins = {}
    for joint in joints:
        twin = twin_path(joint)
        if not twin:
            raise BridgeError(
                f"No twin was made for '{display_name(joint)}'.",
                hint="Its name has no side Maya could swap; rename it like hand_r or L_hand.",
            )
        twins[joint] = twin
    return made, twins


def turn_out(shape: str, reverse: bool) -> None:
    """Clears the opposite flag that freezing a negative scale sets, and turns the faces so
    that they face out without it: game engines ignore the flag."""
    cmds.setAttr(shape + ".opposite", False)
    if reverse:
        cmds.polyNormal(shape, normalMode=0, userNormalMode=0, constructionHistory=False)


def mirrored_mesh(source: str, shape: str, cluster: str, mirror: Mirror, target: str) -> str:
    reverse = not cmds.getAttr(shape + ".opposite")
    parent = cmds.listRelatives(source, parent=True, fullPath=True)
    pivot = cmds.xform(source, query=True, worldSpace=True, rotatePivot=True)
    copy = cmds.ls(cmds.duplicate(source, name=target)[0], long=True)
    copy_path = f"{parent[0]}|{target}" if parent else f"|{target}"
    if len(copy) == 1:
        copy_path = copy[0]
    for attr in ("translate", "rotate", "scale"):
        for axis in ("", "X", "Y", "Z"):
            cmds.setAttr(f"{copy_path}.{attr}{axis}", lock=False)
    for child in cmds.listRelatives(copy_path, children=True, fullPath=True) or []:
        is_shape = cmds.objectType(child, isAType="shape")
        if not is_shape or cmds.getAttr(child + ".intermediateObject"):
            cmds.delete(child)
    if parent:
        copy_path = cmds.ls(cmds.parent(copy_path, world=True)[0], long=True)[0]
    cmds.makeIdentity(copy_path, apply=True, translate=True, rotate=True, scale=True)
    copy_shape = cmds.listRelatives(copy_path, shapes=True, fullPath=True)[0]
    if cluster:
        geometry = om.MMatrix(cmds.getAttr(cluster + ".geomMatrix"))
        bound = [point * geometry for point in skin_input_points(cluster, shape)]
        om.MFnMesh(dag_path(copy_shape)).setPoints(om.MPointArray(bound))
    plane = [0.0, 0.0, 0.0]
    plane[mirror.axis] = mirror.at
    cmds.xform(copy_path, worldSpace=True, pivots=plane)
    cmds.setAttr(f"{copy_path}.scale{AXES[mirror.axis].upper()}", -1)
    cmds.makeIdentity(copy_path, apply=True, scale=True)
    turn_out(copy_shape, reverse)
    cmds.delete(copy_path, constructionHistory=True)
    pivot_at = skeleton.reflect((pivot[0], pivot[1], pivot[2]), mirror.axis, mirror.at)
    cmds.xform(copy_path, worldSpace=True, pivots=pivot_at)
    if parent:
        copy_path = cmds.ls(cmds.parent(copy_path, parent[0])[0], long=True)[0]
        cmds.makeIdentity(copy_path, apply=True, translate=True, rotate=True, scale=True)
        copy_shape = cmds.listRelatives(copy_path, shapes=True, fullPath=True)[0]
        if cmds.getAttr(copy_shape + ".opposite"):
            turn_out(copy_shape, True)
    return copy_path


def skin_copy(
    source_shape: str, cluster: str, copy: str, twins: dict[str, str]
) -> tuple[str, int, list[str]]:
    """Binds the copy to the twin joints like the source and gives every vertex the
    source vertex's weights with the joints swapped."""
    copy_shape = cmds.listRelatives(copy, shapes=True, fullPath=True)[0]
    fn = skin_fn(cluster)
    paths = [path.fullPathName() for path in fn.influenceObjects()]
    names = [path.partialPathName() for path in fn.influenceObjects()]
    rows = read_skin(cluster, source_shape).rows
    bound = cmds.skinCluster(
        [twins.get(path, path) for path in paths],
        copy,
        toSelectedBones=True,
        maximumInfluences=cmds.getAttr(cluster + ".maxInfluences"),
        obeyMaxInfluences=cmds.getAttr(cluster + ".maintainMaxInfluences"),
        normalizeWeights=cmds.getAttr(cluster + ".normalizeWeights"),
        skinMethod=cmds.getAttr(cluster + ".skinningMethod"),
        name=f"{short_name(copy).rsplit(':', 1)[-1]}_skin",
    )[0]
    partial = {}
    for path in skin_fn(bound).influenceObjects():
        partial[path.fullPathName()] = path.partialPathName()
    swap = {}
    for index in range(len(paths)):
        swap[names[index]] = partial[twins.get(paths[index], paths[index])]
    after = [skinweights.renamed_row(row, lambda name: swap.get(name, name)) for row in rows]
    before = read_skin(bound, copy_shape).rows
    write_undoable(bound, copy_shape, before, after)
    unpaired = [display_name(path) for path in paths if twins.get(path, path) == path]
    return bound, len(paths) - len(unpaired), unpaired


def twins_off(twins: dict[str, str], mirror: Mirror) -> list[str]:
    """Twins that stand away from their joint's mirror image."""
    off = []
    for joint, twin in twins.items():
        if twin == joint:
            continue
        want = skeleton.reflect(point_of(world_matrix(joint)), mirror.axis, mirror.at)
        have = point_of(world_matrix(twin))
        size = max(1.0, skeleton.length(want))
        if skeleton.length(skeleton.sub(want, have)) > skeleton.PLACE_SHARE * size:
            off.append(display_name(twin))
    return off


def mirror_node(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    node = resolve(name)
    mirror = parse_mirror(text_param(params, "axis") or "x", AXIS_HINT)
    target = text_param(params, "new_name")
    result: dict[str, Any] = {
        "source": display_name(node),
        "axis": AXES[mirror.axis],
        "at": rnd(mirror.at),
    }
    if cmds.nodeType(node) == "joint":
        if target:
            raise BridgeError(
                "--name names a mirrored mesh; mirrored joints take their twins' names.",
                hint=f"bmaya mirror {name}",
            )
        with at_bind(subtree(node)) as stuck:
            refuse_stuck(stuck)
            made, _ = mirror_joints(subtree(node), mirror)
        result["joints"] = {"made": listing([display_name(j) for j in made], NAMES_SHOWN)}
        result["next"] = "`bmaya bones ROOT` shows both sides and whether they mirror"
        return result

    shape = single_mesh(name)
    source = transform_of(shape)
    clusters = clusters_of(shape)
    if len(clusters) > 1:
        raise BridgeError(
            f"'{name}' has {len(clusters)} skin clusters.",
            hint="Game engines take one skin per mesh; keep one, then mirror.",
        )
    cluster = clusters[0] if clusters else ""
    leaf = short_name(source)
    target = target or skinweights.other_side(leaf)
    if target == leaf:
        raise BridgeError(
            f"'{leaf}' has no side in its name to swap.",
            hint=f"Name the copy: bmaya mirror {name} --name {leaf}_l.",
        )
    if cmds.objExists(target):
        raise BridgeError(
            f"'{target}' already exists.",
            hint=f"Delete it first, or name the copy: bmaya mirror {name} --name OTHER.",
        )
    twins: dict[str, str] = {}
    made: list[str] = []
    off: list[str] = []
    if cluster:
        joints = [path.fullPathName() for path in skin_fn(cluster).influenceObjects()]
        with at_bind(joints) as stuck:
            refuse_stuck(stuck)
            made, twins = mirror_joints(joints, mirror)
            off = twins_off(twins, mirror)
    copy = mirrored_mesh(source, shape, cluster, mirror, target)
    result["made"] = display_name(copy)
    copy_shape = cmds.listRelatives(copy, shapes=True, fullPath=True)[0]
    result["verts"] = om.MFnMesh(dag_path(copy_shape)).numVertices
    issues = []
    if cluster:
        result["joints"] = {
            "made": listing([display_name(joint) for joint in made], NAMES_SHOWN),
            "existed": len({twin for joint, twin in twins.items() if twin != joint}) - len(made),
        }
        bound, paired, unpaired = skin_copy(shape, cluster, copy, twins)
        result["skin"] = bound
        result["weights"] = {
            "influences": len(influence_names(bound)),
            "paired": paired,
            "unpaired": listing(unpaired, NAMES_SHOWN),
        }
        if off:
            issues.append(
                f"{len(off)} twin joints stand away from the mirror image of the source's bind "
                "pose, and the copy is bound to them as they stand: " + skeleton.some(off)
            )
    had = audit_one(shape)["issues"]
    issues.extend(issue for issue in audit_one(copy_shape)["issues"] if issue not in had)
    result["issues"] = issues
    result["next"] = (
        f"`bmaya views {display_name(source)} {result['made']} --views front,top` shows both; "
        "`bmaya undo` takes the mirror back"
    )
    return result


def refuse_stuck(stuck: list[str]) -> None:
    if stuck:
        raise BridgeError(
            f"{len(stuck)} joints cannot be put at the bind pose to mirror from it: "
            + skeleton.some(stuck),
            hint="They are driven by IK, a constraint or a lock. `bmaya rig` shows what drives "
            "them; free them or mirror with them at the bind pose.",
        )
