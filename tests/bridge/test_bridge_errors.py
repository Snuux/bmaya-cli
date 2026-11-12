from __future__ import annotations

from bmaya_bridge.errors import (
    FALLBACK_HINT,
    MAX_ERROR_STDOUT,
    BridgeError,
    attach_stdout,
    error_payload,
    user_code_line,
)


def raise_from_agent_code(code: str) -> BaseException:
    try:
        exec(compile(code, "<agent>", "exec"), {})
    except BaseException as exc:
        return exc
    raise AssertionError("code did not raise")


def test_bridge_error_keeps_message_and_hint() -> None:
    error = BridgeError("Nothing selected.", hint="Select a mesh first.")
    assert str(error) == "Nothing selected."
    assert error_payload(error) == {
        "type": "BridgeError",
        "message": "Nothing selected.",
        "hint": "Select a mesh first.",
    }


def test_line_of_innermost_agent_frame() -> None:
    code = "def inner():\n    return 1 / 0\n\ninner()\n"
    exc = raise_from_agent_code(code)
    assert user_code_line(exc) == 2
    payload = error_payload(exc)
    assert payload["type"] == "ZeroDivisionError"
    assert payload["hint"] == "The error is on line 2 of the submitted code."


def test_exception_outside_agent_code_gets_fallback_hint() -> None:
    try:
        int("nope")
    except ValueError as exc:
        payload = error_payload(exc)
    assert payload["type"] == "ValueError"
    assert payload["hint"] == FALLBACK_HINT


def test_system_exit_in_agent_code_is_reported() -> None:
    exc = raise_from_agent_code("import sys\nsys.exit(3)\n")
    assert error_payload(exc)["type"] == "SystemExit"
    assert user_code_line(exc) == 2


def test_stdout_is_kept_and_clipped_from_the_start() -> None:
    exc = raise_from_agent_code("raise KeyError('k')\n")
    attach_stdout(exc, "a" * 10 + "b" * MAX_ERROR_STDOUT)
    payload = error_payload(exc)
    assert payload["type"] == "KeyError"
    assert payload["stdout"].endswith("b" * MAX_ERROR_STDOUT)
    assert payload["stdout"].startswith("[10 chars cut]")
    assert "stdout" not in error_payload(BridgeError("no output"))
