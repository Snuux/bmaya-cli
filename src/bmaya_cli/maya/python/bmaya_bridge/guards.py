from __future__ import annotations

import re

OGS_RESET = re.compile(r"\bogs\s*\([^)]*\breset\s*=\s*(True|1)\b")


def headless_crash_reason(code: str) -> str:
    if OGS_RESET.search(code):
        return "cmds.ogs(reset=True)"
    return ""
