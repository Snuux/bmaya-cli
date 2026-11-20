import json
from pathlib import Path

import pytest

from bmaya_cli import cli, sessions
from bmaya_cli.contents import Contents, Joint, Mesh, Take, Texture
from bmaya_cli.modelfile import issues_of, report

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
