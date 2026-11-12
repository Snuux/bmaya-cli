import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from bmaya_mcp.install import PACKAGED, default_maya_app_dir, installed_version
from bmaya_mcp.journal import pid_alive

logger = logging.getLogger(__name__)

HEADLESS = "headless"
GUI = "gui"
OFF = "off"
MODES = (HEADLESS, GUI, OFF)
MODE_ENV = "BMAYA_MCP_AUTOSTART"
MAYA_ENV = "BMAYA_MCP_MAYA"
IDLE_EXIT_SECONDS = 600
LOCK_SECONDS = 180.0
LOCK_NAME = "launch.json"
VERSION = re.compile(r"maya(\d{4}(?:\.\d+)?)$", re.IGNORECASE)


class LaunchError(Exception):
    pass


@dataclass(frozen=True)
class MayaInstall:
    bin_dir: Path
    version: str

    @property
    def mayapy(self) -> Path:
        return self.bin_dir / executable("mayapy")

    @property
    def maya(self) -> Path:
        return self.bin_dir / executable("maya")


@dataclass(frozen=True)
class Launch:
    pid: int
    mode: str
    log: Path | None = None

    def alive(self) -> bool:
        return self.pid <= 0 or pid_alive(self.pid)

    def describe(self) -> str:
        if self.pid > 0:
            return f"{self.mode}, pid {self.pid}"
        return self.mode


def executable(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def autostart_mode() -> str:
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if not raw:
        return HEADLESS
    if raw in MODES:
        return raw
    logger.warning("%s=%r is not one of %s; autostart is off", MODE_ENV, raw, ", ".join(MODES))
    return OFF


def default_search_roots() -> list[Path]:
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
        return [Path(program_files) / "Autodesk"]
    if sys.platform == "darwin":
        return [Path("/Applications/Autodesk")]
    return [Path("/usr/autodesk")]


def bin_dir_of(path: Path) -> Path | None:
    if path.is_file():
        path = path.parent
    for candidate in (path / "bin", path, path / "Maya.app" / "Contents" / "bin"):
        if (candidate / executable("mayapy")).is_file():
            return candidate
    return None


def version_of(bin_dir: Path) -> str:
    for part in reversed(bin_dir.parts):
        match = VERSION.match(part)
        if match:
            return match.group(1)
    return ""


def find_maya(search_roots: Iterable[Path] | None = None, override: str = "") -> MayaInstall | None:
    override = override or os.environ.get(MAYA_ENV, "").strip()
    if override:
        bin_dir = bin_dir_of(Path(override).expanduser())
        if bin_dir is None:
            return None
        return MayaInstall(bin_dir, version_of(bin_dir))
    found: list[MayaInstall] = []
    for root in search_roots if search_roots is not None else default_search_roots():
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            match = VERSION.match(folder.name)
            bin_dir = bin_dir_of(folder) if match else None
            if match and bin_dir is not None:
                found.append(MayaInstall(bin_dir, match.group(1)))
    if not found:
        return None
    return max(found, key=lambda install: float(install.version))


class Launcher:
    def __init__(
        self,
        home: Path,
        mode: str = HEADLESS,
        search_roots: Iterable[Path] | None = None,
        maya_app_dir: Path | None = None,
    ) -> None:
        self.home = home
        self.mode = mode
        self._search_roots = None if search_roots is None else list(search_roots)
        self._maya_app_dir = maya_app_dir

    @property
    def lock_path(self) -> Path:
        return self.home / LOCK_NAME

    def start(self) -> Launch:
        pending = self.pending()
        if pending is not None:
            return pending
        install = find_maya(self._search_roots)
        if install is None:
            raise LaunchError(
                "No Maya is running and no Maya installation was found to start one. "
                f"Set {MAYA_ENV} to the Maya install folder, or start Maya yourself."
            )
        command = self.command(install)
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            lock = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return self.pending() or Launch(0, self.mode)
        try:
            launch = self.spawn(command)
        except OSError as exc:
            os.close(lock)
            self.clear()
            raise LaunchError(f"Could not start Maya ({command[0]}): {exc}") from exc
        with os.fdopen(lock, "w", encoding="utf-8") as handle:
            json.dump({"pid": launch.pid, "mode": launch.mode, "log": str(launch.log)}, handle)
        logger.info("started Maya (%s): %s", launch.describe(), " ".join(command))
        return launch

    def pending(self) -> Launch | None:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
        except OSError:
            return None
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
            launch = Launch(int(data["pid"]), str(data["mode"]), Path(str(data["log"])))
        except (OSError, ValueError, KeyError, TypeError):
            # Another server created the lock a moment ago and has not written the pid yet.
            launch = Launch(0, self.mode)
        if age > LOCK_SECONDS or not launch.alive():
            self.clear()
            return None
        return launch

    def clear(self) -> None:
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    def command(self, install: MayaInstall) -> list[str]:
        if self.mode == GUI:
            app_dir = self._maya_app_dir or default_maya_app_dir()
            if installed_version(app_dir) is None:
                raise LaunchError(
                    "No Maya is running, and Maya cannot be started with the bridge because the "
                    "module is not installed. Run `bmaya-mcp install` once, or set "
                    f"{MODE_ENV}={HEADLESS}."
                )
            return [str(install.maya)]
        return headless_command(install, IDLE_EXIT_SECONDS)

    def spawn(self, command: list[str]) -> Launch:
        logs = self.home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"launch-{time.strftime('%Y%m%d-%H%M%S')}.log"
        with open(log, "w", encoding="utf-8") as output:
            process = spawn_detached(command, output)
        return Launch(process.pid, self.mode, log)


def headless_command(install: MayaInstall, idle_exit: int, scene: str = "") -> list[str]:
    command = [str(install.mayapy), "-m", "bmaya_bridge.headless", "--idle-exit", str(idle_exit)]
    if scene:
        command += ["--scene", scene]
    return command


def run_headless(install: MayaInstall, idle_exit: int, scene: str) -> int:
    process = subprocess.Popen(headless_command(install, idle_exit, scene), env=bridge_env())
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # Ctrl+C reaches Maya too; keep waiting while it shuts the bridge down.
            continue


def bridge_env() -> dict[str, str]:
    env = os.environ.copy()
    bridge = str(PACKAGED / "python")
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = bridge + os.pathsep + inherited if inherited else bridge
    return env


def spawn_detached(command: list[str], output: IO[str]) -> "subprocess.Popen[bytes]":
    # The server's stdout is the MCP channel, so the child must never inherit it.
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": output,
        "stderr": subprocess.STDOUT,
        "env": bridge_env(),
    }
    if sys.platform != "win32":
        return subprocess.Popen(command, start_new_session=True, **options)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Outlive the agent's process tree when its job object allows it.
        return subprocess.Popen(
            command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options
        )
    except OSError:
        return subprocess.Popen(command, creationflags=flags, **options)
