import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from bmaya_cli.client import BridgeCommandError, BridgeConnectionError
from bmaya_cli.identity import client_label, sanitize
from bmaya_cli.journal import pid_alive
from bmaya_cli.launch import GUI, HEADLESS, OFF, Launcher, autostart_mode
from bmaya_cli.sessions import MayaStarting, SessionError, SessionManager, bmaya_home
from bmaya_cli.state import StateStore

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_RETRY = 3

START_WAIT = 120.0
STOP_WAIT = 20.0
STOP_POLL = 0.2
SHOTS_DIR = "shots"
SHOTS_KEPT = 30

# Must mirror bmaya_bridge.errors.BRIDGE_ERROR_TYPES, which this package cannot import.
# Any other type is a Python exception raised in Maya.
BRIDGE_ERROR_TYPES = frozenset(
    {"BridgeError", "Timeout", "Unauthorized", "UnknownCommand", "BadRequest"}
)


class CommandFailed(Exception):
    def __init__(self, message: str, hint: str = "", code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


def emit(value: object) -> None:
    print(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def report(message: str, hint: str = "", code: int = EXIT_ERROR) -> int:
    print(f"error: {message}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    return code


def report_failure(error: Exception) -> int | None:
    """Exit code for a failure the caller can act on; None for a bug that needs its traceback."""
    if isinstance(error, CommandFailed):
        return report(error.message, error.hint, error.code)
    if isinstance(error, BridgeCommandError):
        return report_command_error(error)
    if isinstance(error, MayaStarting):
        return report(str(error), code=EXIT_RETRY)
    if isinstance(error, BridgeConnectionError):
        return report(str(error), code=EXIT_RETRY if error.timed_out else EXIT_ERROR)
    if isinstance(error, (SessionError, OSError)):
        return report(str(error))
    return None


def report_command_error(error: BridgeCommandError) -> int:
    message = error.message
    if error.type not in BRIDGE_ERROR_TYPES:
        message = f"{error.type}: {message}"
    # A timed-out command keeps running in Maya, so the caller must wait, not resend.
    code = EXIT_RETRY if error.type == "Timeout" else EXIT_ERROR
    report(message, error.hint, code)
    if error.stdout:
        print("printed before the error:", file=sys.stderr)
        print(error.stdout.rstrip("\n"), file=sys.stderr)
    return code


def label_of(args: argparse.Namespace) -> str:
    return sanitize(getattr(args, "client", "") or "") or client_label()


def manager_for(args: argparse.Namespace, autostart: bool = True) -> SessionManager:
    home = bmaya_home()
    label = label_of(args)
    store = StateStore(home, label)
    store.prune()
    launcher = None
    mode = autostart_mode() if autostart else OFF
    if mode != OFF:
        launcher = Launcher(home, mode)
    return SessionManager(
        home,
        label,
        launcher=launcher,
        store=store,
        override=getattr(args, "session", "") or "",
    )


def call(args: argparse.Namespace, command: str, params: dict[str, Any]) -> dict[str, Any]:
    manager = manager_for(args)
    try:
        return manager.call(command, params)
    finally:
        started = manager.autostarted
        if started is not None:
            # Nothing stops this Maya by itself, so whoever caused it has to be told.
            print(
                f"note: no Maya was running, so session {started.id} was started without a "
                "window. It runs until `bmaya stop`.",
                file=sys.stderr,
            )


def forward(args: argparse.Namespace, command: str, params: dict[str, Any]) -> int:
    emit(call(args, command, params))
    return EXIT_OK


def absolute(path: str) -> str:
    """Maya has its own working directory, so a relative path must be resolved here."""
    return str(Path(path).expanduser().resolve()).replace("\\", "/")


def cmd_scene(args: argparse.Namespace) -> int:
    params = {"query": args.query, "node_type": args.type, "limit": args.limit}
    return forward(args, "get_scene_info", params)


def cmd_object(args: argparse.Namespace) -> int:
    return forward(args, "get_object_info", {"name": args.name})


def read_code(args: argparse.Namespace) -> str:
    if args.file and args.code:
        raise CommandFailed("Pass the code or -f FILE, not both.", code=EXIT_USAGE)
    if args.file:
        try:
            return Path(args.file).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise CommandFailed(f"Cannot read {args.file}: {exc}") from exc
    if args.code and args.code != "-":
        return args.code
    if sys.stdin is None or sys.stdin.isatty():
        raise CommandFailed(
            "No code to run.",
            hint="Pass it as an argument, with -f FILE, or pipe it in: bmaya exec < script.py",
            code=EXIT_USAGE,
        )
    return sys.stdin.read().lstrip("﻿")


def cmd_exec(args: argparse.Namespace) -> int:
    return forward(args, "execute_maya_code", {"code": read_code(args)})


def shot_target(output: str) -> Path:
    if output:
        return Path(absolute(output))
    folder = bmaya_home() / SHOTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    old = sorted(folder.glob("*.png"), key=lambda path: path.stat().st_mtime)
    for path in old[: max(0, len(old) - SHOTS_KEPT + 1)]:
        path.unlink(missing_ok=True)
    return folder / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.png"


def cmd_shot(args: argparse.Namespace) -> int:
    params = {
        "max_size": args.size,
        "camera": args.camera,
        "frames": args.frames,
        "clip": args.clip,
        "aspect": args.aspect,
        "light": args.light,
    }
    reply = call(args, "get_viewport_screenshot", params)
    target = shot_target(args.output)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(reply["path"], target)
    except OSError as exc:
        raise CommandFailed(
            f"The screenshot was taken but could not be written to {target}: {exc}",
            hint=f"It is still in {reply['path']}.",
        ) from exc
    reply["path"] = target.as_posix()
    emit(reply)
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    return forward(args, "audit_mesh", {"names": ",".join(args.names)})


def cmd_anim(args: argparse.Namespace) -> int:
    return forward(args, "get_animation_info", {"name": args.name})


def cmd_motion(args: argparse.Namespace) -> int:
    params = {
        "names": ",".join(args.names),
        "frames": args.frames,
        "clip": args.clip,
        "loop": args.loop,
        "contacts": args.contacts,
        "ground": args.ground,
    }
    return forward(args, "get_motion", params)


def cmd_export(args: argparse.Namespace) -> int:
    params = {
        "path": absolute(args.path),
        "preset": args.preset,
        "selection_only": args.selection,
        "animation": args.animation,
        "start": args.start,
        "end": args.end,
        "clip": args.clip,
    }
    return forward(args, "export_fbx", params)


def cmd_file(args: argparse.Namespace) -> int:
    params = {
        "action": args.action,
        "path": absolute(args.path) if args.path else "",
        "force": args.force,
    }
    return forward(args, "scene_file", params)


def cmd_undo(args: argparse.Namespace) -> int:
    return forward(args, "undo", {"steps": args.steps, "force": args.force})


def cmd_sessions(args: argparse.Namespace) -> int:
    manager = manager_for(args, autostart=False)
    listing = manager.list_sessions()
    listing["client"] = manager.label
    emit(listing)
    return EXIT_OK


def cmd_use(args: argparse.Namespace) -> int:
    session = manager_for(args, autostart=False).use_session(args.id)
    emit(session.to_dict(current=True))
    return EXIT_OK


def cmd_start(args: argparse.Namespace) -> int:
    scene = ""
    if args.scene:
        scene = absolute(args.scene)
        if not Path(scene).is_file():
            raise CommandFailed(f"Scene file not found: {scene}")
    home = bmaya_home()
    label = label_of(args)
    launcher = Launcher(home, GUI if args.gui else HEADLESS, scene=scene)
    manager = SessionManager(home, label, launcher=launcher, store=StateStore(home, label))
    session = manager.start(START_WAIT)
    emit(session.to_dict(current=True))
    return EXIT_OK


def cmd_stop(args: argparse.Namespace) -> int:
    manager = manager_for(args, autostart=False)
    if args.id:
        manager.override = args.id.strip()
    session, reply = manager.call_session("stop", {"force": args.force})
    deadline = time.monotonic() + STOP_WAIT
    pid = int(session.id)
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(STOP_POLL)
    manager.forget(session.id)
    emit({"stopped": session.id, "scene": reply.get("scene", ""), "exited": not pid_alive(pid)})
    return EXIT_OK


def cmd_guide(args: argparse.Namespace) -> int:
    text = Path(__file__).with_name("guide.md").read_text(encoding="utf-8")
    sys.stdout.write(text)
    return EXIT_OK
