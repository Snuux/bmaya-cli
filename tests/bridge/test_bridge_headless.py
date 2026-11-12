from __future__ import annotations

import threading
from typing import Any

from bmaya_bridge.headless import IDLE, STOPPED, MainLoop
from bmaya_bridge.journal import Journal
from bmaya_bridge.server import BridgeServer

from .conftest import Client, UndoRecorder


def test_loop_runs_callbacks_on_the_thread_that_runs_it() -> None:
    loop = MainLoop(poll=0.01)
    ran: list[str] = []

    def work() -> None:
        ran.append(threading.current_thread().name)
        loop.stop()

    threading.Thread(target=loop.schedule, args=(work,), name="socket").start()
    assert loop.run(lambda: 0.0) == STOPPED
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
    assert loop.run(lambda: 0.0) == STOPPED
    assert ran == ["finish"]


def test_loop_without_idle_exit_ignores_idle_time() -> None:
    loop = MainLoop(idle_exit=0.0, poll=0.01)
    threading.Timer(0.1, loop.stop).start()
    assert loop.run(lambda: 1000.0) == STOPPED


def test_headless_bridge_serves_commands_then_exits_when_everyone_left(
    journal: Journal,
) -> None:
    loop = MainLoop(idle_exit=0.2, poll=0.02)
    threads: list[str] = []

    def where(params: dict[str, Any]) -> dict[str, Any]:
        threads.append(threading.current_thread().name)
        return {"ok": True}

    bridge = BridgeServer(
        journal,
        commands=lambda: {"where": where},
        schedule=loop.schedule,
        undo_chunk=UndoRecorder(),
        headless=True,
    )
    bridge.start()
    replies: list[dict[str, Any]] = []

    def agent() -> None:
        client = Client(bridge.port, bridge.token, name="claude-1")
        replies.append(client.call("ping"))
        replies.append(client.call("where"))
        client.close()

    worker = threading.Thread(target=agent, name="agent")
    worker.start()
    try:
        assert loop.run(bridge.clients.idle_seconds) == IDLE
    finally:
        worker.join(5)
        bridge.stop()
    assert replies[0]["result"]["headless"] is True
    assert replies[1]["result"] == {"ok": True}
    assert threads == [threading.current_thread().name]
