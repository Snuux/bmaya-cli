# Commands

Every command of `bmaya`. `bmaya COMMAND --help` explains each one with examples, and the topic pages in this folder go into detail.

| Command | What it does |
|---|---|
| `bmaya scene [-q QUERY] [-t TYPE] [-n LIMIT]` | File, unit, up axis, fps, frame range, selection, nodes. |
| `bmaya object NAME` | One node: transform, bounding box, mesh counts, UV sets, materials, history, keys. |
| `bmaya exec [CODE] [-f FILE]` | Runs Python in Maya with `cmds`, `mel` and `om` loaded. Assign to `result` to return data. One undo step per call. Code also comes from stdin. |
| `bmaya shot [-o FILE] [--camera C] [--frames 1,12,24\|0:30:2\|--clip NAME] [--size N] [--aspect W:H] [--light unlit\|soft\|scene] [--trail A,B] [--fit A,B]` | Writes a PNG of the viewport and prints its path. `--frames` makes a contact sheet (a range is thinned to 12 tiles), `--aspect` sets the frame shape, `--light` the lighting for this shot, `--trail` draws node paths with a dot per frame, `--fit` frames nodes on every frame without moving the camera. |
| `bmaya playblast OUT.gif\|OUT.mp4\|DIR [--frames\|--clip] [--camera C] [--fit A,B]` | Renders every frame and joins them into a GIF or MP4 with ffmpeg (PNG frames without it). For people to watch. |
| `bmaya views [NAME ...] [-o FILE] [--views front,right,top,persp\|all] [--style parts\|silhouette\|xray\|weights\|influence\|shaded] [--joint J] [--section x\|y=12.5] [--no-wire] [--size N] [--compare OTHER\|mark]` | Draws a model from several sides in one PNG: a color per mesh, edges on top, width and height on the straight-on views. `silhouette` for the shape, `xray` and `--section` for what is inside, `weights --joint J` and `influence` for skin weights, `shaded` for Maya's own render. `--compare` puts another mesh, or the mesh before an edit, beside it with both silhouettes overlaid. |
| `bmaya audit [NAME ...] [--symmetry x\|y\|z]` | Game-readiness check: counts, faces by kind, size, n-gons, non-manifold geometry, flipped or inward normals, UVs and their overlaps, materials, history, transforms, stray and instanced shapes, the opposite flag, skin weights, mirror symmetry. |
| `bmaya diff NAME [--mark] [--against OTHER] [-o FILE] [--views A,B] [--size N]` | What an edit changed in a mesh: counts, size, area, volume, moved vertices and where they are. `-o` draws each vertex colored by how far it moved. |
| `bmaya topo NAME [--ids] [--gap G] [-o FILE [--tubes A,B]]` | Edge flow: shells, valence, closed edge loops around tubes, the tubes, and joint places in the middle of each run of close loops. `-o` draws them over the mesh. |
| `bmaya uv NAME [--texture PNG] [--against MESH\|FILE] [-o FILE] \| --bake position,normal -o DIR` | UV layout: shells, texel density, coverage, overlaps, stretch, padding, a layout hash, textures, whether each texture fits the layout; a picture of the layout over the textures; position and normal maps baked into UV space. |
| `bmaya remesh NAME [--to tris --edge L \| --to quads --faces N]` | Rebuilds the topology: even triangles, or quads with Maya's Retopologize. |
| `bmaya reduce NAME --tris N \| --percent P [--keep-uvs] [--keep-borders] [--keep-hard-edges] [--keep-quads]` | Lowers the triangle count and keeps the shape. |
| `bmaya cleanup NAME [--merge DIST] [--ngons]` | Merges vertices on the same spot, removes bad faces and edges, conforms the normals and turns them outward. |
| `bmaya clip [NAME] [--add A:B\|N] [--set A:B\|N] [--remove [--keys]] [--node N] [--gap G]` | Clips: Game Exporter rows and bookmarks together, with what is off (ranges that differ, overlaps, rows not exported, clips without keys, keys of removed clips); adds, moves and removes a clip's row and bookmark in one undo step. |
| `bmaya anim [NAME] [--plot -o FILE]` | Clips (Game Exporter, time slider bookmarks), curves, key ranges, fps, skeletons, skin clusters. `--plot` draws the node's curves as a PNG, like the Graph Editor. |
| `bmaya skin [NAME ...] [--max N] [--verts SPEC]` | Skin report: influences and their vertex counts, the most per vertex, unused influences, bind pose, issues (over the influence limit, unnormalized, unweighted, dual quaternion). |
| `bmaya skin NAME --save FILE \| --load FILE [--rename MAP] \| --copy-from SRC [--mirror AXIS] [--prune [MIN]] [--max N] [--normalize]` | Weights to and from a JSON file of `{joint: weight}` per vertex, copied from another mesh by closest point, mirrored with left/right joint pairs, pruned, capped, normalized. One undo step; binds a mesh that has no skin. |
| `bmaya skin NAME ... --rebind [--keep-shape]` | Makes the current pose the bind pose; the mesh keeps the shape it shows (or its bound shape with `--keep-shape`). |
| `bmaya bones [ROOT ...] [--axes] [--against FILE [--rename MAP]]` | Skeleton report at the bind pose: positions, lengths, jointOrient, aim axis, left/right twins and whether they mirror, duplicate names, transforms between joints, unweighted joints. `--against` compares it with an FBX, GLB or glTF skeleton. |
| `bmaya bones ROOT --orient world\|xy\|=-zy [--up AXIS]` | New joint axes, joints kept in place, skins compensated so meshes do not move. |
| `bmaya pose ROOT --bind` | Puts the skeleton at the pose its meshes were bound in. |
| `bmaya mirror NAME [--axis x\|x=POS] [--name NEW]` | The other side of a mesh across a world plane, right side out; a skinned mesh brings its twin joints and its weights. A joint mirrors its chain. |
| `bmaya rig [ROOT]` | A rig's controls: keyable and driven attributes, values, limits, rotate order, keys, IK handles, constraints, ik/fk switches, character sets. |
| `bmaya motion [NAME ...] [--frames 0:30\|--clip NAME] [--loop] [--contacts A,B] [--values] [--attrs A,B]` | Checks motion frame by frame: pops, flips, foot slide, sinking, loop seam, Euler jumps, keys off frames or outside clips. Reports measurements, and values per frame on request. |
| `bmaya export FILE.fbx [--preset unity\|unreal\|godot] [--selection] [--animation] [--clip NAME]` | FBX export with an engine preset. `--clip` bakes one clip's frames into its own take. Meshes that would come out empty or inside out stop it; the written file is read back and what it lacks is listed in `issues`. |
| `bmaya export FILE.glb\|FILE.gltf [--selection] [--animation] [--clip NAME] [--clips]` | glTF export through [maya-glb-io](https://github.com/Snuux/maya-glb-io) (optional, `bmaya install --gltf`): Y-up, metres, skin, morphs, every moving node sampled per frame, redundant keys left out, glTF names of imported nodes written back. `--clips` puts every clip in the one file as a named animation. Read back and checked like FBX. |
| `bmaya export --game-exporter [NODE] [--clip NAME] [--copy-textures]` | Exports the way the scene's Game Exporter nodes are set up: their paths, sets and presets, a file per clip with one take each, every file checked. |
| `bmaya inspect FILE [--axes] [--limit N]` | Reads an FBX, GLB or glTF file without Maya: unit, up axis, fps, meshes with counts, UV sets, materials and skin, the skeleton with bind positions, takes with frame ranges, textures and whether they are found. |
| `bmaya file new\|open\|save\|import [PATH] [--force]` | Scene files. |
| `bmaya file status` | What changed since the scene was opened or saved: nodes added, deleted, renamed and changed, settings, and the bmaya commands that edited. |
| `bmaya undo [STEPS] [--force]` | Undoes your own last steps, never another client's. |
| `bmaya sessions`, `bmaya use ID` | Lists the running Mayas, picks the one to talk to. |
| `bmaya start [--gui] [--scene FILE]`, `bmaya stop [ID]` | Starts a Maya, stops one that runs without a window. |
| `bmaya guide` | Prints the manual for agents: working loop, Blender-to-Maya map, recipes. |
| `bmaya install`, `uninstall`, `doctor`, `logs`, `demo` | Maya module, health check, command journal, demo. `install --gltf` adds the glTF library, `install --draco` also its Draco decoder. |

## Output and exit codes

Results go to stdout as one line of JSON, errors to stderr as `error: ...` and `hint: ...`.

| Exit code | Meaning |
|---|---|
| 0 | Done. |
| 1 | Failed. stderr says why and what to do next. |
| 2 | Bad arguments. |
| 3 | Not ready: Maya is starting, or still busy with a command already sent. Wait; do not send it again. |
