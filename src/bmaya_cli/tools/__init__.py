from mcp.server.mcpserver import MCPServer

from bmaya_cli.sessions import SessionManager
from bmaya_cli.tools import code, files, scene, session, viewport


def register_all(server: MCPServer, manager: SessionManager) -> None:
    for module in (session, scene, code, viewport, files):
        module.register(server, manager)
