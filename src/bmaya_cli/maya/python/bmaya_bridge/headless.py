from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable

from bmaya_bridge import lifecycle, server

POLL_SECONDS = 1.0
REPLY_GRACE_SECONDS = 0.5


class MainLoop:
    """Runs bridge commands on the calling thread; batch Maya has no idle queue to do it."""

    def __init__(self, poll: float = POLL_SECONDS) -> None:
        self.poll = poll
        self._jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        self._stopping = threading.Event()

    def schedule(self, callback: Callable[[], None]) -> None:
        self._jobs.put(callback)

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                callback = self._jobs.get(timeout=self.poll)
            except queue.Empty:
                continue
            try:
                callback()
            except Exception:
                traceback.print_exc()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bmaya_bridge.headless")
    parser.add_argument("--scene", default="")
    args = parser.parse_args(argv)

    import maya.standalone

    maya.standalone.initialize(name="python")
    from maya import cmds

    cmds.undoInfo(state=True)
    if args.scene:
        cmds.file(args.scene, open=True, force=True, ignoreVersion=True)

    loop = MainLoop()
    lifecycle.set_exit(loop.stop)
    port = server.start(schedule=loop.schedule, headless=True)
    print(f"bmaya headless bridge listening on {server.HOST}:{port} (session {os.getpid()})")
    sys.stdout.flush()
    try:
        loop.run()
        # The stop command ends the loop from inside its own handler; let its reply leave first.
        time.sleep(REPLY_GRACE_SECONDS)
    except KeyboardInterrupt:
        pass
    server.stop()
    print("bmaya headless bridge stopped")
    sys.stdout.flush()
    maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
