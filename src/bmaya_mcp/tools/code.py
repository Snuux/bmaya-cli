from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from bmaya_mcp.sessions import SessionManager
from bmaya_mcp.tools.common import forward_text

DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description=(
            "Run Python in Maya with `cmds`, `mel` and `om` preloaded. Assign to `result` to "
            "return a value; print for logs. Each call is one undo chunk."
        ),
        annotations=DESTRUCTIVE,
        structured_output=False,
    )
    def execute_maya_code(code: str) -> str:
        return forward_text(manager, "execute_maya_code", {"code": code})

    @server.tool(
        description="Undo the last `steps` undo chunks (one per execute_maya_code call).",
        annotations=DESTRUCTIVE,
        structured_output=False,
    )
    def undo(steps: int = 1) -> str:
        return forward_text(manager, "undo", {"steps": steps})
