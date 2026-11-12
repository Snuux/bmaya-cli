from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bmaya_bridge.handlers import animation, execute, export, mesh, scene, viewport

COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "get_scene_info": scene.get_scene_info,
    "get_object_info": scene.get_object_info,
    "execute_maya_code": execute.execute_maya_code,
    "get_viewport_screenshot": viewport.get_viewport_screenshot,
    "audit_mesh": mesh.audit_mesh,
    "get_animation_info": animation.get_animation_info,
    "export_fbx": export.export_fbx,
    "scene_file": scene.scene_file,
    "undo": scene.undo,
}

# Undo cannot run inside an open undo chunk, and scene file operations are not undoable.
NO_UNDO_CHUNK = frozenset({"undo", "scene_file"})
