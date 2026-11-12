from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from bmaya_mcp.sessions import SessionManager
from bmaya_mcp.tools.common import forward_text

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description=(
            "Scene overview: file, units, up axis, fps, frame range, selection and a node list. "
            "Filter by `query` (name substring) and `node_type` (e.g. mesh, joint); "
            "`limit` caps the list and `total` reports the full count."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    def get_scene_info(query: str = "", node_type: str = "", limit: int = 50) -> str:
        params = {"query": query, "node_type": node_type, "limit": limit}
        return forward_text(manager, "get_scene_info", params)

    @server.tool(
        description=(
            "Details of one node: transform, bounding box, mesh counts, UV sets, materials, "
            "history and keys. `name` is a short name or a full DAG path."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    def get_object_info(name: str) -> str:
        return forward_text(manager, "get_object_info", {"name": name})

    @server.tool(
        description=(
            "Audit meshes for game readiness and list per-mesh stats and issues. "
            "`names` is comma-separated nodes; empty uses the default scope."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    def audit_mesh(names: str = "") -> str:
        return forward_text(manager, "audit_mesh", {"names": names})

    @server.tool(
        description=(
            "Animation data: curves, key ranges, fps, skeleton and skin clusters. "
            "`name` limits it to one node; empty covers the scene."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    def get_animation_info(name: str = "") -> str:
        return forward_text(manager, "get_animation_info", {"name": name})
