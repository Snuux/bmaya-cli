"""Clips: the Game Exporter's animation rows and the time slider bookmarks, listed together
with what is wrong with them, and added, moved or removed in one undo step."""

from __future__ import annotations

import re
from typing import Any

from maya import cmds

from bmaya_bridge import cliplist
from bmaya_bridge.cliplist import Clip, Keys, Mark, Row
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers._common import bool_param, fps, frame, int_param, listing, text_param
from bmaya_bridge.handlers._timeline import nodes_of_type

CLIPS_SHOWN = 100
SPANS_SHOWN = 20
DEFAULT_GAP = 20.0
ANIMATION_EXPORT = 2
BOOKMARK_COLOR = (0.95, 0.75, 0.3)
TIME_CURVES = ["animCurveTA", "animCurveTL", "animCurveTU", "animCurveTT"]
ROW_TEXT = ("animClipName", "animClipSrcNode")
ROW_VALUES = ("animClipStart", "animClipEnd", "exportAnimClip", "animClipId")
NEW_EXPORTER_HINT = (
    "Make one in File > Game Exporter, Animation Clips tab (it sets the path and what to "
    "export); `bmaya export --clip NAME` exports a bookmark as it is."
)


def referenced(node: str) -> bool:
    return bool(cmds.referenceQuery(node, isNodeReferenced=True))


def exporter_kind(node: str) -> str:
    if cmds.getAttr(node + ".exportTypeIndex") != ANIMATION_EXPORT:
        return "model"
    return "referenced" if referenced(node) else ""


def read_rows() -> tuple[list[Row], list[str]]:
    """The rows with a name, and what is wrong with the rows of each node: Maya's Game
    Exporter reads rows 0 to size-1 and stops at a row with no name."""
    rows = []
    faults = []
    for node in nodes_of_type("gameFbxExporter"):
        kind = exporter_kind(node)
        indices = cmds.getAttr(node + ".animClips", multiIndices=True) or []
        if indices != list(range(len(indices))):
            missing = sorted(set(range(max(indices) + 1)) - set(indices))
            faults.append(
                f"{node} has no row at index {missing[0]}: the Game Exporter window and its "
                f"export read rows 0 to {len(indices) - 1} only, so the last ones are lost"
            )
        for index in indices:
            plug = f"{node}.animClips[{index}]"
            name = cmds.getAttr(plug + ".animClipName") or ""
            if not name:
                faults.append(
                    f"{node} has a row with no name at index {index}: the Game Exporter "
                    "refuses to export"
                )
                continue
            rows.append(
                Row(
                    name,
                    round(cmds.getAttr(plug + ".animClipStart"), 3),
                    round(cmds.getAttr(plug + ".animClipEnd"), 3),
                    node,
                    index,
                    bool(cmds.getAttr(plug + ".exportAnimClip")),
                    kind,
                )
            )
    return rows, faults


def read_marks() -> list[Mark]:
    marks = []
    for node in nodes_of_type("timeSliderBookmark"):
        marks.append(
            Mark(
                cmds.getAttr(node + ".name") or node,
                round(cmds.getAttr(node + ".timeRangeStart"), 3),
                round(cmds.getAttr(node + ".timeRangeStop"), 3),
                node,
            )
        )
    return marks


def read_clips() -> list[Clip]:
    return cliplist.merge(read_rows()[0], read_marks())


def time_curves() -> list[str]:
    return cmds.ls(type=TIME_CURVES) or []


def key_times() -> Keys:
    curves = time_curves()
    if not curves:
        return Keys([])
    return Keys(cmds.keyframe(curves, query=True, timeChange=True) or [])


def lost_bookmarks() -> list[str]:
    """Bookmarks Maya read while the timeSliderBookmark plug-in was not loaded: they are
    `unknown` nodes, invisible to the time slider, and setAttr on them fails."""
    out = []
    for node in cmds.ls(type="unknown") or []:
        if cmds.unknownNode(node, query=True, realClassName=True) == "timeSliderBookmark":
            out.append(node)
    return out


def animation_exporters() -> list[str]:
    return [node for node in nodes_of_type("gameFbxExporter") if exporter_kind(node) == ""]


def entry(clip: Clip, keys: Keys) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": clip.name,
        "range": [frame(clip.start), frame(clip.end)],
        "length": frame(clip.end - clip.start),
    }
    if clip.rows:
        out["exporter"] = clip.rows[0].node
        out["index"] = clip.rows[0].index
        if not clip.rows[0].export:
            out["export"] = False
    if clip.marks:
        out["bookmark"] = clip.marks[0].node
    out["keys"] = keys.within(clip.start, clip.end)
    return out


def span_entry(item: cliplist.Span) -> dict[str, Any]:
    out: dict[str, Any] = {"range": [frame(item.start), frame(item.end)], "keys": item.keys}
    if item.near:
        out["near"] = item.near
        out["gap"] = frame(item.gap)
    if item.far:
        out["far"] = True
    return out


def get_clips(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    limit = int_param(params, "limit", CLIPS_SHOWN, 1, 1000)
    rows, faults = read_rows()
    clips = cliplist.merge(rows, read_marks())
    keys = key_times()
    shown = [clip for clip in clips if not name or clip.name == name]
    if name and not shown:
        raise BridgeError(f"No clip named '{name}'.", hint=known_hint(clips))
    issues = [
        problem.text
        for problem in cliplist.problems(clips, keys)
        if not name or name in problem.clips
    ]
    spans = cliplist.outside(clips, keys)
    if not name:
        issues.extend(faults)
        strays = cliplist.stray(spans)
        if strays:
            issues.append(cliplist.stray_text(strays))
        lost = lost_bookmarks()
        if lost:
            issues.append(
                f"{len(lost)} bookmarks are unknown nodes ({', '.join(lost[:5])}): the scene "
                "was opened without the timeSliderBookmark plug-in. Load it and open the "
                "scene again."
            )
    return {
        "fps": fps(),
        "playback": [
            frame(cmds.playbackOptions(query=True, minTime=True)),
            frame(cmds.playbackOptions(query=True, maxTime=True)),
        ],
        "animation": [
            frame(cmds.playbackOptions(query=True, animationStartTime=True)),
            frame(cmds.playbackOptions(query=True, animationEndTime=True)),
        ],
        "exporters": animation_exporters(),
        "clips": listing([entry(clip, keys) for clip in shown], limit),
        "outside": listing(
            [span_entry(item) for item in spans if not name or item.near == name], SPANS_SHOWN
        ),
        "issues": issues,
    }


def known_hint(clips: list[Clip]) -> str:
    if not clips:
        return "The scene has no clips. Add one: bmaya clip NAME --add 0:30."
    names = ", ".join(dict.fromkeys(clip.name for clip in clips[:10]))
    more = f" and {len(clips) - 10} more" if len(clips) > 10 else ""
    return f"Clips: {names}{more}. `bmaya clip` lists them."


def parse_range(text: str, flag: str) -> tuple[float, float] | float:
    """START:END, or one number: a length."""
    try:
        if ":" in text:
            start, _, end = text.partition(":")
            low, high = float(start), float(end)
            if high < low:
                raise ValueError
            return low, high
        length = float(text)
        if length <= 0:
            raise ValueError
        return length
    except ValueError:
        raise BridgeError(
            f"Bad {flag} '{text}'.",
            hint=f"{flag} 0:90 for frames 0 to 90, {flag}=-30:0 for frames before 0, or "
            f"{flag} 90 for a clip 90 frames long.",
        ) from None


def parse_gap(text: str) -> float:
    if not text:
        return DEFAULT_GAP
    try:
        gap = float(text)
    except ValueError:
        gap = -1.0
    if gap < 0:
        raise BridgeError(f"Bad --gap '{text}'.", hint="--gap 0 or more frames, like --gap 200.")
    return gap


def pick_exporter(wanted: str) -> str:
    """The animation Game Exporter node a new row goes to; empty when the scene has none."""
    nodes = animation_exporters()
    if wanted:
        if wanted not in nodes_of_type("gameFbxExporter"):
            raise BridgeError(
                f"No Game Exporter node named '{wanted}'.",
                hint="Animation exporter nodes: " + (", ".join(nodes) or "none") + ".",
            )
        kind = exporter_kind(wanted)
        if kind:
            why = "exports a model, not clips" if kind == "model" else "is from a referenced file"
            raise BridgeError(
                f"'{wanted}' {why}: `bmaya export --game-exporter` would not export the clip.",
                hint="Animation exporter nodes: " + (", ".join(nodes) or "none") + ".",
            )
        return wanted
    if len(nodes) > 1:
        raise BridgeError(
            f"The scene has {len(nodes)} animation Game Exporter nodes: {', '.join(nodes)}.",
            hint=f"Name one: --node {nodes[0]}.",
        )
    return nodes[0] if nodes else ""


def bookmark_name(name: str) -> str:
    return "bookmark_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def make_bookmark(name: str, start: float, end: float) -> str:
    """A bookmark node, named after the clip or with a number when that name is taken; the
    clip is its `.name`, not the node's."""
    cmds.loadPlugin("timeSliderBookmark", quiet=True)
    node = cmds.createNode("timeSliderBookmark", name=bookmark_name(name), skipSelect=True)
    cmds.setAttr(node + ".name", name, type="string")
    cmds.setAttr(node + ".color", *BOOKMARK_COLOR)
    set_mark(node, start, end)
    return node


def set_mark(node: str, start: float, end: float) -> None:
    cmds.setAttr(node + ".timeRangeStart", start)
    cmds.setAttr(node + ".timeRangeStop", end)


def add_row(node: str, name: str, start: float, end: float) -> int:
    indices = cmds.getAttr(node + ".animClips", multiIndices=True) or []
    index = max(indices) + 1 if indices else 0
    plug = f"{node}.animClips[{index}]"
    cmds.setAttr(plug + ".animClipName", name, type="string")
    set_row(node, index, start, end)
    cmds.setAttr(plug + ".exportAnimClip", True)
    return index


def set_row(node: str, index: int, start: float, end: float) -> None:
    plug = f"{node}.animClips[{index}]"
    cmds.setAttr(plug + ".animClipStart", start)
    cmds.setAttr(plug + ".animClipEnd", end)


def remove_row(node: str, index: int) -> None:
    """Takes a row out as the Game Exporter's own delete does: the rows after it move down
    one place and the last place goes, so the indices stay 0 to size-1."""
    indices = sorted(cmds.getAttr(node + ".animClips", multiIndices=True) or [])
    place = index
    for later in [other for other in indices if other > index]:
        source = f"{node}.animClips[{later}]"
        target = f"{node}.animClips[{place}]"
        for attr in ROW_TEXT:
            value = cmds.getAttr(f"{source}.{attr}") or ""
            cmds.setAttr(f"{target}.{attr}", value, type="string")
        for attr in ROW_VALUES:
            cmds.setAttr(f"{target}.{attr}", cmds.getAttr(f"{source}.{attr}"))
        place = later
    cmds.removeMultiInstance(f"{node}.animClips[{place}]", b=True)


def cover(start: float, end: float) -> list[float | int] | None:
    """Widens the animation range to hold the clip; the new range, or None."""
    low = cmds.playbackOptions(query=True, animationStartTime=True)
    high = cmds.playbackOptions(query=True, animationEndTime=True)
    if low <= start and end <= high:
        return None
    cmds.playbackOptions(animationStartTime=min(low, start), animationEndTime=max(high, end))
    return [frame(min(low, start)), frame(max(high, end))]


def edit_clip(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    add = text_param(params, "add")
    move = text_param(params, "set")
    remove = bool_param(params, "remove")
    if not name:
        raise BridgeError("Name the clip.", hint="bmaya clip walk --add 0:30")
    if sum(1 for flag in (add, move, remove) if flag) != 1:
        raise BridgeError(
            "Pass one of --add, --set or --remove.",
            hint=f"bmaya clip {name} --add 0:30, --set 0:40 or --remove.",
        )
    if bool_param(params, "keys") and not remove:
        raise BridgeError("--keys goes with --remove.", hint=f"bmaya clip {name} --remove --keys")
    clips = read_clips()
    named = [clip for clip in clips if clip.name == name]
    if add:
        return add_clip(name, add, clips, named, params)
    if not named:
        raise BridgeError(f"No clip named '{name}'.", hint=known_hint(clips))
    if move:
        return set_clip(named, move)
    return remove_clip(named, clips, bool_param(params, "keys"))


def add_clip(
    name: str, text: str, clips: list[Clip], named: list[Clip], params: dict[str, Any]
) -> dict[str, Any]:
    with_rows = [clip for clip in named if clip.rows]
    if with_rows:
        found = with_rows[0]
        raise BridgeError(
            f"'{name}' already has a Game Exporter row at {cliplist.span(found.start, found.end)}.",
            hint=f"Move it with bmaya clip {name} --set START:END.",
        )
    exporter = pick_exporter(text_param(params, "node"))
    wanted = parse_range(text, "--add")
    gap = parse_gap(text_param(params, "gap"))
    if named and not exporter:
        raise BridgeError(
            f"'{name}' is a bookmark already, and the scene has no animation Game Exporter "
            "node to give it a row.",
            hint=NEW_EXPORTER_HINT,
        )
    if isinstance(wanted, tuple):
        start, end = wanted
    elif named:
        start, end = named[0].start, named[0].start + wanted
    else:
        first = cmds.playbackOptions(query=True, animationStartTime=True)
        start = cliplist.next_start(clips, gap, first, cliplist.outside(clips, key_times()))
        end = start + wanted
    result: dict[str, Any] = {"name": name, "done": "added", "range": [frame(start), frame(end)]}
    if named:
        for mark in named[0].marks:
            set_mark(mark.node, start, end)
        result["bookmark"] = named[0].marks[0].node
    else:
        result["bookmark"] = make_bookmark(name, start, end)
    if exporter:
        result.update({"exporter": exporter, "index": add_row(exporter, name, start, end)})
    else:
        result["note"] = (
            "no animation Game Exporter node, so only a bookmark: `bmaya export --clip "
            f"{name}` exports it, `bmaya export --game-exporter` does not"
        )
    widened = cover(start, end)
    if widened:
        result["animation"] = widened
    return finish(result)


def set_clip(named: list[Clip], text: str) -> dict[str, Any]:
    found = named[0]
    wanted = parse_range(text, "--set")
    start, end = wanted if isinstance(wanted, tuple) else (found.start, found.start + wanted)
    rows = [row for clip in named for row in clip.rows]
    marks = [mark for clip in named for mark in clip.marks]
    refuse_held(found.name, [row.node for row in rows] + [mark.node for mark in marks])
    for row in rows:
        set_row(row.node, row.index, start, end)
    for mark in marks:
        set_mark(mark.node, start, end)
    result: dict[str, Any] = {
        "name": found.name,
        "done": "set",
        "range": [frame(start), frame(end)],
        "was": [frame(found.start), frame(found.end)],
        "rows": len(rows),
        "bookmarks": len(marks),
    }
    widened = cover(start, end)
    if widened:
        result["animation"] = widened
    return finish(result)


def refuse_held(name: str, nodes: list[str]) -> None:
    held = [node for node in dict.fromkeys(nodes) if referenced(node) or locked(node)]
    if held:
        raise BridgeError(
            f"'{name}' is in {', '.join(held)}, from a referenced file or locked: it cannot "
            "be changed here.",
            hint="Edit the clip in the referenced file, or unlock the node.",
        )


def locked(node: str) -> bool:
    return bool((cmds.lockNode(node, query=True, lock=True) or [False])[0])


def remove_clip(named: list[Clip], clips: list[Clip], keys: bool) -> dict[str, Any]:
    found = named[0]
    rows = [row for clip in named for row in clip.rows]
    marks = [mark for clip in named for mark in clip.marks]
    refuse_held(found.name, [row.node for row in rows] + [mark.node for mark in marks])
    others = [clip for clip in clips if clip.name != found.name]
    pieces = [piece for clip in named for piece in cliplist.free(clip.start, clip.end, others)]
    if keys:
        spans = cliplist.padding(cliplist.outside(clips, key_times()), found.name)
        pieces += [(item.start, item.end) for item in spans]
    curves = time_curves()
    before = keys_in(curves, pieces)
    for row in sorted(rows, key=lambda item: (item.node, -item.index)):
        remove_row(row.node, row.index)
    if marks:
        cmds.delete([mark.node for mark in marks])
    result: dict[str, Any] = {
        "name": found.name,
        "done": "removed",
        "range": [frame(found.start), frame(found.end)],
        "rows": len(rows),
        "bookmarks": len(marks),
    }
    issues = []
    if keys and curves:
        for low, high in pieces:
            current = time_curves()
            if current:
                cmds.cutKey(current, time=(low, high), clear=True)
        left = keys_in(time_curves(), pieces)
        result["keys_removed"] = before - left
        if left:
            issues.append(f"{left} keys on referenced or locked curves stay")
    elif before:
        issues.append(
            f"{before} keys in {cliplist.span(found.start, found.end)} stay, in no clip now "
            "(--keys removes them with the clip)"
        )
    return finish(result, issues)


def keys_in(curves: list[str], pieces: list[tuple[float, float]]) -> int:
    """Keys in the ranges. A curve whose last key was cut is gone, so take the list fresh."""
    if not curves:
        return 0
    total = 0
    for low, high in pieces:
        total += len(cmds.keyframe(curves, query=True, time=(low, high), timeChange=True) or [])
    return total


def finish(result: dict[str, Any], extra: list[str] | None = None) -> dict[str, Any]:
    clips = read_clips()
    result["issues"] = [
        problem.text
        for problem in cliplist.problems(clips, key_times())
        if result["name"] in problem.clips
    ] + list(extra or [])
    result["next"] = "`bmaya clip` lists the clips; `bmaya undo` takes this back"
    return result
