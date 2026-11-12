import socket
import struct
import threading
from typing import Any

import pytest
from conftest import BridgeFactory
from fake_bridge import DROP, FakeError, free_port

from bmaya_mcp.client import BridgeClient, BridgeCommandError, BridgeConnectionError


def test_request_returns_result_and_sends_envelope(make_bridge: BridgeFactory) -> None:
    bridge = make_bridge({"get_scene_info": lambda params: {"echo": params}})
    client = BridgeClient(bridge.port, "secret", label="claude-42")
    result = client.request("get_scene_info", {"limit": 5})
    assert result == {"echo": {"limit": 5}}
    sent = bridge.requests[0]
    assert sent["command"] == "get_scene_info"
    assert sent["token"] == "secret"
    assert sent["client"] == "claude-42"
    assert sent["params"] == {"limit": 5}


def test_request_ids_increase_and_unicode_survives(make_bridge: BridgeFactory) -> None:
    bridge = make_bridge({"execute_maya_code": lambda params: {"result": params["code"]}})
    client = BridgeClient(bridge.port, "secret")
    assert client.request("execute_maya_code", {"code": "куб"}) == {"result": "куб"}
    client.request("execute_maya_code", {"code": "x"})
    assert [r["id"] for r in bridge.requests] == [1, 2]


def test_large_payload_roundtrip(make_bridge: BridgeFactory) -> None:
    bridge = make_bridge({"execute_maya_code": lambda params: {"result": params["code"]}})
    code = "a" * 3_000_000
    client = BridgeClient(bridge.port, "secret")
    assert client.request("execute_maya_code", {"code": code}) == {"result": code}


def test_error_reply_becomes_command_error(make_bridge: BridgeFactory) -> None:
    def fail(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("NameError", "name x is not defined", "Define it first.")

    bridge = make_bridge({"execute_maya_code": fail})
    client = BridgeClient(bridge.port, "secret")
    with pytest.raises(BridgeCommandError) as info:
        client.request("execute_maya_code", {"code": "x"})
    assert info.value.type == "NameError"
    assert info.value.message == "name x is not defined"
    assert info.value.hint == "Define it first."
    assert client.request("ping")["maya_version"] == "2027"


def test_bad_token_is_unauthorized(make_bridge: BridgeFactory) -> None:
    bridge = make_bridge()
    client = BridgeClient(bridge.port, "wrong")
    with pytest.raises(BridgeCommandError) as info:
        client.request("ping")
    assert info.value.type == "Unauthorized"


def test_connection_refused_is_flagged() -> None:
    client = BridgeClient(free_port(), "secret", connect_timeout=3)
    with pytest.raises(BridgeConnectionError) as info:
        client.request("ping")
    assert info.value.refused


def test_reconnects_after_bridge_restarts(make_bridge: BridgeFactory) -> None:
    first = make_bridge({"get_scene_info": lambda params: {"n": 1}})
    port = first.port
    client = BridgeClient(port, "secret")
    assert client.request("get_scene_info") == {"n": 1}
    first.stop()
    with pytest.raises(BridgeConnectionError):
        client.request("get_scene_info")
    make_bridge({"get_scene_info": lambda params: {"n": 2}}, port=port)
    assert client.request("get_scene_info") == {"n": 2}
    assert client.reconnects == 1


def test_connection_dropped_mid_request(make_bridge: BridgeFactory) -> None:
    bridge = make_bridge({"undo": lambda params: DROP, "get_scene_info": lambda params: {}})
    client = BridgeClient(bridge.port, "secret")
    with pytest.raises(BridgeConnectionError):
        client.request("undo")
    assert client.request("get_scene_info") == {}


def test_read_timeout_closes_connection(make_bridge: BridgeFactory) -> None:
    bridge = make_bridge()
    bridge.silent = True
    client = BridgeClient(bridge.port, "secret", read_timeout=0.3)
    with pytest.raises(BridgeConnectionError) as info:
        client.request("ping")
    assert info.value.timed_out
    bridge.silent = False
    assert client.request("ping")["busy"] is False


def test_oversized_frame_is_rejected() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def answer() -> None:
        conn, _ = listener.accept()
        conn.recv(4096)
        conn.sendall(struct.pack(">I", 0xFFFFFFFF))
        conn.close()

    threading.Thread(target=answer, daemon=True).start()
    client = BridgeClient(listener.getsockname()[1], "secret")
    with pytest.raises(BridgeConnectionError, match="oversized"):
        client.request("ping")
    listener.close()
