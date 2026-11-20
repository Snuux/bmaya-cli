import json
from pathlib import Path

import pytest

from bmaya_cli import cli, sessions
from bmaya_cli.contents import Contents, Joint, Mesh, Take, Texture, shared_gap
from bmaya_cli.gltfcontents import rate_of
from bmaya_cli.modelfile import at_scene_rate, compare, issues_of, report

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def contents(**changes: object) -> Contents:
    base = Contents(
        path="C:/x/hero.fbx",
        format="fbx",
        version=7700,
        size=10,
        unit="cm",
        unit_scale=1.0,
        up="y",
        fps=30.0,
        fps_from="file",
        meshes=[Mesh("body", 8, 6, 12, ["map1"], ["skin"], 2, 0)],
        joints=[Joint("root", "", (0.0, 0.0, 0.0), AXES), Joint("spine", "root", (0, 1, 0), AXES)],
        joints_from="bind_pose",
        takes=[Take("walk", (0.0, 1.0), 6, 2, (0.0, 1.0), 1 / 30)],
        textures=[Texture("skin", "DiffuseColor", "C:/x/skin.png", "beside")],
    )
    return base._replace(**changes)


def test_report_shape() -> None:
    out = report(contents(), limit=1, axes=True)
    assert out["meshes"]["items"][0]["skin"] == {"influences": 2, "unweighted": 0}
    assert out["joints"] == {
        "items": [
            {"name": "root", "at": [0.0, 0.0, 0.0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        ],
        "shown": 1,
        "total": 2,
        "from": "bind_pose",
    }
    assert out["takes"]["items"] == [
        {"name": "walk", "frames": [0.0, 30.0], "seconds": [0.0, 1.0], "curves": 6, "animated": 2}
    ]
    assert out["issues"] == []
    assert "fps_from" not in out
    json.dumps(out, allow_nan=False)


def test_takes_show_keys_only_when_they_differ() -> None:
    take = Take("Take 001", (0.0, 4.0), 3, 1, (0.0, 1.0), 1 / 30)
    out = report(contents(takes=[take], fps=None, fps_from=""))
    assert out["takes"]["items"][0]["keys"] == [0.0, 1.0]
    assert "frames" not in out["takes"]["items"][0]


def test_keys_baked_at_another_rate_are_shown() -> None:
    take = Take("walk", (0.0, 1.0), 6, 2, (0.0, 1.0), 1 / 24)
    out = report(contents(takes=[take]))
    assert out["takes"]["items"][0]["keys_fps"] == 24
    assert out["issues"] == [
        "take 'walk': keys are baked at 24 fps, the file says 30; Maya's FBX plugin can keep "
        "the rate of an earlier export, export it again"
    ]


def test_thinned_keys_still_give_the_frame_step() -> None:
    frame = 1 / 30
    assert shared_gap({round(2 * frame, 6): 3, round(3 * frame, 6): 1}) == pytest.approx(
        frame, abs=1e-5
    )
    assert shared_gap({round(10 * frame, 6): 1}) == pytest.approx(10 * frame, abs=1e-5)
    assert shared_gap({}) is None


def test_takes_thinned_to_other_steps_share_one_rate() -> None:
    walk = Take("walk", (0.0, 1.0), 3, 1, (0.0, 1.0), 2 / 30)
    run = Take("run", (2.0, 3.0), 3, 1, (2.0, 3.0), 3 / 30)
    fps = rate_of([walk, run])
    assert fps == pytest.approx(30.0, abs=0.01)
    both = contents(format="glb", fps=fps, fps_from="keys", takes=[walk, run])
    assert issues_of(both) == []
    assert rate_of([]) is None


def test_a_gltf_with_few_keys_is_read_at_the_scene_rate() -> None:
    take = Take("walk", (0.0, 1.0), 3, 1, (0.0, 1.0), 10 / 30)
    sparse = contents(format="glb", fps=3.0, fps_from="keys", takes=[take])
    expected = {"fps": 30.0, "takes": [{"name": "walk", "frames": [0, 30]}]}
    assert compare(sparse, expected) == ["the file says 3 fps, the scene runs at 30"]
    assert compare(at_scene_rate(sparse, 30.0), expected) == []
    off_grid = sparse._replace(takes=[take._replace(key_step=1 / 24)], fps=24.0)
    assert at_scene_rate(off_grid, 30.0).fps == 24.0
    assert at_scene_rate(contents(), 24.0).fps == 30.0


def test_issues() -> None:
    found = issues_of(
        contents(
            meshes=[Mesh("ghost", 0, 0, 0, [], [], 0, 0), Mesh("arm", 4, 1, 2, [], [], 3, 2)],
            joints=[Joint("hand", "", (0, 0, 0), AXES), Joint("hand", "", (1, 0, 0), AXES)],
            textures=[Texture("m", "DiffuseColor", "C:/gone.png", "")],
        )
    )
    assert found == [
        "mesh 'ghost' is empty: it has no faces",
        "mesh 'arm': 2 vertices have no skin weight",
        "1 joint names are used twice, engines keep one: hand",
        "1 textures not found: C:/gone.png",
    ]


def test_inspect_runs_without_maya(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_sessions(*args: object, **kwargs: object) -> None:
        raise AssertionError("inspect looked for a Maya session")

    monkeypatch.setattr(sessions.SessionManager, "__init__", no_sessions)
    assert cli.main(["inspect", str(FIXTURES / "walker.fbx"), "--limit", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [t["name"] for t in out["takes"]["items"]] == ["walk", "wave"]
    assert out["joints"]["shown"] == 2 and out["joints"]["total"] == 3


def test_inspect_errors_carry_hints(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert cli.main(["inspect", str(FIXTURES / "crate_ascii.fbx")]) == 1
    err = capsys.readouterr().err
    assert "error: This is an ASCII FBX file" in err and "hint:" in err
    assert cli.main(["inspect", str(tmp_path / "nothing.fbx")]) == 1
    assert "No such file" in capsys.readouterr().err
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    assert cli.main(["inspect", str(text)]) == 1
    assert "It reads binary .fbx" in capsys.readouterr().err


def test_read_layout_gives_the_uvs_of_a_named_mesh() -> None:
    from bmaya_cli.modelfile import read_layout

    layout = read_layout(FIXTURES / "crate.fbx", "|props|crate")
    assert layout["name"] == "crate"
    assert len(layout["faces"]) == 6 and all(len(face) == 4 for face in layout["faces"])
    assert len(layout["uvs"]) == 14
    assert all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in layout["uvs"])


def test_a_file_name_is_matched_the_way_maya_holds_it() -> None:
    from bmaya_cli.modelfile import maya_name

    assert maya_name("Body.001") == "Body_001"
    assert maya_name("Left Arm") == "Left_Arm"
    assert maya_name("crate_7") == "crate_7"
    assert maya_name("\u30eb\u30fc\u30c8") == "___"


def test_read_layout_takes_the_only_mesh_whatever_its_name() -> None:
    from bmaya_cli.modelfile import read_layout

    layout = read_layout(FIXTURES / "walker.fbx", "crate")
    assert layout["name"] != "crate" and layout["faces"]


def test_read_layout_sends_the_uvs_unrounded_with_the_set_name() -> None:
    from bmaya_cli.fbxcontents import read_fbx_layouts
    from bmaya_cli.modelfile import read_layout

    layout = read_layout(FIXTURES / "crate.fbx", "crate")
    raw = read_fbx_layouts(FIXTURES / "crate.fbx")[0]
    assert layout["set"] == "map1"
    assert layout["uvs"] == [[u, v] for u, v in raw.uvs]
