from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from bmaya_cli.sessions import SessionManager
from bmaya_cli.tools.common import forward_text

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description=(
            "Scene overview: file, units, up axis, fps, frame range, selection and nodes. "
            "Unfiltered it lists top-level nodes. `query` matches names (substring or * ? "
            "wildcards), `node_type` lists nodes of a type (mesh, joint...). `limit` (max 500) "
            "caps the list; `total` is the full count."
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
            "Game-readiness audit: tris, UVs, materials, n-gons, non-manifold geometry, history, "
            "unfrozen transforms, default names. Scope: comma-separated `names` (groups include "
            "their meshes), else the selection, else every mesh. Max 50 meshes."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    def audit_mesh(names: str = "") -> str:
        return forward_text(manager, "audit_mesh", {"names": names})

    @server.tool(
        description=(
            "Animation data. Empty `name`: scene overview of fps, ranges, animated nodes, "
            "skeletons, skin clusters, blend shapes and layers. With `name`: that node's curves, "
            "keys, tangents and infinity, plus the hierarchy for a joint."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    def get_animation_info(name: str = "") -> str:
        return forward_text(manager, "get_animation_info", {"name": name})
