from __future__ import annotations

import glob
import math
import os
import re
from typing import TYPE_CHECKING, Any, NamedTuple

import maya.api.OpenMaya as om
from maya import cmds

from bmaya_bridge import softrender, uvcheck
from bmaya_bridge.errors import BridgeError
from bmaya_bridge.handlers import viewport
from bmaya_bridge.handlers._common import (
    cm_per_unit,
    display_name,
    int_param,
    listing,
    rnd,
    rnd_list,
    text_param,
)
from bmaya_bridge.handlers._qt import qt_core, qt_gui
from bmaya_bridge.handlers._skin import clusters_of, dag_path, skin_fn
from bmaya_bridge.handlers.mesh import single_mesh, transform_of

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

SHELLS_SHOWN = 30
TEXTURES_SHOWN = 10
WORST_SHOWN = 10
FIT_SIZE = 1024
FITS_MOST = 4
MOST_FACES = 250_000
BAKE_MOST = 2048
IMAGE_MEGABYTES = 4096
TILE_TOKENS = re.compile(r"<udim>|<uvtile>|<u>|<v>|<f>|u<u>_v<v>", re.IGNORECASE)
DEFAULT_SIZE = 1024
SHEET = 1600
STRETCH_LIMIT = 2.0
PADDING_WANTED = 2.0
COLOR_SLOTS = ("color", "baseColor", "diffuse", "outColor")
NORMAL_SLOTS = ("normalCamera", "normal")
TINT_ALPHA = 45
EDGE_INK = (20, 20, 20)
OVERLAP_INK = (230, 30, 30)
FLIPPED_INK = (200, 0, 200)
BAKE_MAPS = ("position", "normal")


class Layout(NamedTuple):
    uv_set: str
    uvs: list[uvcheck.UV]
    face_uvs: list[list[int]]
    points: list[uvcheck.Vec]
    face_points: list[list[int]]


def uv_sets(shape: str) -> tuple[list[str], str]:
    names = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
    current = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [""])[0]
    return names, current


def pick_set(shape: str, wanted: str) -> str:
    names, current = uv_sets(shape)
    if not names:
        raise BridgeError(
            f"'{display_name(transform_of(shape))}' has no UVs.",
            hint="Unwrap it first; `bmaya guide` has UV recipes (polyAutoProjection, u3dUnfold).",
        )
    if wanted and wanted not in names:
        raise BridgeError(
            f"No UV set named '{wanted}'.", hint="The mesh has: " + ", ".join(names) + "."
        )
    return wanted or current


def read_layout(shape: str, uv_set: str, points_from: om.MObject | None = None) -> Layout:
    """UVs corner by corner and the face points, in scene units of the mesh's own space."""
    mesh = om.MFnMesh(dag_path(shape))
    us, vs = mesh.getUVs(uv_set)
    counts, ids = mesh.getAssignedUVs(uv_set)
    face_uvs = []
    start = 0
    for count in counts:
        face_uvs.append(list(ids[start : start + count]))
        start += count
    source = om.MFnMesh(points_from) if points_from is not None else mesh
    factor = 1.0 / cm_per_unit()
    points = [(p.x * factor, p.y * factor, p.z * factor) for p in source.getPoints()]
    face_points = [list(mesh.getPolygonVertices(face)) for face in range(mesh.numPolygons)]
    uvs = [(us[index], vs[index]) for index in range(len(us))]
    return Layout(uv_set, uvs, face_uvs, points, face_points)


def file_nodes(shape: str) -> list[dict[str, Any]]:
    """The file textures of the materials on this instance of the mesh, with the slot each
    one feeds."""
    path = dag_path(shape)
    sets, _ = om.MFnMesh(path).getConnectedShaders(path.instanceNumber())
    engines = [om.MFnDependencyNode(sets[index]).name() for index in range(len(sets))]
    found: dict[str, dict[str, Any]] = {}
    for engine in dict.fromkeys(engines):
        shaders = cmds.listConnections(engine + ".surfaceShader", source=True) or []
        for shader in shaders:
            for node in cmds.ls(cmds.listHistory(shader) or [], type="file") or []:
                if node in found:
                    continue
                found[node] = texture_entry(node, shader)
    return list(found.values())


def texture_entry(node: str, shader: str) -> dict[str, Any]:
    path = cmds.getAttr(node + ".fileTextureName") or ""
    slot = slot_of(node, shader)
    entry: dict[str, Any] = {
        "node": node,
        "path": path.replace("\\", "/"),
        "slot": slot,
        "found": bool(path) and os.path.isfile(path),
    }
    first = path if entry["found"] else ""
    if path and (cmds.getAttr(node + ".uvTilingMode") or TILE_TOKENS.search(path)):
        tiles = sorted(glob.glob(TILE_TOKENS.sub("*", path)))
        entry["tiles"] = len(tiles)
        entry["found"] = bool(tiles)
        first = tiles[0] if tiles else ""
    if first:
        size = qt_gui().QImageReader(first).size()
        if size.width() > 0:
            entry["size"] = [size.width(), size.height()]
    entry["color_space"] = cmds.getAttr(node + ".colorSpace")
    return entry


def slot_of(node: str, shader: str) -> str:
    """Which shader input the file node ends up in: color, normal, or that attribute's name."""
    for plug in cmds.listConnections(node, source=False, destination=True, plugs=True) or []:
        owner, _, attr = plug.partition(".")
        if owner == shader:
            return "color" if attr in COLOR_SLOTS else attr
        if cmds.nodeType(owner) in ("bump2d", "aiNormalMap"):
            return "normal"
    for slots, slot in ((NORMAL_SLOTS, "normal"), (COLOR_SLOTS, "color")):
        for attr in slots:
            plug = f"{shader}.{attr}"
            if cmds.objExists(plug) and cmds.listConnections(plug, source=True, destination=False):
                if node in (cmds.listHistory(plug) or []):
                    return slot
    return "other"


def read_image(path: str) -> QImage:
    """The image at full size. Qt refuses images over 256 MB unpacked (an 8K 16-bit PNG), so
    the limit is raised while it reads."""
    reader = qt_gui().QImageReader
    limit = reader.allocationLimit()
    reader.setAllocationLimit(IMAGE_MEGABYTES)
    try:
        return qt_gui().QImage(path)
    finally:
        reader.setAllocationLimit(limit)


def gray_pixels(path: str, size: int) -> list[float] | None:
    """The image as gray values, scaled to `size` by `size`; None when it cannot be read."""
    gui = qt_gui()
    core = qt_core()
    image = read_image(path)
    if image.isNull():
        return None
    image = image.scaled(size, size, core.Qt.IgnoreAspectRatio, core.Qt.SmoothTransformation)
    image = image.convertToFormat(gui.QImage.Format_Grayscale8)
    stride = image.bytesPerLine()
    data = bytes(image.constBits())[: stride * size]
    values: list[float] = []
    for row in range(size):
        values.extend(map(float, data[row * stride : row * stride + size]))
    return values


def qt_band(layout: Layout, size: int) -> bytearray:
    """Texels within uvcheck.BAND of a shell outline, drawn as thick lines with Qt."""
    gui = qt_gui()
    core = qt_core()
    image = gui.QImage(size, size, gui.QImage.Format_Grayscale8)
    image.fill(0)
    painter = gui.QPainter(image)
    try:
        painter.setPen(gui.QPen(gui.QColor(255, 255, 255), 2 * uvcheck.BAND + 1))
        for one, two in uvcheck.border_segments(layout.face_uvs):
            a = uvcheck.to_canvas(layout.uvs[one], size)
            b = uvcheck.to_canvas(layout.uvs[two], size)
            painter.drawLine(core.QPointF(*a), core.QPointF(*b))
    finally:
        painter.end()
    stride = image.bytesPerLine()
    data = bytes(image.constBits())[: stride * size]
    band = bytearray(size * size)
    for row in range(size):
        line = data[row * stride : row * stride + size]
        band[row * size : (row + 1) * size] = bytes(1 if value else 0 for value in line)
    return band


def shell_of_faces(found: list[uvcheck.Shell], face_count: int) -> list[int]:
    owner = [-1] * face_count
    for index, shell in enumerate(found):
        for face in shell.faces:
            owner[face] = index
    return owner


def stretch_report(layout: Layout) -> dict[str, Any]:
    ratios = uvcheck.stretch(layout.uvs, layout.face_uvs, layout.points, layout.face_points)
    known = sorted(ratio for ratio in ratios if not math.isnan(ratio) and ratio > 0.0)
    if not known:
        return {"faces": 0}
    worst = sorted(
        (
            {"face": face, "ratio": rnd(ratio, 3)}
            for face, ratio in enumerate(ratios)
            if not math.isnan(ratio)
            and (ratio == 0.0 or ratio > STRETCH_LIMIT or ratio < 1.0 / STRETCH_LIMIT)
        ),
        key=lambda entry: -abs(math.log(max(entry["ratio"], 1e-6))),
    )
    return {
        "low": rnd(known[int(0.05 * (len(known) - 1))], 3),
        "high": rnd(known[int(0.95 * (len(known) - 1))], 3),
        "off": listing(worst, WORST_SHOWN),
    }


def compare_layout(layout: Layout, other: dict[str, Any]) -> dict[str, Any]:
    """The layout against another one: same faces, same UVs, how far they moved. When the
    faces have other corner counts (a triangulated export) the UVs are matched by position
    alone and `same` stays null."""
    theirs_uvs = [(float(u), float(v)) for u, v in other["uvs"]]
    theirs_faces = [list(face) for face in other["faces"]]
    out: dict[str, Any] = {
        "name": other.get("name", ""),
        "sets": [layout.uv_set, other.get("set", "")],
        "faces": [len(layout.face_uvs), len(theirs_faces)],
        "uvs": [len(layout.uvs), len(theirs_uvs)],
        "uv_area": [
            rnd(sum(abs(uvcheck.signed_area([layout.uvs[u] for u in f])) for f in layout.face_uvs)),
            rnd(sum(abs(uvcheck.signed_area([theirs_uvs[u] for u in f])) for f in theirs_faces)),
        ],
    }
    out["hash"] = [
        uvcheck.fingerprint(layout.uvs, layout.face_uvs),
        uvcheck.fingerprint(theirs_uvs, theirs_faces),
    ]
    if [len(f) for f in layout.face_uvs] != [len(f) for f in theirs_faces]:
        out["same"] = None
        out["corners"] = "not compared: the faces have other corner counts (triangulated?)"
        out["unmatched_uvs"] = [
            uvcheck.unmatched(layout.uvs, theirs_uvs),
            uvcheck.unmatched(theirs_uvs, layout.uvs),
        ]
    else:
        moved = 0
        worst = 0.0
        for face, ids in enumerate(layout.face_uvs):
            for corner, uv in enumerate(ids):
                a = layout.uvs[uv]
                b = theirs_uvs[theirs_faces[face][corner]]
                gap = math.hypot(a[0] - b[0], a[1] - b[1])
                if gap > 1e-4:
                    moved += 1
                worst = max(worst, gap)
        out["moved_corners"] = moved
        out["max_move"] = rnd(worst)
        out["same"] = moved == 0
    return out


def layout_tile(
    layout: Layout,
    found: list[uvcheck.Shell],
    flipped: set[int],
    overlaps: set[int],
    tile: int,
    texture: str,
) -> QImage:
    gui = qt_gui()
    core = qt_core()
    image = gui.QImage(tile, tile, gui.QImage.Format_RGB32)
    image.fill(gui.QColor(*softrender.BACKGROUND))
    painter = gui.QPainter(image)
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        if texture:
            picture = read_image(texture)
            if not picture.isNull():
                painter.drawImage(core.QRectF(0, 0, tile, tile), picture)
        for index, shell in enumerate(found):
            color = softrender.part_color(index)
            fill = gui.QColor(color[0], color[1], color[2], TINT_ALPHA)
            for face in shell.faces:
                polygon = gui.QPolygonF(
                    [
                        core.QPointF(*uvcheck.to_canvas(layout.uvs[uv], tile))
                        for uv in layout.face_uvs[face]
                    ]
                )
                painter.setPen(core.Qt.NoPen)
                painter.setBrush(gui.QColor(*OVERLAP_INK, 150) if face in overlaps else fill)
                painter.drawPolygon(polygon)
        pen = gui.QPen(gui.QColor(*EDGE_INK), 1.0)
        painter.setBrush(core.Qt.NoBrush)
        for face, ids in enumerate(layout.face_uvs):
            if len(ids) < 2:
                continue
            polygon = gui.QPolygonF(
                [core.QPointF(*uvcheck.to_canvas(layout.uvs[uv], tile)) for uv in ids]
            )
            painter.setPen(gui.QPen(gui.QColor(*FLIPPED_INK), 1.6) if face in flipped else pen)
            painter.drawPolygon(polygon)
        painter.setPen(gui.QPen(gui.QColor(90, 90, 90), 1.0, core.Qt.DashLine))
        painter.drawRect(core.QRectF(0.5, 0.5, tile - 1, tile - 1))
    finally:
        painter.end()
    return image


def overlap_faces(raster: uvcheck.Raster, layout: Layout, size: int) -> set[int]:
    """Faces with texels that another place also covers, found again at the picture's scale."""
    faces = set()
    if not raster.overlap:
        return faces
    owner = array_owner(layout, size)
    for face, texels in owner.items():
        for texel in texels:
            first = raster.owner[texel]
            if first != face and first >= 0:
                faces.add(face)
                faces.add(first)
                break
    return faces


def array_owner(layout: Layout, size: int) -> dict[int, list[int]]:
    out = {}
    for face, ids in enumerate(layout.face_uvs):
        if len(ids) < 3:
            continue
        spots = [uvcheck.to_canvas(layout.uvs[uv], size) for uv in ids]
        texels: list[int] = []
        for first, second, third in uvcheck.triangles(spots):
            texels.extend(uvcheck.covered(spots[first], spots[second], spots[third], size))
        out[face] = texels
    return out


def get_uv(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    shape = single_mesh(name)
    uv_set = pick_set(shape, text_param(params, "uv_set"))
    names, current = uv_sets(shape)
    faces = om.MFnMesh(dag_path(shape)).numPolygons
    if faces > MOST_FACES:
        raise BridgeError(
            f"{faces} faces are more than a UV report reads in time ({MOST_FACES} at most).",
            hint="Check the game mesh or a lower level of detail, or reduce a copy: "
            f"bmaya reduce COPY --tris {MOST_FACES}.",
        )
    layout = read_layout(shape, uv_set)
    textures = file_nodes(shape)
    given = [path for path in (params.get("textures") or []) if path]
    size = int_param(params, "size", 0, 16, 8192)
    if not size:
        sized = [entry["size"][0] for entry in textures if entry.get("size")]
        size = max(sized) if sized else DEFAULT_SIZE
    found = uvcheck.shells(layout.uvs, layout.face_uvs, layout.points, layout.face_points)
    shell_of = shell_of_faces(found, len(layout.face_uvs))
    raster = uvcheck.raster(layout.uvs, layout.face_uvs, shell_of, min(size, FIT_SIZE))
    canvas = min(size, FIT_SIZE)
    covered = sum(1 for owner in raster.owner if owner >= 0)
    shell_of_uv = {uv: shell_of[face] for face, ids in enumerate(layout.face_uvs) for uv in ids}
    between, edge = uvcheck.padding(layout.uvs, layout.face_uvs, shell_of_uv, size)
    flipped = {
        face
        for face, ids in enumerate(layout.face_uvs)
        if len(ids) >= 3 and uvcheck.signed_area([layout.uvs[uv] for uv in ids]) < -1e-12
    }
    without = sum(1 for ids in layout.face_uvs if not ids)
    us = [uv[0] for uv in layout.uvs] or [0.0]
    vs = [uv[1] for uv in layout.uvs] or [0.0]
    shells_out = [
        {
            "id": index,
            "faces": len(shell.faces),
            "uv_area": rnd(shell.uv_area, 5),
            "area": rnd(shell.area),
            "density": rnd(uvcheck.density(shell.uv_area, shell.area, size), 2),
            "box": rnd_list(shell.box, 4),
        }
        for index, shell in enumerate(found)
    ]
    densities = [entry["density"] for entry in shells_out if entry["density"] > 0]
    result: dict[str, Any] = {
        "name": display_name(transform_of(shape)),
        "sets": [{"name": set_name, "current": set_name == current} for set_name in names],
        "set": uv_set,
        "uvs": len(layout.uvs),
        "faces": len(layout.face_uvs),
        "range": {"u": [rnd(min(us)), rnd(max(us))], "v": [rnd(min(vs)), rnd(max(vs))]},
        "size": size,
        "shells": listing(shells_out, SHELLS_SHOWN),
        "coverage": rnd(covered / float(canvas * canvas), 4),
        "overlap": rnd(raster.overlap / float(canvas * canvas), 4),
        "flipped": len(flipped),
        "density": {
            "low": rnd(min(densities), 2) if densities else 0,
            "high": rnd(max(densities), 2) if densities else 0,
            "unit": cmds.currentUnit(query=True, linear=True),
        },
        "stretch": stretch_report(layout),
        "padding": {
            "between": rnd(between, 2) if between is not None else None,
            "edge": rnd(edge, 2) if edge is not None else None,
            "checked_up_to": rnd(uvcheck.PADDING_CAP),
        },
        "hash": uvcheck.fingerprint(layout.uvs, layout.face_uvs),
        "textures": listing(textures, TEXTURES_SHOWN),
    }
    issues = []
    if without:
        result["faces_without_uvs"] = without
        issues.append(f"{without} faces have no UVs in {uv_set}")
    if raster.overlap:
        issues.append(
            f"UV shells overlap: {rnd(100.0 * result['overlap'], 2)}% of the texture is used "
            f"twice ({len(raster.pairs)} pairs of shells)"
        )
    if between is not None and between < PADDING_WANTED:
        issues.append(
            f"shells are {rnd(between, 2)} texels apart at {size} px: mipmaps will bleed "
            "one into the other"
        )
    for entry in textures:
        if entry["slot"] == "normal" and entry["color_space"] != "Raw":
            issues.append(f"normal map {entry['node']} is read as {entry['color_space']}, not Raw")
        if entry["path"] and not entry["found"]:
            issues.append(f"texture {entry['node']} not found: {entry['path']}")
    fits = []
    wanted = list(
        dict.fromkeys(
            given
            + [
                entry["path"]
                for entry in textures
                if entry["slot"] == "color" and entry["found"] and "tiles" not in entry
            ]
        )
    )
    band = qt_band(layout, FIT_SIZE) if wanted else bytearray()
    for path in wanted[:FITS_MOST]:
        gray = gray_pixels(path, FIT_SIZE)
        if gray is None:
            fits.append({"path": path, "verdict": "unreadable"})
            continue
        fit = uvcheck.texture_fit(gray, FIT_SIZE, band)
        entry = {
            "path": path,
            "verdict": fit.verdict,
            "lift": rnd(fit.lift, 2) if fit.lift is not None else None,
        }
        fits.append(entry)
        if fit.verdict == "does not fit":
            issues.append(
                f"{os.path.basename(path)} does not fit this layout (lift {entry['lift']}): "
                "it looks painted for another UV layout"
            )
    if fits:
        result["fit"] = {"items": fits, "shown": len(fits), "total": len(wanted)}
    if len(wanted) > FITS_MOST:
        issues.append(
            f"fit checked {FITS_MOST} of {len(wanted)} textures; left out: "
            + ", ".join(os.path.basename(path) for path in wanted[FITS_MOST:])
        )
    against = params.get("against")
    if isinstance(against, dict) and against.get("faces") is not None:
        result["against"] = compare_layout(layout, against)
    elif text_param(params, "against_mesh"):
        other_shape = single_mesh(text_param(params, "against_mesh"))
        same_set = uv_set if uv_set in uv_sets(other_shape)[0] else ""
        other = read_layout(other_shape, pick_set(other_shape, same_set))
        result["against"] = compare_layout(
            layout,
            {
                "name": display_name(transform_of(other_shape)),
                "set": other.uv_set,
                "uvs": other.uvs,
                "faces": other.face_uvs,
            },
        )
    result["issues"] = issues
    if params.get("image"):
        draw(result, layout, found, flipped, raster, given, textures, params)
    return result


def draw(
    result: dict[str, Any],
    layout: Layout,
    found: list[uvcheck.Shell],
    flipped: set[int],
    raster: uvcheck.Raster,
    given: list[str],
    textures: list[dict[str, Any]],
    params: dict[str, Any],
) -> None:
    pictures = list(dict.fromkeys(given + [t["path"] for t in textures if t["found"]]))[:4]
    longest = int_param(params, "max_size", SHEET, 400, 4000)
    count = max(1, len(pictures))
    columns = count if count <= 2 else 2
    tile = longest // columns
    overlaps = overlap_faces(raster, layout, int(math.sqrt(len(raster.owner))))
    tiles = []
    fits = {entry["path"]: entry for entry in result.get("fit", {}).get("items", [])}
    for path in pictures or [""]:
        label = os.path.basename(path) if path else f"{result['name']} {layout.uv_set}"
        if path in fits and fits[path].get("lift") is not None:
            label += f": {fits[path]['verdict']} (lift {fits[path]['lift']})"
        tiles.append((label, layout_tile(layout, found, flipped, overlaps, tile, path)))
    path = viewport.new_image_path("uv")
    width, height = viewport.compose_sheet(tiles, columns, (tile, tile), path)
    result.update({"path": path, "width": width, "height": height})


def bake_uv(params: dict[str, Any]) -> dict[str, Any]:
    name = text_param(params, "name")
    folder = text_param(params, "folder")
    if not folder:
        raise BridgeError(
            "No folder to write the maps to.",
            hint=f"bmaya uv {name} --bake position,normal -o maps",
        )
    maps = [item.strip() for item in text_param(params, "maps").split(",") if item.strip()]
    unknown = [item for item in maps if item not in BAKE_MAPS]
    if not maps or unknown:
        raise BridgeError(
            f"Unknown map '{unknown[0] if unknown else ''}'.",
            hint="--bake takes position, normal or both: --bake position,normal.",
        )
    size = int_param(params, "size", BAKE_MOST, 16, BAKE_MOST)
    shape = single_mesh(name)
    uv_set = pick_set(shape, text_param(params, "uv_set"))
    pose = text_param(params, "pose") or "bind"
    source = None
    clusters = clusters_of(shape)
    if pose == "bind" and clusters:
        fn = skin_fn(clusters[0])
        source = fn.inputShapeAtIndex(fn.indexForOutputShape(dag_path(shape).node()))
    elif pose not in ("bind", "current"):
        raise BridgeError(f"Bad --pose '{pose}'.", hint="--pose bind (default) or current.")
    if source is not None:
        shown = om.MFnMesh(dag_path(shape))
        bound = om.MFnMesh(source)
        if (shown.numVertices, shown.numPolygons) != (bound.numVertices, bound.numPolygons):
            raise BridgeError(
                "The mesh's faces changed after it was skinned, so its bind pose does not "
                "line up with its UVs.",
                hint=f"Bake the pose it has now: bmaya uv {name} --bake position --pose current "
                "-o maps.",
            )
    layout = read_layout(shape, uv_set, source)
    mesh = om.MFnMesh(source if source is not None else dag_path(shape))
    normals = [(n.x, n.y, n.z) for n in mesh.getNormals()]
    counts, normal_ids = mesh.getNormalIds()
    face_normals = []
    start = 0
    for count in counts:
        face_normals.append(list(normal_ids[start : start + count]))
        start += count
    baked = uvcheck.bake(
        layout.uvs, layout.face_uvs, layout.points, layout.face_points, normals, face_normals, size
    )
    os.makedirs(folder, exist_ok=True)
    stem = display_name(transform_of(shape)).rsplit("|", 1)[-1].replace(":", "_")
    written = []
    for kind in maps:
        values = baked.position if kind == "position" else baked.normal
        path = os.path.join(folder, f"{stem}_{kind}.npy").replace("\\", "/")
        with open(path, "wb") as handle:
            handle.write(uvcheck.npy_bytes(values, (size, size, 3)))
        written.append(path)
    mask_path = os.path.join(folder, f"{stem}_mask.png").replace("\\", "/")
    write_mask(baked.mask, size, mask_path)
    written.append(mask_path)
    result: dict[str, Any] = {
        "name": display_name(transform_of(shape)),
        "set": uv_set,
        "size": size,
        "pose": "bind" if source is not None else "current",
        "space": "object",
        "unit": cmds.currentUnit(query=True, linear=True),
        "covered": rnd(baked.covered / float(size * size), 4),
        "files": written,
        "bounds": bounds_of(baked),
    }
    return result


def bounds_of(baked: uvcheck.Baked) -> list[list[float | int]]:
    low = [math.inf] * 3
    high = [-math.inf] * 3
    for texel, inside in enumerate(baked.mask):
        if not inside:
            continue
        for k in range(3):
            value = baked.position[texel * 3 + k]
            low[k] = min(low[k], value)
            high[k] = max(high[k], value)
    if low[0] == math.inf:
        return []
    return [rnd_list(low), rnd_list(high)]


def write_mask(mask: bytearray, size: int, path: str) -> None:
    gui = qt_gui()
    data = bytes(255 if value else 0 for value in mask)
    image = gui.QImage(data, size, size, size, gui.QImage.Format_Grayscale8).copy()
    image.save(path)
