import json
from pathlib import Path
from typing import Any

import pytest

from bmaya_mcp import journal

CRASH_CODE = "import maya.cmds as cmds\nfor i in range(10):\n    cmds.polyCube()\n" + "x = 1\n" * 6
ALIVE = {333}


def record(event: str, **fields: object) -> dict[str, Any]:
    return {"t": "2026-11-12T10:00:00.000", "event": event, **fields}


def write_journal(logs: Path, name: str, records: list[dict[str, Any]]) -> Path:
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / name
    lines = [json.dumps(item) for item in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(journal, "pid_alive", lambda pid: pid in ALIVE)
    logs = tmp_path / "logs"
    write_journal(
        logs,
        "20261112-090000-111.log",
        [
            record("bridge_start", pid=111, port=5000),
            record("start", id=1, client="claude-1", command="get_scene_info", params={}),
            record("end", id=1, ms=3.2, ok=True, error=None),
            record(
                "start",
                id=2,
                client="claude-1",
                command="execute_maya_code",
                params={"code": CRASH_CODE},
            ),
        ],
    )
    (logs / "20261112-090000-111.crash.log").write_text(
        "Fatal Python error: Segmentation fault\n\nCurrent thread:\n  File maya\n",
        encoding="utf-8",
    )
    write_journal(
        logs,
        "20261112-100000-222.log",
        [record("bridge_start", pid=222), record("bridge_stop", pid=222)],
    )
    write_journal(logs, "20261112-110000-333.log", [record("bridge_start", pid=333)])
    write_journal(logs, "20261112-080000-444.log", [record("bridge_start", pid=444)])
    with open(logs / "20261112-080000-444.log", "a", encoding="utf-8") as handle:
        handle.write('{"t": "2026-11-12T08:00:01", "event": "sta')
    (logs / "server-555.log").write_text("not a journal\n", encoding="utf-8")
    return logs


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BMAYA_MCP_HOME", str(tmp_path))
    return tmp_path
