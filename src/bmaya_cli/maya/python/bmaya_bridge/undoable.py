"""Edits Maya's undo queue cannot see, like MFnSkinCluster.setWeights, as undoable steps.

The bridge puts a step here and runs the `bmayaUndoable` command from plug-ins/bmaya_undo.py,
which takes it and keeps it in Maya's undo queue. No maya import here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

COMMAND = "bmayaUndoable"
PLUGIN = "bmaya_undo.py"


class Step(NamedTuple):
    do: Callable[[], None]
    undo: Callable[[], None]


PENDING: list[Step] = []


def plugin_path() -> Path:
    return Path(__file__).resolve().parents[2] / "plug-ins" / PLUGIN


def put(step: Step) -> None:
    PENDING.clear()
    PENDING.append(step)


def take() -> Step:
    if not PENDING:
        raise RuntimeError(f"{COMMAND} was run without a step; the bmaya bridge runs it.")
    return PENDING.pop()
