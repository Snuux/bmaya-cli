<p align="center">
  <img src="assets/banner.svg" alt="bmaya-cli: drive Autodesk Maya from the command line" width="100%">
</p>

<p align="center">
  <img alt="Maya 2027" src="https://img.shields.io/badge/Maya-2027-37a5cc?style=flat-square">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-7fd1a8?style=flat-square">
</p>

`bmaya` is a command-line tool that lets an AI agent or a script work in Autodesk Maya: inspect the scene, run `maya.cmds`, look at a screenshot, audit meshes, export FBX. Anything with a shell can use it: Claude Code, Codex, Cursor, CI.

- Each command prints one line of JSON and exits. No server to run, no dependencies.
- Nothing sits in the agent's context: it reads `bmaya --help` or `bmaya guide` when it needs them.
- Works in the Maya you have open, or starts one without a window when none is running.
- Several agents can share one Maya: each undoes only its own steps.
- Errors say what to try next, and every command is journaled before it runs, so a crash leaves a trace.

<p align="center">
  <img src="assets/how-it-works.svg" alt="An agent or script runs bmaya in a shell; each bmaya process talks to a bridge inside Maya over a local socket" width="100%">
</p>

## Install

You need Autodesk Maya (tested on 2027) and [uv](https://docs.astral.sh/uv/). Only Windows is tested.

```bash
git clone https://github.com/Snuux/bmaya-cli.git
uv tool install ./bmaya-cli
```

That is enough for a Maya without a window. To also use a Maya you open yourself, copy the bridge into it and restart Maya:

```bash
bmaya install
```

`bmaya doctor` checks the result. To update: `git pull`, `uv tool install --reinstall ./bmaya-cli`, `bmaya install`, restart Maya.

## Quick start

No Maya needs to be open. The first command starts one without a window, which takes 10 to 20 seconds.

```bash
bmaya scene
bmaya exec "result = cmds.polyCube(name='crate')"
bmaya shot -o shot.png
bmaya undo
```

Each prints one line (the first is shortened here):

```json
{"file":"untitled","unit":"cm","up":"y","fps":24,"nodes":[],"shown":0,"total":0}
{"stdout":"","result":["crate","polyCube1"],"truncated":false}
{"path":"C:/work/shot.png","width":800,"height":450,"frames":[1],"offscreen":true}
{"undone":1}
```

A Maya without a window runs until you stop it:

```bash
bmaya file save crate.ma
bmaya stop
```

`bmaya demo` runs the whole loop (build, animate, audit, export, screenshot) in a running Maya and writes the results to `demo/`.

## Tell your agent

An agent does not know the tool exists until told. Paste this into your project's `CLAUDE.md` or `AGENTS.md`:

```markdown
## Maya

Autodesk Maya is driven with the `bmaya` command in the shell.
Run `bmaya guide` once before the first use; `bmaya --help` lists the commands.
Every command prints one line of JSON on stdout. Errors go to stderr as `error:` and `hint:` lines; follow the hint.
Exit code 3 means Maya is still starting or still busy: wait and check `bmaya sessions`, do not send the command again.
Pass multi-line code through stdin (`bmaya exec <<'PY' ... PY`) or `bmaya exec -f FILE`, not as an argument.
Look at your work with `bmaya shot -o shot.png`, or `bmaya views NAME -o views.png` for a model, and open that file.
If a `note:` line says a Maya was started for you, save with `bmaya file save PATH` and run `bmaya stop` when the work is done.
```

## Commands

| Command | What it does |
|---|---|
| `bmaya scene [-q QUERY] [-t TYPE] [-n LIMIT]` | File, unit, up axis, fps, frame range, selection, nodes. |
| `bmaya object NAME` | One node: transform, bounding box, mesh counts, UV sets, materials, history, keys. |
| `bmaya exec [CODE] [-f FILE]` | Runs Python in Maya with `cmds`, `mel` and `om` loaded. Assign to `result` to return data. One undo step per call. Code also comes from stdin. |
| `bmaya shot [-o FILE] [--camera C] [--frames 1,12,24\|0:30:2\|--clip NAME] [--size N] [--aspect W:H] [--light unlit\|soft\|scene] [--trail A,B] [--fit A,B]` | Writes a PNG of the viewport and prints its path. `--frames` makes a contact sheet (a range is thinned to 12 tiles), `--aspect` sets the frame shape, `--light` the lighting for this shot, `--trail` draws node paths with a dot per frame, `--fit` frames nodes on every frame without moving the camera. |
| `bmaya playblast OUT.gif\|OUT.mp4\|DIR [--frames\|--clip] [--camera C] [--fit A,B]` | Renders every frame and joins them into a GIF or MP4 with ffmpeg (PNG frames without it). For people to watch. |
| `bmaya views [NAME ...] [-o FILE] [--views front,right,top,persp\|all] [--style parts\|silhouette\|xray\|weights\|influence\|shaded] [--joint J] [--section x\|y=12.5] [--no-wire] [--size N]` | Draws a model from several sides in one PNG: a color per mesh, edges on top, width and height on the straight-on views. `silhouette` for the shape, `xray` and `--section` for what is inside, `weights --joint J` and `influence` for skin weights, `shaded` for Maya's own render. |
| `bmaya audit [NAME ...] [--symmetry x\|y\|z]` | Game-readiness check: counts, faces by kind, size, n-gons, non-manifold geometry, flipped or inward normals, UVs and their overlaps, materials, history, transforms, stray and instanced shapes, the opposite flag, skin weights, mirror symmetry. |
| `bmaya diff NAME [--mark] [--against OTHER]` | What an edit changed in a mesh: counts, size, area, volume, moved vertices and where they are. |
| `bmaya topo NAME [--ids] [--gap G] [-o FILE [--tubes A,B]]` | Edge flow: shells, valence, closed edge loops around tubes, the tubes, and joint places in the middle of each run of close loops. `-o` draws them over the mesh. |
| `bmaya remesh NAME [--to tris --edge L \| --to quads --faces N]` | Rebuilds the topology: even triangles, or quads with Maya's Retopologize. |
| `bmaya reduce NAME --tris N \| --percent P [--keep-uvs] [--keep-borders] [--keep-hard-edges] [--keep-quads]` | Lowers the triangle count and keeps the shape. |
| `bmaya cleanup NAME [--merge DIST] [--ngons]` | Merges vertices on the same spot, removes bad faces and edges, conforms the normals and turns them outward. |
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
| `bmaya export --game-exporter [NODE] [--clip NAME] [--copy-textures]` | Exports the way the scene's Game Exporter nodes are set up: their paths, sets and presets, a file per clip with one take each, every file checked. |
| `bmaya inspect FILE [--axes] [--limit N]` | Reads an FBX, GLB or glTF file without Maya: unit, up axis, fps, meshes with counts, UV sets, materials and skin, the skeleton with bind positions, takes with frame ranges, textures and whether they are found. |
| `bmaya file new\|open\|save\|import [PATH] [--force]` | Scene files. |
| `bmaya undo [STEPS] [--force]` | Undoes your own last steps, never another client's. |
| `bmaya sessions`, `bmaya use ID` | Lists the running Mayas, picks the one to talk to. |
| `bmaya start [--gui] [--scene FILE]`, `bmaya stop [ID]` | Starts a Maya, stops one that runs without a window. |
| `bmaya guide` | Prints the manual for agents: working loop, Blender-to-Maya map, recipes. |
| `bmaya install`, `uninstall`, `doctor`, `logs`, `demo` | Maya module, health check, command journal, demo. |

`bmaya COMMAND --help` explains each one with examples.

Results go to stdout as one line of JSON, errors to stderr as `error: ...` and `hint: ...`.

| Exit code | Meaning |
|---|---|
| 0 | Done. |
| 1 | Failed. stderr says why and what to do next. |
| 2 | Bad arguments. |
| 3 | Not ready: Maya is starting, or still busy with a command already sent. Wait; do not send it again. |

## More

- [Sessions, sharing one Maya, start and stop](docs/sessions.md): several Mayas, who owns which undo step, environment variables, screenshots without a window.
- [FBX export](docs/export.md): what each preset writes.
- [Modeling](docs/modeling.md): view sheets and their styles, sections, audit numbers, diff, remesh, reduce, cleanup.
- [Skin weights](docs/skin.md): the skin report, weights files, copy, mirror, prune, weight pictures.
- [Topology](docs/topology.md): edge loops around tubes, runs of loops, joint places, the loops picture.
- [Skeletons and mirroring](docs/skeleton.md): the bones report, comparing with a game file, new axes, bind pose, rebinding, mirroring a skinned mesh.
- [Animation](docs/animation.md): frame specs and clips, motion checks, rig inventory, trails, curve plots, previews.
- [Troubleshooting](docs/troubleshooting.md): common messages, the crash journal, where state is kept.

## Security

The bridge listens on `127.0.0.1` only, on a random port, and every request carries a per-session token kept in `~/.bmaya/sessions/`. This is not a sandbox: `bmaya exec` runs arbitrary Python in Maya with your user rights, and any local process that can read your home folder can read the token.

## Uninstall

```bash
bmaya uninstall
uv tool uninstall bmaya-cli
```

Delete `~/.bmaya/` if you don't need the logs.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
"C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe" tests/maya/check_handlers.py
```

The tests don't need Maya. The last command checks the Maya-side handlers in Maya's own Python.

## License

MIT, see [LICENSE](LICENSE).
