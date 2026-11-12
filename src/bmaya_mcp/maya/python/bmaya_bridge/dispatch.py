from __future__ import annotations

import collections
import contextlib
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

from bmaya_bridge.errors import BridgeError, error_payload
from bmaya_bridge.journal import Journal

DEFAULT_TIMEOUT = 50.0

Handler = Callable[[dict[str, Any]], dict[str, Any]]
Scheduler = Callable[[Callable[[], None]], None]
UndoChunk = Callable[[str], AbstractContextManager[Any]]


def maya_schedule(callback: Callable[[], None]) -> None:
    import maya.utils

    maya.utils.executeDeferred(callback)


@contextlib.contextmanager
def maya_undo_chunk(name: str) -> Iterator[None]:
    from maya import cmds

    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


class Job:
    def __init__(
        self,
        request_id: object,
        client: str,
        command: str,
        handler: Handler,
        params: dict[str, Any],
    ) -> None:
        self.request_id = request_id
        self.client = client
        self.command = command
        self.handler = handler
        self.params = params
        self.done = threading.Event()
        self.ok = False
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None


class Dispatcher:
    def __init__(
        self,
        journal: Journal,
        schedule: Scheduler = maya_schedule,
        undo_chunk: UndoChunk = maya_undo_chunk,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.journal = journal
        self.timeout = timeout
        self._schedule = schedule
        self._undo_chunk = undo_chunk
        self._queue: collections.deque[Job] = collections.deque()
        self._queue_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._running: Job | None = None

    @property
    def busy(self) -> bool:
        return self._running is not None

    def submit(
        self,
        request_id: object,
        client: str,
        command: str,
        handler: Handler,
        params: dict[str, Any],
    ) -> Job:
        """Queue a command for the main thread and wait for it, at most `timeout` seconds.

        A job that outlives the wait keeps running and still journals its end;
        the returned job then carries the Timeout error instead of its result.
        """
        job = Job(request_id, client, command, handler, params)
        with self._queue_lock:
            self._queue.append(job)
        self._schedule(self._run_next)
        if job.done.wait(self.timeout):
            return job
        self.journal.event("timeout", id=request_id, command=command, seconds=self.timeout)
        late = Job(request_id, client, command, handler, params)
        late.error = {
            "type": "Timeout",
            "message": (
                f"'{command}' is still running in Maya after {self.timeout:g} seconds. "
                "Its result will not be returned."
            ),
            "hint": "Ping the session until busy is false, then inspect the scene before retrying.",
        }
        return late

    def _run_next(self) -> None:
        with self._queue_lock:
            if not self._queue:
                return
            job = self._queue.popleft()
        with self._run_lock:
            self._run(job)

    def _run(self, job: Job) -> None:
        self._running = job
        started = time.perf_counter()
        details = ""
        try:
            self.journal.start(job.request_id, job.client, job.command, job.params)
            with self._undo_chunk(f"bmaya-mcp {job.command}"):
                job.result = job.handler(job.params)
            job.ok = True
        except BridgeError as exc:
            job.error = error_payload(exc)
        except BaseException as exc:
            job.error = error_payload(exc)
            details = traceback.format_exc()
            print(f"bmaya-mcp: {job.command} failed\n{details}", file=sys.stderr)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            try:
                self.journal.end(job.request_id, elapsed, job.ok, job.error, details)
            finally:
                self._running = None
                job.done.set()
