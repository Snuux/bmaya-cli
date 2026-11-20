from __future__ import annotations

import collections
import contextlib
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from bmaya_bridge import clients
from bmaya_bridge.clients import ClientTable
from bmaya_bridge.errors import TIMEOUT, BridgeError, error_payload
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
        undo_chunk: bool = True,
    ) -> None:
        self.request_id = request_id
        self.client = client
        self.command = command
        self.handler = handler
        self.params = params
        self.undo_chunk = undo_chunk
        self.started = ""
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
        client_table: ClientTable | None = None,
    ) -> None:
        self.journal = journal
        self.timeout = timeout
        self.clients = client_table or ClientTable()
        self._schedule = schedule
        self._undo_chunk = undo_chunk
        self._queue: collections.deque[Job] = collections.deque()
        self._queue_lock = threading.Lock()
        self._run_lock = threading.RLock()
        self._running: Job | None = None

    @property
    def busy(self) -> bool:
        return self._running is not None or bool(self._queue)

    @property
    def running(self) -> Job | None:
        """The job on the main thread now; handlers and Maya callbacks run inside it."""
        return self._running

    def submit(
        self,
        request_id: object,
        client: str,
        command: str,
        handler: Handler,
        params: dict[str, Any],
        undo_chunk: bool = True,
    ) -> Job:
        job = Job(request_id, client, command, handler, params, undo_chunk)
        with self._queue_lock:
            self._queue.append(job)
        self._schedule(self._run_next)
        if job.done.wait(self.timeout):
            return job
        self.journal.event("timeout", id=request_id, command=command, seconds=self.timeout)
        late = Job(request_id, client, command, handler, params)
        late.error = {
            "type": TIMEOUT,
            "message": (
                f"'{command}' is still running in Maya after {self.timeout:g} seconds. "
                "Its result will not be returned."
            ),
            "hint": "It was not cancelled, so do not send it again. `bmaya sessions` shows busy "
            "until it ends; then inspect the scene.",
        }
        return late

    def _run_next(self) -> None:
        with self._run_lock:
            if self._running is not None:
                # Re-entered by a handler pumping Maya's idle queue; the outer call keeps draining.
                return
            while True:
                with self._queue_lock:
                    if not self._queue:
                        return
                    job = self._queue.popleft()
                self._run(job)

    def _run(self, job: Job) -> None:
        self._running = job
        job.started = datetime.now().isoformat(timespec="milliseconds")
        started = time.perf_counter()
        details = ""
        try:
            self.journal.start(job.request_id, job.client, job.command, job.params)
            with clients.acting(job.client, self.clients):
                if job.undo_chunk:
                    with self._undo_chunk(clients.chunk_name(job.client, job.command)):
                        job.result = job.handler(job.params)
                else:
                    job.result = job.handler(job.params)
            job.ok = True
        except BridgeError as exc:
            job.error = error_payload(exc)
        except BaseException as exc:
            job.error = error_payload(exc)
            details = traceback.format_exc()
            print(f"bmaya: {job.command} failed\n{details}", file=sys.stderr)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            try:
                self.journal.end(job.request_id, elapsed, job.ok, job.error, details)
            finally:
                self._running = None
                job.done.set()
