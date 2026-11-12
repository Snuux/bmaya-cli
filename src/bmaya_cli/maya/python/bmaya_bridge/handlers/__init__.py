from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from maya import cmds

from bmaya_bridge.handlers import animation, execute, export, mesh, scene, viewport

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def without_undo(handler: Handler) -> Handler:
    @functools.wraps(handler)
    def run(params: dict[str, Any]) -> dict[str, Any]:
        recording = cmds.undoInfo(query=True, state=True)
        if recording:
            cmds.undoInfo(stateWithoutFlush=False)
        try:
            return handler(params)
        finally:
            if recording:
                cmds.undoInfo(stateWithoutFlush=True)

    return run


# These leave the scene as it was but still touch undoable state on the way (camera swap,
# time change, FBX bake). Recording that would make `undo` revert them instead of the
# agent's last edit.
UNRECORDED: dict[str, Handler] = {
    "get_scene_info": without_undo(scene.get_scene_info),
    "get_object_info": without_undo(scene.get_object_info),
    "get_viewport_screenshot": without_undo(viewport.get_viewport_screenshot),
    "audit_mesh": without_undo(mesh.audit_mesh),
    "get_animation_info": without_undo(animation.get_animation_info),
    "export_fbx": without_undo(export.export_fbx),
}

COMMANDS: dict[str, Handler] = {
    **UNRECORDED,
    "execute_maya_code": execute.execute_maya_code,
    "scene_file": scene.scene_file,
    "undo": scene.undo,
    "stop": scene.stop,
}

# Undo cannot run inside an open undo chunk, and scene file operations are not undoable.
NO_UNDO_CHUNK = frozenset({"undo", "scene_file", "stop", *UNRECORDED})
