from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from bmaya_bridge import registry, server
from bmaya_bridge.errors import BridgeError, attach_stdout
from bmaya_bridge.journal import Journal
from bmaya_bridge.server import BridgeServer

from .conftest import Client, FakeMainThread, UndoRecorder, read_journal

AGENT_CODE = "x = 1\ny = 2\nraise ValueError('bad value')\n"


def run_agent_code(params: dict[str, Any]) -> dict[str, Any]:
    exec(compile(params["code"], "<agent>", "exec"), {})
    return {"result": None}


def echo(params: dict[str, Any]) -> dict[str, Any]:
    return {"echo": params, "thread": threading.current_thread().name}


def refuse(params: dict[str, Any]) -> dict[str, Any]:
    raise BridgeError("No such node 'pCube9'.", hint="Call get_scene_info to list nodes.")


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition was not met in time"
        time.sleep(0.01)


def events(journal: Journal, name: str) -> list[dict[str, Any]]:
    return [record for record in read_journal(journal.path) if record["event"] == name]


def test_ping_answers_without_main_thread(journal: Journal) -> None:
    def no_main_thread(callback: Callable[[], None]) -> None:
        raise AssertionError("ping must not be scheduled")

    bridge = BridgeServer(
        journal, commands=dict, schedule=no_main_thread, maya_version="2027", scene="C:/a.ma"
    )
    bridge.start()
    try:
        client = Client(bridge.port, bridge.token)
        reply = client.call("ping")
        client.close()
    finally:
        bridge.stop()
    assert reply["ok"] is True
    assert reply["result"] == {
        "pid": bridge.pid,
        "maya_version": "2027",
        "scene": "C:/a.ma",
        "busy": False,
    }


def test_bad_token_is_unauthorized_and_closes(make_bridge: Callable[..., BridgeServer]) -> None:
    bridge = make_bridge({"echo": echo})
    client = Client(bridge.port, bridge.token)
    reply = client.call("echo", token="wrong")
    assert reply["ok"] is False
    assert reply["error"]["type"] == "Unauthorized"
    assert client.sock.recv(1) == b""
    client.close()


def test_command_runs_on_main_thread_in_undo_chunk(
    make_bridge: Callable[..., BridgeServer],
) -> None:
    undo = UndoRecorder()
    bridge = make_bridge({"echo": echo}, undo_chunk=undo)
    client = Client(bridge.port, bridge.token)
    reply = client.call("echo", {"value": 3})
    client.close()
    assert reply == {
        "id": 1,
        "ok": True,
        "result": {"echo": {"value": 3}, "thread": "fake-main"},
    }
    assert undo.events == ["open bmaya-mcp echo", "close bmaya-mcp echo"]


def test_immediate_scheduler(make_bridge: Callable[..., BridgeServer]) -> None:
    bridge = make_bridge({"echo": echo}, schedule=lambda callback: callback())
    client = Client(bridge.port, bridge.token)
    reply = client.call("echo", {"value": 1})
    client.close()
    assert reply["ok"] is True
    assert reply["result"]["echo"] == {"value": 1}


def test_unknown_command_lists_valid_ones(make_bridge: Callable[..., BridgeServer]) -> None:
    bridge = make_bridge({"echo": echo, "refuse": refuse})
    client = Client(bridge.port, bridge.token)
    reply = client.call("nope")
    client.close()
    assert reply["error"]["type"] == "UnknownCommand"
    assert reply["error"]["hint"] == "Valid commands: echo, ping, refuse."


def test_bridge_error_mapping(make_bridge: Callable[..., BridgeServer], journal: Journal) -> None:
    bridge = make_bridge({"refuse": refuse})
    client = Client(bridge.port, bridge.token)
    reply = client.call("refuse")
    client.close()
    assert reply["error"] == {
        "type": "BridgeError",
        "message": "No such node 'pCube9'.",
        "hint": "Call get_scene_info to list nodes.",
    }
    end = events(journal, "end")[-1]
    assert end["ok"] is False
    assert end["error"]["type"] == "BridgeError"


def test_exception_mapping_points_at_user_code_line(
    make_bridge: Callable[..., BridgeServer],
    journal: Journal,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = make_bridge({"execute_maya_code": run_agent_code})
    client = Client(bridge.port, bridge.token)
    reply = client.call("execute_maya_code", {"code": AGENT_CODE})
    syntax = client.call("execute_maya_code", {"code": "x = (\n"})
    client.close()
    assert reply["error"]["type"] == "ValueError"
    assert reply["error"]["message"] == "bad value"
    assert "line 3" in reply["error"]["hint"]
    assert "Traceback" not in json.dumps(reply)
    assert syntax["error"]["type"] == "SyntaxError"
    assert "line 1" in syntax["error"]["hint"]
    end = events(journal, "end")[0]
    assert "ValueError: bad value" in end["traceback"]
    assert "ValueError: bad value" in capsys.readouterr().err


def test_unserializable_result_is_reported(make_bridge: Callable[..., BridgeServer]) -> None:
    bridge = make_bridge({"odd": lambda params: {"value": object()}})
    client = Client(bridge.port, bridge.token)
    reply = client.call("odd")
    still_alive = client.call("ping")
    client.close()
    assert reply["ok"] is False
    assert reply["error"]["type"] == "BridgeError"
    assert "TypeError" in reply["error"]["message"]
    assert still_alive["ok"] is True


def test_timeout_reply_while_job_keeps_running(
    make_bridge: Callable[..., BridgeServer], journal: Journal
) -> None:
    release = threading.Event()

    def slow(params: dict[str, Any]) -> dict[str, Any]:
        release.wait(10)
        return {"done": True}

    bridge = make_bridge({"slow": slow}, timeout=0.2)
    client = Client(bridge.port, bridge.token)
    reply = client.call("slow")
    assert reply["ok"] is False
    assert reply["error"]["type"] == "Timeout"
    assert "still running" in reply["error"]["message"]
    assert client.call("ping")["result"]["busy"] is True
    assert events(journal, "timeout")[0]["command"] == "slow"
    assert events(journal, "end") == []
    release.set()
    wait_for(lambda: len(events(journal, "end")) == 1)
    assert events(journal, "end")[0]["ok"] is True
    assert client.call("ping")["result"]["busy"] is False
    client.close()


def test_two_clients_are_served_concurrently(make_bridge: Callable[..., BridgeServer]) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking(params: dict[str, Any]) -> dict[str, Any]:
        started.set()
        release.wait(10)
        return {"who": "first"}

    bridge = make_bridge({"blocking": blocking, "echo": echo})
    first = Client(bridge.port, bridge.token, name="first")
    second = Client(bridge.port, bridge.token, name="second")
    replies: dict[str, dict[str, Any]] = {}

    def call_first() -> None:
        replies["first"] = first.call("blocking")

    def call_second() -> None:
        replies["second"] = second.call("echo", {"n": 2})

    worker = threading.Thread(target=call_first)
    worker.start()
    assert started.wait(5)
    assert second.call("ping")["result"]["busy"] is True
    queued = threading.Thread(target=call_second)
    queued.start()
    time.sleep(0.1)
    assert "second" not in replies
    release.set()
    worker.join(5)
    queued.join(5)
    first.close()
    second.close()
    assert replies["first"]["result"] == {"who": "first"}
    assert replies["second"]["result"]["echo"] == {"n": 2}


def test_registry_file_lifecycle(home: Path, journal: Journal) -> None:
    bridge = BridgeServer(journal, commands=dict, maya_version="2027", scene="C:/a.ma")
    port = bridge.start()
    assert bridge.start() == port
    path = registry.session_path()
    info = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent == home / "sessions"
    assert info["pid"] == bridge.pid
    assert info["port"] == port
    assert info["token"] == bridge.token
    assert info["maya_version"] == "2027"
    assert info["scene"] == "C:/a.ma"
    assert info["started"]
    bridge.set_scene("C:/b.ma")
    assert json.loads(path.read_text(encoding="utf-8"))["scene"] == "C:/b.ma"
    bridge.stop()
    bridge.stop()
    assert not path.exists()
    assert not bridge.running
    names = [record["event"] for record in read_journal(journal.path)]
    assert names == ["bridge_start", "bridge_stop"]


def test_client_connect_and_disconnect_are_journaled(
    make_bridge: Callable[..., BridgeServer], journal: Journal
) -> None:
    bridge = make_bridge({"echo": echo})
    client = Client(bridge.port, bridge.token, name="claude-1")
    client.call("echo")
    client.close()
    wait_for(lambda: len(events(journal, "client_disconnect")) == 1)
    assert len(events(journal, "client_connect")) == 1
    assert events(journal, "client_disconnect")[0]["client"] == "claude-1"
    start = events(journal, "start")[0]
    assert start["client"] == "claude-1"
    assert start["command"] == "echo"


def test_module_api_is_idempotent(home: Path) -> None:
    try:
        port = server.start()
        assert server.start() == port
        assert server.is_running()
        assert registry.session_path().exists()
        server.stop()
        assert not server.is_running()
        assert not registry.session_path().exists()
        server.stop()
    finally:
        server.stop()
        if server._journal is not None:
            server._journal.close()
            server._journal = None


def test_commands_listed_as_no_undo_chunk_run_outside_the_chunk(
    journal: Journal, main_thread: FakeMainThread
) -> None:
    undo = UndoRecorder()
    bridge = BridgeServer(
        journal,
        commands=lambda: {"echo": echo, "undo": echo},
        schedule=main_thread.schedule,
        undo_chunk=undo,
        no_undo_chunk=lambda: {"undo"},
    )
    bridge.start()
    try:
        client = Client(bridge.port, bridge.token)
        assert client.call("undo")["ok"] is True
        assert undo.events == []
        assert client.call("echo")["ok"] is True
        client.close()
    finally:
        bridge.stop()
    assert undo.events == ["open bmaya-mcp echo", "close bmaya-mcp echo"]


def test_stdout_printed_before_an_error_reaches_reply_and_journal(
    make_bridge: Callable[..., BridgeServer], journal: Journal
) -> None:
    def noisy(params: dict[str, Any]) -> dict[str, Any]:
        try:
            print("making cubes")
            raise RuntimeError("cube failed")
        except RuntimeError as exc:
            attach_stdout(exc, "making cubes\n")
            raise

    bridge = make_bridge({"execute_maya_code": noisy})
    client = Client(bridge.port, bridge.token)
    reply = client.call("execute_maya_code", {"code": "..."})
    client.close()
    assert reply["error"]["type"] == "RuntimeError"
    assert reply["error"]["stdout"] == "making cubes\n"
    assert events(journal, "end")[0]["error"]["stdout"] == "making cubes\n"


def test_start_refuses_batch_maya_without_a_scheduler(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server, "_maya_batch", lambda: True)
    try:
        assert server.start() == 0
        assert "batch mode" in capsys.readouterr().err
        assert not server.is_running()
        assert not registry.session_path().exists()
        assert server.start(schedule=lambda callback: callback()) > 0
        assert server.is_running()
    finally:
        server.stop()
        if server._journal is not None:
            server._journal.close()
            server._journal = None
