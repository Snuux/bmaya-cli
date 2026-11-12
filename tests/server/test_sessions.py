from pathlib import Path
from typing import Any

import pytest
from conftest import BridgeFactory
from fake_bridge import DROP, FakeBridge, free_port

from bmaya_cli.client import BridgeConnectionError
from bmaya_cli.sessions import SessionError, SessionManager, bmaya_home


def test_home_follows_environment(home: Path) -> None:
    assert bmaya_home() == home


def test_no_sessions_tells_agent_to_start_maya(manager: SessionManager) -> None:
    with pytest.raises(SessionError, match="No Maya") as info:
        manager.resolve()
    assert "bmaya install" in str(info.value)


def test_single_session_is_used_automatically(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    bridge = make_bridge({"get_scene_info": lambda params: {"total": 3}})
    bridge.write_registry(home, 111)
    assert manager.resolve().id == "111"
    assert manager.call("get_scene_info", {}) == {"total": 3}


def test_several_sessions_require_a_choice(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    first = make_bridge(scene="C:/a/hero.ma")
    second = make_bridge(scene="D:\\b\\prop.ma", maya_version="2026")
    first.write_registry(home, 111)
    second.write_registry(home, 222)
    with pytest.raises(SessionError) as info:
        manager.call("get_scene_info", {})
    message = str(info.value)
    assert "111 (hero.ma, Maya 2027)" in message
    assert "222 (prop.ma, Maya 2026)" in message
    assert "use_session" in message


def test_use_session_wins_over_everything(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    first = make_bridge({"get_scene_info": lambda params: {"who": "a"}})
    second = make_bridge({"get_scene_info": lambda params: {"who": "b"}})
    first.write_registry(home, 111)
    second.write_registry(home, 222)
    manager.use_session("222")
    assert manager.call("get_scene_info", {}) == {"who": "b"}
    listing = manager.list_sessions()
    assert listing["current"] == "222"
    assert [s["current"] for s in listing["sessions"]] == [False, True]


def test_use_session_rejects_unknown_id(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    make_bridge().write_registry(home, 111)
    with pytest.raises(SessionError, match="No live session '999'. Available: 111"):
        manager.use_session("999")


def test_dead_registry_file_is_pruned(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    make_bridge().write_registry(home, 111)
    stale = FakeBridge(port=free_port()).write_registry(home, 222)
    assert [s.id for s in manager.discover()] == ["111"]
    assert not stale.exists()
    assert (home / "sessions" / "111.json").exists()


def test_busy_but_alive_session_is_kept(home: Path, make_bridge: BridgeFactory) -> None:
    manager = SessionManager(home, label="test-1", ping_timeout=0.3)
    bridge = make_bridge()
    bridge.silent = True
    path = bridge.write_registry(home, 111)
    sessions = manager.discover()
    assert [s.id for s in sessions] == ["111"]
    assert sessions[0].busy
    assert path.exists()


def test_unreadable_registry_file_is_ignored(manager: SessionManager, home: Path) -> None:
    sessions_dir = home / "sessions"
    sessions_dir.mkdir()
    broken = sessions_dir / "5.json"
    broken.write_text("{not json", encoding="utf-8")
    assert manager.discover() == []
    assert broken.exists()


def test_chosen_session_that_vanished_is_reported(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    bridge = make_bridge({"get_scene_info": lambda params: {}})
    bridge.write_registry(home, 111)
    manager.use_session("111")
    manager.call("get_scene_info", {})
    bridge.stop()
    with pytest.raises(SessionError, match="exited or crashed") as info:
        manager.call("get_scene_info", {})
    assert "get_scene_info" in str(info.value)
    assert manager.current is None


def test_session_dying_mid_call_names_command_and_journal(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    bridge = make_bridge()

    def crash(params: dict[str, Any]) -> dict[str, Any]:
        bridge.stop()
        return DROP

    bridge.handlers["execute_maya_code"] = crash
    path = bridge.write_registry(home, 4321)
    with pytest.raises(SessionError) as info:
        manager.call("execute_maya_code", {"code": "boom()"})
    message = str(info.value)
    assert "exited or crashed" in message
    assert "execute_maya_code" in message
    assert str(home / "logs" / "*-4321.log") in message
    assert "boom()" not in message
    assert not path.exists()


def test_lost_session_names_the_real_journal_and_crash_log(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    logs = home / "logs"
    logs.mkdir()
    (logs / "20261112-090000-4321.log").write_text("", encoding="utf-8")
    journal = logs / "20261112-100000-4321.log"
    journal.write_text("", encoding="utf-8")
    crash_log = logs / "20261112-100000-4321.crash.log"
    crash_log.write_text("Fatal Python error: Segmentation fault", encoding="utf-8")
    bridge = make_bridge({"get_scene_info": lambda params: {}})
    bridge.write_registry(home, 4321)
    manager.call("get_scene_info", {})
    bridge.stop()
    with pytest.raises(SessionError) as info:
        manager.call("get_scene_info", {})
    message = str(info.value)
    assert f"Its journal is {journal} and the crash log is {crash_log}." in message
    assert "last command sent to it was get_scene_info" in message


def test_connection_drop_with_live_maya_is_not_called_a_crash(
    manager: SessionManager, home: Path, make_bridge: BridgeFactory
) -> None:
    bridge = make_bridge({"undo": lambda params: DROP})
    bridge.write_registry(home, 111)
    with pytest.raises(BridgeConnectionError, match="still running"):
        manager.call("undo", {"steps": 1})
    assert (home / "sessions" / "111.json").exists()
