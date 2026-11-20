import pytest

from bmaya_cli import fbxfile
from bmaya_cli.fbxfile import FbxError

from .fbx_writer import Arr, N, Raw, p70, write

TREE = [
    N(
        "Top",
        [("Y", -3), True, 7, ("F", 1.5), 2.25, 2**40, "text", Raw(b"\x00\x01")],
        [
            N("Ints", [Arr("i", [1, -2, 3])]),
            N("Packed", [Arr("d", [0.5] * 100, compress=True)]),
            N("Longs", [Arr("l", [2**40, -1])]),
            N("Floats", [Arr("f", [0.25, -0.5])]),
            N("Bools", [Arr("b", [1, 0, 1])]),
            p70(("Speed", "double", "Number", "", 4.0), ("Name", "KString", "", "", "hi")),
        ],
    ),
    N("Second", ["Model" + "\x00\x01" + "LimbNode"]),
]


@pytest.mark.parametrize("version", [7400, 7500, 7700])
def test_reads_every_property_type(version: int) -> None:
    doc = fbxfile.parse(write(TREE, version))
    assert doc.version == version
    top = fbxfile.child(doc, "Top")
    assert top is not None
    assert top.props[:7] == [-3, True, 7, 1.5, 2.25, 2**40, "text"]
    assert top.props[7] == b"\x00\x01"
    assert fbxfile.child(top, "Ints").props[0].values() == [1, -2, 3]
    packed = fbxfile.child(top, "Packed").props[0]
    assert packed.count == 100 and packed.encoding == 1
    assert packed.values() == [0.5] * 100
    assert fbxfile.child(top, "Longs").props[0].values() == [2**40, -1]
    assert fbxfile.child(top, "Floats").props[0].values() == [0.25, -0.5]
    assert fbxfile.child(top, "Bools").props[0].values() == [1, 0, 1]
    assert fbxfile.props70(top) == {"Speed": [4.0], "Name": ["hi"]}
    assert [n.name for n in doc.nodes] == ["Top", "Second"]


def test_splits_binary_and_ascii_names() -> None:
    assert fbxfile.split_name("hand\x00\x01Model") == ("hand", "Model")
    assert fbxfile.split_name("Model::hand") == ("hand", "Model")
    assert fbxfile.split_name("plain") == ("plain", "")


def test_refuses_what_it_cannot_read() -> None:
    with pytest.raises(FbxError, match="ASCII") as ascii_error:
        fbxfile.parse(b"; FBX 7.7.0 project file\nFBXHeaderExtension:  {\n")
    assert "binary" in ascii_error.value.hint
    with pytest.raises(FbxError, match="not an FBX"):
        fbxfile.parse(b"PK\x03\x04 zip")
    with pytest.raises(FbxError, match="too old"):
        fbxfile.parse(write(TREE, 6100))


def test_reports_a_truncated_file() -> None:
    data = write(TREE, 7700)
    with pytest.raises(FbxError, match="ends at byte") as error:
        fbxfile.parse(data[:120])
    assert "export it again" in error.value.hint


def test_reports_a_damaged_compressed_array() -> None:
    damaged = fbxfile.Array("d", 4, 1, b"not zlib")
    with pytest.raises(FbxError, match="damaged"):
        damaged.values()
    short = fbxfile.Array("i", 4, 0, b"\x00" * 8)
    with pytest.raises(FbxError, match="holds 8 bytes"):
        short.values()
