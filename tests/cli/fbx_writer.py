"""A small binary FBX writer for building edge cases the Maya fixtures do not have."""

from __future__ import annotations

import struct
import zlib
from typing import Any, NamedTuple

HEADER = b"Kaydara FBX Binary  \x00\x1a\x00"
SEP = "\x00\x01"


class N(NamedTuple):
    name: str
    props: list[Any]
    children: list[N] = []


class Arr(NamedTuple):
    kind: str
    values: list[Any]
    compress: bool = False


class Raw(NamedTuple):
    data: bytes


ARRAY_FORMATS = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "b"}


def prop_bytes(value: object) -> bytes:
    if isinstance(value, bool):
        return b"C" + struct.pack("<?", value)
    if isinstance(value, int):
        if -(2**31) <= value < 2**31:
            return b"I" + struct.pack("<i", value)
        return b"L" + struct.pack("<q", value)
    if isinstance(value, float):
        return b"D" + struct.pack("<d", value)
    if isinstance(value, str):
        data = value.encode("utf-8")
        return b"S" + struct.pack("<I", len(data)) + data
    if isinstance(value, Raw):
        return b"R" + struct.pack("<I", len(value.data)) + value.data
    if isinstance(value, Arr):
        data = struct.pack("<" + ARRAY_FORMATS[value.kind] * len(value.values), *value.values)
        encoding = 0
        if value.compress:
            data = zlib.compress(data)
            encoding = 1
        head = struct.pack("<III", len(value.values), encoding, len(data))
        return value.kind.encode() + head + data
    if isinstance(value, tuple) and len(value) == 2 and value[0] in ("Y", "F"):
        code = {"Y": "<h", "F": "<f"}[value[0]]
        return value[0].encode() + struct.pack(code, value[1])
    raise TypeError(value)


def node_bytes(node: N, at: int, wide: bool) -> bytes:
    record = struct.Struct("<QQQB" if wide else "<IIIB")
    null = b"\x00" * record.size
    props = b"".join(prop_bytes(p) for p in node.props)
    name = node.name.encode("ascii")
    start = at + record.size + len(name) + len(props)
    body = b""
    for kid in node.children:
        body += node_bytes(kid, start + len(body), wide)
    if node.children:
        body += null
    end = start + len(body)
    return record.pack(end, len(node.props), len(props), len(name)) + name + props + body


def write(nodes: list[N], version: int = 7700) -> bytes:
    wide = version >= 7500
    data = HEADER + struct.pack("<I", version)
    for node in nodes:
        data += node_bytes(node, len(data), wide)
    data += b"\x00" * (25 if wide else 13)
    return data


def p70(*entries: tuple[Any, ...]) -> N:
    return N("Properties70", [], [N("P", list(entry)) for entry in entries])


def name(text: str, kind: str) -> str:
    return text + SEP + kind
