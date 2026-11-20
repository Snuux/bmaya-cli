# Scene changes

`bmaya file status` answers "what changed in this Maya since the file was saved": by an agent, by another agent, or by the user in the Maya window. Agents used to compare the `modified` flag, `polyEvaluate` counts and the file's time, or import the saved file into a namespace and compare counts, and still could not say which nodes changed.

## How it knows

The bridge takes a snapshot of the scene when a file is opened, created or saved, and when it starts on a scene without unsaved changes. `status` takes another and compares the two node by node, by UUID, so a rename is a rename and not a delete plus an add.

What a snapshot keeps:

| Node | Compared |
|---|---|
| transforms and joints | translate, rotate, scale, joint orient, visibility, parent |
| meshes | vertex, face and edge counts, a checksum of the points, of the UVs corner by corner, the materials, whether it is an intermediate shape |
| animation curves | key count, a checksum of the key times (in seconds) and values |
| skin clusters | a checksum of all weights, the number of influences |
| file textures | path, color space |
| time slider bookmarks, Game Exporter nodes | ranges in seconds, names, clips, export path |
| shading engines, materials, place2d, bump, sets, dag poses | that they exist, their names |

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

On the Zombie hand scene a snapshot takes about 0.1 s, so the bridge takes one at every save without a pause anyone would notice.

## Seeing a change

- `bmaya diff NAME -o moved.png` after `bmaya diff NAME --mark` draws the mesh from several sides with each vertex colored by how far it moved, blue for not at all to red for the most. The legend gives half and all of the longest move in scene units.
- `bmaya views NAME --compare mark -o edit.png` draws the shape at the mark beside the shape now, and both silhouettes over each other. `--compare OTHER` does the same with another mesh. See [modeling](modeling.md#comparing-shapes).
