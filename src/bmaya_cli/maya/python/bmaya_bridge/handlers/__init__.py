from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from maya import cmds

from bmaya_bridge.handlers import (
    animation,
    bones,
    execute,
    export,
    game_export,
    mesh,
    mirror,
    modeling,
    motion,
    plot,
    rig,
    scene,
    skin,
    viewport,
    views,
)

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
    "render_frames": without_undo(viewport.render_frames),
    "get_model_views": without_undo(views.get_model_views),
    "audit_mesh": without_undo(mesh.audit_mesh),
    "diff_mesh": without_undo(mesh.diff_mesh),
    "get_animation_info": without_undo(animation.get_animation_info),
    "plot_animation": without_undo(plot.plot_animation),
    "get_motion": without_undo(motion.get_motion),
    "get_rig_info": without_undo(rig.get_rig_info),
    "get_bones": without_undo(bones.get_bones),
    "get_skin_info": without_undo(skin.get_skin_info),
    "save_skin_weights": without_undo(skin.save_skin_weights),
    "export_fbx": without_undo(export.export_fbx),
    "plan_game_export": without_undo(game_export.plan_game_export),
    "export_game_file": without_undo(game_export.export_game_file),
}

COMMANDS: dict[str, Handler] = {
    **UNRECORDED,
    "execute_maya_code": execute.execute_maya_code,
    "remesh_mesh": modeling.remesh_mesh,
    "reduce_mesh": modeling.reduce_mesh,
    "cleanup_mesh": modeling.cleanup_mesh,
    "edit_skin": skin.edit_skin,
    "rebind_skin": skin.rebind_skin,
    "set_pose": bones.set_pose,
    "orient_joints": bones.orient_joints,
    "mirror_node": mirror.mirror_node,
    "scene_file": scene.scene_file,
    "undo": scene.undo,
    "stop": scene.stop,
}

# Undo cannot run inside an open undo chunk, and scene file operations are not undoable.
NO_UNDO_CHUNK = frozenset({"undo", "scene_file", "stop", *UNRECORDED})
