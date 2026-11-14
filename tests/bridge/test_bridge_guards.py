import pytest
from bmaya_bridge.guards import headless_crash_reason


@pytest.mark.parametrize(
    "code",
    [
        "cmds.ogs(reset=True)",
        "cmds.ogs( reset = 1 )",
        "x = 1\ncmds.ogs(pause=False, reset=True)\n",
    ],
)
def test_ogs_reset_is_flagged(code: str) -> None:
    assert headless_crash_reason(code) == "cmds.ogs(reset=True)"


@pytest.mark.parametrize(
    "code",
    [
        "cmds.ogs(reloadTextures=True)",
        "cmds.ogs(reset=False)",
        "reset = True",
    ],
)
def test_safe_code_passes(code: str) -> None:
    assert headless_crash_reason(code) == ""
