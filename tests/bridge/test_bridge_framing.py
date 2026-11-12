from __future__ import annotations

import ast
import json
import socket
import struct
from pathlib import Path

import pytest
from bmaya_bridge import server
from bmaya_bridge.server import MAX_FRAME, FrameError, encode_frame, read_frame

BRIDGE_DIR = Path(server.__file__).parent
CORE_FILES = ["__init__.py", "server.py", "dispatch.py", "registry.py", "journal.py", "errors.py"]


def test_frame_round_trip() -> None:
    left, right = socket.socketpair()
    with left, right:
        message = {"id": 7, "command": "get_scene_info", "params": {"query": "pCube*"}}
        left.sendall(encode_frame(message) + encode_frame({"text": "ünïcode"}))
        assert json.loads(read_frame(right) or b"") == message
        assert json.loads(read_frame(right) or b"") == {"text": "ünïcode"}
        left.close()
        assert read_frame(right) is None


def test_frame_header_is_big_endian_length() -> None:
    frame = encode_frame({"a": 1})
    assert struct.unpack(">I", frame[:4])[0] == len(frame) - 4


def test_oversized_frame_is_rejected() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack(">I", MAX_FRAME + 1))
        with pytest.raises(FrameError):
            read_frame(right)


def test_truncated_frame_is_an_error() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack(">I", 10) + b"abc")
        left.close()
        with pytest.raises(FrameError):
            read_frame(right)


@pytest.mark.parametrize("name", CORE_FILES)
def test_core_parses_as_python_39(name: str) -> None:
    source = (BRIDGE_DIR / name).read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 9))
    assert "bmaya_mcp" not in source
