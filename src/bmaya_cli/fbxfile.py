"""Records of a binary FBX file: nodes, their properties and arrays."""

from __future__ import annotations

import struct
import sys
import zlib
from array import array
from pathlib import Path
from typing import NamedTuple

from bmaya_cli.contents import ModelFileError

MAGIC = b"Kaydara FBX Binary  \x00"
HEADER_SIZE = 27
MIN_VERSION = 7100
WIDE_VERSION = 7500
NAME_SEPARATOR = "\x00\x01"

SCALARS = {
    "Y": struct.Struct("<h"),
    "C": struct.Struct("<?"),
    "I": struct.Struct("<i"),
    "F": struct.Struct("<f"),
    "D": struct.Struct("<d"),
    "L": struct.Struct("<q"),
}
ARRAY_CODES = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "b"}
ARRAY_HEADER = struct.Struct("<III")
U32 = struct.Struct("<I")
NARROW_RECORD = struct.Struct("<IIIB")
WIDE_RECORD = struct.Struct("<QQQB")


class FbxError(ModelFileError):
    pass


class Array:
    """An array property, unpacked only when its values are asked for."""

    __slots__ = ("kind", "count", "encoding", "raw")

    def __init__(self, kind: str, count: int, encoding: int, raw: bytes) -> None:
        self.kind = kind
        self.count = count
        self.encoding = encoding
        self.raw = raw

    def values(self) -> list:
        code = ARRAY_CODES[self.kind]
        data = self.raw
        if self.encoding == 1:
            try:
                data = zlib.decompress(data)
            except zlib.error as error:
                raise FbxError(f"A compressed {self.kind} array is damaged: {error}") from error
        elif self.encoding != 0:
            raise FbxError(f"An array uses an unknown encoding {self.encoding}.")
        items = array(code)
        if len(data) != self.count * items.itemsize:
            raise FbxError(
                f"An array of {self.count} '{self.kind}' values holds {len(data)} bytes."
            )
        items.frombytes(data)
        if sys.byteorder == "big":
            items.byteswap()
        return items.tolist()

    def __repr__(self) -> str:
        return f"Array({self.kind!r}, {self.count})"


Value = bool | int | float | str | bytes | Array


class Node(NamedTuple):
    name: str
    props: list[Value]
    children: list[Node]


class Document(NamedTuple):
    version: int
    nodes: list[Node]


class Reader:
    def __init__(self, data: bytes, version: int) -> None:
        self.data = data
        self.record = WIDE_RECORD if version >= WIDE_VERSION else NARROW_RECORD

    def need(self, at: int, size: int) -> None:
        if at + size > len(self.data):
            raise FbxError(
                f"The file ends at byte {len(self.data)}, inside a record at byte {at}.",
                "The file is cut short: export it again.",
            )

    def node(self, at: int) -> tuple[Node | None, int]:
        self.need(at, self.record.size)
        end, prop_count, _, name_size = self.record.unpack_from(self.data, at)
        at += self.record.size
        if end == 0:
            return None, at
        if end > len(self.data) or end < at:
            self.need(end, 0)
            raise FbxError(f"A record at byte {at} ends before it starts.")
        self.need(at, name_size)
        name = self.data[at : at + name_size].decode("ascii", "replace")
        at += name_size
        props = []
        for _ in range(prop_count):
            value, at = self.prop(at)
            props.append(value)
        children = []
        while at < end:
            child, at = self.node(at)
            if child is None:
                break
            children.append(child)
        return Node(name, props, children), end

    def prop(self, at: int) -> tuple[Value, int]:
        self.need(at, 1)
        kind = chr(self.data[at])
        at += 1
        scalar = SCALARS.get(kind)
        if scalar is not None:
            self.need(at, scalar.size)
            return scalar.unpack_from(self.data, at)[0], at + scalar.size
        if kind in ARRAY_CODES:
            self.need(at, ARRAY_HEADER.size)
            count, encoding, size = ARRAY_HEADER.unpack_from(self.data, at)
            at += ARRAY_HEADER.size
            self.need(at, size)
            return Array(kind, count, encoding, self.data[at : at + size]), at + size
        if kind in ("S", "R"):
            self.need(at, U32.size)
            size = U32.unpack_from(self.data, at)[0]
            at += U32.size
            self.need(at, size)
            raw = self.data[at : at + size]
            if kind == "R":
                return raw, at + size
            return raw.decode("utf-8", "replace"), at + size
        raise FbxError(f"Unknown property type {kind!r} at byte {at - 1}.")


def parse(data: bytes) -> Document:
    if not data.startswith(MAGIC):
        if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b";"):
            raise FbxError(
                "This is an ASCII FBX file; only binary FBX is read.",
                "Export it again as binary FBX: `bmaya export` writes binary.",
            )
        raise FbxError("This is not an FBX file.")
    if len(data) < HEADER_SIZE:
        raise FbxError("The file ends inside the FBX header.", "Export it again.")
    version = U32.unpack_from(data, 23)[0]
    if version < MIN_VERSION:
        raise FbxError(
            f"FBX version {version} is too old; versions from {MIN_VERSION} are read.",
            "Export it again with FBX 2011 or newer.",
        )
    reader = Reader(data, version)
    nodes = []
    at = HEADER_SIZE
    while at < len(data):
        node, at = reader.node(at)
        if node is None:
            break
        nodes.append(node)
    return Document(version, nodes)


def read(path: str | Path) -> Document:
    return parse(Path(path).read_bytes())


def child(node: Node | Document, name: str) -> Node | None:
    items = node.nodes if isinstance(node, Document) else node.children
    for item in items:
        if item.name == name:
            return item
    return None


def children(node: Node | Document, name: str) -> list[Node]:
    items = node.nodes if isinstance(node, Document) else node.children
    return [item for item in items if item.name == name]


def props70(node: Node | None) -> dict[str, list[Value]]:
    """Property name -> its values, from a node's Properties70 block."""
    block = child(node, "Properties70") if node is not None else None
    if block is None:
        return {}
    return {str(p.props[0]): p.props[4:] for p in block.children if p.name == "P" and p.props}


def split_name(raw: Value) -> tuple[str, str]:
    """`Name\\x00\\x01Class` in a binary file -> (name, class)."""
    text = str(raw)
    if NAME_SEPARATOR in text:
        name, kind = text.split(NAME_SEPARATOR, 1)
        return name, kind
    if "::" in text:
        kind, name = text.split("::", 1)
        return name, kind
    return text, ""
