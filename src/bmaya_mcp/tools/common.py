import json
import logging
import time
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from bmaya_mcp.client import BridgeCommandError, BridgeConnectionError
from bmaya_mcp.sessions import SessionError, SessionManager

logger = logging.getLogger(__name__)

# Error types the bridge itself produces; any other type is a Python exception raised
# in Maya. Mirrors bmaya_bridge.errors.BRIDGE_ERROR_TYPES, which this package cannot import.
BRIDGE_ERROR_TYPES = frozenset(
    {"BridgeError", "Timeout", "Unauthorized", "UnknownCommand", "BadRequest"}
)


def format_command_error(error: BridgeCommandError) -> str:
    text = f"{error.message} {error.hint}".strip()
    if error.type not in BRIDGE_ERROR_TYPES:
        text = f"{error.type}: {text}"
    if error.stdout:
        text += f"\nPrinted before the error:\n{error.stdout}"
    return text


def forward(manager: SessionManager, command: str, params: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = manager.call(command, params)
    except BridgeCommandError as exc:
        _log(command, started, f"bridge error {exc.type}")
        raise ToolError(format_command_error(exc)) from exc
    except (SessionError, BridgeConnectionError) as exc:
        _log(command, started, "failed")
        raise ToolError(str(exc)) from exc
    _log(command, started, "ok")
    return result


def _log(command: str, started: float, outcome: str) -> None:
    elapsed = (time.perf_counter() - started) * 1000
    logger.info("%s %s in %.0f ms", command, outcome, elapsed)


def dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def forward_text(manager: SessionManager, command: str, params: dict[str, Any]) -> str:
    return dump(forward(manager, command, params))
