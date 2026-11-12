from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bmaya_cli.journal import find_crashes, find_session_log, read_records

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
BRIDGE_PATH = SRC / "bmaya_cli" / "maya" / "python"
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


@dataclass
class Run:
    code: int
    out: str
    err: str

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.out)


@pytest.fixture
def home(tmp_path: Path) -> Path:
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


def bmaya(home: Path, *argv: str, client: str = "agent-a", stdin: str | None = None) -> Run:
    """Runs the real command line, one process per command, as an agent's shell would."""
    env = {
        **os.environ,
        "BMAYA_HOME": str(home),
        "BMAYA_CLIENT": client,
        "BMAYA_AUTOSTART": "off",
        "PYTHONPATH": str(SRC),
    }
    done = subprocess.run(
        [sys.executable, "-m", "bmaya_cli", *argv],
        input=stdin,
        stdin=subprocess.DEVNULL if stdin is None else None,
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    return Run(done.returncode, done.stdout, done.stderr)


def journal_of(home: Path, bridge: BridgeProcess) -> list[dict[str, Any]]:
    log = find_session_log(home / "logs", bridge.pid)
    assert log is not None
    return read_records(log.path)


def test_normal_call_and_session_listing(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    listing = bmaya(home, "sessions")
    assert listing.code == 0
    assert listing.err == ""
    assert listing.json == {
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
        "client": "agent-a",
    }
    reply = bmaya(home, "exec", "print('hi')\nresult = 6 * 7")
    assert reply.out == '{"stdout":"hi\\n","result":42,"truncated":false}\n'
    started = [record for record in journal_of(home, bridge) if record["event"] == "start"]
    assert started[0]["client"] == "agent-a"
    assert started[0]["command"] == "execute_maya_code"


def test_code_with_quotes_and_other_alphabets_survives_stdin(home: Path, spawn: Spawn) -> None:
    spawn()
    code = "name = 'ящик \"большой\"'\nprint(name)\nresult = [name, len(name)]\n"
    reply = bmaya(home, "exec", stdin=code)
    assert reply.code == 0
    assert reply.json == {
        "stdout": 'ящик "большой"\n',
        "result": ['ящик "большой"', 14],
        "truncated": False,
    }
    assert "ящик" in reply.out


def test_errors_read_as_message_and_hint(home: Path, spawn: Spawn) -> None:
    spawn()
    missing = bmaya(home, "object", "barrel")
    assert missing.code == 1
    assert missing.out == ""
    assert missing.err == (
        "error: No object named 'barrel' in the scene.\nhint: `bmaya scene` lists the nodes.\n"
    )
    failed = bmaya(home, "exec", "print('made 2 cubes')\nraise ValueError('no')")
    assert failed.code == 1
    assert failed.err == (
        "error: ValueError: no\n"
        "hint: The error is on line 2 of the submitted code.\n"
        "printed before the error:\n"
        "made 2 cubes\n"
    )


def test_timeout_reply_then_session_recovers(home: Path, spawn: Spawn) -> None:
    spawn(timeout=0.5)
    slow = bmaya(home, "exec", "import time\ntime.sleep(2.5)")
    assert slow.code == 3
    assert "still running in Maya after 0.5 seconds" in slow.err
    assert "do not send it again" in slow.err
    assert bmaya(home, "sessions").json["sessions"][0]["busy"] is True
    deadline = time.monotonic() + 10
    while bmaya(home, "sessions").json["sessions"][0]["busy"]:
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert bmaya(home, "exec", "result = 'back'").json["result"] == "back"


def test_two_agents_share_one_bridge(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    replies: dict[str, Any] = {}

    def work(label: str) -> None:
        code = f"import time\ntime.sleep(0.3)\nresult = '{label}'"
        replies[label] = bmaya(home, "exec", code, client=label).json

    threads = [threading.Thread(target=work, args=(label,)) for label in ("agent-a", "agent-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert replies["agent-a"]["result"] == "agent-a"
    assert replies["agent-b"]["result"] == "agent-b"
    commands = [r for r in journal_of(home, bridge) if r["event"] in ("start", "end")]
    assert [r["event"] for r in commands] == ["start", "end", "start", "end"]
    assert {r["client"] for r in commands if r["event"] == "start"} == {"agent-a", "agent-b"}
    assert bmaya(home, "sessions").json["sessions"][0]["clients"] == 2


def test_two_bridges_need_a_choice_that_each_agent_keeps(home: Path, spawn: Spawn) -> None:
    crate = spawn(scene="C:/work/crate.ma")
    barrel = spawn(scene="C:/work/barrel.ma")
    undecided = bmaya(home, "scene")
    assert undecided.code == 1
    assert "Several Maya sessions are running" in undecided.err
    chosen = bmaya(home, "use", barrel.id).json
    assert chosen["current"] is True
    assert chosen["scene"] == "C:/work/barrel.ma"
    assert bmaya(home, "scene").json["file"] == "C:/work/barrel.ma"
    assert bmaya(home, "scene", "--session", crate.id).json["file"] == "C:/work/crate.ma"
    assert bmaya(home, "scene", client="agent-b").code == 1
    bmaya(home, "use", crate.id, client="agent-b")
    assert bmaya(home, "scene", client="agent-b").json["file"] == "C:/work/crate.ma"
    assert bmaya(home, "scene").json["file"] == "C:/work/barrel.ma"


def test_bridge_killed_mid_command(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    assert bmaya(home, "scene").code == 0
    registry_file = home / "sessions" / f"{bridge.pid}.json"
    assert registry_file.exists()

    lost = bmaya(home, "exec", "import os\nos._exit(3)")
    bridge.process.wait(10)

    log = find_session_log(home / "logs", bridge.pid)
    assert log is not None
    assert lost.code == 1
    assert lost.out == ""
    assert f"Maya session {bridge.id} exited or crashed." in lost.err
    assert "The last command sent to it was execute_maya_code." in lost.err
    assert f"Its journal is {log.path}" in lost.err
    assert not registry_file.exists()
    assert "No Maya with the bmaya bridge is running" in bmaya(home, "scene").err

    records = read_records(log.path)
    assert records[-1]["event"] == "start"
    assert records[-1]["command"] == "execute_maya_code"
    assert "bridge_stop" not in {record["event"] for record in records}
    crashes = find_crashes(home / "logs")
    assert [crash.log.pid for crash in crashes] == [bridge.pid]
    assert crashes[0].pending is not None
    assert crashes[0].pending["params"]["code"] == "import os\nos._exit(3)"


def test_session_that_died_between_commands_is_reported_once(home: Path, spawn: Spawn) -> None:
    bridge = spawn()
    assert bmaya(home, "scene").code == 0
    bridge.process.kill()
    bridge.process.wait(10)
    lost = bmaya(home, "scene")
    assert lost.code == 1
    assert f"Maya session {bridge.id} exited or crashed." in lost.err
    assert "The last command sent to it was get_scene_info." in lost.err
    again = bmaya(home, "scene")
    assert again.err.startswith("error: No Maya with the bmaya bridge is running.")
