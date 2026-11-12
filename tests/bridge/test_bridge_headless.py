from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from bmaya_bridge import lifecycle
from bmaya_bridge.headless import MainLoop
from bmaya_bridge.journal import Journal
from bmaya_bridge.server import BridgeServer

from .conftest import Client, UndoRecorder


@pytest.fixture
def no_exit_hook() -> Iterator[None]:
    yield
    lifecycle.set_exit(None)


def test_loop_runs_callbacks_on_the_thread_that_runs_it() -> None:
    loop = MainLoop(poll=0.01)
    ran: list[str] = []

    def work() -> None:
        ran.append(threading.current_thread().name)
        loop.stop()

    threading.Thread(target=loop.schedule, args=(work,), name="socket").start()
    loop.run()
    assert ran == [threading.current_thread().name]


def test_loop_survives_a_failing_callback() -> None:
    loop = MainLoop(poll=0.01)
    ran: list[str] = []

    def fail() -> None:
        raise RuntimeError("boom")

    def finish() -> None:
        ran.append("finish")
        loop.stop()

    loop.schedule(fail)
    loop.schedule(finish)
    loop.run()
    assert ran == ["finish"]


def test_a_maya_with_a_window_has_no_exit_hook(no_exit_hook: None) -> None:
    assert not lifecycle.can_exit()
    lifecycle.request_exit()
    stopped: list[bool] = []
    lifecycle.set_exit(lambda: stopped.append(True))
    assert lifecycle.can_exit()
    lifecycle.request_exit()
    assert stopped == [True]


def test_headless_bridge_runs_until_a_command_asks_it_to_stop(
    journal: Journal, no_exit_hook: None
) -> None:
    loop = MainLoop(poll=0.02)
    lifecycle.set_exit(loop.stop)
    threads: list[str] = []

    def where(params: dict[str, Any]) -> dict[str, Any]:
        threads.append(threading.current_thread().name)
        return {"ok": True}

    def stop(params: dict[str, Any]) -> dict[str, Any]:
        lifecycle.request_exit()
        return {"stopped": True}

    bridge = BridgeServer(
        journal,
        commands=lambda: {"where": where, "stop": stop},
        schedule=loop.schedule,
        undo_chunk=UndoRecorder(),
        headless=True,
    )
    bridge.start()
    replies: list[dict[str, Any]] = []

    def agent() -> None:
        for command in ("ping", "where", "stop"):
            client = Client(bridge.port, bridge.token, name="claude-1")
            replies.append(client.call(command))
            client.close()

    worker = threading.Thread(target=agent, name="agent")
    worker.start()
    try:
        loop.run()
        worker.join(5)
    finally:
        bridge.stop()
    assert replies[0]["result"]["headless"] is True
    assert replies[1]["result"] == {"ok": True}
    assert replies[2]["result"] == {"stopped": True}
    assert threads == [threading.current_thread().name]
