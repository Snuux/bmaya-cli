import json
import os
import time
from pathlib import Path
from typing import IO

import pytest
from conftest import BridgeFactory
from fake_bridge import FakeBridge

from bmaya_cli import launch
from bmaya_cli.install import install
from bmaya_cli.launch import Launch, Launcher, LaunchError, MayaInstall, find_maya
from bmaya_cli.sessions import SessionError, SessionManager


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def make_install(root: Path, name: str) -> Path:
    bin_dir = root / name / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / launch.executable("mayapy")).write_text("", encoding="utf-8")
    (bin_dir / launch.executable("maya")).write_text("", encoding="utf-8")
    return bin_dir


@pytest.fixture
def no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(launch.MAYA_ENV, raising=False)
    monkeypatch.delenv(launch.MODE_ENV, raising=False)


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_spawn(command: list[str], output: IO[str]) -> FakeProcess:
        commands.append(command)
        return FakeProcess(os.getpid())

    monkeypatch.setattr(launch, "spawn_detached", fake_spawn)
    return commands


def test_newest_install_with_mayapy_wins(tmp_path: Path, no_override: None) -> None:
    make_install(tmp_path, "Maya2025")
    newest = make_install(tmp_path, "Maya2027")
    (tmp_path / "Maya2028").mkdir()
    make_install(tmp_path, "MayaUSD")
    found = find_maya([tmp_path, tmp_path / "missing"])
    assert found == MayaInstall(newest, "2027")
    assert found.mayapy.name == launch.executable("mayapy")


def test_no_install_found(tmp_path: Path, no_override: None) -> None:
    assert find_maya([tmp_path]) is None


def test_environment_points_at_an_install_or_its_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_install(tmp_path / "default", "Maya2027")
    custom = make_install(tmp_path / "custom", "maya2026")
    monkeypatch.setenv(launch.MAYA_ENV, str(custom.parent))
    assert find_maya([tmp_path / "default"]) == MayaInstall(custom, "2026")
    monkeypatch.setenv(launch.MAYA_ENV, str(custom / launch.executable("mayapy")))
    assert find_maya([tmp_path / "default"]) == MayaInstall(custom, "2026")
    monkeypatch.setenv(launch.MAYA_ENV, str(tmp_path / "nowhere"))
    assert find_maya([tmp_path / "default"]) is None


@pytest.mark.parametrize(
    ("raw", "mode"),
    [("", "headless"), ("GUI", "gui"), (" off ", "off"), ("headless", "headless"), ("yes", "off")],
)
def test_autostart_mode_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, mode: str
) -> None:
    monkeypatch.setenv(launch.MODE_ENV, raw)
    assert launch.autostart_mode() == mode


def test_headless_launch_runs_the_bridge_in_mayapy(
    tmp_path: Path, no_override: None, spawned: list[list[str]]
) -> None:
    bin_dir = make_install(tmp_path / "apps", "Maya2027")
    launcher = Launcher(tmp_path / "home", search_roots=[tmp_path / "apps"])
    started = launcher.start()
    assert started.pid == os.getpid()
    assert started.mode == "headless"
    assert spawned == [
        [
            str(bin_dir / launch.executable("mayapy")),
            "-m",
            "bmaya_bridge.headless",
            "--idle-exit",
            "600",
        ]
    ]
    assert json.loads(launcher.lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_second_launch_waits_for_the_first(
    tmp_path: Path, no_override: None, spawned: list[list[str]]
) -> None:
    make_install(tmp_path / "apps", "Maya2027")
    first = Launcher(tmp_path / "home", search_roots=[tmp_path / "apps"])
    second = Launcher(tmp_path / "home", search_roots=[tmp_path / "apps"])
    assert first.start().pid == second.start().pid
    assert len(spawned) == 1


def test_lock_of_a_dead_launch_is_ignored(
    tmp_path: Path,
    no_override: None,
    spawned: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_install(tmp_path / "apps", "Maya2027")
    launcher = Launcher(tmp_path / "home", search_roots=[tmp_path / "apps"])
    launcher.start()
    monkeypatch.setattr(launch, "pid_alive", lambda pid: False)
    assert launcher.pending() is None
    launcher.start()
    assert len(spawned) == 2


def test_maya_that_hangs_while_starting_is_not_started_twice(
    tmp_path: Path, no_override: None, spawned: list[list[str]]
) -> None:
    make_install(tmp_path / "apps", "Maya2027")
    launcher = Launcher(tmp_path / "home", search_roots=[tmp_path / "apps"])
    launcher.start()
    old = time.time() - launch.LOCK_SECONDS - 120
    os.utime(launcher.lock_path, (old, old))
    with pytest.raises(LaunchError, match="started 5 minutes ago .* has not come up"):
        launcher.start()
    assert len(spawned) == 1
    older = time.time() - launch.STUCK_SECONDS - 1
    os.utime(launcher.lock_path, (older, older))
    launcher.start()
    assert len(spawned) == 2


def test_bridge_path_is_first_on_the_child_python_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    first, rest = launch.bridge_env()["PYTHONPATH"].split(os.pathsep, 1)
    assert (Path(first) / "bmaya_bridge" / "headless.py").is_file()
    assert rest == "elsewhere"


def test_launch_without_an_install_explains(tmp_path: Path, no_override: None) -> None:
    launcher = Launcher(tmp_path / "home", search_roots=[tmp_path])
    with pytest.raises(LaunchError, match=launch.MAYA_ENV):
        launcher.start()
    assert not launcher.lock_path.exists()


def test_gui_launch_needs_the_installed_module(
    tmp_path: Path, no_override: None, spawned: list[list[str]]
) -> None:
    bin_dir = make_install(tmp_path / "apps", "Maya2027")
    app_dir = tmp_path / "maya"
    launcher = Launcher(
        tmp_path / "home", "gui", search_roots=[tmp_path / "apps"], maya_app_dir=app_dir
    )
    with pytest.raises(LaunchError, match="bmaya install"):
        launcher.start()
    assert spawned == []
    install(app_dir)
    assert launcher.start().mode == "gui"
    assert spawned == [[str(bin_dir / launch.executable("maya"))]]


class FakeLauncher:
    def __init__(self, pid: int = 0, error: str = "") -> None:
        self.pid = pid or os.getpid()
        self.mode = "headless"
        self.error = error
        self.starts = 0
        self.cleared = 0
        self.bridge: FakeBridge | None = None
        self.home: Path | None = None

    def start(self) -> Launch:
        self.starts += 1
        if self.error:
            raise LaunchError(self.error)
        if self.bridge is not None and self.home is not None:
            self.bridge.write_registry(self.home, 555)
        return Launch(self.pid, "headless", Path("launch.log"))

    def clear(self) -> None:
        self.cleared += 1


def manager_with(home: Path, launcher: FakeLauncher, wait: float = 0.6) -> SessionManager:
    return SessionManager(home, label="test-1", launcher=launcher, launch_wait=wait)  # type: ignore[arg-type]


def test_tool_call_starts_maya_when_none_runs(home: Path, make_bridge: BridgeFactory) -> None:
    launcher = FakeLauncher()
    launcher.bridge = make_bridge({"get_scene_info": lambda params: {"total": 0}})
    launcher.home = home
    manager = manager_with(home, launcher)
    assert manager.call("get_scene_info", {}) == {"total": 0}
    assert manager.call("get_scene_info", {}) == {"total": 0}
    assert launcher.starts == 1
    assert launcher.cleared == 1


def test_slow_start_asks_to_call_again(home: Path) -> None:
    launcher = FakeLauncher()
    with pytest.raises(SessionError, match="starting .headless, pid .*Call the tool again"):
        manager_with(home, launcher).resolve()


def test_maya_that_dies_while_starting_is_reported(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launch, "pid_alive", lambda pid: False)
    launcher = FakeLauncher(pid=4242)
    with pytest.raises(SessionError, match="exited before the bridge came up") as info:
        manager_with(home, launcher).resolve()
    assert "launch.log" in str(info.value)


def test_launch_error_reaches_the_agent(home: Path) -> None:
    launcher = FakeLauncher(error="No Maya installation was found.")
    with pytest.raises(SessionError, match="No Maya installation was found"):
        manager_with(home, launcher).resolve()


def test_listing_sessions_does_not_start_maya(home: Path) -> None:
    launcher = FakeLauncher()
    assert manager_with(home, launcher).list_sessions() == {"sessions": [], "current": None}
    assert launcher.starts == 0


def test_lost_session_is_reported_before_a_new_maya_starts(
    home: Path, make_bridge: BridgeFactory
) -> None:
    launcher = FakeLauncher()
    manager = manager_with(home, launcher, wait=0.1)
    bridge = make_bridge({"get_scene_info": lambda params: {"total": 0}})
    bridge.write_registry(home, 111)
    manager.call("get_scene_info", {})
    bridge.stop()
    with pytest.raises(SessionError, match="exited or crashed") as lost:
        manager.resolve()
    assert "starts a new empty one (headless)" in str(lost.value)
    assert launcher.starts == 0
    with pytest.raises(SessionError, match="starting"):
        manager.resolve()
    assert launcher.starts == 1


def test_sessions_report_clients_and_headless(home: Path, make_bridge: BridgeFactory) -> None:
    bridge = make_bridge()
    bridge.clients = 2
    bridge.write_registry(home, 111)
    manager = SessionManager(home, label="test-1")
    session = manager.list_sessions()["sessions"][0]
    assert session["clients"] == 2
    assert "headless" not in session
