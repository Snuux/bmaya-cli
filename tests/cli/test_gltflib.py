import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from bmaya_cli import gltflib
from bmaya_cli.cli import main
from bmaya_cli.install import InstallError

PACKAGE = "maya-glb-io/scripts/maya_glb_io/__init__.py"


def archive(files: dict[str, str]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as writer:
        for name, text in files.items():
            writer.writestr(name, text)
    return data.getvalue()


RELEASE = archive(
    {
        PACKAGE: '__version__ = "1.0.3"\n',
        "maya-glb-io/scripts/maya_glb_io/exporter.py": "\n",
        "maya-glb-io/plug-ins/maya_glb_io.py": "\n",
        "maya-glb-io/LICENSE": "MIT\n",
        "maya-glb-io/install.py": "print('not wanted')\n",
        "maya-glb-io/docs/index.html": "<html></html>\n",
    }
)


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return RELEASE

    monkeypatch.setattr(gltflib, "SHA256", hashlib.sha256(RELEASE).hexdigest())
    monkeypatch.setattr(gltflib, "download", fetch)
    return fetched


def test_install_keeps_the_library_and_writes_a_module_file(
    tmp_path: Path, pinned: list[str]
) -> None:
    paths = gltflib.install_library(tmp_path)
    assert pinned == [gltflib.URL]
    assert (paths.module_dir / "scripts" / "maya_glb_io" / "exporter.py").is_file()
    assert (paths.module_dir / "plug-ins" / "maya_glb_io.py").is_file()
    assert (paths.module_dir / "LICENSE").is_file()
    assert not (paths.module_dir / "install.py").exists()
    assert not (paths.module_dir / "docs").exists()
    mod = paths.mod_file.read_text(encoding="utf-8")
    assert mod.startswith(f"+ maya_glb_io {gltflib.VERSION} {paths.module_dir.as_posix()}\n")
    assert "PYTHONPATH +:= scripts" in mod
    assert gltflib.library_version(tmp_path) == gltflib.VERSION


def test_install_replaces_an_older_copy(tmp_path: Path, pinned: list[str]) -> None:
    paths = gltflib.library_paths(tmp_path)
    (paths.module_dir / "scripts").mkdir(parents=True)
    (paths.module_dir / "scripts" / "stale.py").write_text("\n", encoding="utf-8")
    gltflib.install_library(tmp_path)
    assert not (paths.module_dir / "scripts" / "stale.py").exists()
    assert not paths.module_dir.with_name("maya_glb_io.new").exists()


def test_a_wrong_checksum_installs_nothing(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="checksum"):
        gltflib.install_library(tmp_path, fetch=lambda url: RELEASE)
    assert not gltflib.library_paths(tmp_path).module_dir.exists()
    assert gltflib.library_version(tmp_path) is None


@pytest.mark.parametrize(
    "name", ["maya-glb-io/../evil.py", "/maya-glb-io/scripts/x.py", "maya-glb-io/scripts/..\\x"]
)
def test_unsafe_paths_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    bad = archive({PACKAGE: "\n", name: "\n"})
    monkeypatch.setattr(gltflib, "SHA256", hashlib.sha256(bad).hexdigest())
    with pytest.raises(InstallError, match="unsafe path"):
        gltflib.install_library(tmp_path, fetch=lambda url: bad)
    assert not gltflib.library_paths(tmp_path).module_dir.exists()


def test_an_archive_without_the_package_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = archive({"maya-glb-io/LICENSE": "MIT\n"})
    monkeypatch.setattr(gltflib, "SHA256", hashlib.sha256(empty).hexdigest())
    with pytest.raises(InstallError, match="no scripts/maya_glb_io"):
        gltflib.install_library(tmp_path, fetch=lambda url: empty)


def test_install_and_uninstall_from_the_command_line(
    tmp_path: Path, pinned: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    app_dir = tmp_path / "maya"
    assert main(["install", "--gltf", "--maya-app-dir", str(app_dir)]) == 0
    out = capsys.readouterr().out
    assert f"Installed maya-glb-io {gltflib.VERSION}" in out
    assert not (app_dir / "modules" / "bmaya.mod").exists()
    main(["doctor", "--maya-app-dir", str(app_dir)])
    assert f"glTF: maya-glb-io {gltflib.VERSION} in" in capsys.readouterr().out
    assert main(["uninstall", "--gltf", "--maya-app-dir", str(app_dir)]) == 0
    assert "Removed" in capsys.readouterr().out
    main(["doctor", "--maya-app-dir", str(app_dir)])
    assert "`bmaya install --gltf`" in capsys.readouterr().out


def test_doctor_names_an_old_library(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = gltflib.library_paths(tmp_path)
    paths.module_dir.mkdir(parents=True)
    paths.mod_file.write_text("+ maya_glb_io 1.0.1 C:/old\n", encoding="utf-8")
    main(["doctor", "--maya-app-dir", str(tmp_path)])
    assert f"1.0.1 is installed but this bmaya pins {gltflib.VERSION}" in capsys.readouterr().out
