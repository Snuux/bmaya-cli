from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime

from bmaya_bridge import registry, server

BOOTSTRAP_LOG = "bootstrap-error.log"
WINDOW_WAIT_SECONDS = 120.0
WINDOW_POLL_MS = 500


def window_timer() -> type | None:
    try:
        from PySide6 import QtCore
    except ImportError:
        try:
            from PySide2 import QtCore
        except ImportError:
            return None
    return QtCore.QTimer


def window_shown() -> bool:
    try:
        from PySide6 import QtWidgets
    except ImportError:
        try:
            from PySide2 import QtWidgets
        except ImportError:
            return True
    app = QtWidgets.QApplication.instance()
    if not isinstance(app, QtWidgets.QApplication):
        return True
    return any(
        widget.objectName() == "MayaWindow" and widget.isVisible()
        for widget in app.topLevelWidgets()
    )


def start_in_maya(deadline: float = 0.0) -> int:
    """Starts the bridge and returns its port. In a Maya with a window it waits for the main
    window to be on screen first and returns 0 meanwhile: Maya runs deferred startup code
    before it shows the window, and a bridge registered then answered `bmaya shot` with a
    viewport that had never been drawn."""
    deadline = deadline or time.monotonic() + WINDOW_WAIT_SECONDS
    timer = window_timer()
    if timer is not None and not window_shown() and time.monotonic() < deadline:
        timer.singleShot(WINDOW_POLL_MS, lambda: start_in_maya(deadline))
        return 0
    already_running = server.is_running()
    try:
        port = server.start()
    except Exception:
        report_failure(traceback.format_exc())
        return 0
    if port and not already_running:
        print(f"bmaya bridge listening on {server.HOST}:{port} (session {os.getpid()})")
    return port


def report_failure(details: str) -> None:
    print(f"bmaya: the bridge did not start.\n{details}", file=sys.stderr)
    try:
        path = registry.logs_dir() / BOOTSTRAP_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} pid {os.getpid()}\n{details}\n")
    except OSError:
        pass
