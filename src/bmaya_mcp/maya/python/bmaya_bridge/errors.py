from __future__ import annotations

import traceback
from typing import Any

FALLBACK_HINT = "The full traceback is in the Maya Script Editor and the bmaya-mcp journal."


class BridgeError(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def user_code_line(exc: BaseException) -> int | None:
    """Line number inside code compiled from a string, such as execute_maya_code input.

    Code run from a string has a pseudo filename like "<agent>", so the last frame
    with such a name points into the agent's code rather than into the bridge.
    """
    if isinstance(exc, SyntaxError) and _is_pseudo_file(exc.filename):
        return exc.lineno
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if _is_pseudo_file(frame.filename):
            line = frame.lineno
    return line


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, BridgeError):
        return {"type": "BridgeError", "message": exc.message, "hint": exc.hint}
    line = user_code_line(exc)
    if line is None:
        hint = FALLBACK_HINT
    else:
        hint = f"The error is on line {line} of the submitted code."
    return {"type": type(exc).__name__, "message": str(exc), "hint": hint}


def _is_pseudo_file(filename: str | None) -> bool:
    return bool(filename) and filename.startswith("<") and not filename.startswith("<frozen")
