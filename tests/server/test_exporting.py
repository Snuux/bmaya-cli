import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import BridgeFactory
from fake_bridge import FakeError, Handler

from bmaya_cli.cli import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
Output = pytest.CaptureFixture[str]
WALKER = {
    "meshes": ["body"],
    "joints": ["root", "spine", "head"],
    "takes": [{"name": "walk", "frames": [0, 16]}],
    "fps": 30.0,
    "up": "y",
}


@pytest.fixture(autouse=True)
def caller(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMAYA_CLIENT", "tester")
    monkeypatch.setenv("BMAYA_AUTOSTART", "off")


def exporter(
    fixture: str, expected: dict[str, Any] | None, issues: list[str] | None = None
) -> Handler:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        shutil.copy(FIXTURES / fixture, params["path"])
        reply: dict[str, Any] = {"path": params["path"], "size": 1, "got": params}
        if expected is not None:
            reply["expected"] = expected
        if issues is not None:
            reply["issues"] = issues
        return reply

    return handler


def export(
    home: Path, make_bridge: BridgeFactory, capsys: Output, handler: Handler, *argv: str
) -> tuple[int, Any, str]:
    bridge = make_bridge({"export_fbx": handler})
    bridge.write_registry(home, 111)
    code = main(["export", *argv])
    captured = capsys.readouterr()
    out = captured.out.strip()
    return code, json.loads(out) if out else None, captured.err


def test_a_file_that_holds_what_the_scene_meant_has_no_issues(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    target = tmp_path / "walk.fbx"
    handler = exporter("walker.fbx", WALKER, ["body: bodyShape is instanced under 2 transforms"])
    code, out, _ = export(home, make_bridge, capsys, handler, str(target), "--clip", "walk")
    assert code == 0
    assert "expected" not in out
    assert out["got"]["ignore_checks"] is False and out["got"]["clip"] == "walk"
    (written,) = out["written"]["items"]
    assert written["path"] == target.resolve().as_posix()
    assert (written["meshes"], written["joints"]) == (1, 3)
    assert written["takes"][0] == {"name": "walk", "frames": [0.0, 16.0]}
    assert out["issues"] == {
        "items": ["body: bodyShape is instanced under 2 transforms"],
        "shown": 1,
        "total": 1,
    }


def test_what_the_file_lacks_is_listed(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    wanted = {
        "meshes": ["body", "hat"],
        "joints": ["root", "spine", "head", "jaw"],
        "takes": [{"name": "walk", "frames": [0, 20]}, {"name": "run", "frames": [0, 10]}],
        "fps": 24.0,
        "up": "y",
    }
    code, out, _ = export(
        home, make_bridge, capsys, exporter("walker.fbx", wanted), str(tmp_path / "a.fbx")
    )
    assert code == 0
    assert out["issues"]["items"] == [
        "1 meshes are not in the file: hat",
        "1 of 4 scene joints are not in the file: jaw",
        "the file says 30 fps, the scene runs at 24",
        "take 'walk' covers frames 0:12.8, the scene asked for 0:20",
        "take 'walk' is baked at 30 fps, the scene runs at 24: export it again; if this stays, "
        "restart that Maya",
        "no take named 'run' in the file (takes: walk, wave)",
    ]


def test_an_older_bridge_and_an_unreadable_file_still_report(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    code, out, _ = export(
        home, make_bridge, capsys, exporter("crate.fbx", None), str(tmp_path / "c.fbx")
    )
    assert code == 0 and out["issues"]["total"] == 0
    assert out["written"]["items"][0]["meshes"] == 1
    code, out, _ = export(
        home, make_bridge, capsys, exporter("crate_ascii.fbx", WALKER), str(tmp_path / "t.fbx")
    )
    assert code == 0
    assert out["issues"]["items"] == [
        "t.fbx: could not read it back: This is an ASCII FBX file; only binary FBX is read."
    ]


def test_a_refusal_keeps_its_hint(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    def refuse(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError(
            "BridgeError", "Export refused: box: boxShape has no faces.", "--ignore-checks ..."
        )

    code, out, err = export(home, make_bridge, capsys, refuse, str(tmp_path / "x.fbx"))
    assert code == 1 and out is None
    assert "error: Export refused" in err and "hint: --ignore-checks" in err
