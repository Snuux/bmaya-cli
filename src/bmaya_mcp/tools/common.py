import json
import logging
import time
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from bmaya_mcp.client import BridgeCommandError, BridgeConnectionError
from bmaya_mcp.sessions import SessionError, SessionManager

logger = logging.getLogger(__name__)

BRIDGE_ERROR_TYPES = {"BridgeError", "Timeout", "Unauthorized", "UnknownCommand", "BadRequest"}


def format_command_error(command: str, error: BridgeCommandError) -> str:
    text = f"{error.message} {error.hint}".strip()
    if command == "execute_maya_code" and error.type not in BRIDGE_ERROR_TYPES:
        return f"{error.type}: {text}"
    return text


def forward(manager: SessionManager, command: str, params: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = manager.call(command, params)
    except BridgeCommandError as exc:
        _log(command, started, f"bridge error {exc.type}")
        raise ToolError(format_command_error(command, exc)) from exc
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
