from __future__ import annotations

import contextlib
import io
import json
import math
from typing import Any

import maya.api.OpenMaya as om
from maya import cmds, mel

from bmaya_bridge.errors import BridgeError, attach_stdout
from bmaya_bridge.guards import headless_crash_reason

MAX_CHARS = 8000
MAX_DEPTH = 6


def json_safe(value: object, depth: int = 0) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return repr(value)
    if depth >= MAX_DEPTH:
        return repr(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item, depth + 1) for item in value]
    if isinstance(value, (om.MVector, om.MPoint, om.MMatrix, om.MColor, om.MQuaternion)):
        return [json_safe(item, depth + 1) for item in value]
    return repr(value)


def clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_CHARS:
        return text, False
    head = MAX_CHARS * 3 // 4
    tail = MAX_CHARS - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n... [{cut} chars cut] ...\n{text[-tail:]}", True


def execute_maya_code(params: dict[str, Any]) -> dict[str, Any]:
    code = params.get("code") or ""
    if not code.strip():
        raise BridgeError(
            "No code given.",
            hint="Pass Python source as an argument, with -f FILE or on stdin. "
            "cmds, mel and om are preloaded; "
            "assign to a variable named result to return a value.",
        )

    crash_reason = headless_crash_reason(code)
    if crash_reason and cmds.about(batch=True):
        raise BridgeError(
            f"The code calls {crash_reason}, which crashes a Maya without a window.",
            hint="Use cmds.ogs(reloadTextures=True) to reload textures instead.",
        )

    compiled = compile(code, "<agent>", "exec")
    namespace: dict[str, Any] = {
        "__name__": "__agent__",
        "__builtins__": __builtins__,
        "cmds": cmds,
        "mel": mel,
        "om": om,
    }
    out = io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compiled, namespace)
    except BaseException as exc:
        attach_stdout(exc, out.getvalue())
        raise

    stdout, truncated = clip(out.getvalue())
    response: dict[str, Any] = {"stdout": stdout}
    stderr = err.getvalue()
    if stderr:
        response["stderr"], clipped = clip(stderr)
        truncated = truncated or clipped

    if "result" in namespace:
        result = json_safe(namespace["result"])
        encoded = json.dumps(result, separators=(",", ":"))
        if len(encoded) > MAX_CHARS:
            result, _ = clip(encoded)
            truncated = True
        response["result"] = result

    response["truncated"] = truncated
    return response
