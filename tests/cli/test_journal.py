import os
import subprocess
import sys
from pathlib import Path

from bmaya_mcp.journal import (
    describe_pending,
    find_crashes,
    find_session_log,
    format_record,
    pending_command,
    pid_alive,
    session_logs,
)

from .conftest import CRASH_CODE, record


def test_session_logs_are_sorted_and_skip_other_files(logs: Path) -> None:
    assert [log.pid for log in session_logs(logs)] == [444, 111, 222, 333]
    found = find_session_log(logs, 111)
    assert found is not None
    assert found.crash_path.name == "20261112-090000-111.crash.log"


def test_crashes_are_dead_sessions_without_bridge_stop(logs: Path) -> None:
    crashes = find_crashes(logs)
    assert [crash.log.pid for crash in crashes] == [444, 111]
    unfinished = crashes[1]
    assert unfinished.pending is not None
    assert unfinished.pending["command"] == "execute_maya_code"
    assert unfinished.crash_tail[0] == "Fatal Python error: Segmentation fault"
    assert crashes[0].pending is None


def test_pending_command_is_the_start_without_end() -> None:
    finished = [record("start", id=1), record("end", id=1)]
    assert pending_command(finished) is None
    assert pending_command([*finished, record("start", id=1, command="undo")]) == record(
        "start", id=1, command="undo"
    )


def test_pending_code_is_shown_in_its_first_lines() -> None:
    lines = describe_pending(
        record("start", id=2, command="execute_maya_code", params={"code": CRASH_CODE})
    )
    assert lines[0] == "execute_maya_code (request 2) started 2026-11-12T10:00:00.000"
    assert lines[1:4] == [
        "    import maya.cmds as cmds",
        "    for i in range(10):",
        "        cmds.polyCube()",
    ]
    assert lines[-1] == "    ... 4 more lines"


def test_records_read_as_compact_lines() -> None:
    start = record("start", id=3, client="claude-7", command="undo", params={"steps": 2})
    assert format_record(start) == '10:00:00.000 start #3 undo by claude-7 {"steps": 2}'
    failed = record("end", id=3, ms=1.5, ok=False, error={"type": "BridgeError", "message": "x"})
    assert format_record(failed) == "10:00:00.000 end #3 failed in 1.5 ms: BridgeError: x"
    assert format_record(record("client_connect", peer=5)) == "10:00:00.000 client_connect peer=5"


def test_pid_alive_tells_running_from_finished_processes() -> None:
    assert pid_alive(os.getpid())
    finished = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not pid_alive(int(finished.stdout))
    assert not pid_alive(0)
