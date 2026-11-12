"""Starts the bmaya-mcp bridge once Maya has finished starting up.

Maya may run this file in a namespace shared with other userSetup.py files, so every name
here is prefixed, and nothing may raise: a failure must never break Maya startup.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime


def _bmaya_mcp_log_failure(details: str) -> None:
    print(f"bmaya-mcp: the bridge did not start.\n{details}", file=sys.stderr)
    home = os.environ.get("BMAYA_MCP_HOME") or os.path.join(os.path.expanduser("~"), ".bmaya-mcp")
    try:
        logs = os.path.join(home, "logs")
        os.makedirs(logs, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(logs, "bootstrap-error.log"), "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} pid {os.getpid()}\n{details}\n")
    except Exception:
        pass


def _bmaya_mcp_start() -> None:
    try:
        from bmaya_bridge.bootstrap import start_in_maya

        start_in_maya()
    except Exception:
        _bmaya_mcp_log_failure(traceback.format_exc())


def _bmaya_mcp_schedule() -> None:
    try:
        from maya import cmds, utils

        if not cmds.about(batch=True):
            utils.executeDeferred(_bmaya_mcp_start)
    except Exception:
        _bmaya_mcp_log_failure(traceback.format_exc())


_bmaya_mcp_schedule()
