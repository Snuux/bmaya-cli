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
        "no take named 'run' in the file (takes: walk, wave)",
    ]


def test_keys_at_a_stale_rate_are_exported_again_with_the_plugin_reloaded(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    calls = []
    wanted = {"meshes": ["paced"], "joints": [], "takes": [], "fps": 30.0, "up": "y"}

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(params)
        fixture = "walker.fbx" if params.get("reload_fbx") else "stale_rate.fbx"
        shutil.copy(FIXTURES / fixture, params["path"])
        reply = {"path": params["path"], "expected": wanted}
        if params.get("reload_fbx"):
            reply["reloaded"] = True
        return reply

    code, out, _ = export(home, make_bridge, capsys, handler, str(tmp_path / "p.fbx"))
    assert code == 0
    assert [c.get("reload_fbx", False) for c in calls] == [False, True]
    assert out["reloaded"] is True
    assert not any("baked" in issue for issue in out["issues"]["items"]), out["issues"]


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


def game_plan(folder: Path, texture: Path) -> dict[str, Any]:
    crate = {"meshes": ["crate"], "joints": [], "takes": [], "fps": 30.0, "up": "y"}
    return {
        "jobs": [
            {
                "node": "gx_model",
                "kind": "model",
                "path": (folder / "crate.fbx").as_posix(),
                "textures": [
                    {"node": "skin_file", "path": texture.as_posix()},
                    {"node": "tiles_file", "path": "C:/art/rock.<UDIM>.png"},
                    {"node": "gone_file", "path": (folder / "nowhere.png").as_posix()},
                ],
                "expected": crate,
            },
            {
                "node": "gx_clips",
                "kind": "clip",
                "clip": "walk",
                "path": (folder / "anim" / "hero_walk.fbx").as_posix(),
                "frames": [0, 16],
                "expected": {**WALKER, "meshes": []},
            },
        ],
        "skipped": [{"node": "rig:gx", "reason": "referenced"}],
        "issues": ["crate: crateShape is instanced under 2 transforms"],
    }


def game_bridge(
    home: Path, make_bridge: BridgeFactory, plan: dict[str, Any], fail_on: str = ""
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def write(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(params)
        if params["path"].endswith(fail_on) and fail_on:
            raise FakeError("Timeout", "still running in Maya")
        target = Path(params["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        fixture = "walker.fbx" if params["kind"] == "clip" else "crate.fbx"
        shutil.copy(FIXTURES / fixture, target)
        return {"path": params["path"], "size": target.stat().st_size}

    bridge = make_bridge({"plan_game_export": lambda params: plan, "export_game_file": write})
    bridge.write_registry(home, 111)
    return calls


def test_game_exporter_writes_each_planned_file_and_copies_textures(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    texture = tmp_path / "src" / "skin.png"
    texture.parent.mkdir()
    texture.write_bytes(b"png")
    calls = game_bridge(home, make_bridge, game_plan(tmp_path / "out", texture))
    code = main(["export", "--game-exporter", "--copy-textures"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [Path(item["path"]).name for item in out["written"]["items"]] == [
        "crate.fbx",
        "hero_walk.fbx",
    ]
    copy = (tmp_path / "out" / "skin.png").as_posix()
    assert out["textures"] == {"copied": [copy], "kept": 0}
    assert calls[0]["textures"] == {"skin_file": copy}
    assert calls[1] == {
        "node": "gx_clips",
        "kind": "clip",
        "clip": "walk",
        "path": (tmp_path / "out" / "anim" / "hero_walk.fbx").as_posix(),
        "textures": {},
    }
    issues = out["issues"]["items"]
    assert issues[0] == "crate: crateShape is instanced under 2 transforms"
    assert any(i.startswith("tiles_file: tiled or empty path") for i in issues), issues
    assert any(i.startswith("gone_file:") and "does not exist" in i for i in issues), issues
    assert out["skipped"] == [{"node": "rig:gx", "reason": "referenced"}]
    assert out["calls"] == 3

    game_bridge(home, make_bridge, game_plan(tmp_path / "out", texture))
    assert main(["export", "--game-exporter", "--copy-textures"]) == 0
    assert json.loads(capsys.readouterr().out)["textures"] == {"copied": [], "kept": 1}


def test_game_exporter_stops_with_what_was_written(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    plan = game_plan(tmp_path / "out", tmp_path / "none.png")
    game_bridge(home, make_bridge, plan, fail_on="hero_walk.fbx")
    code = main(["export", "--game-exporter", "gx_clips"])
    err = capsys.readouterr().err
    assert code == 3
    assert "still writing" in err and "written before it: crate.fbx" in err
    assert "Do not resend" in err


def test_game_exporter_arguments(capsys: Output) -> None:
    for argv in (
        ["export"],
        ["export", "a.fbx", "--game-exporter"],
        ["export", "--game-exporter", "--preset", "godot"],
        ["export", "a.fbx", "--copy-textures"],
    ):
        assert main(argv) == 2, argv
        assert "error:" in capsys.readouterr().err


def test_game_exporter_on_an_old_maya_module(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    make_bridge({}).write_registry(home, 111)
    assert main(["export", "--game-exporter"]) == 1
    err = capsys.readouterr().err
    assert "does not know --game-exporter" in err and "bmaya install" in err
