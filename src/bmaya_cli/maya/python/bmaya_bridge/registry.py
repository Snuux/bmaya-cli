from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home() -> Path:
    override = os.environ.get("BMAYA_HOME")
    if override:
        return Path(override)
    return Path.home() / ".bmaya"


def sessions_dir() -> Path:
    return home() / "sessions"


def logs_dir() -> Path:
    return home() / "logs"


def session_path(pid: int | None = None) -> Path:
    if pid is None:
        pid = os.getpid()
    return sessions_dir() / f"{pid}.json"


def write_session(info: dict[str, Any]) -> Path:
    path = session_path(info["pid"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(info, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return path


def remove_session(pid: int | None = None) -> None:
    try:
        session_path(pid).unlink()
    except FileNotFoundError:
        pass
