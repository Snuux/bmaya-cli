"""Skeletons as plain data: each joint's name, parent, world position and axes.

Pure Python: no maya import. Axes are the joint's x, y and z as unit vectors in world space.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from bmaya_bridge.skinweights import other_side

Vec = tuple[float, float, float]
Axes = tuple[Vec, Vec, Vec]
Matrix3 = list[list[float]]

AXES = "xyz"
JACOBI_SWEEPS = 50
TURN_TOLERANCE = 1.0
LENGTH_TOLERANCE = 0.02
PLACE_SHARE = 0.002
SHOWN = 20


class Bone(NamedTuple):
    name: str
    parent: str
    at: Vec
    axes: Axes


class Fit(NamedTuple):
    """target = scale * rotation(source) + move, the closest such map in least squares."""

    scale: float
    rotation: Matrix3
    move: Vec


def leaf(name: str) -> str:
    return name.rsplit("|", 1)[-1]


def clashes(names: Sequence[str]) -> list[str]:
    """Short names that more than one joint has: FBX and game engines keep one of them."""
    counts = Counter(leaf(name) for name in names)
    return sorted(name for name, count in counts.items() if count > 1)


def pairs(names: Sequence[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Left and right twins found by name, each pair once, and sided names with no twin."""
    present = set(names)
    found = []
    lonely = []
    for name in names:
        twin = other_side(name)
        if twin == name:
            continue
        if twin not in present:
            lonely.append(name)
        elif name < twin:
            found.append((name, twin))
    return found, lonely


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def reflect(point: Vec, axis: int, at: float) -> Vec:
    out = list(point)
    out[axis] = 2.0 * at - point[axis]
    return (out[0], out[1], out[2])


def apply(rotation: Matrix3, vector: Vec) -> Vec:
    return (
        dot((rotation[0][0], rotation[0][1], rotation[0][2]), vector),
        dot((rotation[1][0], rotation[1][1], rotation[1][2]), vector),
        dot((rotation[2][0], rotation[2][1], rotation[2][2]), vector),
    )


def mirror_gaps(bones: Sequence[Bone], axis: int, at: float) -> list[dict[str, Any]]:
    """How far each joint's twin stands from the joint's mirror image, worst first."""
    by_name = {bone.name: bone for bone in bones}
    found, _ = pairs([bone.name for bone in bones])
    gaps = []
    for name, twin in found:
        gap = length(sub(by_name[twin].at, reflect(by_name[name].at, axis, at)))
        gaps.append({"name": name, "twin": twin, "off": gap})
    gaps.sort(key=lambda entry: -entry["off"])
    return gaps


def mirror_kind(bone: Bone, twin: Bone, axis: int) -> str:
    """How `twin`'s axes relate to `bone`'s: `behavior` when each is the negated mirror image,
    so equal rotate values move both sides as mirror images (mirrorJoint's default);
    `orientation` when they point the same way; `other` for neither."""
    flipped = []
    for vector in bone.axes:
        image = reflect(vector, axis, 0.0)
        flipped.append((-image[0], -image[1], -image[2]))
    if angle_between(twin.axes, (flipped[0], flipped[1], flipped[2])) <= TURN_TOLERANCE:
        return "behavior"
    if angle_between(twin.axes, bone.axes) <= TURN_TOLERANCE:
        return "orientation"
    return "other"


def angle_between(one: Axes, other: Axes) -> float:
    """Degrees of the turn that takes one set of axes onto the other."""
    trace = sum(dot(one[index], other[index]) for index in range(3))
    cosine = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


def rotation_angle(rotation: Matrix3) -> float:
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    return math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0))))


def largest_eigenvector(matrix: list[list[float]]) -> list[float]:
    """Eigenvector of the largest eigenvalue of a small symmetric matrix, by Jacobi turns."""
    size = len(matrix)
    a = [row[:] for row in matrix]
    vectors = [[1.0 if row == column else 0.0 for column in range(size)] for row in range(size)]
    for _ in range(JACOBI_SWEEPS):
        off = sum(
            a[row][column] ** 2 for row in range(size) for column in range(size) if row != column
        )
        if off < 1e-22:
            break
        for p in range(size - 1):
            for q in range(p + 1, size):
                if abs(a[p][q]) < 1e-30:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(size):
                    akp = a[k][p]
                    akq = a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(size):
                    apk = a[p][k]
                    aqk = a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(size):
                    vkp = vectors[k][p]
                    vkq = vectors[k][q]
                    vectors[k][p] = c * vkp - s * vkq
                    vectors[k][q] = s * vkp + c * vkq
    best = max(range(size), key=lambda index: a[index][index])
    return [vectors[row][best] for row in range(size)]


def centroid(points: Sequence[Vec]) -> Vec:
    count = float(len(points))
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
        sum(point[2] for point in points) / count,
    )


def spread(points: Sequence[Vec]) -> float:
    middle = centroid(points)
    return sum(dot(sub(point, middle), sub(point, middle)) for point in points)


def similarity(source: Sequence[Vec], target: Sequence[Vec]) -> Fit | None:
    """Scale, turn and move that put `source` closest onto `target` (Horn's quaternion method).

    None when the points do not pin down a turn: fewer than three, or all on one line."""
    if len(source) < 3 or len(source) != len(target):
        return None
    from_middle = centroid(source)
    to_middle = centroid(target)
    xs = [sub(point, from_middle) for point in source]
    ys = [sub(point, to_middle) for point in target]
    size = spread(source)
    if size < 1e-18:
        return None
    s = [
        [sum(xs[k][row] * ys[k][column] for k in range(len(xs))) for column in range(3)]
        for row in range(3)
    ]
    n = [
        [
            s[0][0] + s[1][1] + s[2][2],
            s[1][2] - s[2][1],
            s[2][0] - s[0][2],
            s[0][1] - s[1][0],
        ],
        [
            s[1][2] - s[2][1],
            s[0][0] - s[1][1] - s[2][2],
            s[0][1] + s[1][0],
            s[2][0] + s[0][2],
        ],
        [
            s[2][0] - s[0][2],
            s[0][1] + s[1][0],
            -s[0][0] + s[1][1] - s[2][2],
            s[1][2] + s[2][1],
        ],
        [
            s[0][1] - s[1][0],
            s[2][0] + s[0][2],
            s[1][2] + s[2][1],
            -s[0][0] - s[1][1] + s[2][2],
        ],
    ]
    w, x, y, z = largest_eigenvector(n)
    norm = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    rotation = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    if not line_free(xs):
        return None
    turned = [apply(rotation, point) for point in xs]
    scale = sum(dot(turned[k], ys[k]) for k in range(len(ys))) / size
    if scale < 1e-9:
        return None
    middle = apply(rotation, from_middle)
    move = (
        to_middle[0] - scale * middle[0],
        to_middle[1] - scale * middle[1],
        to_middle[2] - scale * middle[2],
    )
    return Fit(scale, rotation, move)


def line_free(offsets: Sequence[Vec]) -> bool:
    """True when the points do not all lie on one line through their centre."""
    longest = max(offsets, key=length)
    reach = length(longest)
    if reach < 1e-12:
        return False
    direction = (longest[0] / reach, longest[1] / reach, longest[2] / reach)
    for point in offsets:
        along = dot(point, direction)
        aside = sub(point, (direction[0] * along, direction[1] * along, direction[2] * along))
        if length(aside) > 1e-6 * reach:
            return True
    return False


def placed(fit: Fit, point: Vec) -> Vec:
    turned = apply(fit.rotation, point)
    return (
        fit.scale * turned[0] + fit.move[0],
        fit.scale * turned[1] + fit.move[1],
        fit.scale * turned[2] + fit.move[2],
    )


def turned_axes(fit: Fit, axes: Axes) -> Axes:
    return (
        apply(fit.rotation, axes[0]),
        apply(fit.rotation, axes[1]),
        apply(fit.rotation, axes[2]),
    )


def box_size(points: Sequence[Vec]) -> float:
    if not points:
        return 0.0
    low = [min(point[index] for point in points) for index in range(3)]
    high = [max(point[index] for point in points) for index in range(3)]
    return length((high[0] - low[0], high[1] - low[1], high[2] - low[2]))


def rounded(value: float, digits: int = 4) -> float | int:
    number = round(float(value), digits)
    if number == int(number):
        return int(number)
    return number


def listing(items: list[Any], limit: int = SHOWN) -> dict[str, Any]:
    return {"items": items[:limit], "shown": min(len(items), limit), "total": len(items)}


def bone_lengths(bones: dict[str, Bone]) -> dict[str, float]:
    out = {}
    for name, bone in bones.items():
        if bone.parent in bones:
            out[name] = length(sub(bone.at, bones[bone.parent].at))
    return out


def common_ratio(ratios: list[float]) -> float:
    """The ratio most bones agree on: one odd bone must not pull it, as it pulls a median of few."""
    best: list[float] = []
    for ratio in ratios:
        near = [other for other in ratios if abs(other - ratio) <= LENGTH_TOLERANCE * ratio]
        if len(near) > len(best):
            best = near
    return sum(best) / len(best)


def over(offsets: list[float], tolerance: float) -> int:
    return sum(1 for offset in offsets if offset > tolerance)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def refined_fit(source: list[Vec], target: list[Vec], tolerance: float) -> Fit | None:
    """The best fit, fitted again on the joints that agree with it, so that a few joints in
    the wrong place do not tilt it for all the others."""
    fit = similarity(source, target)
    if fit is None:
        return None
    misses = [length(sub(target[k], placed(fit, source[k]))) for k in range(len(source))]
    limit = max(tolerance, 2.0 * median(misses))
    keep = [k for k in range(len(source)) if misses[k] <= limit]
    if 3 <= len(keep) < len(source):
        better = similarity([source[k] for k in keep], [target[k] for k in keep])
        if better is not None:
            return better
    return fit


def compare(
    scene: Sequence[Bone],
    reference: Sequence[Bone],
    rename: Callable[[str], str],
) -> tuple[dict[str, Any], list[str]]:
    """The scene skeleton against a reference one, names mapped by `rename`.

    Positions are compared as they stand, or after the scale, turn and move that fit the
    reference best when that leaves fewer joints off: a skeleton that is the same but placed
    elsewhere still reads as the same, and a few wrong joints are not smeared over all."""
    ours = {bone.name: bone for bone in scene}
    theirs = {}
    named = {}
    for bone in reference:
        name = rename(bone.name)
        parent = rename(bone.parent) if bone.parent else ""
        theirs[name] = Bone(name, parent, bone.at, bone.axes)
        named[name] = bone.name
    matched = [name for name in theirs if name in ours]
    missing = [named[name] for name in theirs if name not in ours]
    extra = [name for name in ours if name not in theirs]
    parents = []
    for name in matched:
        want = theirs[name].parent if theirs[name].parent in theirs else ""
        have = ours[name].parent if ours[name].parent in ours else ""
        if want != have:
            parents.append({"name": name, "scene": have or None, "file": want or None})
    result: dict[str, Any] = {
        "joints": len(reference),
        "matched": len(matched),
        "missing": listing(missing),
        "extra": listing(extra),
        "parents": listing(parents),
    }
    issues = []
    if missing:
        issues.append(f"{len(missing)} joints of the file are not in the scene: " + some(missing))
    if extra:
        issues.append(f"{len(extra)} scene joints are not in the file: " + some(extra))
    if parents:
        issues.append(
            f"{len(parents)} joints hang under another parent than in the file: "
            + some([entry["name"] for entry in parents])
        )
    if not matched:
        result["same"] = False
        return result, issues

    ref_points = [theirs[name].at for name in matched]
    our_points = [ours[name].at for name in matched]
    tolerance = max(1e-6, PLACE_SHARE * box_size(our_points))
    as_is = [length(sub(ours[name].at, theirs[name].at)) for name in matched]
    fit = None
    offsets = as_is
    if max(as_is) > tolerance:
        fit = refined_fit(ref_points, our_points, tolerance)
        if fit is not None:
            fitted = [length(sub(ours[name].at, placed(fit, theirs[name].at))) for name in matched]
            if over(fitted, tolerance) < over(as_is, tolerance):
                offsets = fitted
            else:
                fit = None
    place: dict[str, Any] = {"off": rounded(max(as_is)), "tolerance": rounded(tolerance)}
    if fit is not None:
        place["fit"] = {
            "scale": rounded(fit.scale),
            "turned": rounded(rotation_angle(fit.rotation), 2),
            "moved": [rounded(value) for value in fit.move],
            "off": rounded(max(offsets)),
        }
    result["place"] = place
    moved = sorted(
        (
            {"name": matched[k], "off": rounded(offsets[k])}
            for k in range(len(matched))
            if offsets[k] > tolerance
        ),
        key=lambda entry: -entry["off"],
    )
    result["moved"] = listing(moved)
    if moved:
        how = "after the best fit" if fit is not None else "where they stand"
        issues.append(
            f"{len(moved)} joints stand elsewhere than in the file ({how}): "
            + some([entry["name"] for entry in moved])
        )

    turns = []
    for name in matched:
        file_axes = theirs[name].axes
        if fit is not None:
            file_axes = turned_axes(fit, file_axes)
        angle = angle_between(ours[name].axes, file_axes)
        if angle > TURN_TOLERANCE:
            turns.append({"name": name, "turned": rounded(angle, 2)})
    turns.sort(key=lambda entry: -entry["turned"])
    result["turned"] = listing(turns)
    if turns:
        issues.append(
            f"{len(turns)} joints have other axes than in the file: "
            + some([entry["name"] for entry in turns])
        )

    our_lengths = bone_lengths(ours)
    their_lengths = bone_lengths(theirs)
    ratios = {
        name: our_lengths[name] / their_lengths[name]
        for name in matched
        if name in our_lengths and their_lengths.get(name, 0.0) > 1e-9
    }
    if ratios:
        typical = common_ratio(list(ratios.values()))
        odd = sorted(
            (
                {
                    "name": name,
                    "scene": rounded(our_lengths[name]),
                    "file": rounded(their_lengths[name]),
                }
                for name, ratio in ratios.items()
                if abs(ratio - typical) > LENGTH_TOLERANCE * typical
            ),
            key=lambda entry: entry["name"],
        )
        result["lengths"] = {"ratio": rounded(typical), "odd": listing(odd)}
        if odd:
            issues.append(
                f"{len(odd)} bones have other proportions than in the file: "
                + some([entry["name"] for entry in odd])
            )
    result["same"] = not issues
    return result, issues


def some(names: list[str], count: int = 5) -> str:
    shown = ", ".join(names[:count])
    if len(names) > count:
        shown += f" and {len(names) - count} more"
    return shown
