"""`bmaya inspect`: read a model file without Maya and describe it."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

from bmaya_cli.contents import Contents, Joint, ModelFileError, Take
from bmaya_cli.fbxcontents import read_fbx, read_fbx_layouts
from bmaya_cli.fbxfile import MAGIC as FBX_MAGIC
from bmaya_cli.gltfcontents import GLB_MAGIC, read_gltf, read_gltf_layouts

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
NAMES_IN_ISSUE = 5
FRAME_TOLERANCE = 0.5
RATE_TOLERANCE = 0.01
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


def read_layout(path: str | Path, mesh: str, uv_set: str = "") -> dict[str, Any]:
    """The UV layout of the mesh named `mesh` in a model file, or of its only mesh: the UV
    set named `uv_set` when an FBX has one, else the first. The UVs go out unrounded: the
    bridge's hash rounds them, and rounding twice moves values that sit near a tie."""
    file = Path(path)
    contents = read(file)
    if contents.format == "fbx":
        layouts = read_fbx_layouts(file, uv_set)
    else:
        layouts = read_gltf_layouts(file)
    if not layouts:
        raise ModelFileError(
            f"{file.name} has no mesh with UVs.", f"`bmaya inspect {file.as_posix()}` shows it."
        )
    wanted = leaf(mesh)
    picked = [layout for layout in layouts if leaf(layout.mesh) == wanted]
    if not picked and len(layouts) == 1:
        picked = layouts
    if len(picked) != 1:
        names = ", ".join(leaf(layout.mesh) for layout in layouts[:NAMES_IN_ISSUE])
        raise ModelFileError(
            f"{file.name} has no single mesh named {wanted}; it has: {names}.",
            "Name the scene mesh the way the file does, or rename one of them.",
        )
    layout = picked[0]
    return {
        "path": file.as_posix(),
        "name": leaf(layout.mesh),
        "set": layout.uv_set,
        "uvs": [[u, v] for u, v in layout.uvs],
        "faces": layout.faces,
    }


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


def off_rate(take: Take, fps: float | None) -> float | None:
    """The rate the keys were baked at, when their spacing falls between the file's frames.

    Keys every second frame are on the frame grid and fine; keys 1/24 s apart in a 30 fps
    file are not."""
    if not take.key_step or not fps:
        return None
    frames = take.key_step * fps
    if abs(frames - round(frames)) < RATE_TOLERANCE * max(1.0, frames):
        return None
    return round(1.0 / take.key_step, 3)


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
    baked = off_rate(take, fps)
    if baked:
        entry["keys_fps"] = baked
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
    for take in contents.takes:
        baked = off_rate(take, contents.fps)
        if baked:
            out.append(
                f"take '{take.name}': keys are baked at {baked:g} fps, the file says "
                f"{contents.fps:g}; Maya's FBX plugin can keep the rate of an earlier export, "
                "export it again"
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


def leaf(name: str) -> str:
    return name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def some(names: list[str]) -> str:
    shown = ", ".join(names[:NAMES_IN_ISSUE])
    if len(names) > NAMES_IN_ISSUE:
        shown += f" and {len(names) - NAMES_IN_ISSUE} more"
    return shown


def written_entry(contents: Contents) -> dict[str, Any]:
    takes = []
    for take in contents.takes:
        entry: dict[str, Any] = {"name": take.name}
        frames = frames_of(take.seconds, contents.fps)
        entry["frames" if frames is not None else "seconds"] = frames or rounded(take.seconds)
        takes.append(entry)
    return {
        "path": contents.path,
        "size": contents.size,
        "meshes": len(contents.meshes),
        "joints": len(contents.joints),
        "takes": takes,
    }


def compare_takes(contents: Contents, wanted: list[dict[str, Any]], fps: float) -> list[str]:
    out = []
    by_name = {take.name: take for take in contents.takes}
    for want in wanted:
        take = by_name.get(want["name"])
        if take is None:
            found = ", ".join(t.name for t in contents.takes) or "none"
            out.append(f"no take named '{want['name']}' in the file (takes: {found})")
            continue
        first, last = want["frames"]
        frames = [round(s * fps, 3) for s in take.seconds]
        if abs(frames[0] - first) > FRAME_TOLERANCE or abs(frames[1] - last) > FRAME_TOLERANCE:
            out.append(
                f"take '{take.name}' covers frames {frames[0]:g}:{frames[1]:g}, "
                f"the scene asked for {first:g}:{last:g}"
            )
        if take.curves == 0:
            out.append(f"take '{take.name}' has no animation curves")
    return out


def compare(contents: Contents, expected: dict[str, Any]) -> list[str]:
    """What the written file lacks or got wrong, against what the scene meant to export."""
    out = []
    meshes = {leaf(mesh.name): mesh for mesh in contents.meshes}
    missing = [name for name in expected.get("meshes", []) if name not in meshes]
    if missing:
        out.append(f"{len(missing)} meshes are not in the file: {some(missing)}")
    empty = [
        name for name in expected.get("meshes", []) if name in meshes and meshes[name].polys == 0
    ]
    if empty:
        out.append(f"{len(empty)} meshes are empty in the file: {some(empty)}")
    joints = {leaf(joint.name) for joint in contents.joints}
    wanted = expected.get("joints", [])
    lost = [name for name in wanted if name not in joints]
    if lost:
        out.append(f"{len(lost)} of {len(wanted)} scene joints are not in the file: {some(lost)}")
    fps = expected.get("fps")
    if fps and contents.fps and abs(contents.fps - fps) > RATE_TOLERANCE * fps:
        out.append(f"the file says {contents.fps:g} fps, the scene runs at {fps:g}")
    if expected.get("takes"):
        out.extend(compare_takes(contents, expected["takes"], fps or contents.fps or 0.0))
    up = expected.get("up")
    if up and contents.up != up:
        out.append(f"the file's up axis is {contents.up}, the export asked for {up}")
    return out


class ReadBack(NamedTuple):
    written: dict[str, Any]
    issues: list[str]
    stale_rate: bool


def read_back(path: str, expected: dict[str, Any] | None) -> ReadBack:
    """The written file as `written` reports it, the issues found by comparing it, and whether
    its keys were baked at another rate than the file's (Maya's FBX plugin needs a reload)."""
    try:
        contents = read(path)
    except ModelFileError as error:
        issue = f"{Path(path).name}: could not read it back: {error.message}"
        return ReadBack({"path": path}, [issue], False)
    issues = issues_of(contents)
    if expected is not None:
        issues += compare(contents, expected)
    stale = any(off_rate(take, contents.fps) for take in contents.takes)
    return ReadBack(written_entry(contents), issues, stale)


def cmd_inspect(args: argparse.Namespace) -> int:
    from bmaya_cli.commands import EXIT_OK, CommandFailed, emit

    limit = max(1, min(args.limit or DEFAULT_LIMIT, MAX_LIMIT))
    try:
        contents = read(args.path)
    except ModelFileError as error:
        raise CommandFailed(error.message, error.hint) from error
    emit(report(contents, limit, args.axes))
    return EXIT_OK
