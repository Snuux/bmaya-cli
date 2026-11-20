"""`bmaya export`: write FBX in Maya, then read the file back and check it."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path
from typing import Any

from bmaya_cli.client import BridgeCommandError, BridgeConnectionError
from bmaya_cli.commands import (
    EXIT_OK,
    EXIT_RETRY,
    EXIT_USAGE,
    CommandFailed,
    absolute,
    call,
    emit,
    manager_for,
    note_autostart,
)
from bmaya_cli.modelfile import read_back
from bmaya_cli.sessions import SessionManager

ISSUES_SHOWN = 40
WRITTEN_SHOWN = 200
ALL_NODES = "*"
FILE_ONLY_FLAGS = ("preset", "selection", "animation", "start", "end")
UDIM_TOKENS = ("<udim>", "<uvtile>", "<f>", "u<u>_v<v>")


def listing(items: list[Any], limit: int) -> dict[str, Any]:
    return {"items": items[:limit], "shown": min(len(items), limit), "total": len(items)}


def checked(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply with `written` and every issue: Maya's checks before writing, the file's after."""
    expected = reply.pop("expected", None)
    issues = list(reply.pop("issues", []))
    written, found = read_back(reply["path"], expected)
    reply["written"] = listing([written], WRITTEN_SHOWN)
    reply["issues"] = listing(issues + found, ISSUES_SHOWN)
    return reply


def refuse(message: str, hint: str) -> CommandFailed:
    return CommandFailed(message, hint, code=EXIT_USAGE)


def check_arguments(args: argparse.Namespace) -> None:
    if args.game_exporter:
        if args.path:
            raise refuse(
                "Give FILE.fbx or --game-exporter, not both.",
                "--game-exporter writes to the paths set on the Game Exporter nodes.",
            )
        used = [flag for flag in FILE_ONLY_FLAGS if getattr(args, flag)]
        if used:
            names = ", ".join("--" + flag for flag in used)
            raise refuse(
                f"{names} cannot go with --game-exporter.",
                "The Game Exporter nodes carry their own preset, set and frames. --clip NAME "
                "narrows the export to one clip.",
            )
        return
    if not args.path:
        raise refuse(
            "Give the file to write, or --game-exporter.",
            "bmaya export hero.fbx --preset godot, or bmaya export --game-exporter to export "
            "the way the scene's Game Exporter nodes are set up.",
        )
    if args.copy_textures:
        raise refuse(
            "--copy-textures goes with --game-exporter.",
            "Drop it, or export with --game-exporter.",
        )


def is_tiled(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in UDIM_TOKENS)


class TextureCopies:
    """Copies textures next to the files that use them, as the Game Exporter pipeline expects."""

    def __init__(self) -> None:
        self.claimed: dict[str, str] = {}
        self.copied: list[str] = []
        self.kept = 0
        self.issues: list[str] = []

    def place(self, job: dict[str, Any]) -> dict[str, str]:
        folder = Path(job["path"]).parent
        moves = {}
        for texture in job.get("textures", []):
            source = texture["path"]
            if texture.get("referenced"):
                self.issues.append(f"{texture['node']}: comes from a referenced file, not copied")
                continue
            if not source or is_tiled(source):
                self.issues.append(f"{texture['node']}: tiled or empty path '{source}', not copied")
                continue
            origin = Path(source)
            if not origin.is_file():
                self.issues.append(f"{texture['node']}: {source} does not exist, not copied")
                continue
            target = folder / origin.name
            key = target.as_posix().lower()
            if self.claimed.get(key, source) != source:
                self.issues.append(
                    f"{texture['node']}: {origin.name} is also the name of "
                    f"{self.claimed[key]}, not copied"
                )
                continue
            self.claimed[key] = source
            if target.resolve() != origin.resolve():
                if not target.is_file() or origin.stat().st_mtime > target.stat().st_mtime:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(origin, target)
                    self.copied.append(target.as_posix())
                else:
                    self.kept += 1
            moves[texture["node"]] = target.as_posix()
        return moves


def stopped(
    job: dict[str, Any], written: list[dict[str, Any]], error: str, code: int
) -> CommandFailed:
    done = ", ".join(Path(item["path"]).name for item in written) or "none"
    if code == EXIT_RETRY:
        return CommandFailed(
            f"Maya is still writing {job['path']}; written before it: {done}.",
            hint="Do not resend. Wait until `bmaya sessions` shows the session idle, then export "
            "what is left with --clip NAME.",
            code=EXIT_RETRY,
        )
    hint = f"Written before it: {done}."
    if "UnknownCommand" in error:
        hint = (
            "The Maya module is older than this bmaya: run `bmaya install` and restart that Maya."
        )
    return CommandFailed(f"Game Exporter export stopped at {job['path']}: {error}", hint=hint)


def export_jobs(manager: SessionManager, plan: dict[str, Any], copy: bool) -> dict[str, Any]:
    written: list[dict[str, Any]] = []
    issues = list(plan["issues"])
    copies = TextureCopies()
    started = time.monotonic()
    for job in plan["jobs"]:
        moves = copies.place(job) if copy and job["kind"] == "model" else {}
        params = {
            "node": job["node"],
            "kind": job["kind"],
            "clip": job.get("clip", ""),
            "path": job["path"],
            "textures": moves,
        }
        try:
            reply = manager.call("export_game_file", params)
        except BridgeCommandError as error:
            code = EXIT_RETRY if error.type == "Timeout" else 1
            raise stopped(job, written, f"{error.type}: {error.message}", code) from error
        except BridgeConnectionError as error:
            code = EXIT_RETRY if error.timed_out else 1
            raise stopped(job, written, str(error), code) from error
        entry, found = read_back(reply["path"], job["expected"])
        if reply.get("retried"):
            entry["retried"] = True
        written.append(entry)
        name = Path(job["path"]).name
        issues += [f"{name}: {issue}" for issue in found]
    result: dict[str, Any] = {
        "written": listing(written, WRITTEN_SHOWN),
        "skipped": plan["skipped"],
    }
    if copy:
        result["textures"] = {"copied": copies.copied, "kept": copies.kept}
    result["issues"] = listing(issues + copies.issues, ISSUES_SHOWN)
    result["calls"] = len(plan["jobs"]) + 1
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def export_game(args: argparse.Namespace) -> dict[str, Any]:
    node = "" if args.game_exporter == ALL_NODES else args.game_exporter
    params = {"node": node, "clip": args.clip, "ignore_checks": args.ignore_checks}
    manager = manager_for(args)
    try:
        session, plan = manager.call_session("plan_game_export", params)
        manager.override = session.id
        return export_jobs(manager, plan, args.copy_textures)
    except BridgeCommandError as error:
        if error.type == "UnknownCommand":
            raise CommandFailed(
                "This Maya does not know --game-exporter yet.",
                hint="The Maya module is older than this bmaya: run `bmaya install` and restart "
                "that Maya.",
            ) from error
        raise
    finally:
        note_autostart(manager)


def cmd_export(args: argparse.Namespace) -> int:
    check_arguments(args)
    if args.game_exporter:
        emit(export_game(args))
        return EXIT_OK
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
