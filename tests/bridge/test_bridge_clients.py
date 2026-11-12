from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from bmaya_bridge import clients
from bmaya_bridge.clients import MAYA_UI, ClientTable
from bmaya_bridge.server import BridgeServer

from .conftest import Client, UndoRecorder


def echo(params: dict[str, Any]) -> dict[str, Any]:
    return {"echo": params}


def test_chunk_name_carries_its_owner() -> None:
    name = clients.chunk_name("claude-12", "execute_maya_code")
    assert name == "bmaya claude-12: execute_maya_code"
    assert clients.chunk_owner(name) == "claude-12"


def test_entries_from_the_maya_ui_have_no_owner() -> None:
    assert clients.chunk_owner("polyCube") == ""
    assert clients.chunk_owner("") == ""
    assert clients.chunk_owner(clients.chunk_name("", "execute_maya_code")) == ""


def test_undo_is_allowed_only_on_own_entries() -> None:
    mine = clients.chunk_name("claude-12", "execute_maya_code")
    theirs = clients.chunk_name("codex-7", "execute_maya_code")
    assert clients.undo_blocker(mine, "claude-12") == ""
    assert clients.undo_blocker(theirs, "claude-12") == "codex-7"
    assert clients.undo_blocker("polyCube", "claude-12") == MAYA_UI


def test_selection_changes_from_the_ui_do_not_block_undo() -> None:
    assert clients.is_selection("select -r pCube1 ;", "claude-12")
    assert clients.is_selection("selectionMaskResetAll", "claude-12")
    assert not clients.is_selection("move -r 1 0 0", "claude-12")
    assert not clients.is_selection("", "claude-12")
    assert not clients.is_selection(clients.chunk_name("select-7", "undo"), "claude-12")
    assert not clients.is_selection("select -r pCube1 ;", "")


def test_caller_without_a_label_is_never_blocked() -> None:
    theirs = clients.chunk_name("codex-7", "execute_maya_code")
    assert clients.undo_blocker(theirs, "") == ""
    assert clients.undo_blocker("polyCube", "") == ""


def test_only_connected_labels_that_ran_a_command_are_counted() -> None:
    table = ClientTable()
    table.connected("pinger")
    assert table.count() == 0
    table.connected("worker")
    table.touch("worker")
    assert table.count() == 1
    table.connected("worker")
    assert table.count() == 1
    table.disconnected("worker")
    assert table.count() == 1
    table.disconnected("worker")
    assert table.count() == 0


def test_others_recent_leaves_out_the_caller_and_old_clients() -> None:
    table = ClientTable(recent_seconds=0.2)
    table.touch("old")
    time.sleep(0.3)
    table.touch("first")
    table.touch("second")
    assert [label for label, _ in table.others_recent("first")] == ["second"]
    assert table.others_recent("nobody")[0][1] < 0.2


def test_idle_time_runs_only_while_no_client_is_connected() -> None:
    table = ClientTable()
    table.connected("worker")
    table.touch("worker")
    time.sleep(0.05)
    assert table.idle_seconds() == 0.0
    table.disconnected("worker")
    time.sleep(0.05)
    assert 0.04 < table.idle_seconds() < 1.0


def test_handler_sees_the_acting_client_and_its_neighbours(
    make_bridge: Callable[..., BridgeServer],
) -> None:
    def who(params: dict[str, Any]) -> dict[str, Any]:
        others = [label for label, _ in clients.other_recent_clients()]
        return {"client": clients.acting_client(), "others": others}

    bridge = make_bridge({"who": who})
    first = Client(bridge.port, bridge.token, name="first")
    second = Client(bridge.port, bridge.token, name="second")
    assert first.call("who")["result"] == {"client": "first", "others": []}
    assert second.call("who")["result"] == {"client": "second", "others": ["first"]}
    first.close()
    second.close()
    assert clients.acting_client() == ""


def test_ping_counts_clients_that_ran_commands(make_bridge: Callable[..., BridgeServer]) -> None:
    bridge = make_bridge({"echo": echo})
    first = Client(bridge.port, bridge.token, name="first")
    second = Client(bridge.port, bridge.token, name="second")
    watcher = Client(bridge.port, bridge.token, name="watcher")
    assert watcher.call("ping")["result"]["clients"] == 0
    first.call("echo")
    second.call("echo")
    assert watcher.call("ping")["result"]["clients"] == 2
    assert first.call("ping")["result"]["clients"] == 2
    second.close()
    deadline = time.monotonic() + 5.0
    while watcher.call("ping")["result"]["clients"] != 1:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    first.close()
    watcher.close()


def test_undo_chunk_is_named_after_the_client(make_bridge: Callable[..., BridgeServer]) -> None:
    undo = UndoRecorder()
    bridge = make_bridge({"echo": echo}, undo_chunk=undo)
    client = Client(bridge.port, bridge.token, name="codex-7")
    client.call("echo")
    client.close()
    assert undo.events == ["open bmaya codex-7: echo", "close bmaya codex-7: echo"]
