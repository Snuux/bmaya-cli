from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from maya import cmds

from bmaya_bridge.errors import BridgeError

FPS_BY_UNIT = {
    "game": 15.0,
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
}


def text_param(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if value is None:
        return ""
    return str(value).strip()


def int_param(params: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = params.get(key)
    if value in (None, "", 0):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError) as exc:
        raise BridgeError(f"'{key}' must be a number, got {value!r}.") from exc
    return max(low, min(high, number))


def float_param(params: dict[str, Any], key: str) -> float | None:
    value = params.get(key)
    if value in (None, "", 0):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BridgeError(f"'{key}' must be a number, got {value!r}.") from exc


def bool_param(params: dict[str, Any], key: str) -> bool:
    value = params.get(key)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def rnd(value: float, digits: int = 4) -> float | int:
    rounded = round(float(value), digits)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def rnd_list(values: Iterable[float], digits: int = 4) -> list[float | int]:
    return [rnd(v, digits) for v in values]


def frame(value: float) -> float | int:
    return rnd(value, 3)


def listing(items: list[Any], limit: int) -> dict[str, Any]:
    return {"items": items[:limit], "shown": min(len(items), limit), "total": len(items)}


def short_name(path: str) -> str:
    return path.rsplit("|", 1)[-1]


def display_name(path: str) -> str:
    names = cmds.ls(path)
    if names:
        return names[0]
    return short_name(path)


def resolve(name: str) -> str:
    if not name:
        raise BridgeError(
            "No object name given.",
            hint="Pass a node name. `bmaya scene` lists the nodes in the scene.",
        )
    matches = cmds.ls(name, long=True) or []
    if not matches:
        raise BridgeError(
            f"No object named '{name}' in the scene.",
            hint=f"`bmaya scene --query {short_name(name)}` finds similar names.",
        )
    if len(matches) > 1:
        shown = ", ".join(matches[:10])
        more = f" and {len(matches) - 10} more" if len(matches) > 10 else ""
        raise BridgeError(
            f"'{name}' matches {len(matches)} nodes.",
            hint=f"Use a full path instead: {shown}{more}.",
        )
    return matches[0]


def is_default_camera(node: str) -> bool:
    shapes = cmds.listRelatives(node, shapes=True, type="camera", fullPath=True) or []
    if cmds.nodeType(node) == "camera":
        shapes = [node]
    return any(cmds.camera(shape, query=True, startupCamera=True) for shape in shapes)


def shapes_of(transform: str, node_type: str = "") -> list[str]:
    kwargs: dict[str, Any] = {"shapes": True, "fullPath": True, "noIntermediate": True}
    if node_type:
        kwargs["type"] = node_type
    return cmds.listRelatives(transform, **kwargs) or []


def display_type(node: str) -> str:
    node_type = cmds.nodeType(node)
    if node_type != "transform":
        return node_type
    shapes = shapes_of(node)
    if shapes:
        return cmds.nodeType(shapes[0])
    if cmds.listRelatives(node, children=True):
        return "group"
    return "transform"


def fps() -> float:
    unit = cmds.currentUnit(query=True, time=True)
    if unit in FPS_BY_UNIT:
        return FPS_BY_UNIT[unit]
    match = re.match(r"^([\d.]+)fps$", unit)
    if match:
        return float(match.group(1))
    return 24.0


def time_info() -> dict[str, Any]:
    return {
        "fps": rnd(fps(), 3),
        "time_unit": cmds.currentUnit(query=True, time=True),
        "playback": [
            frame(cmds.playbackOptions(query=True, minTime=True)),
            frame(cmds.playbackOptions(query=True, maxTime=True)),
        ],
        "anim_range": [
            frame(cmds.playbackOptions(query=True, animationStartTime=True)),
            frame(cmds.playbackOptions(query=True, animationEndTime=True)),
        ],
        "frame": frame(cmds.currentTime(query=True)),
    }


def key_times(curve: str) -> list[float]:
    return cmds.keyframe(curve, query=True, timeChange=True) or []


def anim_curves(node: str) -> list[tuple[str, str]]:
    pairs = (
        cmds.listConnections(
            node,
            type="animCurve",
            source=True,
            destination=False,
            connections=True,
            plugs=False,
        )
        or []
    )
    result = []
    for index in range(0, len(pairs), 2):
        plug = pairs[index]
        attr = plug.split(".", 1)[1] if "." in plug else plug
        result.append((attr, pairs[index + 1]))
    return result
