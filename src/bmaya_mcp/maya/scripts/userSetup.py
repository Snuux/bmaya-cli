"""Runs in Maya's shared __main__: prefix every name, keep imports inside functions, never raise."""


def _bmaya_mcp_log_failure(details: str) -> None:
    import datetime
    import os
    import sys

    print(f"bmaya-mcp: the bridge did not start.\n{details}", file=sys.stderr)
    home = os.environ.get("BMAYA_MCP_HOME") or os.path.join(os.path.expanduser("~"), ".bmaya-mcp")
    try:
        logs = os.path.join(home, "logs")
        os.makedirs(logs, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(logs, "bootstrap-error.log"), "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} pid {os.getpid()}\n{details}\n")
    except Exception:
        pass


def _bmaya_mcp_start() -> None:
    import traceback

    try:
        from bmaya_bridge.bootstrap import start_in_maya

        start_in_maya()
    except Exception:
        _bmaya_mcp_log_failure(traceback.format_exc())


def _bmaya_mcp_schedule() -> None:
    import traceback

    try:
        from maya import cmds, utils

        if not cmds.about(batch=True):
            utils.executeDeferred(_bmaya_mcp_start)
    except Exception:
        _bmaya_mcp_log_failure(traceback.format_exc())


_bmaya_mcp_schedule()
