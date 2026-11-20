"""Problems with the shapes under one transform that an exported file would carry."""

from __future__ import annotations

from typing import NamedTuple

NAMES_SHOWN = 5


class ShapeFacts(NamedTuple):
    name: str
    intermediate: bool
    faces: int
    feeds: bool
    instances: int
    opposite: bool
    inward: bool


class Problem(NamedTuple):
    text: str
    blocking: bool


def names(facts: list[ShapeFacts]) -> str:
    shown = ", ".join(fact.name for fact in facts[:NAMES_SHOWN])
    if len(facts) > NAMES_SHOWN:
        shown += f" and {len(facts) - NAMES_SHOWN} more"
    return shown


def classify(facts: list[ShapeFacts]) -> list[Problem]:
    visible = [fact for fact in facts if not fact.intermediate]
    unused = [fact for fact in facts if fact.intermediate and not fact.feeds]
    out = []
    for fact in visible:
        if fact.faces == 0:
            out.append(Problem(f"{fact.name} has no faces", True))
        if fact.opposite and fact.inward:
            out.append(
                Problem(
                    f"{fact.name} has the opposite flag over inside-out faces: Maya draws it "
                    "the right way, the exported file gets it inside out",
                    True,
                )
            )
        elif fact.opposite:
            out.append(
                Problem(
                    f"{fact.name} has the opposite flag: Maya draws its faces flipped from how "
                    "they are stored, and the exported file gets them as stored",
                    False,
                )
            )
        if fact.instances > 1:
            text = f"{fact.name} is instanced under {fact.instances} transforms"
            out.append(Problem(text, False))
    if len(visible) > 1:
        text = f"{len(visible)} visible shapes on one transform: {names(visible)}"
        out.append(Problem(text, False))
    if unused:
        out.append(
            Problem(
                f"{len(unused)} unused intermediate shapes, left from earlier versions: "
                + names(unused),
                False,
            )
        )
    return out
