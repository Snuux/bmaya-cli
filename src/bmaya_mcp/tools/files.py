from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from bmaya_mcp.sessions import SessionManager
from bmaya_mcp.tools.common import forward_text


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description=(
            "Export FBX to an absolute `path` ending in .fbx. `preset` is unity, unreal or godot. "
            "Exports all top-level nodes, or the selection with `selection_only`. `animation` "
            "bakes keys over `start`-`end` (0 means the playback range)."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        ),
        structured_output=False,
    )
    def export_fbx(
        path: str,
        preset: str = "unity",
        selection_only: bool = False,
        animation: bool = False,
        start: int = 0,
        end: int = 0,
    ) -> str:
        params = {
            "path": path,
            "preset": preset,
            "selection_only": selection_only,
            "animation": animation,
            "start": start,
            "end": end,
        }
        return forward_text(manager, "export_fbx", params)

    @server.tool(
        description=(
            "Manage the scene file. `action`: new, open (.ma/.mb), save (a `path` makes it "
            "save-as) or import (.ma/.mb/.fbx/.obj). Not undoable; new and open refuse unsaved "
            "changes unless `force`."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, open_world_hint=False
        ),
        structured_output=False,
    )
    def scene_file(action: str, path: str = "", force: bool = False) -> str:
        params = {"action": action, "path": path, "force": force}
        return forward_text(manager, "scene_file", params)
