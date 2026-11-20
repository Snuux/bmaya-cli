# Skin weights

`bmaya skin` reads, writes and edits the weights of a skinned mesh in one call, with numbers to check the result and pictures through `bmaya views`. Agents used to do this with `skinPercent` loops, `copySkinWeights` wrappers and numpy.

## The report

`bmaya skin [NAMES]` reports each mesh. With no names it takes the selected skinned meshes, or every skinned mesh in the scene.

| Key | Means |
|---|---|
| `skin` | The skin cluster; `null` for a named mesh that has none. |
| `influences` | Each influence with `verts` (how many vertices it moves) and `max` (its biggest weight), most vertices first. `--limit` sets how many are listed (default 30). |
| `most_per_vertex` | The most influences any one vertex has. |
| `max_influences`, `method`, `normalize` | The cluster's own settings. |
| `bind_pose` | `at: true` when every influence stands where it stood at bind time, else the joints that `moved`. |
| `unused` | Influences that carry no weight. |
| `issues`, `sample` | What a game engine trips on, with a few vertex numbers per kind. |
| `rows` | With `--verts 0:20,57`: the weights of those vertices. |

The issues are:
- vertices with more influences than `--max` (default 4: Godot and Unity take 4 by default);
- weights that do not add up to 1 (off by more than 0.001);
- unweighted vertices;
- dual quaternion or blended skinning;
- influences that are not joints;
- several skin clusters on one mesh.

A skeleton away from its bind pose and influences with no weight are reported, not counted as issues, because both are normal in an animated scene.

`bmaya audit` adds the same issues to a skinned mesh's entry, with `skin: {name, influences, most_per_vertex}`. `bmaya export` and `export --game-exporter` list them in `issues` and still write the file.

## Weights files

`--save FILE` writes:

```json
{"mesh":"hand_r","skin":"hand_r_skin","verts":623,"joints":["hand_r","arm_r"],"max_influences":4,
 "weights":[{"hand_r":0.7,"arm_r":0.3},{"hand_r":1.0}]}
```

Weights are rounded to 6 digits, largest first.

`--load FILE` reads one of three forms:
- a list with one `{joint: weight}` object per vertex;
- an object whose `"weights"` holds that list (other keys are ignored, so a file that also carries bones or points loads as it is);
- `{"weights": {"12": {...}, "40": {...}}}`, which sets only the vertices it names.

The list must have one entry per vertex. For a mesh whose topology changed, copy instead (below).

`--rename` maps the joint names in the file to the scene's joints: `"{}_r"` puts each name into the pattern, `"rig:{}"` adds a namespace, `arm=forearm_r` maps one name. Entries combine: `"{}_r,arm=forearm_r"`. Keep the quotes: PowerShell reads `{}` as a script block.

The file is read by Maya itself, so a large mesh does not travel through the command's reply. 50,000 vertices with 60 joints make a 2.9 MB file and load in under a second.

## Edits

Every edit is one undo step in your own undo chunk (`bmaya undo` takes it back), and the reply has:
- `changed`: how many vertices got different weights;
- `pruned`, `capped`, `normalized`: counts for those steps;
- `after`: `influences` and `most_per_vertex`;
- `issues`: what is left.

| Flag | Does |
|---|---|
| `--load FILE` | Sets the weights from a file. |
| `--copy-from SRC` | Each vertex takes the weights of the closest point on SRC's surface, blended across that triangle. |
| `--mirror AXIS` | With `--copy-from`: SRC's mirror image. Alone: one half of the mesh onto the other. |
| `--prune [MIN]` | Drops weights under MIN (default 0.01). A vertex keeps its biggest weight. |
| `--max N` | With an edit: keeps the N biggest weights per vertex. |
| `--normalize` | Makes each vertex's weights add up to 1. |

The steps run in that order. The vertices an edit sets are always normalized; `--normalize` alone normalizes every vertex. `--max` alone only changes the report's threshold. To cap the influences without any other edit, pass `--normalize --max 4`.

**A mesh with no skin** is bound by `--load` and `--copy-from` to the joints the weights name. The cluster is called `<mesh>_skin`, with `maximumInfluences` set to `--max` or 4. The reply says `bound`. Joints that the weights name but the cluster lacks are added with zero weight and listed in `added`.

**Copying** reads SRC at its bind pose: the cluster's input mesh, not the deformed one. The skeleton may stand in any pose while you copy onto a skinned mesh. For a mesh without a skin, its points as they are now are matched against SRC's bind pose. The copy is therefore refused while SRC's skeleton is posed: the new mesh would be bound in another pose. On a remeshed copy the result matches Maya's `copySkinWeights` with closest point and name association to 1e-6. Vertices farther from SRC than 5% of its size are counted in `far`: the meshes may not line up.

**Mirroring** reflects positions across the plane through 0 on the axis (`x=12.5` moves the plane), the plane that `mirrorJoint` uses. Joints pair by name:
- the tokens `l`, `r`, `left`, `right`, `lf`, `rt` in any case, between `_ . : - |` or spaces (`hand_l`, `L_hand`, `Bip01 L Hand`);
- otherwise `Left` or `Right` at the start of a word (`mixamorig:LeftArm`, `leftFoot`).

A joint without a pair (`spine`) stays itself. The reply counts `paired` names and lists the `unpaired` ones.

Within one mesh the sign names the source half: `--mirror +x` copies +x onto -x. Vertices on the plane are left alone.

Weights are written through `MFnSkinCluster.setWeights` inside a small command from `plug-ins/bmaya_undo.py`, which the bridge loads on first use. That command is what puts the write in Maya's undo queue. Writing 50,000 vertices takes about 0.3 s, against about 10 ms per vertex with `skinPercent`.

## Pictures

- `bmaya views NAME --style weights --joint J` colors each vertex by J's weight, dark blue for none through green and yellow to red for all, blended across the faces and lit like `parts`. Meshes without a skin are grey.
- `--style influence` gives each influence a color and blends them by weight; the legend and `influences` list the joints with the most vertices first. Past 12 influences colors repeat and `note` says so.

Both draw the deformed mesh, so posing the skeleton with `bmaya exec` first shows the deformation too.

A copy or mirror whose meshes only partly overlap is slow: every vertex farther than 5% of the source's size takes an unbounded closest-point search (about 0.3 ms each). Line the meshes up first.
