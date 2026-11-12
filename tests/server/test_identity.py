import os
from pathlib import Path

import pytest

from bmaya_cli import identity
from bmaya_cli.identity import ProcessTable, client_label, host_process, program_name, sanitize
from bmaya_cli.state import KEEP_SECONDS, ClientState, StateStore

BASH_CHAIN: ProcessTable = {
    10: (9, "python.exe"),
    9: (8, "uv.exe"),
    8: (7, "bash.exe"),
    7: (6, "bash.exe"),
    6: (5, "claude.exe"),
    5: (1, "explorer.exe"),
}


@pytest.fixture
def no_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(identity.CLIENT_ENV, raising=False)
    for variable, _ in identity.AGENT_SESSIONS:
        monkeypatch.delenv(variable, raising=False)


def test_label_is_made_safe_for_a_file_name_and_an_undo_chunk() -> None:
    assert sanitize("  codex: 7 / main  ") == "codex-7-main"
    assert sanitize("claude-48d81738") == "claude-48d81738"
    assert sanitize("../..") == "..-.."
    assert sanitize(": ") == ""
    assert len(sanitize("x" * 200)) == identity.MAX_LABEL


def test_program_name_drops_folder_and_extension() -> None:
    assert program_name(r"C:\Tools\Claude.EXE") == "claude"
    assert program_name("/usr/bin/zsh") == "zsh"
    assert program_name("node") == "node"


def test_shells_and_launchers_are_skipped_on_the_way_up() -> None:
    assert host_process(BASH_CHAIN, 10) == (6, "claude")
    powershell: ProcessTable = {4: (3, "bmaya.exe"), 3: (2, "pwsh.exe"), 2: (1, "Code.exe")}
    assert host_process(powershell, 4) == (2, "code")


def test_unknown_or_looping_ancestry_gives_no_host() -> None:
    assert host_process({}, 10) is None
    assert host_process({10: (9, "python.exe")}, 10) is None
    assert host_process({10: (9, "python"), 9: (10, "bash")}, 10) is None
    assert host_process({10: (9, "python"), 9: (8, "bash"), 8: (0, "sh")}, 10) is None


def test_explicit_name_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(identity.CLIENT_ENV, "render farm 3")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "48d81738-df1b-4be6")
    assert client_label() == "render-farm-3"


def test_agent_session_id_names_the_agent(monkeypatch: pytest.MonkeyPatch, no_hints: None) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "48d81738-df1b-4be6")
    assert client_label() == "claude-48d81738"


def test_host_process_is_the_fallback(monkeypatch: pytest.MonkeyPatch, no_hints: None) -> None:
    table = {os.getpid(): (8, "python.exe"), **BASH_CHAIN}
    monkeypatch.setattr(identity, "process_table", lambda: table)
    assert client_label() == "claude-6"
    monkeypatch.setattr(identity, "process_table", lambda: {})
    assert client_label() == identity.FALLBACK_LABEL


def test_real_process_table_knows_this_process(no_hints: None) -> None:
    table = identity.process_table()
    assert os.getpid() in table
    assert client_label() == client_label()


def test_state_survives_between_processes(tmp_path: Path) -> None:
    store = StateStore(tmp_path, "claude-1")
    assert store.load() == ClientState()
    store.save(ClientState("111", "111", {"111": "undo"}))
    assert StateStore(tmp_path, "claude-1").load() == ClientState("111", "111", {"111": "undo"})
    assert StateStore(tmp_path, "codex-2").load() == ClientState()
    assert [path.name for path in (tmp_path / "clients").iterdir()] == ["claude-1.json"]


def test_broken_state_file_reads_as_empty(tmp_path: Path) -> None:
    store = StateStore(tmp_path, "claude-1")
    store.directory.mkdir()
    for text in ("{not json", "[1, 2]", '{"current": 5, "last_command": "x"}'):
        store.path.write_text(text, encoding="utf-8")
        assert store.load() == ClientState()


def test_state_that_cannot_be_written_does_not_fail_the_command(tmp_path: Path) -> None:
    blocker = tmp_path / "clients"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    StateStore(tmp_path, "claude-1").save(ClientState("111"))


def test_old_state_files_of_other_clients_are_removed(tmp_path: Path) -> None:
    mine = StateStore(tmp_path, "claude-1")
    old = StateStore(tmp_path, "gone-agent")
    recent = StateStore(tmp_path, "codex-2")
    for store in (mine, old, recent):
        store.save(ClientState("111"))
    stale = old.path.stat().st_mtime - KEEP_SECONDS - 60
    os.utime(old.path, (stale, stale))
    os.utime(mine.path, (stale, stale))
    mine.prune()
    assert sorted(path.name for path in mine.directory.iterdir()) == [
        "claude-1.json",
        "codex-2.json",
    ]
