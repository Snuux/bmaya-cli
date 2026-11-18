# Animation

An agent writes keys with `bmaya exec` and `maya.cmds` like any other change. What it cannot do is watch the result play, so bmaya gives it the motion in numbers and in pictures it can read.

## Frames and clips

Every command that takes frames reads the same spec: `1,12,24`, a range `0:30` (both ends included) or `0:30:2`. Frame 0 is a frame. Negative frames go as `--frames=-10:20`.

`--clip NAME` takes a clip's range instead. Clips are read from the Game Exporter (`gameFbxExporter` `animClips`) and from time slider bookmarks; `bmaya anim` lists them. `bmaya export --clip NAME` bakes one clip.

## Checking motion

`bmaya motion [NAMES]` (a group means everything under it, a skeleton's top joint the whole skeleton) samples the world position and rotation of nodes on every frame, the way playback shows them (IK, constraints, expressions), and reports:

| Issue | Means |
|---|---|
| `pop` | A node moves or turns in one frame far more than in the frames around it. |
| `flip` | A node turns 90° or more in one frame. |
| `loop_pose`, `loop_speed` | With `--loop`: the last frame does not repeat the first pose, or the motion does not continue smoothly through the seam. Measured relative to the common parent. |
| `foot_slide` | With `--contacts`: a node that is planted on the ground moves along it. For a cycle in place the planted feet share a treadmill speed, which is subtracted and reported as `treadmill_speed`. |
| `below_ground` | With `--contacts`: a node under `--ground` (default 0). |
| `euler_jump` | A rotate curve changes by more than 180° between two keys. The issue carries the `cmds.filterCurve` call that fixes it. |
| `clip_edge` | A channel has keys before and after a clip's first or last frame but none on it, so the pose there leans toward the next clip. |
| `subframe_keys`, `stray_keys` | Keys between whole frames, or outside every clip. |

Thresholds scale with the size of what is checked (`size` in the result, scene units), so they hold for a coin and for a giant; `limits` shows them. A descendant that only inherits its parent's problem is counted under the parent as `children`. `--values` adds world positions per frame, `--attrs` lists attributes per frame; both stay under 3000 numbers.

## Rigs

`bmaya rig [ROOT]` lists what an animator keys: transforms with curve shapes and anything already keyed, or the joints when there are no controls. For each control it shows keyable attributes, `driven` ones (a key there does nothing), values that differ from the default, limits, rotate order and keys. Then IK handles with their pole and driver, constraints, switch attributes such as `ik` that drive `ikBlend`, visibility or constraint weights, and character sets.

## Pictures

- `bmaya shot --clip walk --trail hand_r,foot_l` draws the world path of up to 4 nodes: a dot per frame (spacing), a numbered dot per key, a ring where the node is on each tile.
- `bmaya shot --fit hero` frames the nodes on every frame of the shot through a temporary copy of the camera. The camera itself is not moved.
- `bmaya anim NAME --plot -o curves.png` draws a node's curves like the Graph Editor, with clips shaded and red ticks on Euler jumps.

## Preview for people

`bmaya playblast walk.gif --clip walk --camera side --fit hero` renders every frame and joins them into a GIF or an MP4 with ffmpeg when it is on PATH; without it, or with a folder as the output, the PNG frames are kept. The frames are sent to Maya in chunks that each finish well within the 50-second reply limit, so a long range does not time out. A frame takes about 0.2 to 0.4 seconds.

## Limits

| What | Most |
|---|---|
| Frames in a spec | 5000 |
| `motion` | 1000 frames, 200 nodes, 30 seconds of sampling |
| `shot` | 12 tiles, 4 trail nodes |
| `playblast` | 1500 frames |
| `anim --plot` | 12 curves over 2000 frames |
