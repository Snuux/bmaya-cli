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

For `.glb` and `.gltf` export and import, also run `bmaya install --gltf`. It adds the optional [maya-glb-io](docs/export.md#gltf) library; everything else works without it.

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

## What it can do

| | Commands | Read more |
|---|---|---|
| Look | `scene` `object` `shot` `views` `playblast` | [Modeling](docs/modeling.md), [Animation](docs/animation.md) |
| Change | `exec` `undo` `file` `file status` | [Scene changes](docs/scene.md) |
| Model | `audit` `diff` `remesh` `reduce` `cleanup` `mirror` | [Modeling](docs/modeling.md) |
| UVs and edge flow | `uv` `topo` | [UVs and textures](docs/uv.md), [Topology](docs/topology.md) |
| Skin and skeleton | `skin` `bones` `pose` `rig` | [Skin weights](docs/skin.md), [Skeletons and mirroring](docs/skeleton.md) |
| Animate | `anim` `clip` `motion` | [Animation](docs/animation.md) |
| Export | `export` `inspect` | [Export](docs/export.md): FBX presets for Unity, Unreal and Godot, glTF, the Game Exporter |
| Run Maya | `sessions` `use` `start` `stop` `doctor` `logs` | [Sessions](docs/sessions.md), [Troubleshooting](docs/troubleshooting.md) |

Pictures are PNG files an agent opens and reads: viewport shots and contact sheets, a model from several sides, skin weights, UV layouts, edge loops, animation curves. Checks answer with numbers and a list of `issues`. Exports are read back from disk and compared with the scene.

[All commands with their options](docs/commands.md) are on one page, with the exit codes. `bmaya COMMAND --help` explains each one with examples, and `bmaya guide` prints the manual for agents.

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
