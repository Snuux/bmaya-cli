from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from bmaya_cli.journal import find_crashes, find_session_log, read_records
from bmaya_cli.server import create_server
from bmaya_cli.sessions import SessionManager

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PATH = REPO / "src" / "bmaya_cli" / "maya" / "python"
BRIDGE_SCRIPT = Path(__file__).with_name("bridge_process.py")


class BridgeProcess:
    def __init__(self, home: Path, scene: str, timeout: float) -> None:
        env = {**os.environ, "BMAYA_HOME": str(home), "PYTHONPATH": str(BRIDGE_PATH)}
        self.process = subprocess.Popen(
            [sys.executable, str(BRIDGE_SCRIPT), "--scene", scene, "--timeout", str(timeout)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            text=True,
        )
        assert self.process.stdout is not None
        ready = self.process.stdout.readline().split()
        assert ready[:1] == ["READY"], f"bridge did not start: {ready}"
        self.port = int(ready[1])
        self.pid = int(ready[2])
        self.id = str(self.pid)

    def close(self) -> None:
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.close()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        if self.process.stdout is not None:
            self.process.stdout.close()


Spawn = Callable[..., BridgeProcess]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BMAYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def spawn(home: Path) -> Iterator[Spawn]:
    processes: list[BridgeProcess] = []

    def start(scene: str = "C:/work/crate.ma", timeout: float = 5.0) -> BridgeProcess:
        bridge = BridgeProcess(home, scene, timeout)
        processes.append(bridge)
        return bridge

    yield start
    for bridge in processes:
        bridge.close()


def mcp_server(home: Path, label: str = "agent-a") -> MCPServer:
    return create_server(SessionManager(home, label=label, ping_timeout=3.0))


def call(server: MCPServer, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    result = asyncio.run(server.call_tool(name, arguments or {}))
    assert isinstance(result, CallToolResult)
    return json.loads(result.content[0].text)


def journal_of(home: Path, bridge: BridgeProcess) -> list[dict[str, Any]]:
    log = find_session_log(home / "logs", bridge.pid)
    assert log is not None
    return read_records(log.path)


def test_normal_call_and_session_listing(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    server = mcp_server(home)
    listing = call(server, "list_sessions")
    assert listing == {
        "sessions": [
            {
                "id": bridge.id,
                "scene": "C:/work/crate.ma",
                "maya_version": "2027",
                "busy": False,
                "clients": 0,
                "current": False,
            }
        ],
        "current": None,
    }
    reply = call(server, "execute_maya_code", {"code": "print('hi')\nresult = 6 * 7"})
    assert reply == {"stdout": "hi\n", "result": 42, "truncated": False}
    started = [record for record in journal_of(home, bridge) if record["event"] == "start"]
    assert started[0]["client"] == "agent-a"
    assert started[0]["command"] == "execute_maya_code"


def test_errors_read_as_message_and_hint(home: Path, spawn: Spawn) -> None:
    spawn()
    server = mcp_server(home)
    with pytest.raises(ToolError) as missing:
        call(server, "get_object_info", {"name": "barrel"})
    assert str(missing.value).endswith(
        ": No object named 'barrel' in the scene. Use get_scene_info to find similar names."
    )
    with pytest.raises(ToolError) as failed:
        call(server, "execute_maya_code", {"code": "print('made 2 cubes')\nraise ValueError('no')"})
    assert str(failed.value).endswith(
        ": ValueError: no The error is on line 2 of the submitted code.\n"
        "Printed before the error:\nmade 2 cubes\n"
    )


def test_timeout_reply_then_session_recovers(home: Path, spawn: Spawn) -> None:
    spawn(timeout=0.5)
    server = mcp_server(home)
    with pytest.raises(ToolError, match="still running in Maya after 0.5 seconds"):
        call(server, "execute_maya_code", {"code": "import time\ntime.sleep(1.5)"})
    assert call(server, "list_sessions")["sessions"][0]["busy"] is True
    deadline = time.monotonic() + 5
    while call(server, "list_sessions")["sessions"][0]["busy"]:
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert call(server, "execute_maya_code", {"code": "result = 'back'"})["result"] == "back"


def test_two_agents_share_one_bridge(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    agents = {"agent-a": mcp_server(home, "agent-a"), "agent-b": mcp_server(home, "agent-b")}
    replies: dict[str, Any] = {}

    def work(label: str) -> None:
        code = f"import time\ntime.sleep(0.3)\nresult = '{label}'"
        replies[label] = call(agents[label], "execute_maya_code", {"code": code})

    threads = [threading.Thread(target=work, args=(label,)) for label in agents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert replies["agent-a"]["result"] == "agent-a"
    assert replies["agent-b"]["result"] == "agent-b"
    commands = [r for r in journal_of(home, bridge) if r["event"] in ("start", "end")]
    assert [r["event"] for r in commands] == ["start", "end", "start", "end"]
    assert {r["client"] for r in commands if r["event"] == "start"} == set(agents)
    assert call(agents["agent-a"], "list_sessions")["sessions"][0]["clients"] == 2


def test_two_bridges_need_use_session(home: Path, spawn: Spawn) -> None:
    spawn(scene="C:/work/crate.ma")
    barrel = spawn(scene="C:/work/barrel.ma")
    server = mcp_server(home)
    with pytest.raises(ToolError, match="Several Maya sessions are running"):
        call(server, "get_scene_info")
    chosen = call(server, "use_session", {"session": barrel.id})
    assert chosen["current"] is True
    assert chosen["scene"] == "C:/work/barrel.ma"
    assert call(server, "get_scene_info")["file"] == "C:/work/barrel.ma"


def test_bridge_killed_mid_command(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    server = mcp_server(home)
    call(server, "get_scene_info")
    registry_file = home / "sessions" / f"{bridge.pid}.json"
    assert registry_file.exists()

    with pytest.raises(ToolError) as lost:
        call(server, "execute_maya_code", {"code": "import os\nos._exit(3)"})
    bridge.process.wait(10)

    message = str(lost.value)
    log = find_session_log(home / "logs", bridge.pid)
    assert log is not None
    assert f"Maya session {bridge.id} exited or crashed." in message
    assert "The last command sent to it was execute_maya_code." in message
    assert f"Its journal is {log.path}" in message
    assert not registry_file.exists()

    records = read_records(log.path)
    assert records[-1]["event"] == "start"
    assert records[-1]["command"] == "execute_maya_code"
    assert "bridge_stop" not in {record["event"] for record in records}
    crashes = find_crashes(home / "logs")
    assert [crash.log.pid for crash in crashes] == [bridge.pid]
    assert crashes[0].pending is not None
    assert crashes[0].pending["params"]["code"] == "import os\nos._exit(3)"
