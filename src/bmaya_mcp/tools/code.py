from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from bmaya_mcp.sessions import SessionManager
from bmaya_mcp.tools.common import forward_text

DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description=(
            "Run Python in Maya with `cmds`, `mel` and `om` (OpenMaya 2) preloaded. Assign to "
            "`result` to return a value; print for logs. Output is clipped at 8000 chars. "
            "Each call is one undo chunk."
        ),
        annotations=DESTRUCTIVE,
        structured_output=False,
    )
    def execute_maya_code(code: str) -> str:
        return forward_text(manager, "execute_maya_code", {"code": code})

    @server.tool(
        description="Undo your last `steps` (max 50) changing tool calls. Stops at a change made "
        "by another client or in the Maya UI and reports it as `blocked_by`. `force` also "
        "undoes UI changes on top of your steps, never another client's.",
        annotations=DESTRUCTIVE,
        structured_output=False,
    )
    def undo(steps: int = 1, force: bool = False) -> str:
        return forward_text(manager, "undo", {"steps": steps, "force": force})
