from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fake_bridge import FakeBridge, Handler

from bmaya_mcp.sessions import SessionManager

BridgeFactory = Callable[..., FakeBridge]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BMAYA_MCP_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def make_bridge() -> Iterator[BridgeFactory]:
    bridges: list[FakeBridge] = []

    def factory(
        handlers: dict[str, Handler] | None = None,
        port: int = 0,
        maya_version: str = "2027",
        scene: str = "C:/work/hero.ma",
    ) -> FakeBridge:
        bridge = FakeBridge(handlers, port=port, maya_version=maya_version, scene=scene).start()
        bridges.append(bridge)
        return bridge

    yield factory
    for bridge in bridges:
        bridge.stop()


@pytest.fixture
def manager(home: Path) -> SessionManager:
    return SessionManager(home, label="test-1", ping_timeout=3.0)
