# FBX export

`bmaya export` resets the FBX exporter and applies a preset. All presets use Y up, FBX 2020 (`FBX202000`), binary, tangents on.

| Preset | Up axis | Triangulate | Smoothing groups |
|---|---|---|---|
| `unity` (default) | Y | no | no |
| `unreal` | Y | yes | yes |
| `godot` | Y | no | no |

Unreal gets triangulation and smoothing groups so its tangent space matches a bake done on the exported triangles and the importer doesn't warn about smoothing. Unity and Godot triangulate on import, so quads are kept.

All presets export skins and blend shapes and skip cameras, lights, constraints and embedded textures. Hard edges and mesh smoothing are off.

With `--animation` the exporter bakes at step 1 from `--start` to `--end`, or over the playback range when they are not given. Resampling is on, the constant key reducer is off. The baked frames go into one take, named after `--clip` or after the file (`walk.fbx` gets a take `walk`). Without that take of its own, Maya's take spans the playback range instead of the baked frames.

Units are not converted. `FBXExportConvertUnitString` has no effect in Maya 2027. A metre scene comes out in centimetres with a unit scale that keeps its size, so the engine sees the right size; `bmaya inspect` shows what the file declares, and `options.units` in the result shows the scene unit.

The path must end in `.fbx`. Missing folders are created, and the selection is restored after export.

## Checks before writing

The meshes that go into the file are checked first:

| Problem | What happens |
|---|---|
| A mesh with no faces | Export refused. |
| The opposite flag over inside-out faces, left by freezing a negative scale | Export refused: Maya draws the mesh the right way, the file gets it inside out. `bmaya cleanup NAME` clears the flag and turns the faces out. |
| The opposite flag over faces stored the right way, instanced shapes, several visible shapes on one transform, unused intermediate shapes | Listed in `issues`; the file is written. |

`--ignore-checks` writes the file anyway and lists the problems in `issues`. `bmaya audit` reports the same problems.

## Checks after writing

The command line reads the written file back (the same reader as `bmaya inspect`) and compares it with what the scene meant to export:

- `written` lists each file with its size, mesh and joint counts and its takes.
- `issues` adds what is wrong in the file: meshes that are missing or came out empty, scene joints that are not there (also the joints a skin needs when Include Children is off), a take that is missing, covers other frames than asked, has no curves, or whose keys are baked at another frame rate than the scene's, and an fps or up axis that differs.

A file that was not rewritten (read-only, or open in another program) fails the export instead of passing as an old file. Without a window the first FBX export after a texture change can fail in a UI callback; bmaya tries once more and adds `retried: true`.

Maya's FBX plugin keeps baking at the frame rate of an earlier export in the same Maya: after a 24 fps scene, a 30 fps scene came out with keys 1/24 s apart. Only reloading the plugin clears it, and a reload also puts the exporter's own settings back to their defaults. So bmaya reloads only when the file it read back has keys between the file's frames: it reloads `fbxmaya`, exports that file once more and adds `reloaded: true`. `bmaya inspect` flags a file written some other way with this fault: its take gets `keys_fps`, and `issues` says the keys are baked at another rate than the file's.

## Game Exporter nodes

`bmaya export --game-exporter [NODE]` exports the way File > Game Exporter's Export button does, for every Game Exporter node in the scene or one of them:

- Each node's export path, file name, preset settings (`gameExp_SetFbxPropertiesWithAttributes`), set (all, selection or a selection set) and Include Children.
- Model nodes write one file. Clip nodes write a file per clip, `<file name><clip>.fbx`, each with one take covering exactly that clip; with "one file" and a file name, all clips go into that file as takes. A clip node set to one file but without a file name writes a file per clip, as a script exporter would, and says so in `issues`.
- `--clip NAME` exports only that clip. `--ignore-checks` works as above.
- `--copy-textures` copies the textures of a model's materials next to its FBX, only when the copy is missing or older, and points the file at the copies; the scene keeps its own paths and color spaces. Tiled (`<UDIM>`), missing, referenced and same-named textures are listed in `issues` and not copied.
- Skipped, with the reason in `skipped`: referenced nodes, Time Editor clips, Move To Origin, a file per root, an empty export path or set.
- Each file is its own call to Maya, so a long list of clips never hits the 50-second limit. If Maya is still busy, the command exits with code 3 and names the files written before.

## Reading a file

`bmaya inspect FILE` reads binary `.fbx` (7.1 and newer), `.glb` and `.gltf` without Maya: unit, up axis, fps, meshes with counts, UV sets, materials and skin, the skeleton with world positions at bind time (`--axes` adds each joint's axes), takes with frame ranges and curve counts, and textures with whether they are found by their path, relative to the file or next to it. `issues` lists empty meshes, unweighted vertices, joint names used twice, keys baked between the file's frames and missing textures.
