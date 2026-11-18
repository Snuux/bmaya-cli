from __future__ import annotations

from typing import Any

from maya import cmds

from bmaya_bridge.handlers._common import (
    display_name,
    frame,
    is_default_camera,
    key_times,
    listing,
    resolve,
    rnd,
    text_param,
)
from bmaya_bridge.handlers.animation import TIME_CURVE_TYPES

CONTROL_LIMIT = 60
IK_LIMIT = 20
CONSTRAINT_LIMIT = 30
SWITCH_LIMIT = 20
SET_LIMIT = 10
SWITCH_DEPTH = 3
KEY_DEPTH = 4
SWITCH_DRIVES = 6
ROTATE_ORDERS = ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx")
TRIPLES = ("translate", "rotate", "scale")
LIMIT_NAMES = {"translate": "Trans", "rotate": "Rot", "scale": "Scale"}
UTILITY_TYPES = frozenset(
    {
        "reverse",
        "condition",
        "blendColors",
        "multiplyDivide",
        "unitConversion",
        "plusMinusAverage",
        "setRange",
        "multDoubleLinear",
        "addDoubleLinear",
        "remapValue",
        "clamp",
    }
)
SWITCH_ATTRS = frozenset({"ikBlend", "visibility", "blender", "weight"})
SKIN_ATTRS = frozenset({"lockInfluenceWeights"})


def scope_of(root: str) -> list[str]:
    if root:
        below = cmds.listRelatives(root, allDescendents=True, fullPath=True, type="transform")
        nodes = [root] + (below or [])
    else:
        nodes = cmds.ls(type="transform", long=True) or []
    return sorted(node for node in nodes if not is_default_camera(node))


def has_curve_shape(node: str) -> bool:
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []
    return any(cmds.nodeType(shape) == "nurbsCurve" for shape in shapes)


def is_driven(node: str, attr: str) -> bool:
    """True when something other than keys feeds the attribute, so keying it does nothing."""
    sources = cmds.listConnections(f"{node}.{attr}", source=True, destination=False) or []
    for source in sources:
        if cmds.objectType(source, isAType="animCurve"):
            continue
        if cmds.objectType(source, isAType="animBlendNodeBase"):
            continue
        if cmds.nodeType(source) == "character":
            continue
        return True
    return False


def collapse(attrs: list[str]) -> list[str]:
    result = []
    taken = set()
    for triple in TRIPLES:
        parts = [triple + axis for axis in "XYZ"]
        if all(part in attrs for part in parts):
            result.append(triple)
            taken.update(parts)
    return result + [attr for attr in attrs if attr not in taken]


def default_of(node: str, attr: str) -> float | None:
    try:
        found = cmds.attributeQuery(attr, node=node, listDefault=True)
    except RuntimeError:
        return None
    return found[0] if found else None


def number_of(node: str, attr: str) -> float | None:
    value = cmds.getAttr(f"{node}.{attr}")
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def changed_values(node: str, attrs: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in collapse(attrs):
        parts = [name + axis for axis in "XYZ"] if name in TRIPLES else [name]
        current = [number_of(node, part) for part in parts]
        defaults = [default_of(node, part) for part in parts]
        if any(value is None for value in current):
            continue
        differs = any(
            defaults[index] is None or abs(value - defaults[index]) > 1e-4
            for index, value in enumerate(current)
        )
        if differs:
            rounded = [rnd(value, 3) for value in current]
            values[name] = rounded if name in TRIPLES else rounded[0]
    return values


def limits_of(node: str, attrs: list[str]) -> dict[str, list[float | int | None]]:
    found: dict[str, list[float | int | None]] = {}
    for triple, short in LIMIT_NAMES.items():
        for axis in "XYZ":
            low_on = cmds.getAttr(f"{node}.min{short}{axis}LimitEnable")
            high_on = cmds.getAttr(f"{node}.max{short}{axis}LimitEnable")
            if not (low_on or high_on):
                continue
            low = rnd(cmds.getAttr(f"{node}.min{short}{axis}Limit"), 3) if low_on else None
            high = rnd(cmds.getAttr(f"{node}.max{short}{axis}Limit"), 3) if high_on else None
            found[triple + axis] = [low, high]
    for attr in cmds.listAttr(node, userDefined=True, keyable=True) or []:
        if attr not in attrs:
            continue
        span = attr_range(node, attr)
        if span:
            found[attr] = span
    return found


def attr_range(node: str, attr: str) -> list[float | int | None]:
    try:
        has_low = cmds.attributeQuery(attr, node=node, minExists=True)
        has_high = cmds.attributeQuery(attr, node=node, maxExists=True)
        low = cmds.attributeQuery(attr, node=node, minimum=True)[0] if has_low else None
        high = cmds.attributeQuery(attr, node=node, maximum=True)[0] if has_high else None
    except RuntimeError:
        return []
    if low is None and high is None:
        return []
    return [None if low is None else rnd(low, 3), None if high is None else rnd(high, 3)]


def key_curves(node: str) -> list[str]:
    """Time curves keying the node, also through character sets and animation layers."""
    pairs = cmds.listConnections(node, source=True, destination=False, plugs=True, connections=True)
    plugs = (pairs or [])[1::2]
    curves: list[str] = []
    for _ in range(KEY_DEPTH):
        following = []
        for plug in plugs:
            source = plug.split(".")[0]
            if cmds.nodeType(source) in TIME_CURVE_TYPES:
                if source not in curves:
                    curves.append(source)
            elif cmds.nodeType(source) == "character":
                following += (
                    cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
                )
            elif cmds.objectType(source, isAType="animBlendNodeBase"):
                following += (
                    cmds.listConnections(source, source=True, destination=False, plugs=True) or []
                )
        plugs = following
        if not plugs:
            break
    return curves


def ik_chains() -> dict[str, str]:
    """Joints an IK handle rotates while its ikBlend is on, mapped to the handle's name."""
    chains = {}
    for handle in cmds.ls(type="ikHandle", long=True) or []:
        if cmds.getAttr(handle + ".ikBlend") <= 0:
            continue
        for joint in cmds.ls(cmds.ikHandle(handle, query=True, jointList=True) or [], long=True):
            chains[joint] = display_name(handle)
    return chains


def control_entry(node: str, chains: dict[str, str]) -> dict[str, Any]:
    keyable = cmds.listAttr(node, keyable=True, unlocked=True) or []
    keyable = [attr for attr in keyable if attr not in SKIN_ATTRS]
    solved = node in chains
    driven = [
        attr for attr in keyable if is_driven(node, attr) or (solved and attr.startswith("rotate"))
    ]
    free = [attr for attr in keyable if attr not in driven]
    entry: dict[str, Any] = {"name": display_name(node), "keyable": collapse(free)}
    if driven:
        entry["driven"] = collapse(driven)
    if solved:
        entry["ik"] = chains[node]
    posed = free
    if cmds.nodeType(node) == "joint":
        posed = [attr for attr in free if not attr.startswith("translate")]
    values = changed_values(node, posed)
    if values:
        entry["values"] = values
    limits = limits_of(node, free)
    if limits:
        entry["limits"] = limits
    entry["ro"] = ROTATE_ORDERS[cmds.getAttr(node + ".rotateOrder")]
    times = [at for curve in key_curves(node) for at in key_times(curve)]
    entry["keys"] = len(times)
    if times:
        entry["range"] = [frame(min(times)), frame(max(times))]
    return entry


def pick_controls(nodes: list[str]) -> tuple[list[str], str]:
    curves = [node for node in nodes if has_curve_shape(node)]
    animated = [node for node in nodes if key_curves(node)]
    handles = [node for node in nodes if cmds.nodeType(node) == "ikHandle" and key_curves(node)]
    controls = sorted(set(curves) | set(animated) | set(handles))
    controls = [node for node in controls if cmds.listAttr(node, keyable=True, unlocked=True)]
    if curves:
        return controls, "controls"
    joints = [node for node in nodes if cmds.nodeType(node) == "joint"]
    if joints:
        return joints, "joints"
    return controls, "controls"


def constraint_targets(constraint: str, constrained: str) -> list[str]:
    """Long paths of the nodes a constraint follows."""
    sources = cmds.listConnections(constraint + ".target", source=True, destination=False) or []
    paths = []
    for source in cmds.ls(sources, long=True) or []:
        if source in (constraint, constrained) or source in paths:
            continue
        paths.append(source)
    return paths


def touches(scope: set[str], paths: list[str]) -> bool:
    return not scope or bool(scope.intersection(paths))


def constrained_node(constraint: str) -> str:
    parents = cmds.listRelatives(constraint, parent=True, fullPath=True) or []
    return parents[0] if parents else ""


def ik_entries(scope: set[str]) -> list[dict[str, Any]]:
    result = []
    for handle in cmds.ls(type="ikHandle", long=True) or []:
        chain = cmds.ls(cmds.ikHandle(handle, query=True, jointList=True) or [], long=True) or []
        poles = []
        drivers = []
        sources = cmds.listConnections(handle, source=True, destination=False) or []
        for constraint in cmds.ls(sources, long=True, type="constraint") or []:
            if cmds.nodeType(constraint) == "poleVectorConstraint":
                poles.extend(constraint_targets(constraint, handle))
            else:
                drivers.extend(constraint_targets(constraint, handle))
        parents = cmds.listRelatives(handle, parent=True, fullPath=True) or []
        if not touches(scope, [handle] + chain + poles + drivers + parents):
            continue
        entry: dict[str, Any] = {
            "name": display_name(handle),
            "solver": cmds.ikHandle(handle, query=True, solver=True),
            "joints": [display_name(joint) for joint in chain],
            "blend": rnd(cmds.getAttr(handle + ".ikBlend"), 3),
        }
        if poles:
            entry["pole"] = display_name(poles[0])
        if drivers:
            entry["driven_by"] = [display_name(driver) for driver in drivers]
        if parents:
            entry["under"] = display_name(parents[0])
        result.append(entry)
    return result


def constraint_entries(scope: set[str]) -> list[dict[str, Any]]:
    result = []
    for constraint in cmds.ls(type="constraint", long=True) or []:
        node = constrained_node(constraint)
        targets = constraint_targets(constraint, node)
        if not touches(scope, [node] + targets):
            continue
        result.append(
            {
                "type": cmds.nodeType(constraint),
                "node": display_name(node) if node else "",
                "targets": [display_name(target) for target in targets],
            }
        )
    return result


def is_switch_target(node: str, attr: str) -> bool:
    return attr in SWITCH_ATTRS or cmds.objectType(node, isAType="constraint")


def switch_drives(plug: str) -> list[str]:
    drives: list[str] = []
    level = cmds.listConnections(plug, source=False, destination=True, plugs=True) or []
    for _ in range(SWITCH_DEPTH):
        following = []
        for target in level:
            node, _, attr = target.partition(".")
            if is_switch_target(node, attr.split("[")[0]):
                name = f"{display_name(node)}.{attr}"
                if name not in drives:
                    drives.append(name)
            elif cmds.nodeType(node) in UTILITY_TYPES:
                following.extend(
                    cmds.listConnections(node, source=False, destination=True, plugs=True) or []
                )
        level = following
        if not level:
            break
    return drives[:SWITCH_DRIVES]


def switch_entries(controls: list[str]) -> list[dict[str, Any]]:
    result = []
    for node in controls:
        for attr in cmds.listAttr(node, userDefined=True, keyable=True) or []:
            drives = switch_drives(f"{node}.{attr}")
            if not drives:
                continue
            entry: dict[str, Any] = {"attr": f"{display_name(node)}.{attr}"}
            value = number_of(node, attr)
            if value is not None:
                entry["value"] = rnd(value, 3)
            span = attr_range(node, attr)
            if span:
                entry["range"] = span
            entry["drives"] = drives
            result.append(entry)
    return result


def character_sets() -> list[dict[str, Any]]:
    result = []
    for character in cmds.ls(type="character") or []:
        members = cmds.sets(character, query=True) or []
        result.append({"name": character, "members": len(members)})
    return result


def get_rig_info(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "root")
    root = resolve(name) if name else ""
    nodes = scope_of(root)
    scope = set(nodes) if root else set()
    controls, kind = pick_controls(nodes)

    result: dict[str, Any] = {}
    if root:
        result["root"] = display_name(root)
    result["kind"] = kind
    chains = ik_chains()
    entries = [control_entry(node, chains) for node in controls[:CONTROL_LIMIT]]
    result["controls"] = {**listing(entries, CONTROL_LIMIT), "total": len(controls)}
    sections = {
        "ik": (ik_entries(scope), IK_LIMIT),
        "constraints": (constraint_entries(scope), CONSTRAINT_LIMIT),
        "switches": (switch_entries(controls[:CONTROL_LIMIT]), SWITCH_LIMIT),
        "character_sets": (character_sets(), SET_LIMIT),
    }
    for key, (items, limit) in sections.items():
        if items:
            result[key] = listing(items, limit)
    return result
