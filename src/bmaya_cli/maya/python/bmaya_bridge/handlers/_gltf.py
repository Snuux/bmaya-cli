"""maya-glb-io, the optional glTF library: found, checked and imported without its plug-in."""

from __future__ import annotations

import os
import sys
from types import ModuleType

from maya import cmds

from bmaya_bridge.errors import BridgeError

LIBRARY = "maya_glb_io"
NAME = "maya-glb-io"
NEEDED = (1, 0, 4)
INSTALL_HINT = "Run `bmaya install --gltf`, then restart that Maya."


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for part in text.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def module_scripts() -> str:
    """The scripts folder of the maya_glb_io module Maya knows, or ""."""
    try:
        root = cmds.getModulePath(moduleName=LIBRARY)
    except RuntimeError:
        return ""
    scripts = os.path.join(root, "scripts")
    return scripts if os.path.isdir(scripts) else ""


def library() -> ModuleType:
    """The maya_glb_io package. The plug-in is never loaded: it adds a menu and checks
    GitHub for updates, and the package works without it."""
    # The library would otherwise pip-install DracoPy when a file uses Draco.
    os.environ.setdefault("MAYA_GLB_IO_OFFLINE", "1")
    try:
        import maya_glb_io
    except ImportError:
        scripts = module_scripts()
        if not scripts:
            raise BridgeError(
                f"glTF needs {NAME}, which is not installed in this Maya.", hint=INSTALL_HINT
            ) from None
        sys.path.append(scripts)
        import maya_glb_io
    found = getattr(maya_glb_io, "__version__", "0")
    if version_tuple(found) < NEEDED:
        needed = ".".join(str(part) for part in NEEDED)
        raise BridgeError(
            f"{NAME} {found} is loaded in this Maya; bmaya needs {needed} or newer.",
            hint=INSTALL_HINT,
        )
    return maya_glb_io


def version() -> str:
    return str(getattr(library(), "__version__", ""))
