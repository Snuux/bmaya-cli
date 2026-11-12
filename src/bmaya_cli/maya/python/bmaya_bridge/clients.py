from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator

RECENT_CLIENT_SECONDS = 600.0
CHUNK_PREFIX = "bmaya "
OWNER_SEPARATOR = ": "
MAYA_UI = "maya-ui"
SELECTION_PREFIX = "select"


def chunk_name(client: str, command: str) -> str:
    if client:
        return f"{CHUNK_PREFIX}{client}{OWNER_SEPARATOR}{command}"
    return f"{CHUNK_PREFIX}{command}"


def chunk_owner(name: str) -> str:
    if not name.startswith(CHUNK_PREFIX):
        return ""
    owner, separator, _ = name[len(CHUNK_PREFIX) :].rpartition(OWNER_SEPARATOR)
    return owner if separator else ""


def undo_blocker(top_name: str, caller: str) -> str:
    """Owner of the top undo entry when it is not the caller's; empty when undo is allowed."""
    if not caller:
        return ""
    owner = chunk_owner(top_name)
    if owner == caller:
        return ""
    return owner or MAYA_UI


def is_selection(top_name: str, caller: str) -> bool:
    """A selection change made in the Maya UI; undo steps over it instead of stopping."""
    if not caller or top_name.startswith(CHUNK_PREFIX):
        return False
    return top_name.startswith(SELECTION_PREFIX)


class ClientTable:
    """Clients are labels that ran a command; pings and bare connections do not count."""

    def __init__(self, recent_seconds: float = RECENT_CLIENT_SECONDS) -> None:
        self.recent_seconds = recent_seconds
        self._lock = threading.Lock()
        self._last_command: dict[str, float] = {}
        self._connections: dict[str, int] = {}
        self._last_active = time.monotonic()

    def connected(self, label: str) -> None:
        if not label:
            return
        with self._lock:
            self._connections[label] = self._connections.get(label, 0) + 1

    def disconnected(self, label: str) -> None:
        if not label:
            return
        with self._lock:
            left = self._connections.get(label, 0) - 1
            if left > 0:
                self._connections[label] = left
                return
            self._connections.pop(label, None)
            if label in self._last_command:
                self._last_active = time.monotonic()

    def touch(self, label: str) -> None:
        if not label:
            return
        with self._lock:
            now = time.monotonic()
            self._last_command[label] = now
            self._last_active = now

    def count(self) -> int:
        with self._lock:
            return self._count()

    def others_recent(self, label: str) -> list[tuple[str, float]]:
        now = time.monotonic()
        with self._lock:
            ages = [
                (other, now - seen) for other, seen in self._last_command.items() if other != label
            ]
        recent = [(other, age) for other, age in ages if age <= self.recent_seconds]
        return sorted(recent, key=lambda item: item[1])

    def idle_seconds(self) -> float:
        with self._lock:
            if self._count():
                return 0.0
            return time.monotonic() - self._last_active

    def _count(self) -> int:
        return sum(1 for label in self._connections if label in self._last_command)


_acting_client = ""
_acting_table: ClientTable | None = None


@contextlib.contextmanager
def acting(client: str, table: ClientTable | None) -> Iterator[None]:
    """Marks whose command the main thread is running, for handlers that need to know."""
    global _acting_client, _acting_table
    previous = (_acting_client, _acting_table)
    _acting_client = client
    _acting_table = table
    try:
        yield
    finally:
        _acting_client, _acting_table = previous


def acting_client() -> str:
    return _acting_client


def other_recent_clients() -> list[tuple[str, float]]:
    if _acting_table is None or not _acting_client:
        return []
    return _acting_table.others_recent(_acting_client)
