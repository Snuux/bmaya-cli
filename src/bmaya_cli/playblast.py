import argparse
import os
import shutil
import subprocess
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
    emit,
    manager_for,
    note_autostart,
)
from bmaya_cli.sessions import bmaya_home

FIRST_CHUNK = 12
CHUNK_MIN = 4
CHUNK_MAX = 120
CHUNK_BUDGET_MS = 20000
FFMPEG_SECONDS = 600
WORK_DIR = "playblast"
WORK_KEPT = 5
GIF_FILTER = (
    "split[a][b];[a]palettegen=stats_mode=diff[p];"
    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
)


def output_kind(target: Path) -> str:
    suffix = target.suffix.lower()
    if suffix in (".gif", ".mp4"):
        return suffix[1:]
    if not suffix or target.is_dir():
        return "png"
    raise CommandFailed(
        f"bmaya playblast writes a .gif, an .mp4 or a folder of PNGs, not {suffix}.",
        hint="Name the output like walk.gif, walk.mp4 or walk_frames.",
        code=EXIT_USAGE,
    )


def fresh_folder(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("frame_*.png"):
        old.unlink(missing_ok=True)
    return folder


def work_folder(target: Path) -> Path:
    """A folder for frames that only live until ffmpeg has joined them."""
    root = bmaya_home() / WORK_DIR
    root.mkdir(parents=True, exist_ok=True)
    old = sorted((path for path in root.iterdir() if path.is_dir()), key=os.path.getmtime)
    for stale in old[: max(0, len(old) - WORK_KEPT + 1)]:
        shutil.rmtree(stale, ignore_errors=True)
    return fresh_folder(root / f"{target.stem}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")


def next_count(ms_per_frame: float) -> int:
    return max(CHUNK_MIN, min(CHUNK_MAX, int(CHUNK_BUDGET_MS / max(ms_per_frame, 1.0))))


def ffmpeg_command(ffmpeg: str, folder: Path, target: Path, kind: str, rate: float) -> list[str]:
    if kind == "gif":
        encoding = ["-vf", GIF_FILTER, "-loop", "0"]
    else:
        encoding = [
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
        ]
    source = str(folder / "frame_%05d.png")
    head = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", f"{rate:g}"]
    return [*head, "-i", source, *encoding, str(target)]


def encode(command: list[str], folder: Path) -> None:
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=FFMPEG_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandFailed(
            "ffmpeg did not finish in 10 minutes.", hint=f"The frames are in {folder.as_posix()}."
        ) from exc
    if done.returncode != 0:
        lines = done.stderr.strip().splitlines() or ["no message"]
        raise CommandFailed(
            f"ffmpeg failed: {lines[-1]}", hint=f"The frames are in {folder.as_posix()}."
        )


def still_rendering(folder: Path, done: int) -> CommandFailed:
    return CommandFailed(
        f"Maya is still rendering the frames after the first {done}; those are in "
        f"{folder.as_posix()}.",
        hint="Do not resend. Wait until `bmaya sessions` shows the session idle, then run "
        "again with a smaller --size or a shorter range.",
        code=EXIT_RETRY,
    )


def render_all(args: argparse.Namespace, folder: Path) -> tuple[dict[str, Any], int]:
    params: dict[str, Any] = {
        "frames": args.frames,
        "clip": args.clip,
        "camera": args.camera,
        "max_size": args.size,
        "aspect": args.aspect,
        "light": args.light,
        "trail": args.trail,
        "fit": args.fit,
        "folder": folder.as_posix(),
        "offset": 0,
        "count": FIRST_CHUNK,
    }
    manager = manager_for(args)
    calls = 0
    try:
        while True:
            session, reply = manager.call_session("render_frames", params)
            manager.override = session.id
            calls += 1
            params["offset"] += reply["rendered"]
            if params["offset"] >= reply["total"]:
                return reply, calls
            params["count"] = next_count(reply["ms_per_frame"])
    except BridgeCommandError as error:
        if error.type == "Timeout":
            raise still_rendering(folder, params["offset"]) from error
        raise
    except BridgeConnectionError as error:
        if error.timed_out:
            raise still_rendering(folder, params["offset"]) from error
        raise
    finally:
        note_autostart(manager)


def cmd_playblast(args: argparse.Namespace) -> int:
    target = Path(absolute(args.output))
    kind = output_kind(target)
    ffmpeg = shutil.which("ffmpeg") if kind != "png" else None
    if kind == "png":
        folder = fresh_folder(target)
    elif ffmpeg:
        folder = work_folder(target)
    else:
        folder = fresh_folder(target.with_name(target.stem + "_frames"))

    started = time.monotonic()
    reply, calls = render_all(args, folder)
    rate = reply["fps"] / (reply["step"] or 1)
    result: dict[str, Any] = {"path": folder.as_posix(), "format": "png"}
    if ffmpeg:
        target.parent.mkdir(parents=True, exist_ok=True)
        encode(ffmpeg_command(ffmpeg, folder, target, kind, rate), folder)
        shutil.rmtree(folder, ignore_errors=True)
        result = {"path": target.as_posix(), "format": kind}
    result.update(
        {
            "frames": reply["total"],
            "range": reply["range"],
            "fps": round(rate, 3),
            "width": reply["width"],
            "height": reply["height"],
            "calls": calls,
            "seconds": round(time.monotonic() - started, 1),
        }
    )
    if reply.get("clip"):
        result["clip"] = reply["clip"]
    if kind != "png" and not ffmpeg:
        result["encoded"] = False
        result["note"] = (
            "ffmpeg is not on PATH, so the frames were kept as PNGs. Install ffmpeg and run "
            f"the same command again to get {target.as_posix()}."
        )
    emit(result)
    return EXIT_OK
