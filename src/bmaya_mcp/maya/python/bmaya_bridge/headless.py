from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import traceback
from collections.abc import Callable

from bmaya_bridge import server

POLL_SECONDS = 1.0
IDLE = "idle"
STOPPED = "stopped"


class MainLoop:
    """Runs bridge commands on the calling thread; batch Maya has no idle queue to do it."""

    def __init__(self, idle_exit: float = 0.0, poll: float = POLL_SECONDS) -> None:
        self.idle_exit = idle_exit
        self.poll = poll
        self._jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        self._stopping = threading.Event()

    def schedule(self, callback: Callable[[], None]) -> None:
        self._jobs.put(callback)

    def stop(self) -> None:
        self._stopping.set()

    def run(self, idle_seconds: Callable[[], float]) -> str:
        while not self._stopping.is_set():
            try:
                callback = self._jobs.get(timeout=self.poll)
            except queue.Empty:
                if self.idle_exit > 0 and idle_seconds() >= self.idle_exit:
                    return IDLE
                continue
            try:
                callback()
            except Exception:
                traceback.print_exc()
        return STOPPED


def idle_seconds() -> float:
    bridge = server.current()
    return bridge.clients.idle_seconds() if bridge is not None else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bmaya_bridge.headless")
    parser.add_argument("--idle-exit", type=float, default=0.0)
    parser.add_argument("--scene", default="")
    args = parser.parse_args(argv)

    import maya.standalone

    maya.standalone.initialize(name="python")
    from maya import cmds

    cmds.undoInfo(state=True)
    if args.scene:
        cmds.file(args.scene, open=True, force=True, ignoreVersion=True)

    loop = MainLoop(args.idle_exit)
    port = server.start(schedule=loop.schedule, headless=True)
    print(f"bmaya-mcp headless bridge listening on {server.HOST}:{port} (session {os.getpid()})")
    sys.stdout.flush()
    try:
        reason = loop.run(idle_seconds)
    except KeyboardInterrupt:
        reason = STOPPED
    bridge = server.current()
    if reason == IDLE and bridge is not None:
        bridge.journal.event("idle_exit", seconds=args.idle_exit)
        print(f"bmaya-mcp: no client for {args.idle_exit:g} seconds, exiting")
    server.stop()
    sys.stdout.flush()
    maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
