"""Two snapshots of a scene compared: what was added, deleted, renamed or changed.

Pure Python: no maya import. A snapshot maps each node's UUID to its name, type and a
signature: a few named values that change when the node changes in a way that matters,
like a mesh's point checksum or a transform's channels. Values are rounded when taken, so
noise in the last digits does not count as a change.
"""

from __future__ import annotations

import itertools
import operator
from collections.abc import Sequence
from typing import Any, NamedTuple

Signature = dict[str, Any]


class Node(NamedTuple):
    """`name` is the node's own name, so a rename is a rename; `label` is what the answer
    shows (a DAG node's shortest unique path)."""

    name: str
    type: str
    sig: Signature
    label: str = ""

    def shown(self) -> str:
        return self.label or self.name


class Changes(NamedTuple):
    added: list[dict[str, str]]
    deleted: list[dict[str, str]]
    renamed: list[dict[str, str]]
    changed: list[dict[str, Any]]
    settings: list[str]


def compare(
    before: dict[str, Node],
    after: dict[str, Node],
    before_settings: Signature,
    after_settings: Signature,
) -> Changes:
    added = [
        {"name": after[key].shown(), "type": after[key].type} for key in after if key not in before
    ]
    deleted = [
        {"name": before[key].shown(), "type": before[key].type}
        for key in before
        if key not in after
    ]
    renamed = []
    changed = []
    for key, now in after.items():
        old = before.get(key)
        if old is None:
            continue
        if old.name != now.name:
            renamed.append({"from": old.shown(), "to": now.shown()})
        what = [
            key
            for key in sorted(set(old.sig) | set(now.sig))
            if old.sig.get(key) != now.sig.get(key)
        ]
        if what:
            changed.append({"name": now.shown(), "type": now.type, "what": what})
    settings = [
        key
        for key in sorted(set(before_settings) | set(after_settings))
        if before_settings.get(key) != after_settings.get(key)
    ]
    added.sort(key=lambda entry: entry["name"])
    deleted.sort(key=lambda entry: entry["name"])
    renamed.sort(key=lambda entry: entry["to"])
    changed.sort(key=lambda entry: entry["name"])
    return Changes(added, deleted, renamed, changed, settings)


def rounded(value: float, digits: int = 4) -> float:
    return round(float(value), digits) + 0.0


def checksum(values: Sequence[float], digits: int = 3) -> float:
    """A cheap fingerprint of many numbers: their sum and a sum weighted by position,
    rounded. It changes when any value moves, unless moves cancel out exactly."""
    plain = sum(values)
    weighted = sum(map(operator.mul, values, itertools.cycle(range(1, 98))))
    return rounded(plain, digits) + rounded(weighted, digits) * 1e-6
