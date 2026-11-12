import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bmaya_cli.client import BridgeClient, BridgeCommandError, BridgeConnectionError
from bmaya_cli.journal import find_session_log, read_records, stopped_cleanly
from bmaya_cli.launch import Launch, Launcher, LaunchError
from bmaya_cli.state import ClientState, StateStore

logger = logging.getLogger(__name__)

PING_TIMEOUT = 3.0
LAUNCH_WAIT = 40.0
LAUNCH_POLL = 0.5


class SessionError(Exception):
    pass


class MayaStarting(SessionError):
    """Maya was started and is not up yet; the same command works a little later."""


def bmaya_home() -> Path:
    override = os.environ.get("BMAYA_HOME")
    if override:
        return Path(override)
    return Path.home() / ".bmaya"


@dataclass(frozen=True)
class Session:
    id: str
    port: int
    token: str
    maya_version: str
    scene: str
    busy: bool
    clients: int = 0
    headless: bool = False

    @property
    def scene_name(self) -> str:
        if not self.scene:
            return "untitled"
        return self.scene.replace("\\", "/").rsplit("/", 1)[-1]

    def describe(self) -> str:
        return f"{self.id} ({self.scene_name}, Maya {self.maya_version})"

    def to_dict(self, current: bool = False) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": self.id,
            "scene": self.scene,
            "maya_version": self.maya_version,
            "busy": self.busy,
            "clients": self.clients,
            "current": current,
        }
        if self.headless:
            info["headless"] = True
        return info


class SessionManager:
    def __init__(
        self,
        home: Path | None = None,
        label: str | None = None,
        ping_timeout: float = PING_TIMEOUT,
        launcher: Launcher | None = None,
        launch_wait: float | None = None,
        store: StateStore | None = None,
        override: str = "",
    ) -> None:
        self.home = home or bmaya_home()
        self.label = label
        self.ping_timeout = ping_timeout
        self.launcher = launcher
        self.launch_wait = LAUNCH_WAIT if launch_wait is None else launch_wait
        self.override = override.strip()
        self._store = store
        # Each command is its own process, so what a long-lived server would keep in memory
        # is read from the client's state file and written back.
        state = store.load() if store is not None else ClientState()
        self.current = state.current
        self._last_used = state.last_used
        self._last_command = state.last_command
        self._clients: dict[str, BridgeClient] = {}
        self._lock = threading.Lock()

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    def discover(self) -> list[Session]:
        entries = self._read_entries()
        if not entries:
            return []
        with ThreadPoolExecutor(max_workers=min(len(entries), 8)) as pool:
            probed = list(pool.map(self._probe, entries))
        return sorted((s for s in probed if s is not None), key=lambda s: int(s.id))

    def list_sessions(self) -> dict[str, Any]:
        sessions = self.discover()
        return {
            "sessions": [s.to_dict(s.id == self.current) for s in sessions],
            "current": self.current,
        }

    def use_session(self, session_id: str) -> Session:
        session = self._pick(session_id.strip(), self.discover())
        self.current = session.id
        self._remember()
        return session

    def forget(self, session_id: str) -> None:
        if self.current == session_id:
            self.current = None
        if self._last_used == session_id:
            self._last_used = None
        self._last_command.pop(session_id, None)
        self._remember()

    def resolve(self) -> Session:
        sessions = self.discover()
        if self.override:
            return self._pick(self.override, sessions)
        alive = {session.id for session in sessions}
        lost = self.current or self._last_used
        if lost is not None and lost not in alive:
            message = self._lost_message(lost)
            # Saved before raising, or every later command would report the same loss.
            self.forget(lost)
            raise SessionError(message)
        if self.current is not None:
            return next(session for session in sessions if session.id == self.current)
        if not sessions:
            sessions = self._autostart()
        if len(sessions) > 1:
            raise SessionError(
                "Several Maya sessions are running: "
                + "; ".join(s.describe() for s in sessions)
                + ". Pick one with `bmaya use ID`, or pass --session ID."
            )
        return sessions[0]

    def start(self, wait: float) -> Session:
        """Starts one more Maya and makes it the caller's current session."""
        if self.launcher is None:
            raise SessionError("Starting Maya is not set up for this command.")
        known = {session.id for session in self.discover()}
        session = self._await(self._launch(), known, wait)[0]
        self.current = session.id
        self._remember()
        return session

    def _autostart(self) -> list[Session]:
        if self.launcher is None:
            raise SessionError(self._no_session_message())
        return self._await(self._launch(), set(), self.launch_wait)

    def _launch(self) -> Launch:
        assert self.launcher is not None
        try:
            return self.launcher.start()
        except LaunchError as exc:
            raise SessionError(str(exc)) from exc

    def _await(self, launch: Launch, known: set[str], wait: float) -> list[Session]:
        assert self.launcher is not None
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            # Compared by id, not by the launched pid: `maya` is a wrapper script on some systems.
            fresh = [session for session in self.discover() if session.id not in known]
            if fresh:
                self.launcher.clear()
                return fresh
            if not launch.alive():
                self.launcher.clear()
                where = f" Its output is in {launch.log}." if launch.log else ""
                raise SessionError(
                    f"Maya ({launch.describe()}) was started but exited before the bridge came "
                    f"up.{where} Run `bmaya doctor`."
                )
            time.sleep(LAUNCH_POLL)
        raise MayaStarting(
            f"Maya is starting ({launch.describe()}) and is not ready yet. "
            "Run the command again in about 15 seconds; `bmaya sessions` shows when it is up."
        )

    def call(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_session(command, params)[1]

    def call_session(self, command: str, params: dict[str, Any]) -> tuple[Session, dict[str, Any]]:
        session = self.resolve()
        client = self._client_for(session)
        self._last_used = session.id
        self._last_command = {session.id: command}
        self._remember()
        try:
            return session, client.request(command, params)
        except BridgeConnectionError as exc:
            raise self._explain_failure(session, exc) from exc

    def _pick(self, session_id: str, sessions: list[Session]) -> Session:
        for session in sessions:
            if session.id == session_id:
                return session
        if not sessions:
            raise SessionError(self._no_session_message())
        raise SessionError(
            f"No live session {session_id!r}. Available: "
            + "; ".join(s.describe() for s in sessions)
            + "."
        )

    def _remember(self) -> None:
        if self._store is not None:
            self._store.save(ClientState(self.current, self._last_used, self._last_command))

    def _read_entries(self) -> list[tuple[Path, dict[str, Any]]]:
        entries: list[tuple[Path, dict[str, Any]]] = []
        if not self.sessions_dir.is_dir():
            return entries
        for path in sorted(self.sessions_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                int(data["pid"])
                int(data["port"])
                str(data["token"])
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("skipping unreadable registry file %s", path)
                continue
            entries.append((path, data))
        return entries

    def _probe(self, entry: tuple[Path, dict[str, Any]]) -> Session | None:
        path, data = entry
        session_id = str(int(data["pid"]))
        port = int(data["port"])
        token = str(data["token"])
        session = Session(
            id=session_id,
            port=port,
            token=token,
            maya_version=str(data.get("maya_version", "")),
            scene=str(data.get("scene", "")),
            busy=False,
            headless=bool(data.get("headless", False)),
        )
        probe = BridgeClient(
            port,
            token,
            self.label,
            connect_timeout=self.ping_timeout,
            read_timeout=self.ping_timeout,
        )
        try:
            info = probe.request("ping")
        except BridgeConnectionError as exc:
            if exc.refused:
                self._prune(path, session_id)
                return None
            logger.info("session %s did not answer ping (%s), keeping it", session_id, exc)
            return replace(session, busy=True)
        except BridgeCommandError as exc:
            logger.info("session %s rejected ping (%s), pruning", session_id, exc.type)
            self._prune(path, session_id)
            return None
        finally:
            probe.close()
        return Session(
            id=session_id,
            port=port,
            token=token,
            maya_version=str(info.get("maya_version", session.maya_version)),
            scene=str(info.get("scene", session.scene)),
            busy=bool(info.get("busy", False)),
            clients=int(info.get("clients", 0)),
            headless=bool(info.get("headless", False)),
        )

    def _prune(self, path: Path, session_id: str) -> None:
        logger.info("removing registry file of dead session %s", session_id)
        try:
            path.unlink()
        except OSError:
            pass
        with self._lock:
            client = self._clients.pop(session_id, None)
        if client is not None:
            client.close()

    def _client_for(self, session: Session) -> BridgeClient:
        with self._lock:
            client = self._clients.get(session.id)
            if client is None or client.port != session.port or client.token != session.token:
                if client is not None:
                    client.close()
                client = BridgeClient(session.port, session.token, self.label)
                self._clients[session.id] = client
            return client

    def _explain_failure(self, session: Session, error: BridgeConnectionError) -> Exception:
        last = self._last_command.get(session.id, "unknown")
        logger.warning("session %s failed during %s: %s", session.id, last, error)
        path = self.sessions_dir / f"{session.id}.json"
        probe = BridgeClient(
            session.port,
            session.token,
            self.label,
            connect_timeout=self.ping_timeout,
            read_timeout=self.ping_timeout,
        )
        try:
            probe.request("ping")
        except BridgeConnectionError as ping_error:
            if ping_error.refused:
                self._prune(path, session.id)
                logger.warning("session %s is lost", session.id)
                message = self._lost_message(session.id)
                self.forget(session.id)
                return SessionError(message)
        except BridgeCommandError:
            pass
        finally:
            probe.close()
        if error.timed_out:
            return BridgeConnectionError(
                f"Maya session {session.id} did not reply in time to {last}. The command was "
                "sent and may still be running, so do not send it again: `bmaya sessions` shows "
                "busy until it ends; then inspect the scene.",
                timed_out=True,
            )
        return BridgeConnectionError(
            f"The connection to Maya session {session.id} broke during {last}, but Maya is "
            "still running. The command may or may not have run; inspect the scene, then retry."
        )

    def _no_session_message(self) -> str:
        return (
            "No Maya with the bmaya bridge is running. `bmaya start` starts one without a "
            "window and `bmaya start --gui` one with it; a Maya the user opens also works once "
            "`bmaya install` has been run."
        )

    def _lost_message(self, session_id: str) -> str:
        if self._was_stopped(session_id):
            return f"Maya session {session_id} was stopped or closed. {self._next_step()}"
        last = self._last_command.get(session_id)
        sent = f" The last command sent to it was {last}." if last else ""
        return (
            f"Maya session {session_id} exited or crashed.{sent} "
            f"{self._journal_note(session_id)} "
            f"Unsaved work in it is gone; tell the user. {self._next_step()}"
        )

    def _was_stopped(self, session_id: str) -> bool:
        log = find_session_log(self.home / "logs", int(session_id))
        return log is not None and stopped_cleanly(read_records(log.path))

    def _next_step(self) -> str:
        if self.launcher is None:
            return "`bmaya sessions` lists what is running and `bmaya start` starts a new Maya."
        return (
            "The next command uses another running Maya or starts a new empty one "
            f"({self.launcher.mode})."
        )

    def _journal_note(self, session_id: str) -> str:
        logs = self.home / "logs"
        log = find_session_log(logs, int(session_id))
        if log is None:
            return f"Its journal would be {logs / f'*-{session_id}.log'}."
        note = f"Its journal is {log.path}"
        try:
            crashed = log.crash_path.stat().st_size > 0
        except OSError:
            crashed = False
        if crashed:
            note += f" and the crash log is {log.crash_path}"
        return note + "."
