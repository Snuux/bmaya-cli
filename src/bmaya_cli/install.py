import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from bmaya_cli import __version__

MODULE_NAME = "bmaya"
LEGACY_MODULE_NAME = "bmaya-mcp"
MOD_TEMPLATE = "bmaya.mod"
PACKAGED = Path(__file__).resolve().parent / "maya"
FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class ModulePaths:
    app_dir: Path
    modules_dir: Path
    module_dir: Path
    mod_file: Path


def module_paths(app_dir: Path) -> ModulePaths:
    modules_dir = app_dir / "modules"
    return ModulePaths(
        app_dir=app_dir,
        modules_dir=modules_dir,
        module_dir=modules_dir / MODULE_NAME,
        mod_file=modules_dir / f"{MODULE_NAME}.mod",
    )


def default_maya_app_dir() -> Path:
    override = os.environ.get("MAYA_APP_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        documents = windows_documents_dir() or Path.home() / "Documents"
        return documents / "maya"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "Autodesk" / "maya"
    return Path.home() / "maya"


def windows_documents_dir() -> Path | None:
    import ctypes
    import uuid
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    folder_id = Guid.from_buffer_copy(uuid.UUID(FOLDERID_DOCUMENTS).bytes_le)
    buffer = ctypes.c_void_p()
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    try:
        status = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(buffer)
        )
        if status != 0 or not buffer.value:
            return None
        return Path(ctypes.wstring_at(buffer.value))
    finally:
        ole32.CoTaskMemFree(buffer)


def render_mod(module_dir: Path) -> str:
    template = (PACKAGED / MOD_TEMPLATE).read_text(encoding="utf-8")
    return template.format(version=__version__, path=module_dir.as_posix())


def install(app_dir: Path) -> ModulePaths:
    paths = module_paths(app_dir)
    if not (PACKAGED / "python" / "bmaya_bridge").is_dir():
        raise InstallError(f"The packaged Maya files are missing from {PACKAGED}.")
    _refuse_link(paths.module_dir)
    paths.modules_dir.mkdir(parents=True, exist_ok=True)
    if paths.module_dir.exists():
        shutil.rmtree(paths.module_dir)
    shutil.copytree(
        PACKAGED,
        paths.module_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", MOD_TEMPLATE),
    )
    paths.mod_file.write_text(render_mod(paths.module_dir), encoding="utf-8")
    remove_legacy(paths.modules_dir)
    return paths


def remove_legacy(modules_dir: Path) -> list[Path]:
    """Two modules in one Maya would start two bridges, so the pre-rename one goes."""
    module_dir = modules_dir / LEGACY_MODULE_NAME
    mod_file = modules_dir / f"{LEGACY_MODULE_NAME}.mod"
    _refuse_link(module_dir)
    removed = []
    if mod_file.is_file():
        mod_file.unlink()
        removed.append(mod_file)
    if module_dir.is_dir():
        shutil.rmtree(module_dir)
        removed.append(module_dir)
    return removed


def uninstall(app_dir: Path) -> list[Path]:
    paths = module_paths(app_dir)
    _refuse_link(paths.module_dir)
    removed = []
    if paths.mod_file.is_file():
        paths.mod_file.unlink()
        removed.append(paths.mod_file)
    if paths.module_dir.is_dir():
        shutil.rmtree(paths.module_dir)
        removed.append(paths.module_dir)
    return removed + remove_legacy(paths.modules_dir)


def installed_version(app_dir: Path) -> str | None:
    try:
        lines = module_paths(app_dir).mod_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "+" and parts[1] == MODULE_NAME:
            return parts[2]
    return None


def _refuse_link(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & reparse_point:
        raise InstallError(
            f"{path} is a link, probably to a development checkout. Remove it yourself first."
        )
