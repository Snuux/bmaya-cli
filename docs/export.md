# FBX export

`bmaya export` resets the FBX exporter and applies a preset. All presets use Y up, FBX 2020 (`FBX202000`), binary, tangents on.

| Preset | Up axis | Triangulate | Smoothing groups |
|---|---|---|---|
| `unity` (default) | Y | no | no |
| `unreal` | Y | yes | yes |
| `godot` | Y | no | no |

Unreal gets triangulation and smoothing groups so its tangent space matches a bake done on the exported triangles and the importer doesn't warn about smoothing. Unity and Godot triangulate on import, so quads are kept.

All presets export skins and blend shapes and skip cameras, lights, constraints and embedded textures. Hard edges and mesh smoothing are off.

With `--animation` the exporter bakes at step 1 from `--start` to `--end`, or over the playback range when they are not given. Resampling is on, the constant key reducer is off.

Units are not converted. `FBXExportConvertUnitString` has no effect in Maya 2027, and Unity, Unreal and Godot read the unit scale stored in the file, which follows the scene's linear unit. Keep the scene in the unit you want the engine to see; `options.units` in the result shows which one was used.

The path must end in `.fbx`. Missing folders are created, and the selection is restored after export.
