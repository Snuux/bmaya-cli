import os
import sys
from pathlib import Path

import pytest

from bmaya_mcp import __version__
from bmaya_mcp.install import (
    default_maya_app_dir,
    install,
    installed_version,
    module_paths,
    uninstall,
    windows_documents_dir,
)


@pytest.fixture
def app_dir(tmp_path: Path) -> Path:
    app_dir = tmp_path / "maya"
    modules = app_dir / "modules"
    (modules / "other").mkdir(parents=True)
    (modules / "other" / "tool.py").write_text("print('other tool')\n", encoding="utf-8")
    (modules / "other.mod").write_text("+ other 1.0 C:/other\n", encoding="utf-8")
    (modules / "bmaya-mcp-extras.mod").write_text("+ extras 2.0 C:/extras\n", encoding="utf-8")
    (app_dir / "2027").mkdir()
    (app_dir / "2027" / "userSetup.py").write_text("print('mine')\n", encoding="utf-8")
    return app_dir


def snapshot(app_dir: Path) -> dict[str, bytes]:
    paths = module_paths(app_dir)
    files = {}
    for path in app_dir.rglob("*"):
        if path == paths.mod_file or paths.module_dir in path.parents:
            continue
        if path.is_file():
            files[path.relative_to(app_dir).as_posix()] = path.read_bytes()
    return files


def test_install_copies_module_and_writes_mod_file(app_dir: Path) -> None:
    before = snapshot(app_dir)
    paths = install(app_dir)
    assert paths.module_dir == app_dir / "modules" / "bmaya-mcp"
    assert (paths.module_dir / "python" / "bmaya_bridge" / "server.py").is_file()
    assert (paths.module_dir / "python" / "bmaya_bridge" / "handlers" / "scene.py").is_file()
    assert (paths.module_dir / "scripts" / "userSetup.py").is_file()
    assert (paths.module_dir / "plug-ins" / "bmaya_mcp_plugin.py").is_file()
    assert not (paths.module_dir / "bmaya-mcp.mod").exists()
    assert not list(paths.module_dir.rglob("__pycache__"))
    assert paths.mod_file.read_text(encoding="utf-8").splitlines() == [
        f"+ bmaya-mcp {__version__} {paths.module_dir.as_posix()}",
        "PYTHONPATH+:=python",
        "MAYA_PLUG_IN_PATH+:=plug-ins",
    ]
    assert installed_version(app_dir) == __version__
    assert snapshot(app_dir) == before


def test_install_again_replaces_the_previous_copy(app_dir: Path) -> None:
    paths = install(app_dir)
    leftover = paths.module_dir / "python" / "bmaya_bridge" / "old_handler.py"
    leftover.write_text("stale = True\n", encoding="utf-8")
    paths.mod_file.write_text("+ bmaya-mcp 0.0.1 C:/old\n", encoding="utf-8")
    install(app_dir)
    assert not leftover.exists()
    assert installed_version(app_dir) == __version__


def test_uninstall_removes_only_its_own_files(app_dir: Path) -> None:
    before = snapshot(app_dir)
    paths = install(app_dir)
    assert uninstall(app_dir) == [paths.mod_file, paths.module_dir]
    assert not paths.mod_file.exists()
    assert not paths.module_dir.exists()
    assert snapshot(app_dir) == before
    assert uninstall(app_dir) == []
    assert installed_version(app_dir) is None


def test_install_creates_a_missing_modules_folder(tmp_path: Path) -> None:
    paths = install(tmp_path / "fresh")
    assert paths.mod_file.is_file()


def test_maya_app_dir_follows_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_APP_DIR", str(tmp_path))
    assert default_maya_app_dir() == tmp_path


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folder")
def test_windows_documents_folder_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAYA_APP_DIR", raising=False)
    documents = windows_documents_dir()
    assert documents is not None
    assert documents.is_dir()
    assert default_maya_app_dir() == documents / "maya"
    assert os.path.isabs(documents)
