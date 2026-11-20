# UVs and textures

`bmaya uv` reads a mesh's UV layout in numbers, says whether a texture was painted for it, draws the layout over the textures, compares two layouts, and bakes the surface's position and normal into UV space. Agents used to dump UVs through OpenMaya and draw them with PIL to catch a texture painted for an older layout, hash UVs by hand to check that a layout did not change, and run Blender to bake positions into the layout.

## The report

`bmaya uv NAME [--set S] [--size N]` works on one mesh and its current UV set (or `--set`), up to 250000 faces (about 70 microseconds a face; a denser mesh would not answer in time).

| Key | Means |
|---|---|
| `sets`, `set` | The UV sets, which is current, and the one read. |
| `uvs`, `faces`, `range` | Counts, and the u and v range. `faces_without_uvs` when some faces have none. |
| `size` | The texture size the texel numbers use: `--size`, else the largest texture of the mesh, else 1024. |
| `shells` | Each shell (faces joined through shared UVs) with its `faces`, `uv_area` (share of the 0-1 square), `area` (on the surface, scene units squared), `density` (texels per scene unit at `size`) and `box`, largest first. |
| `coverage` | Share of the 0-1 square the layout covers. |
| `overlap` | Share of the square used more than once: shells on top of each other, or a shell folding over itself. A texel under ten stacked shells counts once. |
| `flipped` | Faces mirrored in UV (their UVs wind the other way). Mirrored halves of a symmetric model are often meant to be. |
| `density` | The lowest and highest shell density, and the unit. |
| `stretch` | How evenly faces get texture: each face's texel density against the mesh's average, as a length ratio. `low` and `high` are the 5th and 95th percentile; `off` lists faces over 2 or under 0.5, the worst first. |
| `padding` | The narrowest gap between two shells and between a shell and the edge of the square, in texels at `size`. `null` means wider than `checked_up_to` (16). |
| `hash` | Eight hex digits of the UVs corner by corner, rounded to 4 digits: the same layout gives the same hash. |
| `textures` | The file textures of the materials on this mesh (this instance of it): `node`, `path`, `slot` (`color`, `normal`, or the attribute they feed, also through a layered texture or a color correction), `size`, `color_space`, `found`, and `tiles` for a UDIM or other tiled path. |

Issues: faces without UVs, overlapping shells, shells less than 2 texels apart (mipmaps bleed), a normal map not read as Raw, textures not found, and textures that do not fit.

## Does a texture fit the layout?

`fit` checks the color textures of the mesh's materials and every `--texture PNG`, four at most (about a second each): `fit` lists `shown` of `total`, and an issue names the ones left out. A painted texture changes sharply along the outlines of its UV shells: seams, the padding color, the end of the painted area. So the strongest edges of a texture crowd near the outlines of the layout it was painted for, and are spread at random over any other layout.

`lift` measures that: the share of the texture's strongest edges (the top 3% after a light blur, at 1024 px) that lie within 2 texels of a shell outline, divided by the outlines' share of the image. 2.5 and up `fits`, 1.5 and below `does not fit`, between is `unclear`. The lift cannot pass 1 over the outlines' share, so where the outlines cover a large part of the image (many small shells, thin strips) both marks come down toward that limit, and past half the image every texture is `unclear`. A texture with almost no sharp edges is `unclear` whatever the layout. Tiled textures (UDIM) are not checked. It is a heuristic, made for the case below; trust the picture over the number when they disagree.

On the Zombie hand's history the three textures and three layouts from git give: textures on their own layout 4.1, 5.5 and 12.0; on another layout 0.56 to 1.34. The texture painted for the layout of commit 406b806 reads `does not fit` (0.56) on the current layout: that is the stale texture the agents caught by eye.

Each texture takes about 1 s; four textures and the picture about 10 s.

## The picture

`bmaya uv NAME -o layout.png` draws the layout over each texture (up to four, side by side): shells tinted in turn, overlapping faces red, mirrored faces with a purple outline, the 0-1 square dashed, and each tile labelled with the texture and its fit. Without textures it draws the bare layout.

## Comparing layouts

`--against MESH` compares with another mesh in the scene, `--against FILE.fbx|.glb|.gltf` with a mesh in a model file (the one with the same name, or the only one), read by the command line. Both sides use the `--set` UV set when they have it (an FBX names its sets; glTF reads `TEXCOORD_0`), and `sets` says which each used. `against` gives the face and UV counts and UV areas of both, both hashes, and when the faces match corner by corner, how many corners moved and how far; `same` is true when none did. When the faces are split another way (glTF has only triangles, and `export --preset unreal` triangulates) `same` is null and `unmatched_uvs` counts the UVs of each side with no UV of the other within 0.0001: `[0, 0]` is the same layout cut into triangles.

## diff

`bmaya diff NAME` after `--mark` also compares the current UV set corner by corner: `uv_changed`, and `uv` with how many corners `moved` and the `max_move` (or the `set` if it changed). An edit that only moved UVs shows `moved: 0` with `uv_changed: true`.

## Baking position and normal

`bmaya uv NAME --bake position,normal [--size 2048] [--pose bind|current] -o FOLDER` writes (2048 is also the largest size: a bigger bake would not answer in time):
- `NAME_position.npy`: float32, size × size × 3, each texel's point on the surface in the mesh's object space, in the scene unit;
- `NAME_normal.npy`: the same with the surface normal, blended from the vertex normals (hard edges stay hard);
- `NAME_mask.png`: white where a face covers the texel.

Row 0 is the top of the texture (v = 1), as in the PNG; `numpy.load` reads the files. A skinned mesh is baked at its bind pose (the mesh the skin starts from), like Blender's rest pose, unless `--pose current`; when faces were added or deleted after the skin, the bind pose no longer lines up and only `--pose current` bakes. Texels outside every face are 0. There is no padding: grow the maps yourself if a filter needs it.

The baker is bmaya's own rasterizer, not Maya's: `convertSolidTx` cannot write float positions in a Maya without a window (EXR fails, TIF is 8-bit and clamped). 2048 × 2048 takes about 4 s per map on the Zombie hand; its coverage, 0.5211, is the layout's UV area.
