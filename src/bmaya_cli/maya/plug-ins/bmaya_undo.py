from __future__ import annotations

import bmaya_bridge
import maya.api.OpenMaya as om
from bmaya_bridge import undoable


def maya_useNewAPI() -> None:
    # Maya looks this up by name to load the plug-in with Python API 2.0.
    pass


class Undoable(om.MPxCommand):
    def __init__(self) -> None:
        om.MPxCommand.__init__(self)
        self.step: undoable.Step | None = None

    def doIt(self, args: om.MArgList) -> None:
        self.step = undoable.take()
        self.step.do()

    def redoIt(self) -> None:
        if self.step is not None:
            self.step.do()

    def undoIt(self) -> None:
        if self.step is not None:
            self.step.undo()

    def isUndoable(self) -> bool:
        return True


def initializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin, "bmaya", bmaya_bridge.__version__, "Any").registerCommand(
        undoable.COMMAND, Undoable
    )


def uninitializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin).deregisterCommand(undoable.COMMAND)
