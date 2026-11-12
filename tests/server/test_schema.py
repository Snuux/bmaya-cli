import asyncio
import json

from mcp.server.mcpserver import MCPServer

from bmaya_cli.server import INSTRUCTIONS, create_server
from bmaya_cli.sessions import SessionManager

EXPECTED_TOOLS = {
    "list_sessions",
    "use_session",
    "get_scene_info",
    "get_object_info",
    "execute_maya_code",
    "get_viewport_screenshot",
    "audit_mesh",
    "get_animation_info",
    "export_fbx",
    "scene_file",
    "undo",
}
FORBIDDEN_KEYS = {"anyOf", "oneOf", "allOf", "$ref"}
DEFINITION_BUDGET_CHARS = 5600
INSTRUCTIONS_BUDGET_WORDS = 120


def list_tools(server: MCPServer) -> list[dict]:
    tools = asyncio.run(server.list_tools())
    return [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]


def keys_in(value: object) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= keys_in(item)
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found |= keys_in(item)
        return found
    return set()


def test_exactly_the_expected_tools(manager: SessionManager) -> None:
    tools = list_tools(create_server(manager))
    assert len(tools) == 11
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
    assert all(len(tool["name"]) < 64 for tool in tools)


def test_input_schemas_are_flat(manager: SessionManager) -> None:
    for tool in list_tools(create_server(manager)):
        assert not keys_in(tool["inputSchema"]) & FORBIDDEN_KEYS, tool["name"]
        assert "outputSchema" not in tool, tool["name"]


def test_every_tool_has_annotations(manager: SessionManager) -> None:
    for tool in list_tools(create_server(manager)):
        annotations = tool["annotations"]
        assert annotations["openWorldHint"] is False
        if tool["name"] in {"execute_maya_code", "scene_file", "undo"}:
            assert annotations["destructiveHint"] is True
        if tool["name"] in {"list_sessions", "audit_mesh"} or tool["name"].startswith("get_"):
            assert annotations["readOnlyHint"] is True


def test_definitions_stay_within_budget(manager: SessionManager) -> None:
    tools = list_tools(create_server(manager))
    size = len(json.dumps(tools, separators=(",", ":"), ensure_ascii=False))
    assert size < DEFINITION_BUDGET_CHARS, f"{size} chars, about {size // 4} tokens"


def test_instructions_are_short() -> None:
    assert len(INSTRUCTIONS.split()) <= INSTRUCTIONS_BUDGET_WORDS
