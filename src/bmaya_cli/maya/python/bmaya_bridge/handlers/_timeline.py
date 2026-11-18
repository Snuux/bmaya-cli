from __future__ import annotations

from typing import Any, NamedTuple

from maya import cmds

from bmaya_bridge import framespec
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import frame, text_param

HINT_CLIPS = 8


class Frames(NamedTuple):
    values: list[float]
    clip: str


def nodes_of_type(node_type: str) -> list[str]:
    # The type is unknown while its plug-in is not loaded; a read must not load it.
    try:
        return cmds.ls(type=node_type) or []
    except RuntimeError:
        return []


def clip_range(start: float, end: float) -> list[float | int]:
    if end < start:
        start, end = end, start
    return [frame(start), frame(end)]


def exporter_clips() -> list[dict[str, Any]]:
    result = []
    for node in nodes_of_type("gameFbxExporter"):
        for index in cmds.getAttr(node + ".animClips", multiIndices=True) or []:
            plug = f"{node}.animClips[{index}]"
            name = cmds.getAttr(plug + ".animClipName") or ""
            if not name:
                continue
            start = cmds.getAttr(plug + ".animClipStart")
            end = cmds.getAttr(plug + ".animClipEnd")
            entry: dict[str, Any] = {
                "name": name,
                "range": clip_range(start, end),
                "exporter": node,
            }
            if not cmds.getAttr(plug + ".exportAnimClip"):
                entry["export"] = False
            result.append(entry)
    return result


def bookmark_clips() -> list[dict[str, Any]]:
    result = []
    for node in nodes_of_type("timeSliderBookmark"):
        name = cmds.getAttr(node + ".name") or node
        start = cmds.getAttr(node + ".timeRangeStart")
        end = cmds.getAttr(node + ".timeRangeStop")
        result.append({"name": name, "range": clip_range(start, end), "bookmark": node})
    return result


def clips() -> list[dict[str, Any]]:
    """Clips of the Game Exporter and time slider bookmarks; both ends of a range are frames."""
    merged: list[dict[str, Any]] = []
    for entry in exporter_clips() + bookmark_clips():
        for known in merged:
            if known["name"] == entry["name"] and known["range"] == entry["range"]:
                known.update(entry)
                break
        else:
            merged.append(dict(entry))
    return merged


def span(entry: dict[str, Any]) -> str:
    start, end = entry["range"]
    return f"{start}:{end}"


def source(entry: dict[str, Any]) -> str:
    return entry.get("exporter") or entry.get("bookmark") or "?"


def clips_hint(known: list[dict[str, Any]]) -> str:
    if not known:
        return (
            "The scene has no Game Exporter clips or time slider bookmarks; "
            "pass --frames START:END instead."
        )
    shown = ", ".join(f"{entry['name']} [{span(entry)}]" for entry in known[:HINT_CLIPS])
    more = f" and {len(known) - HINT_CLIPS} more" if len(known) > HINT_CLIPS else ""
    return f"Clips: {shown}{more}. `bmaya anim` lists them."


def find_clip(name: str) -> dict[str, Any]:
    known = clips()
    matches = [entry for entry in known if entry["name"] == name]
    if not matches:
        raise BridgeError(f"No clip named '{name}'.", hint=clips_hint(known))
    if len(matches) > 1:
        described = ", ".join(f"{source(entry)} [{span(entry)}]" for entry in matches)
        raise BridgeError(
            f"Clip '{name}' has {len(matches)} ranges: {described}.",
            hint=f"Pass the range you mean with --frames, for example --frames {span(matches[0])}.",
        )
    return matches[0]


def resolve_frames(params: dict[str, Any]) -> Frames | None:
    """Frames named by `frames` or `clip`; None when neither is given."""
    spec = text_param(params, "frames")
    name = text_param(params, "clip")
    if spec and name:
        raise BridgeError(
            "Pass --frames or --clip, not both.",
            hint="--clip NAME takes the whole clip; --frames 0:30:2 picks the frames yourself.",
        )
    if name:
        clip = find_clip(name)
        return Frames(framespec.parse(span(clip)), name)
    if spec:
        return Frames(framespec.parse(spec), "")
    return None
