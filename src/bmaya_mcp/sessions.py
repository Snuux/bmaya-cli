import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bmaya_mcp.client import BridgeClient, BridgeCommandError, BridgeConnectionError
from bmaya_mcp.journal import find_session_log

logger = logging.getLogger(__name__)

PING_TIMEOUT = 3.0


class SessionError(Exception):
    pass


def bmaya_home() -> Path:
    override = os.environ.get("BMAYA_MCP_HOME")
    if override:
        return Path(override)
    return Path.home() / ".bmaya-mcp"


@dataclass(frozen=True)
class Session:
    id: str
    port: int
    token: str
    maya_version: str
    scene: str
    busy: bool

    @property
    def scene_name(self) -> str:
        if not self.scene:
            return "untitled"
        return self.scene.replace("\\", "/").rsplit("/", 1)[-1]

    def describe(self) -> str:
        return f"{self.id} ({self.scene_name}, Maya {self.maya_version})"

    def to_dict(self, current: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "scene": self.scene,
            "maya_version": self.maya_version,
            "busy": self.busy,
            "current": current,
        }


class SessionManager:
    def __init__(
        self,
        home: Path | None = None,
        label: str | None = None,
        ping_timeout: float = PING_TIMEOUT,
    ) -> None:
        self.home = home or bmaya_home()
        self.label = label
        self.ping_timeout = ping_timeout
        self.current: str | None = None
        self._last_used: str | None = None
        self._clients: dict[str, BridgeClient] = {}
        self._last_command: dict[str, str] = {}
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
        session_id = session_id.strip()
        sessions = self.discover()
        for session in sessions:
            if session.id == session_id:
                self.current = session.id
                return session
        if not sessions:
            raise SessionError(self._no_session_message())
        raise SessionError(
            f"No live session {session_id!r}. Available: "
            + "; ".join(s.describe() for s in sessions)
            + "."
        )

    def resolve(self) -> Session:
        sessions = self.discover()
        alive = {session.id for session in sessions}
        lost = self.current or self._last_used
        if lost is not None and lost not in alive:
            self.current = None
            self._last_used = None
            raise SessionError(self._lost_message(lost))
        if self.current is not None:
            return next(session for session in sessions if session.id == self.current)
        if not sessions:
            raise SessionError(self._no_session_message())
        if len(sessions) > 1:
            raise SessionError(
                "Several Maya sessions are running: "
                + "; ".join(s.describe() for s in sessions)
                + ". Call use_session with one id."
            )
        return sessions[0]

    def call(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        session = self.resolve()
        client = self._client_for(session)
        self._last_used = session.id
        self._last_command[session.id] = command
        try:
            return client.request(command, params)
        except BridgeConnectionError as exc:
            raise self._explain_failure(session, exc) from exc

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
                self._last_used = None
                if self.current == session.id:
                    self.current = None
                logger.warning("session %s is lost", session.id)
                return SessionError(self._lost_message(session.id))
        except BridgeCommandError:
            pass
        finally:
            probe.close()
        if error.timed_out:
            return BridgeConnectionError(
                f"Maya session {session.id} did not reply in time to {last}; it may still be "
                "busy with a long command. Wait and retry, or check the scene in Maya."
            )
        return BridgeConnectionError(
            f"The connection to Maya session {session.id} broke during {last}, but Maya is "
            "still running. The command may or may not have run; inspect the scene, then retry."
        )

    def _no_session_message(self) -> str:
        return (
            "No Maya with the bmaya bridge is running. Start Maya (the bridge starts "
            "automatically if `bmaya-mcp install` has set up autostart; run it once if not) "
            "and call the tool again."
        )

    def _lost_message(self, session_id: str) -> str:
        last = self._last_command.get(session_id)
        sent = f" The last command sent to it was {last}." if last else ""
        return (
            f"Maya session {session_id} exited or crashed.{sent} "
            f"{self._journal_note(session_id)} "
            "Ask the user to restart Maya, then call list_sessions."
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
