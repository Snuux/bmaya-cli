"""Skin weights as rows: one {influence: weight} dict per vertex. Pure Python: no maya import."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from bmaya_bridge.errors import BridgeError

Row = dict[str, float]
Color = tuple[int, int, int]

NO_WEIGHT = 1e-6
SUM_TOLERANCE = 1e-3
CHANGE_TOLERANCE = 1e-7
SAMPLES = 10
DEFAULT_PRUNE = 0.01
ROUND_DIGITS = 6
SIDES = {"l": "r", "r": "l", "left": "right", "right": "left", "lf": "rt", "rt": "lf"}
SEPARATORS = re.compile(r"([_.:\-| ]+)")
CAMEL_SIDE = re.compile(r"(Left|Right|left|right)(?=[A-Z0-9_]|$)")
HEAT: tuple[tuple[float, Color], ...] = (
    (0.0, (36, 44, 110)),
    (0.25, (40, 130, 215)),
    (0.5, (60, 185, 90)),
    (0.75, (240, 205, 40)),
    (1.0, (215, 40, 30)),
)
NO_SKIN: Color = (150, 150, 150)
VERTS_HINT = (
    "Use 12, a range 0:20 (both ends included), 0:200:10 for every tenth vertex, or a list."
)
FILE_HINT = (
    'A weights file is a JSON list with one {"joint": weight} object per vertex, or an object '
    'whose "weights" holds that list, or {"12": {...}} for only some vertices. '
    "`bmaya skin MESH --save FILE` writes one."
)


class Survey(NamedTuple):
    """What the weights of one mesh look like against an influence budget."""

    influences: list[dict[str, Any]]
    most: int
    over: list[int]
    unnormalized: list[int]
    unweighted: list[int]
    unused: list[str]


class Finished(NamedTuple):
    rows: list[Row]
    changed: int
    pruned: int
    capped: int
    normalized: int


def rows_from_flat(flat: Sequence[float], names: list[str]) -> list[Row]:
    """Rows from Maya's flat weights: every influence for vertex 0, then vertex 1, and on."""
    count = len(names)
    rows = []
    for start in range(0, len(flat), count):
        row = {}
        for index in range(count):
            weight = flat[start + index]
            if weight > NO_WEIGHT:
                row[names[index]] = weight
        rows.append(row)
    return rows


def flat_from_rows(rows: list[Row], names: list[str]) -> list[float]:
    index = {name: position for position, name in enumerate(names)}
    count = len(names)
    flat = [0.0] * (len(rows) * count)
    for vertex, row in enumerate(rows):
        start = vertex * count
        for name, weight in row.items():
            flat[start + index[name]] = weight
    return flat


def survey(rows: list[Row], names: list[str], budget: int) -> Survey:
    verts = dict.fromkeys(names, 0)
    peak = dict.fromkeys(names, 0.0)
    over = []
    unnormalized = []
    unweighted = []
    most = 0
    for vertex, row in enumerate(rows):
        used = 0
        total = 0.0
        for name, weight in row.items():
            if weight <= NO_WEIGHT:
                continue
            used += 1
            total += weight
            verts[name] = verts.get(name, 0) + 1
            peak[name] = max(peak.get(name, 0.0), weight)
        most = max(most, used)
        if used == 0:
            unweighted.append(vertex)
            continue
        if used > budget:
            over.append(vertex)
        if abs(total - 1.0) > SUM_TOLERANCE:
            unnormalized.append(vertex)
    influences = [
        {"name": name, "verts": verts[name], "max": round(peak[name], 4)}
        for name in sorted(verts, key=lambda name: (-verts[name], name))
    ]
    unused = [name for name in names if verts.get(name, 0) == 0]
    return Survey(influences, most, over, unnormalized, unweighted, unused)


def issue_lines(found: Survey, budget: int) -> list[str]:
    lines = []
    if found.over:
        lines.append(f"{len(found.over)} verts with more than {budget} influences")
    if found.unnormalized:
        lines.append(f"{len(found.unnormalized)} verts whose weights do not add up to 1")
    if found.unweighted:
        lines.append(f"{len(found.unweighted)} unweighted verts")
    return lines


def samples(found: Survey) -> dict[str, list[int]]:
    picked = {}
    for key, ids in (
        ("over", found.over),
        ("unnormalized", found.unnormalized),
        ("unweighted", found.unweighted),
    ):
        if ids:
            picked[key] = ids[:SAMPLES]
    return picked


def pruned_row(row: Row, minimum: float) -> Row:
    """The row without weights below `minimum`; its largest weight always stays."""
    if not row:
        return row
    top = max(row, key=lambda name: row[name])
    return {name: weight for name, weight in row.items() if weight >= minimum or name == top}


def capped_row(row: Row, most: int) -> Row:
    if len(row) <= most:
        return row
    kept = sorted(row, key=lambda name: (-row[name], name))[:most]
    return {name: row[name] for name in kept}


def normalized_row(row: Row) -> Row:
    total = sum(row.values())
    if total <= NO_WEIGHT:
        return row
    return {name: weight / total for name, weight in row.items()}


def clean_row(row: Row) -> Row:
    return {name: weight for name, weight in row.items() if weight > NO_WEIGHT}


def differs(one: Row, other: Row) -> bool:
    for name in set(one) | set(other):
        if abs(one.get(name, 0.0) - other.get(name, 0.0)) > CHANGE_TOLERANCE:
            return True
    return False


def finish(
    before: list[Row],
    after: list[Row],
    touched: Sequence[int],
    prune: float | None,
    most: int | None,
) -> Finished:
    """Prunes, caps and normalizes the touched rows, and counts what each step changed."""
    rows = list(after)
    pruned = 0
    capped = 0
    normalized = 0
    for vertex in touched:
        row = clean_row(rows[vertex])
        if prune is not None:
            kept = pruned_row(row, prune)
            if len(kept) < len(row):
                pruned += 1
            row = kept
        if most is not None:
            kept = capped_row(row, most)
            if len(kept) < len(row):
                capped += 1
            row = kept
        total = sum(row.values())
        if total > NO_WEIGHT and abs(total - 1.0) > SUM_TOLERANCE:
            normalized += 1
        rows[vertex] = normalized_row(row)
    changed = sum(1 for vertex in range(len(rows)) if differs(before[vertex], rows[vertex]))
    return Finished(rows, changed, pruned, capped, normalized)


def blend(parts: Sequence[tuple[Row, float]]) -> Row:
    """Rows mixed by shares, like the corners of a triangle by barycentric coordinates."""
    mixed: Row = {}
    for row, share in parts:
        if share <= 0.0:
            continue
        for name, weight in row.items():
            mixed[name] = mixed.get(name, 0.0) + weight * share
    return clean_row(mixed)


def swap_case(token: str, replacement: str) -> str:
    if token.isupper():
        return replacement.upper()
    if token[0].isupper():
        return replacement.capitalize()
    return replacement


def other_side(name: str) -> str:
    """The name of the joint on the other side: hand_l, L_hand, Bip01 L Hand, LeftHand."""
    pieces = SEPARATORS.split(name)
    swapped = False
    for index in range(0, len(pieces), 2):
        piece = pieces[index]
        twin = SIDES.get(piece.lower())
        if twin:
            pieces[index] = swap_case(piece, twin)
            swapped = True
    if swapped:
        return "".join(pieces)
    found = CAMEL_SIDE.search(name)
    if found is None:
        return name
    word = found.group(1)
    twin = SIDES[word.lower()]
    return name[: found.start()] + swap_case(word, twin) + name[found.end() :]


def mirrored_row(row: Row) -> Row:
    mixed: Row = {}
    for name, weight in row.items():
        twin = other_side(name)
        mixed[twin] = mixed.get(twin, 0.0) + weight
    return mixed


def renamer(spec: str) -> Callable[[str], str]:
    """`{}_r` puts every name into the pattern; `arm=forearm_r` maps one name."""
    pairs = {}
    pattern = ""
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            old, _, new = entry.partition("=")
            if not old.strip() or not new.strip():
                raise BridgeError(
                    f"Bad --rename entry '{entry}'.",
                    hint='Map one name as OLD=NEW, or give a pattern like "{}_r".',
                )
            pairs[old.strip()] = new.strip()
        elif "{}" in entry:
            if pattern:
                raise BridgeError(
                    "--rename takes one pattern.", hint='Like --rename "{}_r,arm=forearm_r".'
                )
            pattern = entry
        else:
            raise BridgeError(
                f"Bad --rename entry '{entry}'.",
                hint='Use {} for the name in the file, like "{}_r" or "rig:{}", or OLD=NEW pairs.',
            )

    def rename(name: str) -> str:
        if name in pairs:
            return pairs[name]
        if pattern:
            return pattern.replace("{}", name)
        return name

    return rename


def renamed_row(row: Row, rename: Callable[[str], str]) -> Row:
    out: Row = {}
    for name, weight in row.items():
        new = rename(name)
        out[new] = out.get(new, 0.0) + weight
    return out


def checked_row(raw: object, where: str) -> Row:
    if not isinstance(raw, dict):
        raise BridgeError(f"{where} is not a {{joint: weight}} object.", hint=FILE_HINT)
    row: Row = {}
    for name, weight in raw.items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise BridgeError(f"{where}: the weight of '{name}' is not a number.", hint=FILE_HINT)
        if weight < 0 or weight != weight or weight > 1e6:
            raise BridgeError(
                f"{where}: the weight of '{name}' is {weight}.",
                hint="Weights go from 0 to 1 and add up to 1 per vertex.",
            )
        if weight > NO_WEIGHT:
            row[str(name)] = float(weight)
    return row


def read_rows(data: object, verts: int) -> dict[int, Row]:
    """Rows by vertex from a weights file: every vertex, or only the ones it names."""
    rows = data.get("weights") if isinstance(data, dict) else data
    if isinstance(data, dict) and rows is None:
        raise BridgeError("The weights file has no 'weights'.", hint=FILE_HINT)
    if isinstance(rows, list):
        if len(rows) != verts:
            raise BridgeError(
                f"The file has weights for {len(rows)} verts; the mesh has {verts}.",
                hint="Weights by vertex number need the same mesh. For a remeshed or edited "
                "mesh copy them from a skinned one: bmaya skin NEW --copy-from OLD. To set "
                'only some vertices write {"weights": {"12": {...}}}.',
            )
        return {vertex: checked_row(raw, f"vertex {vertex}") for vertex, raw in enumerate(rows)}
    if isinstance(rows, dict):
        picked = {}
        for key, raw in rows.items():
            try:
                vertex = int(key)
            except ValueError:
                raise BridgeError(f"'{key}' is not a vertex number.", hint=FILE_HINT) from None
            if not 0 <= vertex < verts:
                raise BridgeError(
                    f"Vertex {vertex} is not in the mesh, which has {verts} verts.",
                    hint=f"Vertex numbers go from 0 to {verts - 1}.",
                )
            picked[vertex] = checked_row(raw, f"vertex {vertex}")
        return picked
    raise BridgeError("The weights file holds no weights.", hint=FILE_HINT)


def vertex_ids(spec: str, count: int) -> list[int]:
    picked: set[int] = set()
    for item in spec.replace(" ", "").split(","):
        if not item:
            continue
        parts = item.split(":")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            raise BridgeError(f"Bad vertex spec '{spec}'.", hint=VERTS_HINT) from None
        if len(numbers) > 3 or (len(numbers) == 3 and numbers[2] <= 0):
            raise BridgeError(f"Bad vertex spec '{spec}'.", hint=VERTS_HINT)
        start = numbers[0]
        end = numbers[1] if len(numbers) > 1 else start
        step = numbers[2] if len(numbers) == 3 else 1
        if start < 0 or end < start:
            raise BridgeError(f"Bad vertex range '{item}'.", hint=VERTS_HINT)
        if start >= count:
            raise BridgeError(
                f"Vertex {start} is not in the mesh, which has {count} verts.",
                hint=f"Vertex numbers go from 0 to {count - 1}.",
            )
        picked.update(range(start, min(end, count - 1) + 1, step))
    if not picked:
        raise BridgeError(f"Bad vertex spec '{spec}'.", hint=VERTS_HINT)
    return sorted(picked)


def rounded_row(row: Row, digits: int = ROUND_DIGITS) -> Row:
    rounded = {}
    for name in sorted(row, key=lambda name: (-row[name], name)):
        weight = round(row[name], digits)
        if weight > 0:
            rounded[name] = weight
    return rounded


def heat(weight: float) -> Color:
    """Blue for none, through green and yellow, to red for all of it."""
    weight = min(1.0, max(0.0, weight))
    for index in range(1, len(HEAT)):
        top, high = HEAT[index]
        if weight <= top:
            bottom, low = HEAT[index - 1]
            share = (weight - bottom) / (top - bottom)
            return (
                int(round(low[0] + (high[0] - low[0]) * share)),
                int(round(low[1] + (high[1] - low[1]) * share)),
                int(round(low[2] + (high[2] - low[2]) * share)),
            )
    return HEAT[-1][1]


def mixed_color(row: Row, colors: dict[str, Color]) -> Color:
    """The colors of a vertex's influences mixed by their weights."""
    total = sum(row.values())
    if total <= NO_WEIGHT:
        return NO_SKIN
    red = green = blue = 0.0
    for name, weight in row.items():
        color = colors.get(name, NO_SKIN)
        share = weight / total
        red += color[0] * share
        green += color[1] * share
        blue += color[2] * share
    return int(round(red)), int(round(green)), int(round(blue))
