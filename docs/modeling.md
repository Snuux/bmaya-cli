# Modeling

An agent builds geometry with `bmaya exec` and `maya.cmds` like any other change. What it cannot do is look at the model from all sides, so bmaya gives it the model in pictures and numbers it can read, and Maya's whole-mesh tools as single commands.

## View sheets

`bmaya views [NAMES] -o views.png` draws the named meshes (or the selection, or every visible mesh) from several sides in one PNG. Each tile is labelled with its view.

| View | Looks from, in a Y-up scene |
|---|---|
| `front`, `back` | +Z, -Z |
| `right`, `left` | +X, -X |
| `top`, `bottom` | +Y, -Y |
| `persp`, `persp-back` | three-quarter from the front right, and from behind |

The default is `front,right,top,persp`; `--views all` draws all eight. In a Z-up scene the views turn with the up axis. The straight-on views are orthographic and carry the width and height of what they show, in scene units. `--size` sets the longest side of the sheet (default 1600), so fewer views mean bigger tiles.

| Style | Shows |
|---|---|
| `parts` (default) | A color per mesh, edges on top. `parts` in the result maps names to colors. A face seen from behind is dark, so reversed normals stand out. |
| `silhouette` | The outline alone, black on light. |
| `xray` | Pale surfaces and every edge, hidden ones too. |
| `shaded` | Maya's own render with materials and textures; `--light unlit\|soft\|scene` as in `bmaya shot`. |

`parts`, `silhouette` and `xray` are drawn by bmaya itself with a depth buffer, from the mesh data. They look the same in a Maya with or without a window, need no GPU, and leave the scene untouched. They draw only the named meshes. `shaded` goes through the viewport renderer, so it shows the whole scene, framed on the named nodes.

Edges are left out when they would merge into a blob: past about 10000 triangles for a 400 px tile, 40000 for an 800 px one. The result then has `wire: false` and a `note`. `--no-wire` leaves them out on purpose. Drawing is refused above 3 million triangles times views, and stops with an error after 30 seconds; `shaded` has no such limit.

`--section x`, `y` or `z` cuts the model with a plane across that world axis, through the middle of the model or at a position (`--section y=12.5`), and adds a tile that looks at the cut from the plus side. The cut line is red. In `shaded` the cut is the camera's near clip plane.

## Numbers

`bmaya object NAME` gives the bounding box, the `size` along each axis and the world `pivot`.

`bmaya audit [NAMES]` reports per mesh the vertex and triangle counts, faces by kind (`tris`, `quads`, `ngons`), the size, and these issues:

| Issue | Means |
|---|---|
| `N n-gons`, `non-manifold edges`, `non-manifold verts`, `lamina faces`, `zero-area faces`, `zero-length edges` | Geometry an engine or a later tool chokes on. |
| `N faces with flipped winding` | Faces wound against their neighbours: they light and cull the wrong way. |
| `normals point inward` | A closed shell is inside out. With several shells it says how many. |
| `no UVs`, `N faces without UVs`, `N overlapping UV faces`, `N UV sets` | UV problems. |
| `no material`, `default material only`, `N materials` | Material assignment. |
| `construction history`, `unfrozen transform`, `negative scale`, `pivot not at origin` | Not ready for export. |
| `namespace in name`, `default name`, `non-ASCII name` | Naming. |
| `SHAPE has no faces` | An empty mesh: the exported file has nothing to show. `bmaya export` refuses it. |
| `SHAPE has the opposite flag over inside-out faces` | Left by freezing a negative scale. Maya draws the mesh the right way, the exported file gets it inside out. `bmaya cleanup NAME` clears the flag and turns the faces out; `bmaya export` refuses the mesh until then. |
| `SHAPE has the opposite flag` | The faces are stored the right way, but Maya draws them flipped. |
| `SHAPE is instanced under N transforms`, `N visible shapes on one transform` | One shape in several places, or several meshes under one node: engines may duplicate or merge them. |
| `N unused intermediate shapes, left from earlier versions` | Hidden shapes no deformer reads, often carried along by `duplicate`. Delete them. |
| `N verts without a mirror twin across x` | With `--symmetry x`: the mesh is not mirrored across the plane through its pivot. `symmetry.sample` gives a few of those vertices. |

`locked_normals`, `uvs_outside_01`, `flipped_uv_faces` and `color_sets` are reported but are not issues: tiling textures, custom normals and mirrored UV shells are normal on game assets.

`bmaya diff NAME --mark` remembers a mesh. After an edit, `bmaya diff NAME` gives the counts, size, surface area and volume before and after. When the topology is the same it adds `moved`, `max_move`, `mean_move` and `moved_box`, the box around the vertices that moved. `--against OTHER` compares two meshes as they are. A mark belongs to one client and lives in that Maya until it stops.

## Whole-mesh edits

| Command | Does |
|---|---|
| `bmaya remesh NAME --to tris [--edge L]` | Even triangles with edges near `L` scene units (default: an edge that keeps about the triangle count). Maya's `polyRemesh`. An edge that would make more than 500000 triangles is refused, with the shortest edge that fits. |
| `bmaya remesh NAME --to quads [--faces N] [--keep-hard-edges]` | A quad mesh of about `N` faces (default: half the triangle count). Maya's Retopologize, `polyRetopo`. Up to 200000 triangles in; reduce a denser mesh first. |
| `bmaya reduce NAME --tris N` or `--percent P` | Fewer triangles, same shape. `--percent 50` keeps half. `--keep-borders`, `--keep-uvs`, `--keep-hard-edges`, `--keep-quads` pin what matters. Maya's `polyReduce`. |
| `bmaya cleanup NAME [--merge DIST] [--ngons]` | Merges vertices on the same spot (UV seams stay), removes zero-area faces, zero-length edges and lamina faces, splits non-manifold geometry, makes the faces wind one way and turns the normals outward. It clears the opposite flag a mirrored freeze leaves (`opposite_cleared`). `--ngons` also triangulates faces with five or more sides. |

Each works on one mesh, is one undo step (`bmaya undo`), and answers with `before`, `after` and the audit `issues` that are left. Each marks the mesh before it runs, so `bmaya diff NAME` shows the change even when the reply was lost: exit code 3 means Maya is still working, and the command must not be sent again.

Remeshing replaces the topology, so UVs, vertex colors and skin weights do not survive it. Remesh first, unwrap and skin after.
