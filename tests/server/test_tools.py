import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import BridgeFactory
from fake_bridge import FakeError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from bmaya_mcp.server import create_server
from bmaya_mcp.sessions import SessionManager

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def server(manager: SessionManager) -> MCPServer:
    return create_server(manager)


def call(server: MCPServer, name: str, arguments: dict[str, Any]) -> CallToolResult:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, CallToolResult)
    return result


def text_of(result: CallToolResult) -> str:
    return result.content[0].text


def test_command_tool_forwards_params_and_returns_compact_json(
    server: MCPServer, home: Path, make_bridge: BridgeFactory
) -> None:
    bridge = make_bridge({"get_scene_info": lambda params: {"nodes": ["a", "b"], "total": 2}})
    bridge.write_registry(home, 100)
    result = call(server, "get_scene_info", {"query": "cube"})
    assert text_of(result) == '{"nodes":["a","b"],"total":2}'
    assert result.structured_content is None
    assert bridge.requests[-1]["params"] == {"query": "cube", "node_type": "", "limit": 50}
    assert bridge.requests[-1]["client"] == "test-1"


def test_bridge_error_text_is_message_and_hint(
    server: MCPServer, home: Path, make_bridge: BridgeFactory
) -> None:
    def fail(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("BridgeError", "Node not found.", "Call get_scene_info first.")

    bridge = make_bridge({"get_object_info": fail})
    bridge.write_registry(home, 100)
    with pytest.raises(ToolError, match="Node not found. Call get_scene_info first."):
        call(server, "get_object_info", {"name": "nope"})


def test_python_exception_gets_type_prefix_only_for_execute(
    server: MCPServer, home: Path, make_bridge: BridgeFactory
) -> None:
    def fail(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("NameError", "name x is not defined", "")

    bridge = make_bridge({"execute_maya_code": fail, "get_object_info": fail})
    bridge.write_registry(home, 100)
    with pytest.raises(ToolError, match="NameError: name x is not defined"):
        call(server, "execute_maya_code", {"code": "x"})
    with pytest.raises(ToolError) as info:
        call(server, "get_object_info", {"name": "x"})
    assert "NameError" not in str(info.value)


def test_no_session_error_reaches_the_tool(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="No Maya"):
        call(server, "get_scene_info", {})


def test_list_and_use_session(server: MCPServer, home: Path, make_bridge: BridgeFactory) -> None:
    make_bridge().write_registry(home, 100)
    make_bridge(scene="C:/other.ma").write_registry(home, 200)
    listing = json.loads(text_of(call(server, "list_sessions", {})))
    assert [s["id"] for s in listing["sessions"]] == ["100", "200"]
    assert listing["current"] is None
    chosen = json.loads(text_of(call(server, "use_session", {"session": "200"})))
    assert chosen["id"] == "200"
    assert chosen["current"] is True
    with pytest.raises(ToolError, match="No live session"):
        call(server, "use_session", {"session": "7"})


def test_screenshot_returns_image_and_deletes_file(
    server: MCPServer, home: Path, make_bridge: BridgeFactory, tmp_path: Path
) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    bridge = make_bridge(
        {"get_viewport_screenshot": lambda params: {"path": str(shot), "width": 1, "height": 1}}
    )
    bridge.write_registry(home, 100)
    result = call(server, "get_viewport_screenshot", {"frames": "1,12"})
    assert [c.type for c in result.content] == ["image", "text"]
    assert result.content[0].mime_type == "image/png"
    assert base64.b64decode(result.content[0].data) == PNG
    assert result.content[1].text == "1x1, frames 1,12"
    assert not shot.exists()


def test_screenshot_with_missing_file_is_a_tool_error(
    server: MCPServer, home: Path, make_bridge: BridgeFactory, tmp_path: Path
) -> None:
    missing = tmp_path / "gone.png"
    bridge = make_bridge({"get_viewport_screenshot": lambda params: {"path": str(missing)}})
    bridge.write_registry(home, 100)
    with pytest.raises(ToolError, match="could not be read"):
        call(server, "get_viewport_screenshot", {})


def test_every_command_tool_forwards_to_its_own_command(
    server: MCPServer, home: Path, make_bridge: BridgeFactory
) -> None:
    names = ["get_object_info", "execute_maya_code", "audit_mesh", "get_animation_info"]
    names += ["export_fbx", "scene_file", "undo"]
    args = {
        "get_object_info": {"name": "a"},
        "execute_maya_code": {"code": "1"},
        "audit_mesh": {},
        "get_animation_info": {},
        "export_fbx": {"path": "a.fbx"},
        "scene_file": {"action": "save"},
        "undo": {},
    }
    bridge = make_bridge({name: lambda params: {"ok": True} for name in names})
    bridge.write_registry(home, 100)
    for name in names:
        assert text_of(call(server, name, args[name])) == '{"ok":true}'
    seen = [r["command"] for r in bridge.requests if r["command"] != "ping"]
    assert seen == names
