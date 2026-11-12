from __future__ import annotations

import traceback
from typing import Any

FALLBACK_HINT = "The full traceback is in the Maya Script Editor and the bmaya journal."

BRIDGE_ERROR = "BridgeError"
TIMEOUT = "Timeout"
UNAUTHORIZED = "Unauthorized"
UNKNOWN_COMMAND = "UnknownCommand"
BAD_REQUEST = "BadRequest"

# Any other error type is a Python exception name. bmaya_cli/tools/common.py keeps a copy of
# this set; a test checks that they match.
BRIDGE_ERROR_TYPES = frozenset({BRIDGE_ERROR, TIMEOUT, UNAUTHORIZED, UNKNOWN_COMMAND, BAD_REQUEST})

MAX_ERROR_STDOUT = 2000
_STDOUT_ATTR = "bmaya_stdout"


class BridgeError(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def attach_stdout(exc: BaseException, stdout: str) -> None:
    if stdout:
        setattr(exc, _STDOUT_ATTR, stdout)


def user_code_line(exc: BaseException) -> int | None:
    if isinstance(exc, SyntaxError) and _is_pseudo_file(exc.filename):
        return exc.lineno
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if _is_pseudo_file(frame.filename):
            line = frame.lineno
    return line


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, BridgeError):
        payload = {"type": BRIDGE_ERROR, "message": exc.message, "hint": exc.hint}
    else:
        line = user_code_line(exc)
        if line is None:
            hint = FALLBACK_HINT
        else:
            hint = f"The error is on line {line} of the submitted code."
        payload = {"type": type(exc).__name__, "message": str(exc), "hint": hint}
    stdout = getattr(exc, _STDOUT_ATTR, "")
    if stdout:
        payload["stdout"] = clip_tail(stdout, MAX_ERROR_STDOUT)
    return payload


def clip_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"[{len(text) - limit} chars cut] ...\n{text[-limit:]}"


def _is_pseudo_file(filename: str | None) -> bool:
    return bool(filename) and filename.startswith("<") and not filename.startswith("<frozen")
