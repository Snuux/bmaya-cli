from __future__ import annotations

import contextlib
import json
import queue
import socket
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from bmaya_bridge.dispatch import Handler, Scheduler, UndoChunk
from bmaya_bridge.journal import Journal
from bmaya_bridge.server import HOST, BridgeServer, encode_frame, read_frame


class FakeMainThread:
    def __init__(self) -> None:
        self.jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="fake-main", daemon=True)
        self.thread.start()

    def schedule(self, callback: Callable[[], None]) -> None:
        self.jobs.put(callback)

    def close(self) -> None:
        self.jobs.put(None)
        self.thread.join(5)

    def _loop(self) -> None:
        while True:
            callback = self.jobs.get()
            if callback is None:
                return
            callback()


class UndoRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    @contextlib.contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        self.events.append(f"open {name}")
        try:
            yield
        finally:
            self.events.append(f"close {name}")


class Client:
    def __init__(self, port: int, token: str, name: str = "test-client") -> None:
        self.sock = socket.create_connection((HOST, port), timeout=10)
        self.token = token
        self.name = name
        self.next_id = 0

    def call(
        self, command: str, params: dict[str, Any] | None = None, token: str | None = None
    ) -> dict[str, Any]:
        self.next_id += 1
        request = {
            "id": self.next_id,
            "token": self.token if token is None else token,
            "client": self.name,
            "command": command,
            "params": params or {},
        }
        self.sock.sendall(encode_frame(request))
        body = read_frame(self.sock)
        assert body is not None
        return json.loads(body)

    def close(self) -> None:
        self.sock.close()


def read_journal(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BMAYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def journal(home: Path) -> Iterator[Journal]:
    journal = Journal(home / "logs", crash_log=False)
    yield journal
    journal.close()


@pytest.fixture
def main_thread() -> Iterator[FakeMainThread]:
    fake = FakeMainThread()
    yield fake
    fake.close()


@pytest.fixture
def make_bridge(
    journal: Journal, main_thread: FakeMainThread
) -> Iterator[Callable[..., BridgeServer]]:
    bridges: list[BridgeServer] = []

    def make(
        commands: dict[str, Handler],
        timeout: float = 5.0,
        schedule: Scheduler | None = None,
        undo_chunk: UndoChunk | None = None,
    ) -> BridgeServer:
        bridge = BridgeServer(
            journal,
            commands=lambda: commands,
            schedule=schedule or main_thread.schedule,
            undo_chunk=undo_chunk or UndoRecorder(),
            timeout=timeout,
        )
        bridge.start()
        bridges.append(bridge)
        return bridge

    yield make
    for bridge in bridges:
        bridge.stop()
