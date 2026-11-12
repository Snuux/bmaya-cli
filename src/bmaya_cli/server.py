from mcp.server.mcpserver import MCPServer

from bmaya_cli.launch import OFF, Launcher, autostart_mode
from bmaya_cli.logs import setup_logging
from bmaya_cli.sessions import SessionManager, bmaya_home
from bmaya_cli.tools import register_all

INSTRUCTIONS = (
    "Controls Autodesk Maya through a bridge running inside Maya. Each running Maya is a "
    "session; with one Maya open it is used automatically, otherwise call list_sessions and "
    "use_session. With no Maya running, a headless one is started.\n"
    "Typical loop: get_scene_info, execute_maya_code, get_viewport_screenshot.\n"
    "Maya is not Blender: use maya.cmds, never bpy. Y-up by default, centimeters, rotations "
    "in degrees. Nodes have DAG paths with `|`; a mesh is a transform plus a shape child.\n"
    "Prefer audit_mesh and export_fbx over hand-written code for those jobs."
)


def create_server(manager: SessionManager | None = None) -> MCPServer:
    server = MCPServer("bmaya", instructions=INSTRUCTIONS)
    register_all(server, manager or SessionManager())
    return server


def autostart_manager() -> SessionManager:
    home = bmaya_home()
    mode = autostart_mode()
    launcher = None if mode == OFF else Launcher(home, mode)
    return SessionManager(home, launcher=launcher)


def main() -> None:
    setup_logging()
    create_server(autostart_manager()).run()
