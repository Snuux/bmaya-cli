"""`bmaya export`: write FBX in Maya, then read the file back and check it."""

from __future__ import annotations

import argparse
from typing import Any

from bmaya_cli.commands import EXIT_OK, absolute, call, emit
from bmaya_cli.modelfile import read_back

ISSUES_SHOWN = 40


def listing(items: list[Any], limit: int) -> dict[str, Any]:
    return {"items": items[:limit], "shown": min(len(items), limit), "total": len(items)}


def checked(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply with `written` and every issue: Maya's checks before writing, the file's after."""
    expected = reply.pop("expected", None)
    issues = list(reply.pop("issues", []))
    written, found = read_back(reply["path"], expected)
    reply["written"] = listing([written], ISSUES_SHOWN)
    reply["issues"] = listing(issues + found, ISSUES_SHOWN)
    return reply


def cmd_export(args: argparse.Namespace) -> int:
    params = {
        "path": absolute(args.path),
        "preset": args.preset,
        "selection_only": args.selection,
        "animation": args.animation,
        "start": args.start,
        "end": args.end,
        "clip": args.clip,
        "ignore_checks": args.ignore_checks,
    }
    emit(checked(call(args, "export_fbx", params)))
    return EXIT_OK
