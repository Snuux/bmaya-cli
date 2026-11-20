# Scene changes

`bmaya file status` answers "what changed in this Maya since the file was saved": by an agent, by another agent, or by the user in the Maya window. Agents used to compare the `modified` flag, `polyEvaluate` counts and the file's time, or import the saved file into a namespace and compare counts, and still could not say which nodes changed.

## How it knows

The bridge takes a snapshot of the scene when a file is opened, created or saved, and when it starts on a scene without unsaved changes. `status` takes another and compares the two node by node, by UUID, so a rename is a rename and not a delete plus an add.

What a snapshot keeps:

| Node | Compared |
|---|---|
| transforms and joints | translate, rotate, scale, shear, rotate axis and order, inherits transform, visibility, the parent (by identity, so renaming it changes nothing below); joints also joint orient, preferred angle, segment scale compensate |
| meshes | vertex, face and edge counts, a checksum of the points, the UV set names and every set's UVs corner by corner, which shading engine each face has, whether it is an intermediate shape. Meshes over 200000 vertices: 20000 of their points, no UVs (`sampled` says so) |
| NURBS curves and surfaces | degree, counts, a checksum of the CVs (control curves) |
| animation curves | key count, a checksum of the key times (in seconds, or the driver's values for driven keys), values, tangent types and directions, weighted tangents, pre and post infinity |
| skin clusters | a checksum of the weights of meshes and curves, the number of influences |
| materials (lambert and every shader type) | a checksum of their own numbers: colors, weights, roughness; not the values a texture drives |
| file textures | path, color space |
| time slider bookmarks, Game Exporter nodes | ranges in seconds, names, clips with their export flag, export path |
| shading engines, place2d, bump, sets, dag poses, other DAG nodes | that they exist, their names |

Not compared: set membership, constraint and IK settings, blend shape weights and other deformers' settings, cameras' and lights' settings, locator shapes, render settings, references as such. A file referenced twice gives both copies the same node UUIDs; the namespace tells them apart.

Scene settings: linear unit, time unit, up axis, playback range (in seconds). Times are kept in seconds, so changing the frame rate is one change of `settings`, not a change of every curve and bookmark.

Checksums are sums, not hashes: two moves that cancel out exactly would pass unseen. Values are rounded to 4 digits first, so float noise is no change.

## The answer

| Key | Means |
|---|---|
| `scene`, `modified` | The file and Maya's unsaved-changes flag. |
| `file` | `saved` (the file's time on disk), `size`, and `changed_on_disk: true` when the file was written after the snapshot by something else. |
| `baseline` | When the snapshot was taken and why: `opened`, `new`, `saved`, `bridge start`. `null` when there is none for this scene (it had unsaved changes when the bridge started): save it, and later calls compare with that save. |
| `added`, `deleted` | Nodes with `name` and `type`. |
| `renamed` | `from` and `to`. |
| `changed` | Nodes with `what` changed: `points`, `topology`, `uvs`, `materials`, `translate`, `rotate`, `parent`, `keys`, `weights`, `path`... |
| `settings` | The scene settings that changed. |
| `commands` | The bmaya commands since the snapshot that could change the scene (reads are left out), with time and client. |
| `same` | True when nothing compared differs. With `modified` set it comes with a `note`: a value set back, a selection, an export, or a change of a kind not compared (render settings, UI state). |
| `seconds` | How long the comparison took. |

Listings have `items`, `shown` and `total` (30 shown). The snapshot lives in the bridge's memory: after a restart of Maya the next open or save is the new start.

A snapshot takes 0.03 s on the Zombie hand scene and 0.23 s on an 18.7 MB scene with 20 skinned characters; the bridge takes one at every save. Values are read at the frame the snapshot was taken at, so moving the time slider is no change. A save that leaves the scene modified (an autosave) keeps the old snapshot.

## Seeing a change

- `bmaya diff NAME -o moved.png` after `bmaya diff NAME --mark` draws the mesh from several sides with each vertex colored by how far it moved, blue for not at all to red for the most. Colors follow the square root of each move's share of the longest, so small moves still show; the legend gives 1/25, 1/4 and all of the longest move in scene units.
- `bmaya views NAME --compare mark -o edit.png` draws the shape at the mark beside the shape now, and both silhouettes over each other. `--compare OTHER` does the same with another mesh. See [modeling](modeling.md#comparing-shapes).
