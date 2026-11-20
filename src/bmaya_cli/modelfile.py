"""`bmaya inspect`: read a model file without Maya and describe it."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from bmaya_cli.contents import Contents, Joint, ModelFileError, Take
from bmaya_cli.fbxcontents import read_fbx
from bmaya_cli.fbxfile import MAGIC as FBX_MAGIC
from bmaya_cli.gltfcontents import GLB_MAGIC, read_gltf

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
NAMES_IN_ISSUE = 5
READS = "binary .fbx (version 7.1 and newer), .glb and .gltf"


def read(path: str | Path) -> Contents:
    file = Path(path)
    if not file.is_file():
        raise ModelFileError(f"No such file: {file.as_posix()}", "Check the path.")
    with file.open("rb") as stream:
        head = stream.read(len(FBX_MAGIC))
    suffix = file.suffix.lower()
    if head.startswith(GLB_MAGIC) or suffix in (".gltf", ".glb"):
        return read_gltf(file)
    if head == FBX_MAGIC or suffix == ".fbx":
        return read_fbx(file)
    raise ModelFileError(f"Not a model file this command reads: {file.name}", f"It reads {READS}.")


def rounded(values: tuple[float, ...] | list[float], digits: int = 4) -> list[float]:
    return [round(v, digits) + 0.0 for v in values]


def frames_of(seconds: tuple[float, float], fps: float | None) -> list[float] | None:
    if not fps:
        return None
    return [round(s * fps, 3) + 0.0 for s in seconds]


def listing(items: list[Any], limit: int) -> dict[str, Any]:
    return {"items": items[:limit], "shown": min(len(items), limit), "total": len(items)}


def joint_entry(joint: Joint, axes: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": joint.name}
    if joint.parent:
        entry["parent"] = joint.parent
    entry["at"] = rounded(joint.at)
    if axes:
        entry["axes"] = [rounded(axis) for axis in joint.axes]
    return entry


def take_entry(take: Take, fps: float | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": take.name}
    frames = frames_of(take.seconds, fps)
    if frames is not None:
        entry["frames"] = frames
    entry["seconds"] = rounded(take.seconds)
    entry["curves"] = take.curves
    entry["animated"] = take.animated
    if take.keys is not None and take.keys != take.seconds:
        keys = frames_of(take.keys, fps)
        entry["keys"] = keys if keys is not None else rounded(take.keys)
    return entry


def issues_of(contents: Contents) -> list[str]:
    out = []
    for mesh in contents.meshes:
        if mesh.polys == 0:
            out.append(f"mesh '{mesh.name}' is empty: it has no faces")
        if mesh.unweighted:
            out.append(f"mesh '{mesh.name}': {mesh.unweighted} vertices have no skin weight")
    clashes = [
        name for name, count in Counter(j.name for j in contents.joints).items() if count > 1
    ]
    if clashes:
        out.append(
            f"{len(clashes)} joint names are used twice, engines keep one: "
            + ", ".join(clashes[:NAMES_IN_ISSUE])
        )
    missing = sorted({t.path for t in contents.textures if not t.found})
    if missing:
        out.append(f"{len(missing)} textures not found: " + ", ".join(missing[:NAMES_IN_ISSUE]))
    return out


def report(contents: Contents, limit: int = DEFAULT_LIMIT, axes: bool = False) -> dict[str, Any]:
    meshes = []
    for mesh in contents.meshes:
        entry: dict[str, Any] = {
            "name": mesh.name,
            "verts": mesh.verts,
            "polys": mesh.polys,
            "tris": mesh.tris,
            "uv_sets": mesh.uv_sets,
            "materials": mesh.materials,
        }
        if mesh.influences:
            entry["skin"] = {"influences": mesh.influences, "unweighted": mesh.unweighted}
        if mesh.polys == 0:
            entry["empty"] = True
        meshes.append(entry)
    joints = listing([joint_entry(j, axes) for j in contents.joints], limit)
    if contents.joints_from:
        joints["from"] = contents.joints_from
    textures = [
        {"path": t.path, "material": t.material, "slot": t.slot, "found": t.found}
        for t in contents.textures
    ]
    out: dict[str, Any] = {
        "path": contents.path,
        "format": contents.format,
        "version": contents.version,
        "size": contents.size,
        "unit": contents.unit,
        "unit_scale": contents.unit_scale,
        "up": contents.up,
        "fps": contents.fps,
    }
    if contents.fps_from == "keys":
        out["fps_from"] = "keys"
    out["meshes"] = listing(meshes, limit)
    out["joints"] = joints
    out["takes"] = listing([take_entry(t, contents.fps) for t in contents.takes], limit)
    out["textures"] = listing(textures, limit)
    out["issues"] = issues_of(contents)
    return out


def cmd_inspect(args: argparse.Namespace) -> int:
    from bmaya_cli.commands import EXIT_OK, CommandFailed, emit

    limit = max(1, min(args.limit or DEFAULT_LIMIT, MAX_LIMIT))
    try:
        contents = read(args.path)
    except ModelFileError as error:
        raise CommandFailed(error.message, error.hint) from error
    emit(report(contents, limit, args.axes))
    return EXIT_OK
