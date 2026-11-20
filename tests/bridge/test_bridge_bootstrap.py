from __future__ import annotations

import os
import runpy
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from bmaya_bridge import bootstrap, server

USER_SETUP = Path(server.__file__).parents[2] / "scripts" / "userSetup.py"


@pytest.fixture
def clean_bridge() -> Iterator[None]:
    yield
    server.stop()
    if server._journal is not None:
        server._journal.close()
        server._journal = None


def test_start_in_maya_announces_once(
    home: Path, clean_bridge: None, capsys: pytest.CaptureFixture[str]
) -> None:
    port = bootstrap.start_in_maya()
    assert port > 0
    assert bootstrap.start_in_maya() == port
    announced = f"bmaya bridge listening on 127.0.0.1:{port} (session {os.getpid()})"
    assert capsys.readouterr().out.splitlines() == [announced]


def test_start_failure_is_logged_not_raised(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> int:
        raise OSError("no sockets today")

    monkeypatch.setattr(server, "start", broken)
    assert bootstrap.start_in_maya() == 0
    log = (home / "logs" / "bootstrap-error.log").read_text(encoding="utf-8")
    assert "OSError: no sockets today" in log
    assert "did not start" in capsys.readouterr().err


def test_user_setup_never_raises_outside_maya(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runpy.run_path(str(USER_SETUP))
    log = (home / "logs" / "bootstrap-error.log").read_text(encoding="utf-8")
    assert "maya" in log
    assert "did not start" in capsys.readouterr().err


def test_start_waits_for_the_main_window(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = iter([False, False, True])
    waits: list[int] = []
    started: list[bool] = []

    class Timer:
        @staticmethod
        def singleShot(ms: int, callback: Callable[[], None]) -> None:  # noqa: N802
            waits.append(ms)
            callback()

    monkeypatch.setattr(bootstrap, "window_timer", lambda: Timer)
    monkeypatch.setattr(bootstrap, "window_shown", lambda: next(shown))
    monkeypatch.setattr(server, "is_running", lambda: True)
    monkeypatch.setattr(server, "start", lambda: started.append(True) or 1)
    bootstrap.start_in_maya()
    assert waits == [bootstrap.WINDOW_POLL_MS] * 2 and started == [True]

    monkeypatch.setattr(bootstrap, "window_shown", lambda: False)
    started.clear()
    assert bootstrap.start_in_maya(deadline=1.0) == 1
    assert started == [True]
