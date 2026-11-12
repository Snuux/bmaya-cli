"""Starts the bmaya-mcp bridge when loaded and stops it when unloaded.

A fallback for Maya builds that do not run the module's scripts/userSetup.py. Starting
twice is harmless because bmaya_bridge.start() is idempotent.
"""

from __future__ import annotations

import bmaya_bridge
import maya.api.OpenMaya as om
from bmaya_bridge.bootstrap import start_in_maya


def maya_useNewAPI() -> None:
    """Tells Maya that this plug-in uses the Python API 2.0."""


def initializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin, "bmaya-mcp", bmaya_bridge.__version__, "Any")
    start_in_maya()


def uninitializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin)
    bmaya_bridge.stop()
