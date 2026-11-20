"""maya-glb-io, the optional glTF library: found, checked and imported without its plug-in."""

from __future__ import annotations

import os
import sys
from types import ModuleType

from maya import cmds

from bmaya_bridge.errors import BridgeError

LIBRARY = "maya_glb_io"
NAME = "maya-glb-io"
NEEDED = (1, 1, 0)
INSTALL_HINT = "Run `bmaya install --gltf`, then restart that Maya."
SUFFIXES = (".glb", ".gltf")
NAME_ATTR = "gltfName"
OWNER_ATTR = "gltfNameOf"


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


def string_attr(node: str, attr: str) -> str:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return ""
    return cmds.getAttr(f"{node}.{attr}") or ""


def own_name(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def written_name(node: str) -> str:
    """The name the library gives a node in a file: the glTF name it kept on the node at
    import ("Bone.001", "mixamorig:Hips": names Maya cannot hold), while the node still has the
    Maya name that import gave it; else the node's own name. The same rule as
    maya_glb_io._names, read from the attributes so it works without the library."""
    name = string_attr(node, NAME_ATTR)
    if name and string_attr(node, OWNER_ATTR) == own_name(node):
        return str(name)
    return own_name(node)


def version() -> str:
    return str(getattr(library(), "__version__", ""))
