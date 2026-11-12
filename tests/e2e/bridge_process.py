"""The real bmaya bridge with fake commands, run as its own process by the end-to-end tests.

Prints "READY <port> <pid>" once listening and stops cleanly when stdin closes.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import queue
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

from bmaya_bridge import registry
from bmaya_bridge.errors import BridgeError, attach_stdout
from bmaya_bridge.journal import Journal
from bmaya_bridge.server import BridgeServer


class MainThread:
    """Stands in for Maya's main thread: runs scheduled callables one at a time."""

    def __init__(self) -> None:
        self.jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        threading.Thread(target=self._loop, name="fake-main", daemon=True).start()

    def schedule(self, callback: Callable[[], None]) -> None:
        self.jobs.put(callback)

    def _loop(self) -> None:
        while True:
            self.jobs.get()()


@contextlib.contextmanager
def no_undo_chunk(name: str) -> Iterator[None]:
    yield


def execute_maya_code(params: dict[str, Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "__agent__"}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(params.get("code") or "", "<agent>", "exec"), namespace)
    except BaseException as exc:
        attach_stdout(exc, out.getvalue())
        raise
    return {"stdout": out.getvalue(), "result": namespace.get("result"), "truncated": False}


def make_commands(scene: str) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def get_scene_info(params: dict[str, Any]) -> dict[str, Any]:
        return {"file": scene, "nodes": [], "shown": 0, "total": 0}

    def get_object_info(params: dict[str, Any]) -> dict[str, Any]:
        raise BridgeError(
            f"No object named '{params.get('name')}' in the scene.",
            hint="Use get_scene_info to find similar names.",
        )

    def undo(params: dict[str, Any]) -> dict[str, Any]:
        return {"undone": 0, "queue_empty": True}

    return {
        "get_scene_info": get_scene_info,
        "get_object_info": get_object_info,
        "execute_maya_code": execute_maya_code,
        "undo": undo,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", default="")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    commands = make_commands(args.scene)
    journal = Journal(registry.logs_dir())
    bridge = BridgeServer(
        journal,
        commands=lambda: commands,
        schedule=MainThread().schedule,
        undo_chunk=no_undo_chunk,
        timeout=args.timeout,
        maya_version="2027",
        scene=args.scene,
        no_undo_chunk=lambda: {"undo"},
    )
    port = bridge.start()
    print(f"READY {port} {bridge.pid}", flush=True)
    sys.stdin.read()
    bridge.stop()
    journal.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
