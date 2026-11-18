from __future__ import annotations

import math

from bmaya_bridge.errors import BridgeError

MAX_FRAMES = 5000
SPEC_HINT = (
    "Use 1,12,24, a range 0:30 (both ends included), 0:30:2 for every second frame, "
    "or --clip NAME (`bmaya anim` lists the clips). Negative frames need an equals sign: "
    "--frames=-10:20."
)


def label(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def parse_number(text: str, spec: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise BridgeError(f"Bad frame spec '{spec}'.", hint=SPEC_HINT) from None
    if not math.isfinite(value):
        raise BridgeError(f"Bad frame spec '{spec}'.", hint=SPEC_HINT)
    return value


def range_count(start: float, end: float, step: float) -> int:
    return int(math.floor((end - start) / step + 1e-9)) + 1


def item_frames(item: str, spec: str, room: int) -> list[float]:
    parts = item.split(":")
    if len(parts) == 1:
        return [parse_number(parts[0], spec)]
    if len(parts) > 3:
        raise BridgeError(f"Bad frame spec '{spec}'.", hint=SPEC_HINT)
    start = parse_number(parts[0], spec)
    end = parse_number(parts[1], spec)
    step = parse_number(parts[2], spec) if len(parts) == 3 else 1.0
    if step <= 0:
        raise BridgeError(
            f"The step in '{item}' must be above 0.",
            hint="Write a range as START:END:STEP, for example 0:30:2.",
        )
    if end < start:
        raise BridgeError(
            f"Range {item} runs backwards.",
            hint=f"Write the smaller frame first: {label(end)}:{label(start)}.",
        )
    count = range_count(start, end, step)
    if count > room:
        raise BridgeError(
            f"'{spec}' gives more than {MAX_FRAMES} frames.",
            hint=f"Use a step ({label(start)}:{label(end)}:10) or a shorter range.",
        )
    return [start + index * step for index in range(count)]


def parse(spec: str) -> list[float]:
    """Frames of a spec like `1,12,24`, `0:30` or `0:30:2`, sorted and without repeats."""
    text = spec.replace(" ", "")
    frames: list[float] = []
    for item in text.split(","):
        if not item:
            continue
        frames.extend(item_frames(item, spec, MAX_FRAMES - len(frames)))
    return sorted({round(value, 4) for value in frames})


def thin(frames: list[float], count: int) -> list[float]:
    """Evenly spaced picks that keep the first and the last frame."""
    if len(frames) <= count:
        return list(frames)
    if count <= 1:
        return frames[:1]
    last = len(frames) - 1
    return [frames[int(index * last / (count - 1) + 0.5)] for index in range(count)]


def uniform_step(frames: list[float]) -> float:
    """The spacing of evenly spaced frames, or 0 when the spacing varies."""
    if len(frames) < 2:
        return 0.0
    step = frames[1] - frames[0]
    for index in range(1, len(frames)):
        if abs(frames[index] - frames[index - 1] - step) > 1e-3:
            return 0.0
    return round(step, 4)


def as_spec(frames: list[float]) -> str:
    if not frames:
        return ""
    step = uniform_step(frames)
    if len(frames) > 2 and step:
        span = f"{label(frames[0])}:{label(frames[-1])}"
        return span if step == 1 else f"{span}:{label(step)}"
    return ",".join(label(value) for value in frames)
