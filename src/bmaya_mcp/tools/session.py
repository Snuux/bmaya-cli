from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from bmaya_mcp.sessions import SessionError, SessionManager
from bmaya_mcp.tools.common import dump


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description="List running Maya sessions (id, scene, Maya version, busy, current).",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=False,
    )
    def list_sessions() -> str:
        return dump(manager.list_sessions())

    @server.tool(
        description="Choose which Maya session later tools talk to. Needed only when several run.",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=False,
    )
    def use_session(session: str) -> str:
        try:
            chosen = manager.use_session(session)
        except SessionError as exc:
            raise ToolError(str(exc)) from exc
        return dump(chosen.to_dict(current=True))
