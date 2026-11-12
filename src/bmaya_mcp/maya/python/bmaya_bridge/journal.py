from __future__ import annotations

import faulthandler
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import IO, Any

MAX_PARAM_CHARS = 20 * 1024
KEEP_SESSIONS = 30
SESSION_LOG = re.compile(r"^\d{8}-\d{6}-\d+\.log$")


class Journal:
    def __init__(
        self,
        directory: Path,
        pid: int | None = None,
        keep: int = KEEP_SESSIONS,
        crash_log: bool = True,
    ) -> None:
        if pid is None:
            pid = os.getpid()
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{pid}"
        self.path = directory / f"{stem}.log"
        self.crash_path = directory / f"{stem}.crash.log"
        self._lock = threading.Lock()
        self._file: IO[str] | None = open(self.path, "a", encoding="utf-8")
        self._crash_file: IO[str] | None = None
        if crash_log:
            self._crash_file = open(self.crash_path, "w", encoding="utf-8")
            faulthandler.enable(self._crash_file)
        prune(directory, keep)

    def event(self, name: str, **fields: object) -> None:
        self._write({"event": name, **fields}, sync=False)

    def start(self, request_id: object, client: str, command: str, params: dict[str, Any]) -> None:
        record = {
            "event": "start",
            "id": request_id,
            "client": client,
            "command": command,
            "params": truncate_params(params),
        }
        self._write(record, sync=True)

    def end(
        self,
        request_id: object,
        ms: float,
        ok: bool,
        error: dict[str, Any] | None = None,
        traceback: str = "",
    ) -> None:
        record: dict[str, Any] = {
            "event": "end",
            "id": request_id,
            "ms": round(ms, 1),
            "ok": ok,
            "error": error,
        }
        if traceback:
            record["traceback"] = traceback
        self._write(record, sync=True)

    def close(self) -> None:
        with self._lock:
            if self._crash_file is not None:
                faulthandler.disable()
                self._crash_file.close()
                self._crash_file = None
            if self._file is not None:
                self._file.close()
                self._file = None

    def _write(self, record: dict[str, Any], sync: bool) -> None:
        stamped = {"t": datetime.now().isoformat(timespec="milliseconds"), **record}
        line = json.dumps(stamped, ensure_ascii=False, default=repr, separators=(",", ":"))
        with self._lock:
            if self._file is None:
                return
            self._file.write(line + "\n")
            self._file.flush()
            if sync:
                os.fsync(self._file.fileno())


def truncate_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: _truncate(value) for key, value in params.items()}


def _truncate(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= MAX_PARAM_CHARS:
            return value
        return f"{value[:MAX_PARAM_CHARS]}... [truncated, {len(value)} chars]"
    encoded = json.dumps(value, ensure_ascii=False, default=repr)
    if len(encoded) <= MAX_PARAM_CHARS:
        return value
    return f"{encoded[:MAX_PARAM_CHARS]}... [truncated, {len(encoded)} chars]"


def prune(directory: Path, keep: int = KEEP_SESSIONS) -> None:
    logs = sorted(path for path in directory.iterdir() if SESSION_LOG.match(path.name))
    for path in logs[: max(len(logs) - keep, 0)]:
        crash = path.with_name(f"{path.stem}.crash.log")
        for stale in (path, crash):
            try:
                stale.unlink()
            except OSError:
                pass
