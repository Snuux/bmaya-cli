# Skeletons and mirroring

`bmaya bones` describes a skeleton and compares it with a game file, `bmaya pose --bind` puts it back at its bind pose, `bmaya skin --rebind` makes the current pose the bind pose, and `bmaya mirror` builds the other side of a mesh with its joints and weights. Agents used to do this with `struct`-parsed glTF, a Godot script that printed bone rests, Blender for bone axes, `mirrorJoint` plus a freeze that left the left hand inside out, and numpy to unpose a mesh.

## The report

`bmaya bones [NAMES]` takes joints (a joint means its whole chain), groups, or skinned meshes (their skeleton). With no names it takes the selection, or every joint in the scene.

Positions and axes are read at the bind pose, from each skin's `bindPreMatrix` (`from: bind`), so an animated scene reports the skeleton as it was bound. A skeleton that skins nothing is read as it stands (`from: scene`). Joints that skin nothing but hang under skinned ones are carried along by their own local matrices.

Each joint in `joints`:

| Key | Means |
|---|---|
| `parent` | The nearest joint above it, past any plain transforms in between. |
| `at`, `length` | World position, and the distance to the parent joint. |
| `orient`, `rotate`, `rotate_axis` | jointOrient, rotate (as it is now) and rotateAxis, when not 0. |
| `aim` | Which of the joint's own axes points at its only child: `+x`, `-z`, or `none`. |
| `twin` | The joint of the other side, by name. |
| `verts` | How many vertices it moves, over all skins of the skeleton. |
| `axes` | With `--axes`: its x, y and z in world space. |
| `scale` | When it is not 1. |

Then for the whole set:

- `roots`, `skins` (each with `at_bind`, and the joints that `moved` from it);
- `pairs`: `paired`, `lonely` (sided names without a twin), and `mirror` with `kinds` and `off`. `kinds` counts twins whose axes are the negated mirror image (`behavior`, mirrorJoint's default: the same rotate values move both sides as mirror images), point the same way (`orientation`), or neither (`other`). `off` lists twins that stand away from the mirror image across `--mirror` (x through 0 by default; `--mirror x=12.5` moves the plane);
- `clashes`, `scaled`, `zero_length`, `unweighted`, `between`.

Issues:
- joint names used twice: FBX and engines keep one;
- negative or uneven joint scale;
- some twins off their mirror image while others mirror (all off reads as a skeleton not meant to be symmetric, so it is reported in `pairs` only);
- plain transforms between joints. `cmds.parent` inserts one when the parent joint's matrix has shear, even 1e-5 of it, and FBX writes each as a `Null` node. The Zombie hand rig built from `fp_hands_fit.json` had 13 per hand.

## Comparing with a game file

`bmaya bones ROOT --against FILE` reads the skeleton of an `.fbx`, `.glb` or `.gltf` in the command line (no Maya, the readers of `bmaya inspect`) and compares it with the scene's. `--rename` maps the file's names to the scene's, like `bmaya skin`: `"{}_r"`, `"rig:{}"`, `arm=forearm_r`.

`against` holds:

| Key | Means |
|---|---|
| `matched`, `missing`, `extra` | Joints found by name, joints of the file the scene lacks, scene joints the file lacks. |
| `parents` | Joints under another parent than in the file. |
| `place` | `off`: the farthest joint as they stand, in scene units (the file's unit is converted). `fit`: when the scene skeleton is the file's one scaled, turned and moved, the `scale`, the turn in degrees and the move, and `off` after it. The fit is used only when it leaves fewer joints off than comparing as they stand, so a few wrong joints are not smeared over all of them. |
| `moved` | Joints farther than 0.2% of the skeleton's size from where the file has them. |
| `turned` | Joints whose axes differ by more than a degree (after the fit's turn). |
| `lengths` | The bone length ratio most bones share, and the bones off it by more than 2%. |
| `same` | True when nothing differs. |

Duplicate names in what you compare are refused: name the top joint of one skeleton.

The Zombie game hand against `zombie_hand.fbx`: 14 of 14 joints, `same: true`. Against `hands.glb` (metres): names, parents and axes equal, 12 joints elsewhere and 9 bones of other length, because the hand was refitted. A build that added parent positions twice is named exactly: i2, i3, m2, m3, r2, r3, t2, t3.

## New axes

`bmaya bones ROOT --orient AXES [--up AXIS]` gives the joints new axes and keeps every joint where it is:

- `world`: the world's axes, no turn at rest. Some engines and some game code (`Hands.POSES` in Zombie) set absolute bone rotations and want this;
- `xy`, `-zy`, `yz`...: the first axis points at the child, the second toward `--up` (y by default). A minus needs `=`: `--orient=-zy`.

Rotate and rotateAxis go to 0 and the turn goes into jointOrient. Every skin on these joints gets its `bindPreMatrix` compensated (`bindPreMatrix * oldWorld * newWorld⁻¹`), so the mesh does not move; `mesh_moved` says by how much. A joint with several children aims at the first one that stands away from it, and `aimed` names it; an end joint takes its parent's axes. Joints whose rotations have keys are refused: new axes would change what the keys mean.

## Bind pose and rebinding

`bmaya pose NAMES --bind` puts every joint that skins a mesh where it stood when the mesh was bound, read from the skin itself rather than from a `dagPose` node (there may be several, or none). Joints held by an IK handle, a constraint or a lock are skipped and listed. A joint with keys keeps the pose only until the frame changes: read commands keep it, `bmaya shot`, `playblast` and `motion` set the frame and bring the keys back.

`bmaya skin MESH... --rebind` makes the current pose the bind pose:
- `bindPreMatrix` becomes the inverse of each joint's world matrix, `geomMatrix` the mesh's world matrix;
- the shape the skin starts from takes the points the mesh shows now, so the mesh does not move (`mesh_moved`, about 1e-7). Deformers in front of the skin, like a blendShape, are taken into account: their offsets stay theirs;
- a bind pose node of its own replaces the cluster's old one, which is deleted when no other cluster uses it.

So posing the joints and rebinding bakes the pose into the mesh. This is not an exact inverse of the skin: vertices that blend several joints do not come back to where they were when you pose back. On the Zombie game hand, curling all 12 finger joints by 35°, rebinding and uncurling moved vertices by 0.036 cm on average and 0.58 cm at most (83 of 623 by more than 1 mm). `fp_hands_fit.unpose()` solved that exactly per vertex.

`--keep-shape` keeps the shape the mesh was bound with instead: the joints stand where they are now and the mesh goes back to its bound shape. That is Maya's `moveJointsMode` after the fact, for joints moved to new places.

## Mirroring

`bmaya mirror NAME [--axis x] [--name NEW]` builds the other side across a world plane (x through 0; `--axis x=12.5` moves it):

- **A mesh.** The copy is named by side (`hand_r` gives `hand_l`, `L_arm` gives `R_arm`) or `--name`. It is mirrored by freezing a negative scale with the pivot on the plane, the opposite flag that freeze sets is cleared and the faces are turned, so a game gets it right side out. `polyNormal` runs with `userNormalMode=0`, which mirrors locked normals correctly. Vertex i of the copy is the mirror image of vertex i of the source, and materials stay.
- **A skinned mesh** brings its skeleton. Twin joints that do not exist yet are made with `mirrorJoint` in behavior mode and named by side. `mirrorJoint` mirrors across the plane through the chain's parent, not through 0, so the chain is moved onto the plane afterwards. The copy is bound to the twins with the source's settings and every vertex gets the source vertex's weights with the joints swapped. The source is mirrored at its bind pose, whatever pose it stands in, and put back after.
- **A joint** mirrors its chain only.

The reply has `made`, `joints` (`made`, `existed`), `skin`, `weights` (`paired`, `unpaired`) and `issues`: what the copy has wrong that the source does not. One undo step.

Custom attributes on joints are copied as they are, not mirrored: `fp_hands_rig` adds a `rest` matrix per hand after mirroring, and a mirrored `hand_l` carries `hand_r`'s.

On the Zombie FP hands, `fp_hands_rig.build()` without its left side, then `bmaya mirror fp_zombie_arm_r`, gives the same left hand as the script: joint world matrices, jointOrient, vertex positions, normals and faces equal to 0.0, all 14 twins `behavior`. It takes 0.7 s.
