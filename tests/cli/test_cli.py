import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from bmaya_bridge.journal import Journal
from bmaya_bridge.server import BridgeServer

from bmaya_cli import cli
from bmaya_cli.cli import build_parser, main
from bmaya_cli.launch import MayaInstall


@pytest.fixture
def live_bridge(home: Path) -> Iterator[BridgeServer]:
    bridge_journal = Journal(home / "logs", crash_log=False)
    bridge = BridgeServer(bridge_journal, commands=dict, maya_version="2027", scene="C:/a.ma")
    bridge.start()
    yield bridge
    bridge.stop()
    bridge_journal.close()


def test_no_command_means_serve() -> None:
    assert build_parser().parse_args([]).command is None
    assert build_parser().parse_args(["serve"]).command == "serve"


def test_command_flags_parse() -> None:
    parser = build_parser()
    install = parser.parse_args(["install", "--maya-app-dir", "D:/maya"])
    assert install.maya_app_dir == Path("D:/maya")
    logs = parser.parse_args(["logs", "--session", "12", "--tail", "5", "--crashes"])
    assert (logs.session, logs.tail, logs.crashes) == (12, 5, True)
    defaults = parser.parse_args(["logs"])
    assert (defaults.session, defaults.tail, defaults.crashes) == (None, 20, False)
    demo = parser.parse_args(["demo"])
    assert demo.out == Path("demo")
    assert parser.parse_args(["demo", "--out", "x"]).out == Path("x")
    headless = parser.parse_args(["headless"])
    assert (headless.maya, headless.scene, headless.idle_exit) == ("", "", 0)
    custom = parser.parse_args(["headless", "--maya", "D:/Maya2027", "--idle-exit", "60"])
    assert (custom.maya, custom.idle_exit) == ("D:/Maya2027", 60)


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["reinstall"])


def test_install_and_uninstall_report_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app_dir = tmp_path / "maya"
    assert main(["install", "--maya-app-dir", str(app_dir)]) == 0
    out = capsys.readouterr().out
    assert str(app_dir / "modules" / "bmaya.mod") in out
    assert "restart Maya" in out
    assert main(["uninstall", "--maya-app-dir", str(app_dir)]) == 0
    assert "Removed" in capsys.readouterr().out
    assert main(["uninstall", "--maya-app-dir", str(app_dir)]) == 0
    assert "not installed" in capsys.readouterr().out


def test_logs_shows_newest_session_tail(
    home: Path, logs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["logs", "--tail", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Session 333: ")
    assert out[0].endswith("(1 of 1 records)")
    assert out[1] == "  10:00:00.000 bridge_start pid=333"


def test_logs_for_one_session(home: Path, logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["logs", "--session", "111", "--tail", "2"]) == 0
    out = capsys.readouterr().out
    assert "end #1 ok in 3.2 ms" in out
    assert "start #2 execute_maya_code by claude-1" in out
    assert main(["logs", "--session", "999"]) == 1


def test_logs_crashes_lists_unfinished_command_and_crash_log(
    home: Path, logs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["logs", "--crashes"]) == 0
    out = capsys.readouterr().out
    assert "Session 111 crashed after 2026-11-12T10:00:00.000" in out
    assert "execute_maya_code (request 2) started" in out
    assert "        import maya.cmds as cmds" in out
    assert "Fatal Python error: Segmentation fault" in out
    assert "Session 444 crashed" in out
    assert "no command was running" in out
    assert "222" not in out
    assert "333" not in out


def test_doctor_without_maya_fails(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor", "--maya-app-dir", str(tmp_path / "maya")]) == 1
    out = capsys.readouterr().out
    assert "Module: not installed" in out
    assert "No live Maya session" in out
    assert f"Logs: {home / 'logs'}" in out


def test_doctor_pings_live_sessions_and_drops_stale_ones(
    home: Path,
    tmp_path: Path,
    live_bridge: BridgeServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stale = home / "sessions" / "4242.json"
    stale.write_text(json.dumps({"pid": 4242, "port": 1, "token": "x"}), encoding="utf-8")
    monkeypatch.setattr(cli, "pid_alive", lambda pid: pid == live_bridge.pid)
    app_dir = tmp_path / "maya"
    main(["install", "--maya-app-dir", str(app_dir)])
    capsys.readouterr()
    assert main(["doctor", "--maya-app-dir", str(app_dir)]) == 0
    out = capsys.readouterr().out
    assert "Module: installed, version" in out
    assert f"{live_bridge.pid}: Maya 2027, C:/a.ma, idle, 0 clients, ping" in out
    assert "Autostart: " in out
    assert "4242: stale entry" in out
    assert not stale.exists()


def test_doctor_reports_version_mismatch_and_last_crash(
    home: Path, logs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app_dir = tmp_path / "maya"
    main(["install", "--maya-app-dir", str(app_dir)])
    mod_file = app_dir / "modules" / "bmaya.mod"
    mod_file.write_text("+ bmaya 0.0.1 C:/old\n", encoding="utf-8")
    capsys.readouterr()
    main(["doctor", "--maya-app-dir", str(app_dir)])
    out = capsys.readouterr().out
    assert "version 0.0.1 is installed" in out
    assert "Last crash: session 111" in out
    assert "while running execute_maya_code" in out


def test_demo_without_maya_explains_what_to_do(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 1
    out = capsys.readouterr().out
    assert "Cannot run the demo: No Maya" in out
    assert "bmaya doctor" in out


def test_headless_without_a_maya_install_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "find_maya", lambda override="": None)
    assert main(["headless", "--maya", "D:/nowhere"]) == 1
    assert "No Maya installation found in D:/nowhere" in capsys.readouterr().err


def test_headless_runs_the_found_maya(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    found = MayaInstall(Path("D:/Maya2027/bin"), "2027")
    runs: list[tuple[MayaInstall, int, str]] = []

    def fake_run(install: MayaInstall, idle_exit: int, scene: str) -> int:
        runs.append((install, idle_exit, scene))
        return 0

    monkeypatch.setattr(cli, "find_maya", lambda override="": found)
    monkeypatch.setattr(cli, "run_headless", fake_run)
    assert main(["headless", "--scene", "C:/a.ma", "--idle-exit", "30"]) == 0
    assert runs == [(found, 30, "C:/a.ma")]
    assert "Starting Maya 2027 without a UI" in capsys.readouterr().out
