<p align="center">
  <img src="assets/banner.svg" alt="bmaya-cli: drive Autodesk Maya from the command line" width="100%">
</p>

<p align="center">
  <img alt="Maya 2027" src="https://img.shields.io/badge/Maya-2027-37a5cc?style=flat-square">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-7fd1a8?style=flat-square">
</p>

Ask your agent for a low-poly barrel with a small bounce, exported for Unreal. It runs `bmaya` commands in its shell: builds the mesh in Maya, keys the animation, looks at a screenshot, audits the mesh and writes the FBX. Maya can be the window you have open, where you can take the mouse back at any moment, or a Maya without a window that `bmaya` starts itself.

bmaya-cli is a command-line tool for Autodesk Maya, built around game-dev modeling and animation. Anything that has a shell can use it: Claude Code, Codex, Cursor, a script, CI. It has no dependencies and no server process.

- One command, `bmaya`, with subcommands: `scene`, `object`, `exec`, `shot`, `audit`, `anim`, `export`, `file`, `undo` and a few for managing Mayas.
- Each command prints one line of compact JSON. Every listing has a limit and reports `shown` and `total`.
- Several Mayas and several agents can work at the same time. Agents that share one Maya undo only their own steps and can't swap the scene under each other.
- With no Maya running, the first command starts one without a window. Screenshots are rendered offscreen there.
- Mesh audits, FBX export with Unity, Unreal and Godot presets, and animation contact sheets.
- Every command is written to disk before it runs, so after a crash you can see what Maya was doing.
- Errors say what to try next. Tracebacks go to the journal and the Script Editor, not to the caller.

## How it works

<p align="center">
  <img src="assets/how-it-works.svg" alt="An agent or script runs bmaya in a shell; each bmaya process talks to a bridge inside Maya over a local socket" width="100%">
</p>

Each `bmaya` command is one short process: it finds the Maya to talk to, sends one request over a local socket, prints the answer and exits. The bridge inside Maya is plain standard-library Python. It starts with a Maya you open (after `bmaya install`), or in a Maya without a window that `bmaya` starts. It runs commands on Maya's main thread one at a time. Each `bmaya exec` call is one undo step; the commands that only read the scene, take a screenshot or export leave the undo queue alone.

## Why a CLI and not an MCP server

This project used to be an MCP server. It was rewritten as a command-line tool for these reasons:

- There is no background server to start, reconnect or kill. Each command is a fresh process, so a hung server process cannot block anything.
- Tool definitions do not sit in the agent's context. The agent reads `bmaya --help` or `bmaya guide` when it needs them.
- It works in every agent that has a shell, and in plain scripts and CI, with no client configuration.
- The output is JSON on stdout, so you can pipe it to `jq` or read it from any language.

What it costs:

- Each command starts a process. On one Windows machine `bmaya sessions` takes about 0.4 s of wall time and `bmaya --version` about 0.25 s.
- A screenshot arrives as a file path that the agent has to open, not as an inline image.
- The agent does not know the tool exists until you tell it. You add a short paragraph to your `CLAUDE.md` or `AGENTS.md`, see [Tell your agent](#tell-your-agent).

## Install

You need:

- Autodesk Maya with Python 3.9 or newer. Tested on Maya 2027.
- [uv](https://docs.astral.sh/uv/). `bmaya` needs Python 3.10+, and uv fetches it.

Only Windows is tested. The code is written to be portable, but nobody has run it on macOS or Linux yet.

The package is not on PyPI yet, so install from a clone:

```bash
git clone https://github.com/Snuux/bmaya-cli.git
```

```bash
uv tool install ./bmaya-cli
```

This puts `bmaya` (and the same program as `bmaya-cli`) on your PATH. To skip installing, use `uv run --directory <path-to-clone> bmaya` wherever this README says `bmaya`.

That is enough for a Maya without a window. To also use a Maya that you open yourself, copy the bridge into it:

```bash
bmaya install
```

It goes to `<Documents>/maya/modules/bmaya/` together with `bmaya.mod`, and removes the old `bmaya-mcp` module from before the rename. If your Maya preferences live elsewhere, set `MAYA_APP_DIR` or add `--maya-app-dir PATH` (`uninstall` and `doctor` accept it too). Restart Maya, then check:

```bash
bmaya doctor
```

It reports the installed module and its version, the Maya that would be started, the running sessions with their ping time, stale entries and the last crash.

To update, pull the clone:

```bash
git -C bmaya-cli pull
```

```bash
uv tool install --reinstall ./bmaya-cli
```

```bash
bmaya install
```

Then restart any Maya that is open. A Maya that still runs the older bridge lacks the newer behaviour.

## Quick start

No Maya needs to be open. The first command starts one without a window, which took about 18 seconds on one Windows machine. The values below are an example:

```bash
bmaya scene
```

```json
{"file":"untitled","modified":false,"unit":"cm","up":"y","fps":24,"time_unit":"film","playback":[1,120],"anim_range":[1,200],"frame":1,"selection":{"items":[],"shown":0,"total":0},"nodes":[],"shown":0,"total":0}
```

Change the scene with `maya.cmds`:

```bash
bmaya exec "result = cmds.polyCube(name='crate')"
```

```json
{"stdout":"","result":["crate","polyCube1"],"truncated":false}
```

Look at it. The command writes a PNG and prints where it is:

```bash
bmaya shot -o shot.png
```

```json
{"path":"C:/work/shot.png","width":800,"height":450,"frames":[1],"offscreen":true}
```

Take the last step back:

```bash
bmaya undo
```

```json
{"undone":1}
```

A Maya without a window runs until you stop it, and `stop` refuses to discard unsaved changes. Save, then stop:

```bash
bmaya file save crate.ma
```

```json
{"scene":"C:/work/crate.ma","modified":false}
```

```bash
bmaya stop
```

```json
{"stopped":"18244","scene":"C:/work/crate.ma","exited":true}
```

## Tell your agent

Paste this into your project's `CLAUDE.md` or `AGENTS.md`:

```markdown
## Maya

Autodesk Maya is driven with the `bmaya` command in the shell.
Run `bmaya guide` once before the first use; `bmaya --help` lists the commands.
Every command prints one line of JSON on stdout. Errors go to stderr as `error:` and `hint:` lines; follow the hint.
Exit code 3 means Maya is still starting or still busy: wait and check `bmaya sessions`, do not send the command again.
Pass multi-line code through stdin (`bmaya exec <<'PY' ... PY`) or `bmaya exec -f FILE`, not as an argument.
Look at your work with `bmaya shot -o shot.png` and open that file.
```

`bmaya guide` prints the manual for agents: the working loop, a Blender-to-Maya map and recipes for modeling, UVs, materials, animation and skinning.

## Try the demo

<p align="center">
  <img src="assets/demo-loop.svg" alt="Demo: build with bmaya exec, animate, check with bmaya audit and bmaya anim, export with bmaya export, look with bmaya shot" width="100%">
</p>

The demo runs the whole loop with no agent. It needs a running Maya and does not start one. If none is running, start one first:

```bash
bmaya start
```

```bash
bmaya demo
```

It writes `prop.fbx` and a four-frame `screenshot.png` to `demo/` (change with `--out DIR`). In an untitled, unmodified scene it starts a new scene. In a scene with content it builds inside its own `bmaya_demo` group. With more than one Maya running, pass `--session ID`.

## Commands

| Command | What it does |
|---|---|
| `bmaya scene [-q QUERY] [-t TYPE] [-n LIMIT]` | File, unit, up axis, fps, frame range, selection, nodes. Filter by name or node type. |
| `bmaya object NAME` | One node: transform, bounding box, mesh counts, UV sets, materials, history, keys. |
| `bmaya exec [CODE] [-f FILE]` | Runs Python in Maya with `cmds`, `mel` and `om` loaded. Assign to `result` to return data. One undo step per call. |
| `bmaya shot [-o FILE] [--camera C] [--frames 1,12,24] [--size N]` | Writes a PNG of the viewport. `--frames` makes a contact sheet of up to 12 frames. |
| `bmaya audit [NAME ...]` | Game-readiness check: n-gons, non-manifold, lamina and zero-area faces, UVs, materials, history, transforms, names. |
| `bmaya anim [NAME]` | Curves, key ranges, tangents, fps, skeletons, skin clusters, blend shapes, anim layers. |
| `bmaya export FILE.fbx [--preset unity\|unreal\|godot] [--selection] [--animation] [--start N --end N]` | FBX export with an engine preset. |
| `bmaya file new\|open\|save\|import [PATH] [--force]` | Scene files. `save` with a path saves as `.ma` or `.mb`; `import` takes `.ma`, `.mb`, `.fbx`, `.obj`. |
| `bmaya undo [STEPS] [--force]` | Undoes your own last steps, never another client's. |
| `bmaya sessions` | Running Mayas: id, scene, Maya version, busy, clients, current, and the client name you act under. |
| `bmaya use ID` | Picks the Maya your next commands talk to. |
| `bmaya start [--gui] [--scene FILE]` | Starts one more Maya, without a window unless `--gui`, and waits for it. |
| `bmaya stop [ID] [--force]` | Stops a Maya that runs without a window. |
| `bmaya guide` | Prints the manual for agents. |
| `bmaya install`, `bmaya uninstall` | Adds or removes the Maya module. |
| `bmaya doctor` | Checks the install and the running Mayas. |
| `bmaya logs [--session PID] [--tail N] [--crashes]` | Shows what was sent to a Maya and what it crashed on. |
| `bmaya demo [--out DIR] [--session ID]` | Builds, checks and exports a prop in a running Maya. |

The commands that talk to Maya (`scene` through `stop`) accept two more flags, before or after the command name: `--session ID` picks a Maya for that one command, and `--client LABEL` names the caller. `bmaya COMMAND --help` explains one command with examples.

### Output and exit codes

- stdout carries one line of compact JSON. `guide`, `doctor`, `logs`, `install`, `uninstall` and `demo` print plain text instead.
- An error goes to stderr as an `error: ...` line and, when there is advice, a `hint: ...` line. If the Python code in `bmaya exec` raised, the message starts with the exception name, and what the code printed before it failed follows.
- Relative paths are resolved against the directory you run `bmaya` in.

| Exit code | Meaning |
|---|---|
| 0 | Done. |
| 1 | The command failed. stderr says why and what to do next. |
| 2 | Bad arguments. |
| 3 | Not ready: Maya is still starting, or still busy with a command that was already sent. Do not send it again; wait and check `bmaya sessions`. |

### Screenshots

`bmaya shot` prints `{"path","width","height","frames"}`. The PNG goes to the `-o` path, or to `~/.bmaya/shots/` without one; that folder keeps the 30 newest files. `--size` is the longest side in pixels, 800 by default.

## Sending code

`bmaya exec` takes the code as an argument, from a file or from stdin. A one-liner fits in an argument:

```bash
bmaya exec "result = cmds.ls(selection=True)"
```

Anything with quotes or several lines should go through a file or stdin, so the shell cannot mangle it:

```bash
bmaya exec -f build_crate.py
```

In bash or zsh, a quoted heredoc:

```bash
bmaya exec <<'PY'
crate = cmds.polyCube(name="crate", width=100, height=100, depth=100)[0]
cmds.xform(crate, translation=(0, 50, 0))
result = cmds.ls(crate, long=True)
PY
```

In PowerShell, pipe a file:

```powershell
Get-Content build_crate.py -Raw | bmaya exec
```

What the code prints comes back as `stdout`. Output and `result` are cut at about 8000 characters each, and `truncated` says whether that happened. If the code raises, nothing is rolled back; inspect the scene before retrying.

## Several Mayas

<p align="center">
  <img src="assets/sessions.svg" alt="Three agents running bmaya commands: one with its own Maya, two sharing a Maya, and a Maya without a window started with bmaya start" width="100%">
</p>

Each Maya registers in `~/.bmaya/sessions/<pid>.json`; the session id is the Maya process id. With one Maya running, commands use it. With several, a command fails and lists them until you pick one:

```bash
bmaya sessions
```

```json
{"sessions":[{"id":"4120","scene":"C:/work/hero_rig.ma","maya_version":"2027","busy":false,"clients":1,"current":false},{"id":"7788","scene":"C:/work/props_kit.ma","maya_version":"2027","busy":false,"clients":2,"current":false}],"current":null,"client":"claude-48d81738"}
```

```bash
bmaya use 7788
```

Each command is a new process, so the choice is kept on disk, in `~/.bmaya/clients/<label>.json`, one file per client. Another agent has another label and can pick another Maya. `--session ID` overrides the choice for a single command.

If the Maya you were using is gone, the next command fails once and says so: either the session "was stopped or closed", or it "exited or crashed", with the path of its journal. It is not silently replaced. The command after that uses another running Maya or starts a new empty one.

Every command that talks to Maya pings each registered session and deletes the entries whose Maya is dead. A Maya that doesn't answer within 3 seconds is kept and shown as `busy`.

### Who is calling

The bridge needs to know which commands come from the same agent. The client label is taken from, in this order:

1. `--client LABEL` or the environment variable `BMAYA_CLIENT`.
2. The agent's session id in the environment. Claude Code sets `CLAUDE_CODE_SESSION_ID`, which gives a label like `claude-48d81738`.
3. The nearest ancestor process that is not a shell, as `name-pid`: the agent or the terminal.

So all commands of one agent share a label, and two agents get different ones. `bmaya sessions` shows yours as `client`.

### Sharing one Maya

Several agents can pick the same Maya. Commands still run one at a time, and the bridge keeps the agents out of each other's way:

- `bmaya sessions` shows `clients`: how many callers ran a command in that Maya during the last 10 minutes. Pings and `bmaya sessions` itself don't count.
- `bmaya undo` reverts only the caller's own steps. Maya has one undo queue, so an agent can undo only while its own step is on top. If the latest change came from another client or from you in the Maya window, undo stops there and names it in `blocked_by` (a client label or `maya-ui`); with nothing undone it is an error. Selection changes don't count: undo steps over them. With `--force` the agent also undoes your UI changes that sit on top of its step. Another client's steps are never undone.
- `bmaya file new`, `bmaya file open` and `bmaya stop` are refused without `--force` when another client ran a command in that Maya during the last 10 minutes, or when the scene has unsaved changes. `save` and `import` are not restricted.

## Starting and stopping Maya

When a command needs Maya and none is running, it starts one and uses it: `mayapy` with the bridge, no window, no module install needed. The command waits up to 40 seconds. If Maya needs longer, it exits with code 3 and asks you to run it again in about 15 seconds. Two commands that start at the same moment share one Maya. `sessions`, `use`, `stop` and `demo` never start Maya.

To start one explicitly:

```bash
bmaya start
```

It waits up to 120 seconds (about 18 on one Windows machine), prints the new session and makes it your current one. `--scene FILE` opens a scene. `--gui` starts Maya with its window, which needs `bmaya install`.

There are no timers. A Maya without a window runs until you stop it:

```bash
bmaya stop
```

`stop` takes a session id if you don't mean your current Maya. Nothing asks about unsaved changes: `stop` refuses instead, so save with `bmaya file save` first or pass `--force`. It never closes a Maya that has a window; that one is yours to close.

| Variable | Meaning |
|---|---|
| `BMAYA_AUTOSTART` | What the first command starts when no Maya is running: `headless` (default, no window), `gui` (the normal Maya window, needs `bmaya install`) or `off`. |
| `BMAYA_MAYA` | Maya install folder or its `mayapy`. Default: the newest Maya in the standard install location. |
| `BMAYA_HOME` | State folder. Default: `~/.bmaya`. |
| `BMAYA_CLIENT` | Client label, see [Who is calling](#who-is-calling). |

### Screenshots without a window

A Maya without a window has no viewport, so `bmaya shot` renders through Maya's hardware renderer: 16:9, from `persp` or the camera you name with `--camera`. The result has `offscreen: true`. Frame the subject first with `cmds.viewFit("persp", all=True)`. The default `openPBRSurface` material is drawn as a plain lambert of the same base color. Commands that need Maya's UI (`modelPanel`, `getPanel`, `playblast` from your own code) do not work there.

## FBX presets

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

## Crash journal

State lives in `~/.bmaya/` (set `BMAYA_HOME` to move it):

```
~/.bmaya/
  sessions/<pid>.json                  running Mayas: port and token
  clients/<label>.json                 each client's chosen Maya and last command
  shots/                               screenshots taken without -o
  logs/<YYYYMMDD-HHMMSS>-<pid>.log     command journal, one JSON object per line
  logs/<same-name>.crash.log           faulthandler output
  logs/launch-<time>.log               output of a Maya that bmaya started
```

One journal per Maya session. Each command writes a `start` line, flushed to disk before it runs, and an `end` line with duration and result. The 30 newest sessions are kept; parameters over 20 KB are truncated. The journal uses the bridge's internal command names: `execute_maya_code` is `bmaya exec`, `get_scene_info` is `bmaya scene`, and so on.

In a crashed session the last `start` has no `end` and the file has no `bridge_stop`. That `start` is what Maya was doing. The `.crash.log` holds the native stack if Python could capture it.

List sessions that look crashed:

```bash
bmaya logs --crashes
```

Show the last 50 records of one session:

```bash
bmaya logs --session 1234 --tail 50
```

## Security

The bridge listens on `127.0.0.1` only, on a random port, and each request must carry a per-session token stored in `~/.bmaya/sessions/<pid>.json`.

This is not a sandbox. Any local process that can read your home folder can read the token and run Python in Maya. `bmaya exec` runs arbitrary code by design: the caller can read and write files and start processes with your user rights. Let only agents you trust run `bmaya` against a Maya with valuable work open, and don't move the state folder to a shared location.

## Troubleshooting

- `bmaya` is not found by the agent. Its shell does not have the uv tool folder on PATH. `uv tool dir --bin` prints that folder; add it to PATH or give the agent the full path.
- "No Maya with the bmaya bridge is running." The command is one that never starts Maya (`sessions`, `use`, `stop`, `demo`), or `BMAYA_AUTOSTART` is `off`. Run `bmaya start`. For a Maya you opened yourself, run `bmaya doctor`; if the module is missing, run `bmaya install` and restart Maya. Bridge startup errors show up in the Script Editor.
- "No Maya installation was found to start." Maya is not in the standard install folder. Set `BMAYA_MAYA` to your Maya folder.
- "Maya is starting ... and is not ready yet", again and again. Look at the newest `~/.bmaya/logs/launch-*.log`: it holds the output of the Maya that was started. After three minutes the message changes to say that Maya is still running but its bridge has not come up, for example because it waits on a license dialog. Close that Maya and run the command again.
- "Nothing was undone." The latest change in that Maya is not the caller's. The message names it. Maya's own startup scripts can leave such an entry while Maya is still loading; repeat with `--force`. See [Sharing one Maya](#sharing-one-maya).
- Undo is blocked by a label that looks like your own agent. The label changed between two calls. `bmaya sessions` shows the current one as `client`; set `BMAYA_CLIENT` to keep it fixed.
- "Several Maya sessions are running." Run `bmaya sessions`, then `bmaya use ID`, or pass `--session ID`.
- Exit code 3 with "did not reply in time" or "still running". Maya runs one command at a time on its main thread, and the bridge waits 50 seconds for it. Usually a modal dialog is open (save prompt, plug-in warning, file dialog) or the operation is long. Close the dialog or wait; `bmaya sessions` shows `busy: true` meanwhile. The command may still finish, but its result is lost, so check the scene and do not send it again blindly. Keep `bmaya exec` calls short.
- "The viewport has not been drawn yet." Maya is still starting or its window is minimized. Wait a few seconds or restore the window and retry.
- "Maya session ... exited or crashed." See [Crash journal](#crash-journal). Unsaved work in that Maya is gone.
- `bmaya shot` fails. In a Maya with a window it needs a visible 3D viewport. Without a window it needs a GPU that Maya's hardware renderer can use. `bmaya` must run on the same machine as Maya, because it moves the PNG out of Maya's temp folder.
- Other tools that drive the same Maya. The bridge uses a free port and its own folder under `~/.bmaya`, so it shouldn't collide with other Maya plug-ins. Two plug-ins that run commands in one Maya still share one undo queue; `bmaya undo` treats the other tool's steps like edits made in the Maya window.

## Uninstall

```bash
bmaya uninstall
```

```bash
uv tool uninstall bmaya-cli
```

Restart Maya. Delete `~/.bmaya/` if you don't need the logs, and remove the Maya paragraph from your `CLAUDE.md` or `AGENTS.md`.

## Development

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check
```

```bash
uv run ruff format
```

The tests don't need Maya and never start one. To check the Maya-side handlers, run them in Maya's Python:

```bash
"C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe" tests/maya/check_handlers.py
```

The script calls the handlers directly, without the socket. `maya.utils.executeDeferred` never fires in `mayapy`, so the bridge in a Maya without a window (`bmaya_bridge.headless`) runs its own loop on the main thread.

## License

MIT, see [LICENSE](LICENSE).
