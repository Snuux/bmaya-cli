"""Clips as data: Game Exporter rows and time slider bookmarks merged by name, what is
wrong with them, keys that belong to no clip, and where a new clip goes.

Pure Python: no maya import. A range is two frames, both ends included, as the Game
Exporter keeps them: a clip of length 90 from frame 0 ends on frame 90. Rows keep frames
as 32-bit floats and bookmarks as times, so ranges match within SAME.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from typing import NamedTuple

STEP = 10
SAME = 1e-3
STRAYS_NAMED = 5


class Row(NamedTuple):
    """A Game Exporter row. `kind` is empty for a row the Game Exporter exports, else why
    it does not: "model" (the node exports a model) or "referenced"."""

    name: str
    start: float
    end: float
    node: str
    index: int
    export: bool
    kind: str = ""


class Mark(NamedTuple):
    name: str
    start: float
    end: float
    node: str


class Clip(NamedTuple):
    name: str
    start: float
    end: float
    rows: list[Row]
    marks: list[Mark]


class Problem(NamedTuple):
    clips: tuple[str, ...]
    text: str


class Span(NamedTuple):
    start: float
    end: float
    keys: int
    near: str
    gap: float
    far: bool


class Keys:
    """Key times counted once each, so a range question is two binary searches."""

    def __init__(self, times: Sequence[float]) -> None:
        counts: dict[float, int] = {}
        for time in times:
            counts[time] = counts.get(time, 0) + 1
        self.times = sorted(counts)
        self.counts = [counts[time] for time in self.times]
        self.totals = [0]
        for count in self.counts:
            self.totals.append(self.totals[-1] + count)

    def __len__(self) -> int:
        return self.totals[-1]

    def within(self, start: float, end: float) -> int:
        low = bisect.bisect_left(self.times, start - SAME)
        high = bisect.bisect_right(self.times, end + SAME)
        return self.totals[high] - self.totals[low]


def same(first: float, second: float) -> bool:
    return abs(first - second) < SAME


def ordered(start: float, end: float) -> tuple[float, float]:
    return (start, end) if start <= end else (end, start)


def merge(rows: Sequence[Row], marks: Sequence[Mark]) -> list[Clip]:
    """One clip per name and range, in time order. A bookmark joins the clip of its name
    with the same range, else the first clip of its name."""
    clips: list[Clip] = []
    for row in rows:
        start, end = ordered(row.start, row.end)
        match = next((clip for clip in clips if fits(clip, row.name, start, end)), None)
        if match is None:
            clips.append(Clip(row.name, start, end, [row], []))
        else:
            match.rows.append(row)
    for mark in marks:
        start, end = ordered(mark.start, mark.end)
        named = [clip for clip in clips if clip.name == mark.name]
        match = next((clip for clip in named if fits(clip, mark.name, start, end)), None)
        if match is None and named:
            match = named[0]
        if match is None:
            clips.append(Clip(mark.name, start, end, [], [mark]))
        else:
            match.marks.append(mark)
    return sorted(clips, key=lambda clip: (clip.start, clip.end, clip.name))


def fits(clip: Clip, name: str, start: float, end: float) -> bool:
    return clip.name == name and same(clip.start, start) and same(clip.end, end)


def problems(clips: Sequence[Clip], keys: Keys) -> list[Problem]:
    """What an agent should look at: ranges that differ for one name, overlaps, rows the
    Game Exporter skips, bookmarks with no row (when the scene has rows), clips with no keys."""
    out = []
    exported_rows = any(row.kind == "" for clip in clips for row in clip.rows)
    ranges: dict[str, list[Clip]] = {}
    for clip in clips:
        if clip.rows:
            ranges.setdefault(clip.name, []).append(clip)
    for name, group in ranges.items():
        if len(group) > 1:
            spans = ", ".join(span(clip.start, clip.end) for clip in group)
            text = f"{name} has Game Exporter rows with other ranges: {spans}"
            out.append(Problem((name,), text))
    for clip in clips:
        out.extend(clip_problems(clip, keys, exported_rows))
    for index, first in enumerate(clips):
        for second in clips[index + 1 :]:
            if second.start >= first.end - SAME:
                break
            text = (
                f"{first.name} and {second.name} overlap at "
                f"{span(second.start, min(first.end, second.end))}"
            )
            out.append(Problem((first.name, second.name), text))
    return out


def clip_problems(clip: Clip, keys: Keys, exported_rows: bool) -> list[Problem]:
    mine = (clip.name,)
    out = []
    if len(clip.rows) > 1:
        places = ", ".join(f"{row.node}[{row.index}]" for row in clip.rows)
        out.append(Problem(mine, f"{clip.name} has {len(clip.rows)} Game Exporter rows: {places}"))
    if len(clip.marks) > 1:
        out.append(Problem(mine, f"{clip.name} has {len(clip.marks)} bookmarks"))
    for mark in clip.marks:
        low, high = ordered(mark.start, mark.end)
        if clip.rows and not (same(low, clip.start) and same(high, clip.end)):
            text = (
                f"{clip.name}: the bookmark spans {span(low, high)}, "
                f"the Game Exporter row {span(clip.start, clip.end)}"
            )
            out.append(Problem(mine, text))
    for row in clip.rows:
        if row.kind == "model":
            text = f"{clip.name}: its row is in {row.node}, which exports a model, not clips"
            out.append(Problem(mine, text))
        elif row.kind == "referenced":
            text = (
                f"{clip.name}: its row is in {row.node}, from a referenced file, which "
                "`bmaya export --game-exporter` skips"
            )
            out.append(Problem(mine, text))
        elif not row.export:
            text = f"{clip.name}: export is off in {row.node}, so it is not exported"
            out.append(Problem(mine, text))
    if exported_rows and not clip.rows:
        text = f"{clip.name}: a bookmark with no Game Exporter row, so it is not exported"
        out.append(Problem(mine, text))
    if len(keys) and not keys.within(clip.start, clip.end):
        out.append(Problem(mine, f"{clip.name} has no keys in {span(clip.start, clip.end)}"))
    return out


def inside(clips: Sequence[Clip], time: float) -> bool:
    return any(clip.start - SAME <= time <= clip.end + SAME for clip in clips)


def outside(clips: Sequence[Clip], keys: Keys) -> list[Span]:
    """Keys in no clip, in runs that share their nearest clip and the side of it. `gap` is
    how far the run's closest key is from that clip. A run is `far` when its keys lie
    farther from the clip than the clip is long: not the extra keys a loop keeps one period
    past its ends, but keys of a clip that is gone."""
    length = {clip.name: clip.end - clip.start for clip in clips}
    out: list[Span] = []
    run: tuple[str, int, bool] | None = None
    for position, time in enumerate(keys.times):
        if inside(clips, time):
            continue
        near, gap, side = nearest(clips, time)
        key = (near, side, not near or gap > length[near])
        count = keys.counts[position]
        if out and key == run:
            last = out[-1]
            out[-1] = last._replace(end=time, keys=last.keys + count, gap=min(last.gap, gap))
        else:
            out.append(Span(time, time, count, near, gap, key[2]))
            run = key
    return out


def nearest(clips: Sequence[Clip], time: float) -> tuple[str, float, int]:
    """The clip nearest to a key outside every clip, how far it is, and the side: -1 when
    the key comes before the clip, 1 after. No clip gives an empty name."""
    best = ("", 0.0, 0)
    found = math.inf
    for clip in clips:
        if time < clip.start:
            gap, side = clip.start - time, -1
        else:
            gap, side = time - clip.end, 1
        if gap < found:
            found = gap
            best = (clip.name, gap, side)
    return best


def stray(spans: Sequence[Span]) -> list[Span]:
    return [item for item in spans if item.far]


def padding(spans: Sequence[Span], name: str) -> list[Span]:
    """The keys a loop keeps past the ends of clip `name`."""
    return [item for item in spans if item.near == name and not item.far]


def stray_text(spans: Sequence[Span]) -> str:
    if len(spans) == 1:
        item = spans[0]
        return (
            f"{item.keys} keys at {span(item.start, item.end)} belong to no clip "
            "(a clip that was removed?)"
        )
    keys = sum(item.keys for item in spans)
    named = ", ".join(span(item.start, item.end) for item in spans[:STRAYS_NAMED])
    more = f" and {len(spans) - STRAYS_NAMED} more" if len(spans) > STRAYS_NAMED else ""
    return (
        f"{keys} keys in {len(spans)} runs belong to no clip (clips that were removed?): "
        f"{named}{more}; `outside` lists them"
    )


def free(start: float, end: float, others: Sequence[Clip]) -> list[tuple[float, float]]:
    """The parts of a range no other clip covers; a frame two clips share stays."""
    pieces = [(start, end)]
    for other in others:
        cut = []
        for low, high in pieces:
            if other.end < low - SAME or other.start > high + SAME:
                cut.append((low, high))
                continue
            if other.start > low + SAME:
                cut.append((low, other.start - SAME))
            if other.end < high - SAME:
                cut.append((other.end + SAME, high))
        pieces = cut
    return [(low, high) for low, high in pieces if high >= low]


def next_start(clips: Sequence[Clip], gap: float, first: float, spans: Sequence[Span]) -> float:
    """Where a new clip goes: `gap` frames after the last clip, and past the keys its loop
    keeps after its end, on a multiple of 10."""
    if not clips:
        return first
    last = max(clip.end for clip in clips)
    tail = [item.end + 1 for item in spans if not item.far and item.start > last]
    return math.ceil(max([last + gap] + tail) / STEP) * STEP


def span(start: float, end: float) -> str:
    return f"{number(start)}:{number(end)}"


def number(value: float) -> str:
    rounded = round(float(value), 3)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)
