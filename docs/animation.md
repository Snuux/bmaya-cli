# Animation

An agent writes keys with `bmaya exec` and `maya.cmds` like any other change. What it cannot do is watch the result play, so bmaya gives it the motion in numbers and in pictures it can read.

## Frames and clips

Every command that takes frames reads the same spec: `1,12,24`, a range `0:30` (both ends included) or `0:30:2`. Frame 0 is a frame. Negative frames go as `--frames=-10:20`.

`--clip NAME` takes a clip's range instead. Clips are read from the Game Exporter (`gameFbxExporter` `animClips`) and from time slider bookmarks; `bmaya clip` lists them. `bmaya export --clip NAME` bakes one clip.

## Clips

`bmaya clip` lists the clips, one entry per name: a Game Exporter row and a bookmark of the same name are one clip. Each has its `range` (both ends are frames of the clip, as the Game Exporter keeps them: a clip of length 90 from frame 0 ends on frame 90), `length`, `exporter` and `index` of its row, `bookmark`, `export: false` when the row is switched off, and how many `keys` lie inside. `exporters` names the animation Game Exporter nodes. `bmaya clip NAME` shows one.

`issues` says what is off:

- a row and a bookmark of one name with different ranges;
- clips that overlap;
- rows with export off, and bookmarks with no row when the scene has rows: neither is exported by `bmaya export --game-exporter`;
- clips with no keys;
- keys far from every clip, left by a clip that was removed;
- bookmarks that are `unknown` nodes because the scene was opened without the `timeSliderBookmark` plug-in.

`outside` lists the spans of keys in no clip, with the nearest clip and how far it is. Spans right next to a clip are the extra keys a loop keeps one period past its ends so its curves run smoothly through the seam.

Edits, each one undo step:

| Command | Does |
|---|---|
| `bmaya clip NAME --add 0:90` | A row in the scene's animation Game Exporter node (`--node` when there are several; with none, only a bookmark) and a bookmark, on frames 0 to 90. |
| `bmaya clip NAME --add 90` | The same, 90 frames long, starting `--gap` frames (20) after the last clip, and past the keys its loop keeps after its end, on a multiple of 10. `--add=-30:0` for frames below 0. On a clip that is only a bookmark, `--add` gives it a row. |
| `bmaya clip NAME --set 0:120` | Moves the range of every row and bookmark of that name. `--set 120` keeps the start. |
| `bmaya clip NAME --remove` | Takes out the rows and bookmarks. Rows after it move down one place, as the Game Exporter's own delete does: its window and export read rows 0 to size-1 only. `--keys` also clears the keys inside the range and those its loop keeps past its ends, but not frames another clip shares. |

Rows in a model exporter or a referenced exporter, a referenced or locked bookmark, and an exporter whose rows skip an index or have no name are reported and not edited. The animation range grows to hold a new or moved clip. Bookmarks are made with `createNode` after loading their plug-in: the bookmark API needs a window (`$gPlayBackSlider`), and a bookmark node made while the plug-in is not loaded comes back as an unknown node.

Keys stay where they are when a clip moves: `--set` changes the range, not the animation.

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
