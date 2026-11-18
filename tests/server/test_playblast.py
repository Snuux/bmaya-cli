import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import BridgeFactory
from fake_bridge import FakeError
from test_commands import Output, one_maya, run

from bmaya_cli import commands, playblast
from bmaya_cli.cli import main

PNG = b"\x89PNG\r\n\x1a\n fake"


@pytest.fixture(autouse=True)
def caller(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMAYA_CLIENT", "tester")
    monkeypatch.setenv("BMAYA_AUTOSTART", "off")


class Renderer:
    """Stands in for render_frames: writes the PNGs of each chunk it is asked for."""

    def __init__(self, total: int, ms: int = 100, fail_on_call: int = 0) -> None:
        self.total = total
        self.ms = ms
        self.fail_on_call = fail_on_call
        self.calls: list[dict[str, Any]] = []

    def __call__(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(params))
        if self.fail_on_call and len(self.calls) == self.fail_on_call:
            raise FakeError("Timeout", "'render_frames' is still running.", "Do not resend.")
        offset = params["offset"]
        batch = range(offset, min(self.total, offset + params["count"]))
        for index in batch:
            (Path(params["folder"]) / f"frame_{index:05d}.png").write_bytes(PNG)
        return {
            "total": self.total,
            "offset": offset,
            "rendered": len(batch),
            "range": [0, self.total - 1],
            "width": 640,
            "height": 360,
            "fps": 30,
            "step": 1,
            "ms_per_frame": self.ms,
            "clip": "walk",
        }


def test_chunks_grow_from_the_first_call_and_ffmpeg_joins_them(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    render = Renderer(total=40, ms=1000)
    one_maya(home, make_bridge, {"render_frames": render})
    ran: list[list[str]] = []

    def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        Path(command[-1]).write_bytes(b"GIF89a")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(playblast.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(playblast.subprocess, "run", fake_run)
    target = tmp_path / "out" / "walk.gif"
    code, out, err = run(capsys, "playblast", str(target), "--clip", "walk", "--fit", "zombie")
    assert code == 0, err
    assert [call["count"] for call in render.calls] == [12, 20, 20]
    assert [call["offset"] for call in render.calls] == [0, 12, 32]
    assert render.calls[0]["fit"] == "zombie" and render.calls[0]["clip"] == "walk"
    assert out["path"] == target.resolve().as_posix() and out["format"] == "gif"
    assert (out["frames"], out["fps"], out["calls"], out["clip"]) == (40, 30, 3, "walk")
    command = ran[0]
    assert command[command.index("-framerate") + 1] == "30"
    assert "palettegen" in command[command.index("-vf") + 1]
    assert command[command.index("-i") + 1].endswith("frame_%05d.png")
    assert not Path(command[command.index("-i") + 1]).parent.exists()


def test_mp4_pads_to_even_size(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_maya(home, make_bridge, {"render_frames": Renderer(total=5)})
    ran: list[list[str]] = []

    def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(playblast.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(playblast.subprocess, "run", fake_run)
    assert run(capsys, "playblast", str(tmp_path / "walk.mp4"))[0] == 0
    assert "libx264" in ran[0] and "yuv420p" in ran[0]
    assert ran[0][ran[0].index("-vf") + 1].startswith("pad=")


def test_without_ffmpeg_the_frames_stay_beside_the_output(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_maya(home, make_bridge, {"render_frames": Renderer(total=5)})
    monkeypatch.setattr(playblast.shutil, "which", lambda name: None)
    code, out, _ = run(capsys, "playblast", str(tmp_path / "walk.gif"))
    assert code == 0
    frames = tmp_path / "walk_frames"
    assert out["path"] == frames.resolve().as_posix() and out["encoded"] is False
    assert "ffmpeg is not on PATH" in out["note"]
    assert len(list(frames.glob("frame_*.png"))) == 5


def test_a_folder_gets_png_frames_and_old_ones_are_cleared(
    home: Path, make_bridge: BridgeFactory, capsys: Output, tmp_path: Path
) -> None:
    one_maya(home, make_bridge, {"render_frames": Renderer(total=3)})
    folder = tmp_path / "frames"
    folder.mkdir()
    (folder / "frame_00007.png").write_bytes(PNG)
    code, out, _ = run(capsys, "playblast", str(folder))
    assert code == 0 and out["format"] == "png"
    assert sorted(path.name for path in folder.glob("frame_*.png")) == [
        "frame_00000.png",
        "frame_00001.png",
        "frame_00002.png",
    ]


def test_a_timeout_keeps_the_frames_and_says_not_to_resend(
    home: Path,
    make_bridge: BridgeFactory,
    capsys: Output,
    tmp_path: Path,
) -> None:
    one_maya(home, make_bridge, {"render_frames": Renderer(total=40, fail_on_call=2)})
    folder = tmp_path / "frames"
    code, out, err = run(capsys, "playblast", str(folder))
    assert code == commands.EXIT_RETRY and out is None
    assert "after the first 12" in err and "Do not resend" in err
    assert len(list(folder.glob("frame_*.png"))) == 12


def test_unknown_output_type_is_a_usage_error(tmp_path: Path, capsys: Output) -> None:
    code = main(["playblast", str(tmp_path / "walk.avi")])
    assert code == commands.EXIT_USAGE
    assert ".gif" in capsys.readouterr().err
