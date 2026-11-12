from __future__ import annotations

from collections.abc import Callable

_exit: Callable[[], None] | None = None


def set_exit(callback: Callable[[], None] | None) -> None:
    """Only a Maya that bmaya itself runs registers one; a Maya with a window never does."""
    global _exit
    _exit = callback


def can_exit() -> bool:
    return _exit is not None


def request_exit() -> None:
    if _exit is not None:
        _exit()
