"""maya-glb-io, the optional glTF library: download it, install it as a Maya module, report it.

bmaya pins one release by URL and checksum. The library is a fork kept for bmaya; moving to
another release means changing VERSION, URL and SHA256 together.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bmaya_cli.install import InstallError, mod_version, module_paths, refuse_link

NAME = "maya-glb-io"
MODULE_NAME = "maya_glb_io"
VERSION = "1.1.0"
URL = "https://github.com/Snuux/maya-glb-io/releases/download/v1.1.0/maya-glb-io-v1.1.0.zip"
SHA256 = "05ef8e23429c13a89c7ccbfbdfce7edb7d46de212976e72f316e91d58b3e0b5d"
SOURCE = "https://github.com/Snuux/maya-glb-io"
ARCHIVE_ROOT = "maya-glb-io"
KEPT = ("scripts", "plug-ins", "LICENSE", "CHANGELOG.md", "README.md")
DOWNLOAD_SECONDS = 60
USER_AGENT = "bmaya-cli"
# The library puts the Draco decoder (the DracoPy wheel) here, one folder per Python version.
DRACO_DIR = "draco"
DRACO_SECONDS = 360
DRACO_SCRIPT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from maya_glb_io import _draco\n"
    "ok, text = _draco.install()\n"
    "print(text)\n"
    "sys.exit(0 if ok else 1)\n"
)

Fetch = Callable[[str], bytes]
Run = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class LibraryPaths:
    module_dir: Path
    mod_file: Path


def library_paths(app_dir: Path) -> LibraryPaths:
    modules_dir = module_paths(app_dir).modules_dir
    return LibraryPaths(
        module_dir=modules_dir / MODULE_NAME,
        mod_file=modules_dir / f"{MODULE_NAME}.mod",
    )


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_SECONDS) as response:
        return response.read()


def checked(data: bytes) -> bytes:
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        raise InstallError(
            f"The downloaded {NAME} archive has checksum {digest}, not the pinned {SHA256}. "
            "Nothing was installed."
        )
    return data


def member_path(name: str) -> PurePosixPath | None:
    """Where an archive member goes inside the module folder; None for what is not kept."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise InstallError(f"The {NAME} archive holds an unsafe path: {name}")
    if len(path.parts) < 2 or path.parts[0] != ARCHIVE_ROOT:
        return None
    inner = PurePosixPath(*path.parts[1:])
    if inner.parts[0] not in KEPT:
        return None
    return inner


def unpack(data: bytes, target: Path) -> int:
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            inner = member_path(info.filename)
            if inner is None or info.is_dir():
                continue
            destination = target.joinpath(*inner.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
            count += 1
    if not (target / "scripts" / MODULE_NAME / "__init__.py").is_file():
        raise InstallError(f"The {NAME} archive has no scripts/{MODULE_NAME} package.")
    return count


def render_mod(module_dir: Path) -> str:
    return (
        f"+ {MODULE_NAME} {VERSION} {module_dir.as_posix()}\n"
        "PYTHONPATH +:= scripts\n"
        "MAYA_PLUG_IN_PATH +:= plug-ins\n"
    )


def install_library(app_dir: Path, fetch: Fetch | None = None) -> LibraryPaths:
    paths = library_paths(app_dir)
    refuse_link(paths.module_dir)
    data = checked((fetch or download)(URL))
    staging = paths.module_dir.with_name(MODULE_NAME + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        unpack(data, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    decoder = paths.module_dir / DRACO_DIR
    if decoder.is_dir():
        decoder.rename(staging / DRACO_DIR)
    if paths.module_dir.exists():
        shutil.rmtree(paths.module_dir)
    staging.rename(paths.module_dir)
    paths.mod_file.write_text(render_mod(paths.module_dir), encoding="utf-8")
    return paths


def uninstall_library(app_dir: Path) -> list[Path]:
    paths = library_paths(app_dir)
    refuse_link(paths.module_dir)
    removed = []
    if paths.mod_file.is_file():
        paths.mod_file.unlink()
        removed.append(paths.mod_file)
    if paths.module_dir.is_dir():
        shutil.rmtree(paths.module_dir)
        removed.append(paths.module_dir)
    return removed


def draco_pythons(app_dir: Path) -> list[str]:
    """The Python versions the Draco decoder is installed for, as `3.13`."""
    folder = library_paths(app_dir).module_dir / DRACO_DIR
    if not folder.is_dir():
        return []
    found = []
    for entry in sorted(folder.iterdir()):
        digits = entry.name[2:]
        if (
            entry.is_dir()
            and entry.name.startswith("py")
            and digits.isdigit()
            and any(entry.iterdir())
        ):
            found.append(f"{digits[0]}.{digits[1:]}")
    return found


def install_draco(app_dir: Path, mayapy: Path, run: Run | None = None) -> str:
    """Have the installed library pip-install its Draco decoder with this Maya's Python.
    Returns where it went."""
    paths = library_paths(app_dir)
    scripts = paths.module_dir / "scripts"
    if not (scripts / MODULE_NAME / "_draco.py").is_file():
        raise InstallError(f"{NAME} is not installed in {paths.module_dir}.")
    command = [str(mayapy), "-c", DRACO_SCRIPT, str(scripts)]
    try:
        done = (run or subprocess.run)(
            command, capture_output=True, text=True, timeout=DRACO_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(
            f"Could not run {mayapy} to install the Draco decoder: {error}"
        ) from error
    lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
    if done.returncode != 0:
        tail = "\n".join((lines + (done.stderr or "").splitlines())[-12:])
        raise InstallError(f"pip could not install the Draco decoder (DracoPy):\n{tail}")
    return lines[-1] if lines else str(paths.module_dir / DRACO_DIR)


def library_version(app_dir: Path) -> str | None:
    paths = library_paths(app_dir)
    if not paths.module_dir.is_dir():
        return None
    return mod_version(paths.mod_file, MODULE_NAME)
