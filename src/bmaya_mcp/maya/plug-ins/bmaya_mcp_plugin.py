from __future__ import annotations

import bmaya_bridge
import maya.api.OpenMaya as om
from bmaya_bridge.bootstrap import start_in_maya


def maya_useNewAPI() -> None:
    # Maya looks this up by name to load the plug-in with Python API 2.0.
    pass


def initializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin, "bmaya-mcp", bmaya_bridge.__version__, "Any")
    start_in_maya()


def uninitializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin)
    bmaya_bridge.stop()
