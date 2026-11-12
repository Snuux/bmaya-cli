import logging
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from bmaya_mcp.sessions import SessionManager
from bmaya_mcp.tools.common import forward

logger = logging.getLogger(__name__)


def register(server: MCPServer, manager: SessionManager) -> None:
    @server.tool(
        description=(
            "Capture the viewport as a PNG, longest side `max_size` px (200-2000). `camera` "
            "picks a camera (empty: active view). `frames`, up to 12 comma-separated frames like "
            '"1,12,24", gives one contact sheet to review animation. Needs Maya with a UI.'
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=False,
    )
    def get_viewport_screenshot(
        max_size: int = 800, camera: str = "", frames: str = ""
    ) -> list[Image | str]:
        params = {"max_size": max_size, "camera": camera, "frames": frames}
        result = forward(manager, "get_viewport_screenshot", params)
        image_path = Path(str(result.get("path", "")))
        try:
            data = image_path.read_bytes()
        except OSError as exc:
            raise ToolError(
                f"Screenshot file {image_path} could not be read. Maya must run on this machine."
            ) from exc
        try:
            image_path.unlink()
        except OSError:
            logger.warning("could not delete screenshot %s", image_path)
        summary = f"{result.get('width')}x{result.get('height')}"
        shown = result.get("frames") or []
        if len(shown) > 1:
            summary += ", frames " + ",".join(str(at) for at in shown)
        if result.get("dropped_frames"):
            summary += f", {result['dropped_frames']} frames dropped (max 12)"
        return [Image(data=data, format="png"), summary]
