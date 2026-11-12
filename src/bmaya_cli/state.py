import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = "clients"
KEEP_SECONDS = 7 * 24 * 3600


@dataclass
class ClientState:
    """What one agent has to remember between two commands, since each is a new process."""

    current: str | None = None
    last_used: str | None = None
    last_command: dict[str, str] = field(default_factory=dict)


class StateStore:
    def __init__(self, home: Path, label: str) -> None:
        self.directory = home / STATE_DIR
        self.path = self.directory / f"{label}.json"

    def load(self) -> ClientState:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ClientState()
        if not isinstance(data, dict):
            return ClientState()
        commands = data.get("last_command")
        return ClientState(
            current=_session_id(data.get("current")),
            last_used=_session_id(data.get("last_used")),
            last_command=dict(commands) if isinstance(commands, dict) else {},
        )

    def save(self, state: ClientState) -> None:
        payload = {
            "current": state.current,
            "last_used": state.last_used,
            "last_command": state.last_command,
        }
        temp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            # Two commands of one agent can write at once, and on Windows the loser of that
            # race gets an error. A lost state write must not fail a command.
            logger.info("could not save client state to %s", self.path)
            try:
                temp.unlink()
            except OSError:
                pass

    def prune(self) -> None:
        cutoff = time.time() - KEEP_SECONDS
        try:
            entries = list(self.directory.glob("*.json"))
        except OSError:
            return
        for entry in entries:
            try:
                if entry != self.path and entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue


def _session_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
