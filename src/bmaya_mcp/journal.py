"""Reads the per-session journals the bridge writes to ~/.bmaya-mcp/logs."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SESSION_LOG = re.compile(r"^(\d{8}-\d{6})-(\d+)\.log$")
LIFECYCLE = ("bridge_start", "bridge_stop")
PARAMS_WIDTH = 100
CODE_LINES = 5


@dataclass(frozen=True)
class SessionLog:
    path: Path
    pid: int
    stamp: str

    @property
    def crash_path(self) -> Path:
        return self.path.with_name(f"{self.path.stem}.crash.log")


@dataclass(frozen=True)
class Crash:
    log: SessionLog
    pending: dict[str, Any] | None
    last_time: str
    crash_tail: list[str]


def logs_dir(home: Path) -> Path:
    return home / "logs"


def session_logs(directory: Path) -> list[SessionLog]:
    if not directory.is_dir():
        return []
    logs = []
    for path in directory.iterdir():
        match = SESSION_LOG.match(path.name)
        if match:
            logs.append(SessionLog(path, int(match.group(2)), match.group(1)))
    return sorted(logs, key=lambda log: (log.stamp, log.pid))


def find_session_log(directory: Path, pid: int) -> SessionLog | None:
    matching = [log for log in session_logs(directory) if log.pid == pid]
    return matching[-1] if matching else None


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def pending_command(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The `start` record that never got its `end`.

    The bridge runs one command at a time, so an `end` always closes the latest `start`.
    """
    pending = None
    for record in records:
        if record.get("event") == "start":
            pending = record
        elif record.get("event") == "end":
            pending = None
    return pending


def stopped_cleanly(records: list[dict[str, Any]]) -> bool:
    lifecycle = [record["event"] for record in records if record.get("event") in LIFECYCLE]
    return bool(lifecycle) and lifecycle[-1] == "bridge_stop"


def find_crashes(directory: Path) -> list[Crash]:
    crashes = []
    for log in session_logs(directory):
        records = read_records(log.path)
        if not records or stopped_cleanly(records) or pid_alive(log.pid):
            continue
        crashes.append(
            Crash(
                log=log,
                pending=pending_command(records),
                last_time=str(records[-1].get("t", "")),
                crash_tail=tail_lines(log.crash_path, 15),
            )
        )
    return crashes


def tail_lines(path: Path, count: int) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    lines = [line for line in lines if line.strip()]
    return lines[-count:]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    # Never os.kill on Windows: any signal there terminates the process.
    import ctypes
    from ctypes import wintypes

    query_limited_information = 0x1000
    still_active = 259
    access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def clock(record: dict[str, Any]) -> str:
    stamp = str(record.get("t", ""))
    return stamp.split("T", 1)[-1]


def shorten(text: str, width: int = PARAMS_WIDTH) -> str:
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def format_record(record: dict[str, Any]) -> str:
    event = record.get("event", "?")
    head = f"{clock(record)} {event}"
    if event == "start":
        params = json.dumps(record.get("params") or {}, ensure_ascii=False)
        return (
            f"{head} #{record.get('id')} {record.get('command')} "
            f"by {record.get('client') or '?'} {shorten(params)}"
        )
    if event == "end":
        outcome = "ok" if record.get("ok") else "failed"
        line = f"{head} #{record.get('id')} {outcome} in {record.get('ms')} ms"
        error = record.get("error") or {}
        if error:
            line += f": {error.get('type')}: {shorten(str(error.get('message', '')))}"
        return line
    extras = {key: value for key, value in record.items() if key not in ("t", "event")}
    details = " ".join(f"{key}={value}" for key, value in extras.items())
    return f"{head} {details}".rstrip()


def describe_pending(record: dict[str, Any]) -> list[str]:
    lines = [f"{record.get('command')} (request {record.get('id')}) started {record.get('t')}"]
    code = (record.get("params") or {}).get("code")
    if record.get("command") == "execute_maya_code" and isinstance(code, str):
        code_lines = code.strip("\n").splitlines()
        lines.extend(f"    {line}" for line in code_lines[:CODE_LINES])
        if len(code_lines) > CODE_LINES:
            lines.append(f"    ... {len(code_lines) - CODE_LINES} more lines")
    return lines
