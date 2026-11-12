from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from bmaya_bridge.dispatch import Dispatcher
from bmaya_bridge.journal import MAX_PARAM_CHARS, Journal, truncate_params

from .conftest import UndoRecorder, read_journal


def test_start_is_on_disk_before_the_command_runs(journal: Journal) -> None:
    seen: list[dict[str, Any]] = []

    def inspect(params: dict[str, Any]) -> dict[str, Any]:
        seen.extend(read_journal(journal.path))
        return {}

    dispatcher = Dispatcher(journal, lambda callback: callback(), UndoRecorder())
    dispatcher.submit(4, "claude-1", "inspect", inspect, {"a": 1})
    assert seen[-1]["event"] == "start"
    assert seen[-1]["id"] == 4
    records = read_journal(journal.path)
    assert [record["event"] for record in records] == ["start", "end"]
    assert records[1]["id"] == 4
    assert records[1]["ok"] is True
    assert records[1]["error"] is None
    assert records[1]["ms"] >= 0
    assert all("t" in record for record in records)


def test_crashed_session_shape(home: Path) -> None:
    journal = Journal(home / "logs", crash_log=False)
    journal.event("bridge_start", pid=1, port=2)
    journal.start(1, "claude-1", "execute_maya_code", {"code": "crash()"})
    journal.close()
    records = read_journal(journal.path)
    assert [record["event"] for record in records] == ["bridge_start", "start"]


def test_crash_log_is_created_beside_the_journal(home: Path) -> None:
    journal = Journal(home / "logs")
    try:
        assert journal.crash_path.exists()
        assert journal.crash_path.name == f"{journal.path.stem}.crash.log"
        assert journal.path.name.endswith(f"-{os.getpid()}.log")
    finally:
        journal.close()


def test_long_param_values_are_truncated(journal: Journal) -> None:
    long_text = "x" * (MAX_PARAM_CHARS + 500)
    long_list = list(range(10000))
    params = {"code": long_text, "names": long_list, "limit": 5}
    truncated = truncate_params(params)
    assert truncated["limit"] == 5
    assert truncated["code"].startswith("x" * MAX_PARAM_CHARS)
    assert truncated["code"].endswith(f"[truncated, {len(long_text)} chars]")
    assert isinstance(truncated["names"], str)
    assert "truncated" in truncated["names"]
    journal.start(1, "c", "execute_maya_code", params)
    record = read_journal(journal.path)[-1]
    assert len(record["params"]["code"]) < MAX_PARAM_CHARS + 100


def test_only_newest_sessions_are_kept(home: Path) -> None:
    logs = home / "logs"
    logs.mkdir()
    for index in range(35):
        stem = f"20260101-0000{index:02d}-100"
        (logs / f"{stem}.log").write_text("{}\n", encoding="utf-8")
        (logs / f"{stem}.crash.log").write_text("", encoding="utf-8")
    (logs / "server-42.log").write_text("server\n", encoding="utf-8")
    journal = Journal(logs, crash_log=False)
    journal.close()
    sessions = sorted(path.name for path in logs.glob("*-*-*.log") if ".crash" not in path.name)
    assert len(sessions) == 30
    assert journal.path.name in sessions
    assert "20260101-000000-100.log" not in sessions
    assert not (logs / "20260101-000005-100.crash.log").exists()
    assert (logs / "20260101-000034-100.crash.log").exists()
    assert (logs / "server-42.log").exists()


def test_writes_from_many_threads_stay_whole_lines(journal: Journal) -> None:
    def write(worker: int) -> None:
        for index in range(200):
            journal.event("client_connect", worker=worker, index=index, pad="y" * 300)

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(read_journal(journal.path)) == 1600
