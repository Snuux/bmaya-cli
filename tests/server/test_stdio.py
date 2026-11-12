import asyncio
import base64
import os
import sys
from pathlib import Path
from typing import Any

from conftest import BridgeFactory
from fake_bridge import FakeError
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from test_tools import PNG

SRC = Path(__file__).resolve().parents[2] / "src"
LAUNCH = "from bmaya_mcp.server import main; main()"


def launch_params(home: Path) -> StdioServerParameters:
    env = {**os.environ, "BMAYA_MCP_HOME": str(home), "PYTHONPATH": str(SRC)}
    return StdioServerParameters(command=sys.executable, args=["-c", LAUNCH], env=env)


async def session_roundtrip(home: Path) -> dict[str, Any]:
    async with Client(launch_params(home)) as client:
        tools = await client.list_tools()
        scene = await client.call_tool("get_scene_info", {"query": "cube"})
        shot = await client.call_tool("get_viewport_screenshot", {})
        failed = await client.call_tool("execute_maya_code", {"code": "x"})
        return {
            "tools": tools.tools,
            "scene": scene,
            "shot": shot,
            "failed": failed,
            "instructions": client.instructions,
        }


def test_real_stdio_server_talks_to_fake_bridge(
    home: Path, make_bridge: BridgeFactory, tmp_path: Path
) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)

    def fail(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("NameError", "name x is not defined", "")

    bridge = make_bridge(
        {
            "get_scene_info": lambda params: {"query": params["query"], "total": 1},
            "get_viewport_screenshot": lambda params: {
                "path": str(shot),
                "width": 1,
                "height": 1,
            },
            "execute_maya_code": fail,
        }
    )
    bridge.write_registry(home, 100)

    outcome = asyncio.run(session_roundtrip(home))

    assert len(outcome["tools"]) == 11
    assert "list_sessions" in outcome["instructions"]
    assert outcome["scene"].content[0].text == '{"query":"cube","total":1}'
    assert not outcome["scene"].is_error
    assert [c.type for c in outcome["shot"].content] == ["image", "text"]
    assert base64.b64decode(outcome["shot"].content[0].data) == PNG
    assert outcome["failed"].is_error
    assert "NameError: name x is not defined" in outcome["failed"].content[0].text
    assert (home / "logs").is_dir()
