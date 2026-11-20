import io
import json
from pathlib import Path
from typing import Any

import pytest
from bmaya_bridge.errors import BRIDGE_ERROR_TYPES
from conftest import BridgeFactory
from fake_bridge import FakeBridge, FakeError
from test_launch import FakeLauncher

from bmaya_cli import commands, sessions
from bmaya_cli.cli import build_parser, main

PNG = b"\x89PNG\r\n\x1a\n fake"
Output = pytest.CaptureFixture[str]


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def caller(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMAYA_CLIENT", "tester")
    monkeypatch.setenv("BMAYA_AUTOSTART", "off")


def echo(command: str) -> dict[str, Any]:
    return {command: lambda params: {"got": params}}


def written_crate(params: dict[str, Any]) -> dict[str, Any]:
    target = Path(params["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((Path(__file__).parents[1] / "fixtures" / "crate.fbx").read_bytes())
    return {"path": params["path"], "got": params}


def one_maya(home: Path, make_bridge: BridgeFactory, handlers: dict[str, Any]) -> FakeBridge:
    bridge = make_bridge(handlers)
    bridge.write_registry(home, 111)
    return bridge


def run(capsys: Output, *argv: str) -> tuple[int, Any, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    out = captured.out.strip()
    return code, json.loads(out) if out else None, captured.err


def test_result_is_one_line_of_compact_json(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    one_maya(home, make_bridge, {"get_scene_info": lambda params: {"file": "ящик.ma", "total": 2}})
    assert main(["scene"]) == 0
    captured = capsys.readouterr()
    assert captured.out == '{"file":"ящик.ma","total":2}\n'
    assert captured.err == ""


def test_scene_flags_become_command_params(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    bridge = one_maya(home, make_bridge, echo("get_scene_info"))
    code, out, _ = run(capsys, "scene", "-q", "barrel", "--type", "mesh", "-n", "5")
    assert code == 0
    assert out == {"got": {"query": "barrel", "node_type": "mesh", "limit": 5}}
    assert bridge.requests[-1]["client"] == "tester"


@pytest.mark.parametrize(
    "argv", [["--client", "codex 7", "scene"], ["scene", "--client", "codex 7"]]
)
def test_client_flag_works_on_either_side_of_the_command(
    home: Path, make_bridge: BridgeFactory, capsys: Output, argv: list[str]
) -> None:
    bridge = one_maya(home, make_bridge, echo("get_scene_info"))
    assert run(capsys, *argv)[0] == 0
    assert bridge.requests[-1]["client"] == "codex-7"
    assert (home / "clients" / "codex-7.json").is_file()


def test_simple_commands_map_their_arguments(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    handlers = {
        **echo("get_object_info"),
        **echo("audit_mesh"),
        **echo("get_animation_info"),
        **echo("get_rig_info"),
        **echo("undo"),
    }
    one_maya(home, make_bridge, handlers)
    assert run(capsys, "object", "|props|crate")[1] == {"got": {"name": "|props|crate"}}
    assert run(capsys, "audit", "barrel", "crate")[1] == {
        "got": {"names": "barrel,crate", "symmetry": ""}
    }
    assert run(capsys, "audit", "--symmetry", "x")[1] == {"got": {"names": "", "symmetry": "x"}}
    assert run(capsys, "anim")[1] == {"got": {"name": ""}}
    assert run(capsys, "rig", "rig_ctrls")[1] == {"got": {"root": "rig_ctrls"}}
    assert run(capsys, "undo")[1] == {"got": {"steps": 1, "force": False}}
    assert run(capsys, "undo", "3", "--force")[1] == {"got": {"steps": 3, "force": True}}


def test_motion_maps_names_frames_and_checks(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    one_maya(home, make_bridge, echo("get_motion"))
    argv = ["motion", "hand_r", "foot_*", "--frames", "0:30", "--loop", "--contacts", "foot_l"]
    assert run(capsys, *argv)[1]["got"] == {
        "names": "hand_r,foot_*",
        "frames": "0:30",
        "clip": "",
        "loop": True,
        "contacts": "foot_l",
        "ground": "",
        "values": False,
        "attrs": "",
    }
    clip = run(capsys, "motion", "--clip", "walk", "--ground", "0")[1]["got"]
    assert (clip["names"], clip["clip"], clip["ground"]) == ("", "walk", "0")
    listed = run(capsys, "motion", "hand", "--values", "--attrs", "rx,ik")[1]["got"]
    assert (listed["values"], listed["attrs"]) == (True, "rx,ik")


def test_anim_plot_moves_the_png_and_needs_a_name(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    temp = tmp_path / "curves-from-maya.png"

    def plot(params: dict[str, Any]) -> dict[str, Any]:
        temp.write_bytes(PNG)
        return {"path": str(temp), "width": 1000, "height": 400, "got": params}

    one_maya(home, make_bridge, {"plot_animation": plot})
    target = tmp_path / "curves.png"
    argv = ["anim", "hips", "--plot", "-o", str(target), "--attrs", "ty,rx", "--frames", "0:30"]
    code, out, _ = run(capsys, *argv)
    assert code == 0 and target.read_bytes() == PNG
    assert out["got"] == {
        "name": "hips",
        "attrs": "ty,rx",
        "frames": "0:30",
        "clip": "",
        "max_size": 0,
    }
    assert run(capsys, "anim", "--plot")[0] == commands.EXIT_USAGE
    assert run(capsys, "anim", "hips", "--attrs", "rx")[0] == commands.EXIT_USAGE


def test_relative_paths_are_resolved_where_the_command_runs(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_maya(home, make_bridge, {**echo("scene_file"), "export_fbx": written_crate})
    monkeypatch.chdir(tmp_path)
    here = tmp_path.resolve().as_posix()
    saved = run(capsys, "file", "save", "scenes/a.ma")[1]["got"]
    assert saved == {"action": "save", "path": f"{here}/scenes/a.ma", "force": False}
    assert run(capsys, "file", "new", "--force")[1]["got"] == {
        "action": "new",
        "path": "",
        "force": True,
    }
    exported = run(capsys, "export", "out/hero.fbx", "--preset", "unreal", "--selection")[1]["got"]
    assert exported == {
        "path": f"{here}/out/hero.fbx",
        "preset": "unreal",
        "selection_only": True,
        "animation": False,
        "start": "",
        "end": "",
        "clip": "",
        "ignore_checks": False,
    }
    exported = run(capsys, "export", "walk.fbx", "--animation", "--start", "0", "--end", "30")[1]
    assert (exported["got"]["start"], exported["got"]["end"]) == ("0", "30")


def test_exec_takes_code_from_argument_file_or_stdin(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_maya(home, make_bridge, echo("execute_maya_code"))
    assert run(capsys, "exec", "result = 1")[1] == {"got": {"code": "result = 1"}}
    script = tmp_path / "build.py"
    script.write_text('print("it\'s")\nresult = 2\n', encoding="utf-8")
    assert (
        run(capsys, "exec", "-f", str(script))[1]["got"]["code"] == 'print("it\'s")\nresult = 2\n'
    )
    piped = "name = 'ящик'\nresult = name\n"
    monkeypatch.setattr("sys.stdin", io.StringIO("﻿" + piped))
    assert run(capsys, "exec")[1]["got"]["code"] == piped
    monkeypatch.setattr("sys.stdin", io.StringIO(piped))
    assert run(capsys, "exec", "-")[1]["got"]["code"] == piped


def test_exec_without_code_does_not_wait_for_a_keyboard(
    home: Path, make_bridge: BridgeFactory, capsys: Output, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = one_maya(home, make_bridge, echo("execute_maya_code"))
    monkeypatch.setattr("sys.stdin", Terminal())
    code, out, err = run(capsys, "exec")
    assert code == commands.EXIT_USAGE
    assert out is None
    assert "error: No code to run." in err
    assert "bmaya exec < script.py" in err
    assert run(capsys, "exec", "x = 1", "-f", "a.py")[0] == commands.EXIT_USAGE
    assert not [r for r in bridge.requests if r["command"] == "execute_maya_code"]


def test_shot_moves_the_png_to_the_asked_place(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    temp = tmp_path / "from-maya.png"

    def screenshot(params: dict[str, Any]) -> dict[str, Any]:
        temp.write_bytes(PNG)
        return {"path": str(temp), "width": 800, "height": 450, "frames": [1], "got": params}

    one_maya(home, make_bridge, {"get_viewport_screenshot": screenshot})
    target = tmp_path / "out" / "shot.png"
    code, out, _ = run(
        capsys,
        "shot",
        "-o",
        str(target),
        "--camera",
        "top",
        "--frames",
        "1,12",
        "--aspect",
        "1:1",
        "--light",
        "unlit",
    )
    assert code == 0
    assert out["path"] == target.resolve().as_posix()
    assert out["got"] == {
        "max_size": 0,
        "camera": "top",
        "frames": "1,12",
        "clip": "",
        "aspect": "1:1",
        "light": "unlit",
        "trail": "",
        "fit": "",
    }
    assert target.read_bytes() == PNG
    assert not temp.exists()

    default = Path(run(capsys, "shot")[1]["path"])
    assert default.parent == home / "shots"
    assert default.read_bytes() == PNG


def test_views_maps_its_flags_and_moves_the_png(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    temp = tmp_path / "from-maya.png"

    def views(params: dict[str, Any]) -> dict[str, Any]:
        temp.write_bytes(PNG)
        return {"path": str(temp), "width": 1600, "height": 1600, "got": params}

    one_maya(home, make_bridge, {"get_model_views": views})
    target = tmp_path / "hand.png"
    code, out, _ = run(
        capsys,
        "views",
        "hand",
        "glove",
        "-o",
        str(target),
        "--views",
        "front,persp",
        "--style",
        "xray",
        "--section",
        "x=0.5",
        "--no-wire",
        "--size",
        "1200",
    )
    assert code == 0
    assert out["path"] == target.resolve().as_posix()
    assert out["got"] == {
        "names": "hand,glove",
        "views": "front,persp",
        "style": "xray",
        "joint": "",
        "no_wire": True,
        "section": "x=0.5",
        "light": "",
        "max_size": 1200,
    }
    assert target.read_bytes() == PNG
    assert run(capsys, "views")[1]["got"]["names"] == ""


def test_mesh_edits_map_their_arguments(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    handlers = {
        **echo("diff_mesh"),
        **echo("remesh_mesh"),
        **echo("reduce_mesh"),
        **echo("cleanup_mesh"),
    }
    one_maya(home, make_bridge, handlers)
    assert run(capsys, "diff", "crate", "--mark")[1] == {
        "got": {"name": "crate", "mark": True, "against": ""}
    }
    assert run(capsys, "diff", "crate", "--against", "old")[1]["got"]["against"] == "old"
    assert run(capsys, "remesh", "rock", "--to", "quads", "--faces", "800")[1] == {
        "got": {"name": "rock", "to": "quads", "edge": 0, "faces": 800, "keep_hard_edges": False}
    }
    assert run(capsys, "remesh", "rock", "--edge", "0.5")[1]["got"]["edge"] == 0.5
    assert run(capsys, "reduce", "statue", "--tris", "5000", "--keep-uvs")[1] == {
        "got": {
            "name": "statue",
            "tris": 5000,
            "percent": 0,
            "keep_borders": False,
            "keep_uvs": True,
            "keep_hard_edges": False,
            "keep_quads": False,
        }
    }
    assert run(capsys, "cleanup", "scan", "--merge", "0.01", "--ngons")[1] == {
        "got": {"name": "scan", "merge": 0.01, "ngons": True}
    }


def test_skin_reports_saves_and_edits(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    handlers = {**echo("get_skin_info"), **echo("save_skin_weights"), **echo("edit_skin")}
    one_maya(home, make_bridge, handlers)
    assert run(capsys, "skin", "hand", "glove", "--max", "8")[1] == {
        "got": {"names": "hand,glove", "max": 8, "verts": "", "limit": 0}
    }
    saved = run(capsys, "skin", "hand", "--save", str(tmp_path / "w.json"))[1]
    assert saved == {"got": {"name": "hand", "path": (tmp_path / "w.json").resolve().as_posix()}}
    loaded = run(capsys, "skin", "hand", "--load", "fit.json", "--rename", "{}_r", "--prune")
    assert loaded[1]["got"] == {
        "name": "hand",
        "load": (Path.cwd() / "fit.json").resolve().as_posix(),
        "copy_from": "",
        "mirror": "",
        "rename": "{}_r",
        "prune": "0.01",
        "normalize": False,
        "max": 0,
    }
    mirrored = run(capsys, "skin", "hand_l", "--copy-from", "hand_r", "--mirror", "x")[1]["got"]
    assert (mirrored["copy_from"], mirrored["mirror"], mirrored["load"]) == ("hand_r", "x", "")
    capped = run(capsys, "skin", "body", "--normalize", "--max", "4")[1]["got"]
    assert capped["normalize"] is True and capped["max"] == 4


@pytest.mark.parametrize(
    "argv",
    [
        ["skin", "a", "b", "--prune"],
        ["skin", "--save", "w.json"],
        ["skin", "a", "--save", "w.json", "--prune"],
        ["skin", "a", "--save", "w.json", "--max", "4"],
        ["skin", "a", "--normalize", "--verts", "0"],
        ["skin", "a", "--rename", "{}_r"],
        ["skin", "a", "--load", "w.json", "--mirror", "x"],
        ["skin", "a", "--load", "w.json", "--copy-from", "b"],
    ],
)
def test_skin_refuses_flags_that_do_not_go_together(
    home: Path, make_bridge: BridgeFactory, capsys: Output, argv: list[str]
) -> None:
    bridge = one_maya(home, make_bridge, echo("edit_skin"))
    try:
        code = main(argv)
    except SystemExit as exit_info:
        code = exit_info.code
    assert code == commands.EXIT_USAGE
    assert bridge.requests == []
    capsys.readouterr()


def test_expected_failure_goes_to_stderr_with_its_hint(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    def missing(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("BridgeError", "No object named 'x' in the scene.", "Try `bmaya scene`.")

    one_maya(home, make_bridge, {"get_object_info": missing})
    code, out, err = run(capsys, "object", "x")
    assert code == commands.EXIT_ERROR
    assert out is None
    assert err == "error: No object named 'x' in the scene.\nhint: Try `bmaya scene`.\n"


def test_python_error_names_the_exception_and_what_was_printed(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    def fail(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("NameError", "name 'x' is not defined", "Line 2.", "made 2 cubes\n")

    one_maya(home, make_bridge, {"execute_maya_code": fail})
    code, _, err = run(capsys, "exec", "x")
    assert code == commands.EXIT_ERROR
    assert err == (
        "error: NameError: name 'x' is not defined\n"
        "hint: Line 2.\n"
        "printed before the error:\n"
        "made 2 cubes\n"
    )


def test_command_still_running_in_maya_exits_with_retry_code(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    def slow(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("Timeout", "'execute_maya_code' is still running.", "Do not resend.")

    one_maya(home, make_bridge, {"execute_maya_code": slow})
    code, _, err = run(capsys, "exec", "x")
    assert code == commands.EXIT_RETRY
    assert "hint: Do not resend." in err


def test_no_maya_and_no_autostart_explains_how_to_start_one(home: Path, capsys: Output) -> None:
    code, out, err = run(capsys, "scene")
    assert code == commands.EXIT_ERROR
    assert out is None
    assert err.startswith("error: No Maya with the bmaya bridge is running.")
    assert "bmaya start" in err


def test_maya_that_is_still_starting_exits_with_retry_code(
    home: Path, capsys: Output, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BMAYA_AUTOSTART", "headless")
    monkeypatch.setattr(sessions, "LAUNCH_WAIT", 0.2)
    monkeypatch.setattr(commands, "Launcher", lambda *args, **options: FakeLauncher())
    code, _, err = run(capsys, "scene")
    assert code == commands.EXIT_RETRY
    assert "Run the command again in about 15 seconds" in err


def test_sessions_never_starts_maya_and_names_the_caller(
    home: Path, capsys: Output, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BMAYA_AUTOSTART", "headless")
    monkeypatch.setattr(commands, "Launcher", None)
    assert run(capsys, "sessions")[1] == {"sessions": [], "current": None, "client": "tester"}


def test_use_is_remembered_by_later_commands(
    home: Path, make_bridge: BridgeFactory, capsys: Output
) -> None:
    make_bridge({"get_scene_info": lambda params: {"who": "a"}}).write_registry(home, 111)
    make_bridge({"get_scene_info": lambda params: {"who": "b"}}).write_registry(home, 222)
    code, _, err = run(capsys, "scene")
    assert code == commands.EXIT_ERROR
    assert "bmaya use ID" in err
    chosen = run(capsys, "use", "222")[1]
    assert (chosen["id"], chosen["current"]) == ("222", True)
    assert run(capsys, "scene")[1] == {"who": "b"}
    assert run(capsys, "scene", "--session", "111")[1] == {"who": "a"}
    assert run(capsys, "--session", "111", "scene")[1] == {"who": "a"}
    assert run(capsys, "sessions")[1]["current"] == "222"
    assert run(capsys, "scene", "--client", "someone-else")[0] == commands.EXIT_ERROR


def test_start_waits_for_the_new_maya_and_makes_it_current(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[tuple[str, str]] = []

    def launcher(launcher_home: Path, mode: str, scene: str = "") -> FakeLauncher:
        made.append((mode, scene))
        fake = FakeLauncher()
        fake.bridge = make_bridge({"get_scene_info": lambda params: {"who": "new"}})
        fake.home = launcher_home
        return fake

    monkeypatch.setattr(commands, "Launcher", launcher)
    make_bridge().write_registry(home, 111)
    scene = tmp_path / "barrel.ma"
    scene.write_text("", encoding="utf-8")
    code, out, _ = run(capsys, "start", "--scene", str(scene))
    assert code == 0
    assert (out["id"], out["current"]) == ("555", True)
    assert made == [("headless", scene.resolve().as_posix())]
    monkeypatch.setattr(commands, "Launcher", None)
    assert run(capsys, "scene")[1] == {"who": "new"}

    code, _, err = run(capsys, "start", "--gui", "--scene", str(tmp_path / "missing.ma"))
    assert code == commands.EXIT_ERROR
    assert "Scene file not found" in err


def test_stop_ends_the_maya_and_forgets_it(
    home: Path, make_bridge: BridgeFactory, capsys: Output, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = one_maya(
        home, make_bridge, {"stop": lambda params: {"stopped": True, "scene": "untitled"}}
    )
    monkeypatch.setattr(commands, "pid_alive", lambda pid: False)
    run(capsys, "use", "111")
    code, out, _ = run(capsys, "stop", "--force")
    assert code == 0
    assert out == {"stopped": "111", "scene": "untitled", "exited": True}
    assert bridge.requests[-1]["params"] == {"force": True}
    assert json.loads((home / "clients" / "tester.json").read_text(encoding="utf-8")) == {
        "current": None,
        "last_used": None,
        "last_command": {},
    }


def test_stop_refusal_is_passed_on(home: Path, make_bridge: BridgeFactory, capsys: Output) -> None:
    def refuse(params: dict[str, Any]) -> dict[str, Any]:
        raise FakeError("BridgeError", "This Maya has a window.", "Ask the user to close it.")

    one_maya(home, make_bridge, {"stop": refuse})
    code, out, err = run(capsys, "stop", "111")
    assert code == commands.EXIT_ERROR
    assert out is None
    assert "hint: Ask the user to close it." in err


def test_guide_and_help_cover_every_command(capsys: Output) -> None:
    assert main(["guide"]) == 0
    guide = capsys.readouterr().out
    assert guide.startswith("# Driving Maya with bmaya")
    assert main([]) == 0
    overview = capsys.readouterr().out
    assert "bmaya guide" in overview
    assert "exit codes" in overview
    actions = build_parser()._subparsers._group_actions[0]  # type: ignore[union-attr]
    for name, command in actions.choices.items():  # type: ignore[union-attr]
        assert f"    {name} " in overview
        if command.epilog:
            assert f"bmaya {name}" in command.epilog
    for name in ("scene", "object", "exec", "shot", "audit", "anim", "export", "inspect", "file"):
        assert f"bmaya {name}" in guide
    for name in ("undo", "motion", "rig", "playblast"):
        assert f"bmaya {name}" in guide
    for name in ("views", "diff", "remesh", "reduce", "cleanup"):
        assert f"bmaya {name}" in guide
    for name in ("sessions", "use", "start", "stop"):
        assert f"bmaya {name}" in guide


def test_bad_arguments_exit_with_usage_code() -> None:
    for argv in (
        ["reinstall"],
        ["file", "rename"],
        ["undo", "many"],
        ["object"],
        ["shot", "--frames", "1", "--clip", "walk"],
    ):
        with pytest.raises(SystemExit) as info:
            main(argv)
        assert info.value.code == commands.EXIT_USAGE


def test_bridge_error_types_match_the_bridge() -> None:
    assert commands.BRIDGE_ERROR_TYPES == BRIDGE_ERROR_TYPES


def test_caller_is_told_when_its_command_started_maya(
    home: Path, make_bridge: BridgeFactory, capsys: Output, monkeypatch: pytest.MonkeyPatch
) -> None:
    def launcher(launcher_home: Path, mode: str) -> FakeLauncher:
        fake = FakeLauncher()
        fake.bridge = make_bridge({"get_scene_info": lambda params: {"total": 0}})
        fake.home = launcher_home
        return fake

    monkeypatch.setenv("BMAYA_AUTOSTART", "headless")
    monkeypatch.setattr(commands, "Launcher", launcher)
    code, out, err = run(capsys, "scene")
    assert (code, out) == (0, {"total": 0})
    assert err.startswith("note: no Maya was running, so session 555 was started")
    assert "bmaya stop" in err
    assert run(capsys, "scene") == (0, {"total": 0}, "")
