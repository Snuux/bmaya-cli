from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime

from bmaya_bridge import registry, server

BOOTSTRAP_LOG = "bootstrap-error.log"


def start_in_maya() -> int:
    """Start the bridge from Maya startup or the plug-in. Never raises; returns the port or 0."""
    already_running = server.is_running()
    try:
        port = server.start()
    except Exception:
        report_failure(traceback.format_exc())
        return 0
    if port and not already_running:
        print(f"bmaya-mcp bridge listening on {server.HOST}:{port} (session {os.getpid()})")
    return port


def report_failure(details: str) -> None:
    print(f"bmaya-mcp: the bridge did not start.\n{details}", file=sys.stderr)
    try:
        path = registry.logs_dir() / BOOTSTRAP_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} pid {os.getpid()}\n{details}\n")
    except OSError:
        pass
