# Driving Maya with bmaya

You control Autodesk Maya by running `bmaya` commands in a shell. This is Maya, not Blender: use `maya.cmds`, never `bpy`.

Every command prints one line of JSON on stdout. An error goes to stderr as `error: ...` and `hint: ...`, and the hint says what to do next. `bmaya COMMAND --help` lists the flags of one command. The exceptions that print plain text are `guide`, `doctor`, `logs`, `install`, `uninstall` and `demo`.

Exit codes: `0` done, `1` failed, `2` bad arguments, `3` not ready. Code 3 means Maya is still starting, or still busy with a command you already sent. Do not send that command again: wait, then check `bmaya sessions`.

## Working loop

1. `bmaya scene` to see what exists (unit, up axis, fps, nodes). Filter with `--query` and `--type`.
2. Act: a dedicated command if one fits, otherwise `bmaya exec`.
3. Verify. `bmaya object NAME` for one node, `bmaya audit` for game readiness, `bmaya views NAME -o views.png` to look at a model from several sides, `bmaya anim` for keys and skeletons, `bmaya motion` for how things move, `bmaya shot -o shot.png` for the viewport. Open the PNG it names.

With no Maya running, the first command starts one without a window and says so in a `note:` line on stderr. If it answers with exit code 3, wait about 15 seconds and run it again. A Maya started this way is yours to stop: when the work is done, save with `bmaya file save PATH` and run `bmaya stop`.

## Sending code

`bmaya exec` runs Python on Maya's main thread, one undo step per call. `cmds`, `mel` and `om` (OpenMaya 2) are preloaded. Assign data to `result`; what the code prints comes back as `stdout`. Output is cut at about 8000 characters.

One-liners fit in an argument:

```bash
bmaya exec "result = cmds.ls(selection=True)"
```

Anything with quotes or several lines goes through stdin or a file, so the shell cannot mangle it:

```bash
bmaya exec <<'PY'
crate = cmds.polyCube(name="crate", width=100, height=100, depth=100)[0]
cmds.xform(crate, translation=(0, 50, 0))
result = cmds.ls(crate, long=True)
PY
```

```powershell
Get-Content build_crate.py -Raw | bmaya exec
bmaya exec -f build_crate.py
```

## Dedicated commands first

- `bmaya audit [NAME ...]` instead of hand-written topology checks.
- `bmaya views [NAME ...] -o views.png` instead of several `bmaya shot` calls with moved cameras, when the subject is a model.
- `bmaya remesh`, `bmaya reduce`, `bmaya cleanup` instead of moving, merging and deleting vertices one by one.
- `bmaya export FILE.fbx --preset unity|unreal|godot` instead of `cmds.file(type="FBX export")`. Add `--selection`, `--animation`, `--start N --end N` (frame 0 works), or `--clip NAME` to bake one clip. Units are not converted; the scene unit is what the importer sees.
- `bmaya file new|open|save|import [PATH]`. `new` and `open` refuse to discard unsaved work, and to replace the scene while another client works in that Maya. Never pass `--force` unless the user asked.
- `bmaya undo [STEPS]` reverts one of your own `bmaya exec`, `remesh`, `reduce` or `cleanup` calls per step.
- `bmaya shot --frames 1,6,12,18,24 -o walk.png` puts up to 12 frames on one contact sheet. Use it to review animation instead of many single shots. A range like `--frames 0:32` (both ends included) or `0:32:4` is thinned evenly to 12 tiles. `--clip NAME` takes the frames of a clip. `--camera NAME` gives a fixed view.
- `bmaya shot --aspect 1:1 --light unlit` sets the frame shape and the lighting for that one shot, and puts everything back after. `--light unlit` shows texture and base colors as they are, `soft` adds an even studio light, `scene` uses the scene's lights. Use these instead of building lights, swapping materials or changing color management yourself.
- Never call `cmds.ogs(reset=True)`: it can crash a Maya without a window, and `bmaya shot` does not need it. Save before changing color spaces.

Relative paths are resolved against the directory you run `bmaya` in.

## Modeling

Building geometry is plain `bmaya exec`. What you cannot do is see the model, so check it in numbers and in pictures after every step that changes its shape:

1. Before building: `bmaya scene` for the unit and up axis. Decide the real size first and build to it.
2. Build in small steps, one `bmaya exec` each. Use Maya's tools (recipes below), not vertex-by-vertex edits: booleans, mirror, bevel, extrude, bridge, and `bmaya remesh`, `bmaya reduce`, `bmaya cleanup` for whole-mesh work.
3. After each step:
   - `bmaya views NAME -o views.png` and open it: front, right, top and a three-quarter view on one sheet, a color per mesh, edges on top, width and height written on the straight-on views.
   - `bmaya audit NAME`: counts, size, and what is wrong with the geometry.
   - `bmaya diff NAME` when the step was an edit of an existing mesh: what it changed, and whether it touched more than it should (`bmaya diff NAME --mark` first).
4. Before export: `bmaya cleanup NAME` if the audit lists geometry issues, freeze transforms, delete history, then `bmaya audit` again.

Reading a `views` sheet:

- Each mesh has its own color; `parts` in the result says which name has which color. Parts that should be one mesh, or that float apart, show here.
- A dark face is seen from behind: its normal points away. On a closed model that means reversed normals; `bmaya cleanup` turns them out.
- `front` looks from +Z in a Y-up scene (the model's face, if it faces +Z), `right` from +X, `top` from above. Width and height on these tiles are in scene units: compare them with the size you meant.
- `--views all` adds back, left, bottom and a three-quarter view from behind. Name only the views you need for bigger tiles: `--views front,right`.
- `--style silhouette` shows the outline alone: the fastest way to judge the overall shape and proportions.
- `--style xray` shows every edge, hidden ones too, so parts inside other parts are visible.
- `--section x` (or `y`, `z`, or `x=12.5`) cuts the model and looks at the cut: wall thickness, what is inside, what pokes through. The cut line is red.
- `--style shaded --light soft` is Maya's own render, for materials and textures. It shows the whole scene; the other styles draw only the meshes you name.
- Past about 10000 triangles per 400 px tile the edges are left out and `note` says so. Name fewer meshes or fewer views to get them back.

The commands:

- `bmaya audit NAME --symmetry x` checks that every vertex has a twin across the plane through the mesh's pivot, and gives the positions of a few that have none. Run it after editing one side of a mirrored model.
- `bmaya diff NAME --mark` remembers a mesh; `bmaya diff NAME` after the edit gives counts, size, area and volume before and after, and for an unchanged topology how many vertices moved, how far, and the box they are in. `--against OTHER` compares two meshes.
- `bmaya remesh NAME --to tris --edge 0.5` rebuilds the mesh as even triangles; `--to quads --faces 800` runs Maya's Retopologize for a quad mesh. UVs and skin weights are lost, so remesh before unwrapping and skinning.
- `bmaya reduce NAME --tris 5000` (or `--percent 50`) lowers the triangle count and keeps the shape; `--keep-uvs`, `--keep-borders`, `--keep-hard-edges`, `--keep-quads` pin what matters.
- `bmaya cleanup NAME` merges vertices on the same spot, removes zero-area faces, zero-length edges and lamina faces, makes the faces wind one way and turns the normals outward.

`remesh`, `reduce` and `cleanup` work on one mesh, are one undo step each (`bmaya undo`), answer with `before`, `after` and the audit `issues` that are left, and mark the mesh first. If one exits with code 3, Maya is still working: do not send it again, wait and run `bmaya diff NAME`.

## Animating

Writing keys is plain `bmaya exec`. What you cannot do is watch the motion, so check it in numbers and in pictures after every change:

1. Before keying: `bmaya scene` for fps and the playback range, `bmaya anim` for the clips, `bmaya rig ROOT` for the controls you may key. Set the fps (`cmds.currentUnit(time="ntsc")`) before the first key.
2. Block the key poses with stepped tangents, a few poses per `bmaya exec`. Look at them on one sheet: `bmaya shot --frames 0,8,16,24 --fit hero`.
3. Switch to auto or spline tangents, then check:
   - `bmaya motion root --clip walk --loop --contacts foot_l,foot_r`: fix each issue or know why it stays. Stepped keys pop by design, so run this after splining.
   - `bmaya shot --clip walk --camera side --fit hero --trail hand_r,foot_l`: arcs and spacing.
   - `bmaya anim hips --plot -o hips.png`: eases, overshoot, holds, cycle ends.
4. Register the clip as a Game Exporter clip or a time slider bookmark (recipe below), so `--clip NAME` and `bmaya export --clip NAME` find it.
5. `bmaya playblast walk.gif --clip walk --camera side --fit hero` when the user wants to watch it.

Pass `bmaya motion` the nodes of the clip you check (a group name means everything under it, a skeleton's top joint the whole skeleton): without names it takes the selection, or every animated node in the scene, so other characters' cycles show up as issues and widen the limits. Put every node that touches the ground in `--contacts`, toes included. Shots draw geometry only: a bare skeleton shows nothing, so give it proxy meshes or look at it through `--trail`.

For game clips: the last frame of a cycle repeats the first pose (`bmaya motion --loop` checks that), and every animated channel has a key on the first and last frame of its clip; otherwise the pose at the clip's edge leans toward the next clip on the timeline.

The commands:

- `bmaya anim` lists the clips: Game Exporter clips and time slider bookmarks, with their frame ranges. Commands that take `--frames` also take `--clip NAME`.
- `bmaya rig [ROOT]` before animating someone else's rig: which controls to key, which attributes are `driven` (keys on them do nothing), limits, rotate orders, IK handles with their pole and driver, and switches like `ik` that blend IK and FK.
- `bmaya motion root --clip walk --loop --contacts foot_l,foot_r` checks motion frame by frame, in numbers: pops, flips, foot slide, sinking, a loop seam that does not close, Euler jumps, keys between frames, outside clips or missing on a clip's edge. Each issue names the node, the frames and how big it is. Run it after every change to an animation; a picture shows a pose, not a one-frame pop. `--values` adds world positions per frame, `--attrs rx,ik` lists attributes per frame.
- `bmaya shot --clip walk --trail hand_r,foot_l --camera side` draws the world path of up to 4 nodes over the frames: a dot per frame shows spacing (dots close together are slow, far apart fast), a bigger numbered dot marks each key, a ring shows where the node is on each tile. Use it to judge arcs and eases.
- `bmaya shot --clip walk --camera side --fit hero` frames the named nodes on every frame of the shot through a temporary copy of the camera, so a character that walks away stays in view. The camera itself is not moved. Prefer it to moving cameras with `viewFit`.
- `bmaya anim hips --plot -o curves.png` draws a node's curves like the Graph Editor: a panel each for translate, rotate, scale and the rest, dots on keys, red ticks on Euler jumps. Open it to check eases, overshoot, holds and whether a cycle's ends meet. `--attrs ty,rx` and `--frames` / `--clip` narrow it.
- `bmaya playblast walk.gif --clip walk --camera side --fit hero` renders every frame and joins them into a GIF or MP4 with ffmpeg, for the user to watch. Never step `currentTime` and call `bmaya shot` per frame for this. You cannot watch the result yourself: judge motion with `bmaya motion` and contact sheets. Long ranges take 0.2-0.4 s per frame; run them in the background.

## Sessions

- `bmaya sessions` lists the running Mayas. With one, every command uses it. With several, pick one with `bmaya use ID`; the choice is remembered. `--session ID` picks one for a single command.
- `bmaya start` starts one more Maya without a window (`--scene FILE` opens a file, `--gui` shows the window) and makes it your current session.
- A Maya without a window has `headless: true`. It has no timer and runs until `bmaya stop`. When you are done with one you started: save with `bmaya file save PATH`, then `bmaya stop`. Leave a Maya you did not start alone.
- `bmaya stop` never closes a Maya that has a window. That one is the user's.
- Screenshots of a Maya without a window are rendered offscreen from `persp` or the `--camera` you pass, in the shape of the render resolution (`defaultResolution`). Frame the subject with `--fit NAME`, or move the camera first: `cmds.viewFit("persp", all=True)`. UI commands (`modelPanel`, `playblast`, `getPanel`) fail there.
- If a session you used is gone, the next command says so once and names its journal. Tell the user; unsaved work in it is lost.

## Sharing a Maya

- You act under a client name, shown as `client` by `bmaya sessions`. It stays the same for all your commands. Subagents of one agent session usually inherit that name; when several of them work in one Maya, give each its own with `--client NAME` so their undo steps stay apart. `clients` above 1 on a session means another agent works there: use your own names and groups, and do not delete or rename what you did not create.
- `bmaya undo` never reverts someone else's work. If the result has `blocked_by`, or the error says nothing was undone, the latest change belongs to another client or to the user (`maya-ui`). Stop and ask the user; do not work around it with `cmds.undo()`. If the user agrees to lose their own change made in the Maya window, repeat with `--force`; it never touches another client's steps.

## Blender to Maya

| Blender | Maya |
|---|---|
| `bpy.data.objects` | `cmds.ls(type="transform", long=True)`, `cmds.ls(selection=True)` |
| `bpy.ops.mesh.primitive_cube_add` | `cmds.polyCube(name="crate")` returns `[transform, historyNode]` |
| Object holds mesh data | Transform node plus a shape child (`crate`, `crateShape`). Move/rotate the transform. |
| Collections | Groups (`cmds.group`) for hierarchy, sets (`cmds.sets`) for membership |
| Modifiers | Construction history (`polyBevel1`, ...) and deformers; `cmds.delete(obj, constructionHistory=True)` bakes |
| Z up, meters, radians | Y up, centimeters, degrees (check `bmaya scene`: `up`, `unit`) |
| `obj.keyframe_insert` | `cmds.setKeyframe(obj, attribute="translateY", time=12, value=50)` |
| Armature, bones, vertex groups | Joints (`cmds.joint`) plus `skinCluster` |
| Materials, slots | Shader node plus `shadingEngine` set; assign the object to the engine |
| `obj.name` unique | Short names may clash. Use long paths like `|props|crate` |

Components: `crate.f[1]`, `crate.e[0:3]`, `crate.vtx[0]`. Flatten ranges with `cmds.ls(sel, flatten=True)`. Most `cmds` calls return lists, not strings.

## Recipes

Each block is the code for one `bmaya exec` call. All run in Maya 2027 (Python 3) as written.

```python
# create, place, freeze, clean
crate = cmds.polyCube(name="crate", width=100, height=100, depth=100)[0]
cmds.xform(crate, translation=(0, 50, 0))
cmds.makeIdentity(crate, apply=True, translate=True, rotate=True, scale=True)
cmds.delete(crate, constructionHistory=True)
result = cmds.ls(crate, long=True)
```

```python
# extrude a face, bevel edges, move vertices
cmds.polyExtrudeFacet("crate.f[1]", localTranslateZ=5, localScale=(0.5, 0.5, 1))
cmds.polyBevel3("crate.e[0:3]", fraction=0.3, segments=2, chamfer=True)
cmds.move(0, 2, 0, "crate.vtx[0:3]", relative=True)
cmds.delete("crate", constructionHistory=True)
```

```python
# boolean: 1 union, 2 difference, 3 intersection
wall = cmds.polyCube(name="wall", width=4, height=3, depth=0.4)[0]
hole = cmds.polyCylinder(name="hole", radius=0.6, height=2, axis=(0, 0, 1))[0]
cut = cmds.polyCBoolOp(wall, hole, operation=2, name="wall_cut")[0]
cmds.delete(cut, constructionHistory=True)
```

```python
# mirror: model the +X half with the middle open, then mirror across X and weld the seam
cmds.delete("half.f[5]")  # the face on the mirror plane
cmds.polyMirrorFace("half", axis=0, axisDirection=1, mirrorAxis=2, mergeMode=1,
                    mergeThreshold=0.001)
cmds.delete("half", constructionHistory=True)
# then: bmaya audit half --symmetry x
```

```python
# edge loop, fill a hole, bridge two border edges, weld close vertices
cmds.polySplitRing("box.e[4]", splitType=1, weight=0.5)
cmds.polyCloseBorder("box")
cmds.polyBridgeEdge("strips.e[3]", "strips.e[6]", divisions=0)
cmds.polyMergeVertex("box", distance=0.001)
```

```python
# smooth, soften or harden edges by angle, make all faces wind one way
cmds.polySmooth("rock", divisions=1)
cmds.polySoftEdge("rock", angle=30)  # 0 hardens every edge, 180 softens every edge
cmds.polyNormal("rock", normalMode=2, userNormalMode=0)  # 0 reverses, 2 conforms
```

```python
# sit a prop on the ground and put its pivot at the base
low = cmds.exactWorldBoundingBox("prop")
cmds.move(0, -low[1], 0, "prop", relative=True)
center = cmds.objectCenter("prop")
cmds.xform("prop", worldSpace=True, pivots=(center[0], 0, center[2]))
```

```python
# UVs: unfold and pack with Unfold3D, or copy them from another mesh of the same shape
cmds.loadPlugin("Unfold3D", quiet=True)
cmds.polyAutoProjection("crate.f[*]", layoutMethod=1, percentageSpace=0.2)
cmds.u3dUnfold("crate.map[*]", iterations=1, pack=False)
cmds.u3dLayout("crate", resolution=256, shellSpacing=0.01, tileMargin=0.01)
cmds.transferAttributes("high", "low", transferUVs=2, transferNormals=1, sampleSpace=0)
cmds.delete("low", constructionHistory=True)
```

```python
# UVs: project, pack, add a second set
cmds.polyAutoProjection("crate.f[*]", layoutMethod=1, scaleMode=1, percentageSpace=0.2)
cmds.polyUVSet("crate", create=True, uvSet="lightmap")
cmds.polyUVSet("crate", currentUVSet=True, uvSet="map1")
result = cmds.polyUVSet("crate", query=True, allUVSets=True)
```

```python
# material
mat = cmds.shadingNode("lambert", asShader=True, name="crate_mat")
sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="crate_matSG")
cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
cmds.setAttr(mat + ".color", 0.8, 0.2, 0.1, type="double3")
cmds.sets("crate", edit=True, forceElement=sg)
```

```python
# animation: set fps and range first, then key
cmds.currentUnit(time="ntsc")  # 30 fps; "film" is 24
cmds.playbackOptions(minTime=1, maxTime=24, animationStartTime=1, animationEndTime=24)
cmds.setKeyframe("crate", attribute="translateY", time=1, value=0)
cmds.setKeyframe("crate", attribute="translateY", time=12, value=50)
cmds.setKeyframe("crate", attribute="translateY", time=24, value=0)
cmds.keyTangent("crate", attribute="translateY", edit=True, time=(12, 12),
                inTangentType="auto", outTangentType="auto")
cmds.keyTangent("crate", attribute="rotateY", edit=True,
                inTangentType="linear", outTangentType="linear")  # also: step, spline, clamped, flat
```

Changing `currentUnit(time=...)` after keying retimes the keys. To keep key frame numbers, pass `updateAnimation=False`.

```python
# key a pose on several controls at once, stepped while blocking
pose = {"hips_ctrl": {"translateY": -4}, "hand_r_ctrl": {"translateX": 12, "rotateZ": -30}}
for node, values in pose.items():
    for attr, value in values.items():
        cmds.setKeyframe(node, attribute=attr, time=12, value=value)
controls = list(pose)
cmds.keyTangent(controls, edit=True, outTangentType="step")
# later, to spline: cmds.keyTangent(controls, edit=True, inTangentType="auto", outTangentType="auto")
```

```python
# close a cycle: every curve gets its first-frame value keyed on the last frame
# (copyKey/pasteKey across several nodes can paste onto the wrong curves)
controls, start, end = ["hips_ctrl", "hand_r_ctrl"], 0, 32
for curve in cmds.keyframe(controls, query=True, name=True) or []:
    value = cmds.keyframe(curve, query=True, eval=True, time=(start, start))[0]
    cmds.setKeyframe(curve, time=end, value=value)
cmds.setInfinity(controls, preInfinite="cycle", postInfinite="cycle")  # loops the preview
```

```python
# Euler flips after rotating by hand, keys between frames, IK and constraints baked to joints
cmds.filterCurve(cmds.listConnections(controls, type="animCurveTA"))
cmds.snapKey(controls, timeMultiple=1)
joints = cmds.ls("|root", dagObjects=True, type="joint", long=True)
cmds.bakeResults(joints, time=(0, 32), simulation=True, sampleBy=1, preserveOutsideKeys=True)
```

```python
# register a clip for `--clip walk` and for the Game Exporter
cmds.loadPlugin("gameFbxExporter", quiet=True)
exporter = (cmds.ls(type="gameFbxExporter") or [cmds.createNode("gameFbxExporter")])[0]
used = cmds.getAttr(exporter + ".animClips", multiIndices=True) or []
clip = f"{exporter}.animClips[{max(used) + 1 if used else 0}]"
cmds.setAttr(clip + ".animClipName", "walk", type="string")
cmds.setAttr(clip + ".animClipStart", 0)
cmds.setAttr(clip + ".animClipEnd", 32)
```

```python
# joints and skin
cmds.select(clear=True)  # otherwise the new joint parents under the selection
joints = [cmds.joint(name=n, position=p) for n, p in
          [("spine_01", (0, 0, 0)), ("spine_02", (0, 50, 0)), ("spine_03", (0, 100, 0))]]
cmds.joint(joints[0], edit=True, orientJoint="xyz", secondaryAxisOrient="yup",
           children=True, zeroScaleOrient=True)
# freeze the mesh and delete its history before binding
skin = cmds.skinCluster(joints, "body", toSelectedBones=True, maximumInfluences=4,
                        normalizeWeights=1, name="body_skin")[0]
cmds.skinPercent(skin, "body.vtx[0]", transformValue=[(joints[0], 0.5), (joints[1], 0.5)])
```

```python
# triangle check (bmaya audit also reports tris)
before = cmds.polyEvaluate("crate", face=True), cmds.polyEvaluate("crate", triangle=True)
cmds.polyTriangulate("crate", constructionHistory=False)
result = before
```

Then run `bmaya export crate.fbx`. For Unreal add `--preset unreal`, which triangulates and writes smoothing groups.

## Rules of thumb

- Use long DAG paths (`|group|crate`) whenever a name might clash. A short name that matches several nodes is an error.
- Return data through `result`, not large prints. Return counts and names, not whole point lists.
- Keep each `bmaya exec` call small and about one logical step. Undo works per call and only on your own calls, and the crash journal records the last call that started.
- If a call raises, the error names the exception and the line. No rollback happens: inspect the scene before retrying.
- Freeze transforms and delete history on game assets before export. `bmaya audit` flags both.
- Look at a model with `bmaya views` after every change to its shape. A count that looks right says nothing about a part that points the wrong way.
- Exit code 3 with "still running" means Maya is still working or blocked by a dialog. Do not resend the command; check `bmaya sessions` (`busy`) and inspect the scene.
- Keep the user's unit and up axis unless asked; convert values to the scene's centimeters and degrees.
