from __future__ import annotations

import pytest
from bmaya_bridge import framespec
from bmaya_bridge.errors import BridgeError


def error_of(spec: str) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        framespec.parse(spec)
    assert info.value.hint, info.value.message
    return info.value


def test_lists_ranges_and_steps() -> None:
    assert framespec.parse("1,12,24") == [1, 12, 24]
    assert framespec.parse("0:4") == [0, 1, 2, 3, 4]
    assert framespec.parse("0:8:2") == [0, 2, 4, 6, 8]
    assert framespec.parse("0:9:4") == [0, 4, 8]
    assert framespec.parse(" 24, 1 ,1,0:2 ") == [0, 1, 2, 24]
    assert framespec.parse("-10:-8") == [-10, -9, -8]
    assert framespec.parse("1:2:0.5") == [1, 1.5, 2]
    assert framespec.parse("") == []


def test_bad_specs_explain_the_grammar() -> None:
    assert "0:30:2" in error_of("0-30").hint
    assert "0:30" in error_of("30:0").hint
    assert "above 0" in error_of("0:30:0").message
    assert "1:2:3:4" in error_of("1:2:3:4").message
    assert "5000" in error_of("0:200000").message
    assert framespec.parse("20000:21000")[-1] == 21000


def test_thin_keeps_both_ends_and_spreads_evenly() -> None:
    frames = framespec.parse("0:32")
    thinned = framespec.thin(frames, 12)
    assert thinned == [0, 3, 6, 9, 12, 15, 17, 20, 23, 26, 29, 32]
    assert framespec.thin([1, 2, 3], 12) == [1, 2, 3]
    assert framespec.thin(frames, 2) == [0, 32]


def test_step_and_spec_round_trip() -> None:
    assert framespec.uniform_step(framespec.parse("0:30:2")) == 2
    assert framespec.uniform_step([1, 2, 4]) == 0
    assert framespec.as_spec(framespec.parse("0:30:2")) == "0:30:2"
    assert framespec.as_spec(framespec.parse("5:9")) == "5:9"
    assert framespec.as_spec([1, 12, 24]) == "1,12,24"
    assert framespec.as_spec([1.5]) == "1.5"
