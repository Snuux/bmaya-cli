# bmaya-mcp

A lightweight MCP server for Autodesk Maya, aimed at game-dev modeling and animation work.

An AI agent (Claude Code, Claude Desktop, Codex, any MCP client) talks to the server over stdio. The server talks over a local socket to a small bridge running inside your Maya. The bridge is plain Python from the standard library and starts with Maya.

## Why

- **Small tool surface.** 11 tools, named like the Blender MCP tools (`get_scene_info`, `get_object_info`, `execute_maya_code`, `get_viewport_screenshot`), so agents that know one know the other. Results are compact JSON, every listing is capped and reports `shown` and `total`.
- **Several Mayas, several agents.** Every GUI Maya registers itself as a session. Each agent process keeps its own current session, so two agents can work in two different Maya windows without stepping on each other.
- **Game-dev jobs have their own tools.** `audit_mesh` checks game readiness, `export_fbx` has Unity, Unreal and Godot presets, `get_viewport_screenshot` can render a multi-frame contact sheet for animation review.
- **Crash journal.** Every command is written to disk and flushed before it runs. If Maya dies, you can see what it was doing.
- **Errors say what to do next.** No tracebacks in tool results; the full traceback goes to the Script Editor and the journal.

## Requirements

- Autodesk Maya with Python 3.9 or newer. Developed and tested on Maya 2027.
- [uv](https://docs.astral.sh/uv/) to install and run the server (the server itself needs Python 3.10+, uv fetches it).
- Windows is the only platform tested. macOS and Linux are untested: the code is written to be portable, but nobody has run it there.
- The bridge runs only in Maya with a UI. The screenshot tool needs a visible 3D viewport.

## Install

The package is not on PyPI yet, so install from a local clone.

1. Get the tool.

   ```bash
   git clone <repo-url> bmaya-mcp
   uv tool install ./bmaya-mcp
   ```

   `bmaya-mcp` is now on your PATH. To skip installing, run it as `uv run --directory <path-to-clone> bmaya-mcp` instead.

2. Install the bridge into Maya.

   ```bash
   bmaya-mcp install
   ```

   This copies the bridge to `<Documents>/maya/modules/bmaya-mcp/` and writes `bmaya-mcp.mod`. If your Maya preferences live elsewhere, pass `--maya-app-dir PATH`.

3. Restart Maya. The bridge now starts with every GUI Maya. Check with:

   ```bash
   bmaya-mcp doctor
   ```

   It reports the installed module, live sessions, stale entries and the last crash.

After you update the clone (`git pull`, then `uv tool install --reinstall ./bmaya-mcp`), run `bmaya-mcp install` again to refresh the copy inside Maya.

## Client setup

The server command is `bmaya-mcp` (same as `bmaya-mcp serve`). Use the name `maya` for the server.

**Claude Code**

```bash
claude mcp add maya -- bmaya-mcp
claude mcp add --scope user maya -- bmaya-mcp
```

The first is project-local, the second is available in every project. This repository also ships a `.mcp.json` that runs the server with `uv run bmaya-mcp`, so Claude Code started inside the clone needs no setup.

**Claude Desktop**: edit `claude_desktop_config.json` (`%APPDATA%\Claude\` on Windows) and restart the app.

```json
{
  "mcpServers": {
    "maya": {
      "command": "bmaya-mcp"
    }
  }
}
```

**Codex**: either run

```bash
codex mcp add maya -- bmaya-mcp
```

or add to `~/.codex/config.toml`:

```toml
[mcp_servers.maya]
command = "bmaya-mcp"
args = ["serve"]
```

GUI clients often do not see your shell PATH. If a client reports that it cannot start `bmaya-mcp`, use the absolute path to the executable (`uv tool dir --bin` prints the folder; on Windows the file is `bmaya-mcp.exe`), or use `uv` with `args = ["run", "--directory", "<path-to-clone>", "bmaya-mcp"]`.

To make agents use the tools well, point your project instructions at [LLM.md](LLM.md): working loop, Blender-to-Maya mapping, tested `maya.cmds` recipes.

## Tools

| Tool | What it does |
|---|---|
| `get_scene_info` | Scene overview: file, units, up axis, fps, frame range, selection, node list. Filter by name or node type. |
| `get_object_info` | One node: transform, bounding box, mesh counts, UV sets, materials, history, keys. |
| `execute_maya_code` | Run Python with `cmds`, `mel`, `om` preloaded. Assign to `result` to return data. One undo chunk per call. |
| `get_viewport_screenshot` | PNG of the viewport; `camera` picks a camera; `frames` such as `"1,12,24"` returns one contact sheet. |
| `audit_mesh` | Game-readiness check per mesh: n-gons, non-manifold, lamina and zero-area faces, UVs, materials, history, transforms, names. |
| `get_animation_info` | Curves, key ranges, tangents, fps, skeletons, skin clusters, blend shapes, anim layers. |
| `export_fbx` | FBX export with `unity`, `unreal` or `godot` preset, optional selection only and baked animation. |
| `scene_file` | `new`, `open`, `save` (or save-as with a path), `import` (`.ma`, `.mb`, `.fbx`, `.obj`). Refuses to discard unsaved changes unless `force` is set. |
| `undo` | Undo the last N undo chunks (one per `execute_maya_code` call, one per other command). |
| `list_sessions` | Running Maya sessions: id, scene, Maya version, busy, current. |
| `use_session` | Choose which session later tools talk to. |

## Sessions

Each Maya registers a file `~/.bmaya-mcp/sessions/<pid>.json` and the session id is the Maya process id. With one Maya open, tools use it automatically. With several, tools fail with the list of sessions until the agent calls `use_session`. The choice lives in the memory of that agent's server process, so another agent (its own `bmaya-mcp serve` process) can pick a different Maya. If the chosen Maya exits, the next call tells the agent the session is gone.

The server checks every registry entry with a ping and deletes entries whose Maya is dead. A Maya that does not answer in 3 seconds is kept and shown as `busy`.

## Logs and crash triage

State lives in `~/.bmaya-mcp/` (set `BMAYA_MCP_HOME` to move it).

```
~/.bmaya-mcp/
  sessions/<pid>.json                  live bridge entries
  logs/<YYYYMMDD-HHMMSS>-<pid>.log     command journal, one JSON object per line
  logs/<same-name>.crash.log           faulthandler output
  logs/server-<pid>.log                MCP server log
```

The journal has one file per Maya session. Each command writes a `start` line (fsynced before the command runs) and an `end` line with duration and result. The 30 newest sessions are kept; parameters over 20 KB are truncated.

A crashed session looks like this: a `start` line with no matching `end`, and no `bridge_stop` at the end of the file. The last `start` is what Maya was doing. The `.crash.log` holds the native stack if Python could capture it. The agent also gets an error naming the journal path when it tries to use a dead session.

```bash
bmaya-mcp logs --crashes              # sessions that look crashed
bmaya-mcp logs --session 1234 --tail 50
```

## FBX presets

`export_fbx` resets the FBX exporter and applies these options. Every preset uses Y up and FBX version 2020 (`FBX202000`), tangents on, ASCII off.

| Preset | Up axis | Triangulate | Smoothing groups |
|---|---|---|---|
| `unity` (default) | Y | no | no |
| `unreal` | Y | yes | yes |
| `godot` | Y | no | no |

Unreal gets triangulation and smoothing groups so its tangent space matches a bake done on the exported triangles and the importer does not warn about smoothing. Unity and Godot triangulate on import, so quads are kept.

Common to all presets: skins and blend shapes exported; cameras, lights, constraints and embedded textures not; hard edges off; mesh smoothing off.

With `animation=true` the exporter bakes complex animation at step 1 over `start` to `end`, or over the playback range when both are 0, with resampling on and the constant key reducer off.

Units are not converted. The handler does not rescale to centimeters or meters: the `FBXExportConvertUnitString` option has no effect in Maya 2027, and Unity, Unreal and Godot all read the unit scale stored in the file, which follows the scene's linear unit. Keep your Maya scene in the unit you want the importer to see. The result's `options.units` tells you which unit was used.

`path` must be absolute and end in `.fbx`; missing folders are created. The selection is restored after the export.

## Security

The bridge listens on `127.0.0.1` only, on a random port, and every request must carry a per-session token. The token is stored in `~/.bmaya-mcp/sessions/<pid>.json`.

That is not a sandbox. Any local process that can read your home directory can read the token and run Python inside Maya. `execute_maya_code` is arbitrary code execution by design: the agent can read and write files and start processes with your user rights. Only connect agents you trust to a Maya that has anything valuable open, and do not point the home directory at a shared location.

## Troubleshooting

**"No Maya with the bmaya bridge is running."** Maya is not running, it is a batch or `mayapy` session (the bridge needs the GUI), or the module is not installed. Run `bmaya-mcp doctor`; run `bmaya-mcp install` and restart Maya if the module is missing. Check the Script Editor for bridge errors on startup.

**"Several Maya sessions are running."** Call `list_sessions`, then `use_session` with an id.

**Timeout, or "did not reply in time".** Maya runs one command at a time on its main thread and the bridge waits 50 seconds. The usual causes are a modal dialog (save prompt, plug-in warning, file dialog) or a long operation. Dismiss the dialog or wait; `list_sessions` shows `busy: true` while a command runs. The command may still finish after the timeout, but its result is not returned, so check the scene before retrying. Keep each `execute_maya_code` call short.

**Session lost.** Maya exited or crashed. See Logs and crash triage.

**Other Maya MCP servers.** The bridge picks a free port, never a fixed one, and uses its own folder under `~/.bmaya-mcp`, so it should not collide with other Maya MCP plug-ins. Two plug-ins that both run commands in the same Maya can still interleave their work and share one undo stack. Give each MCP server a distinct name in your client, and expect agents to mix up similarly named tools.

**Screenshot fails.** A visible 3D viewport is required, and the MCP server must run on the same machine as Maya because it reads the PNG from the temp folder.

## Uninstall

```bash
bmaya-mcp uninstall
uv tool uninstall bmaya-mcp
```

Restart Maya. Delete `~/.bmaya-mcp/` if you do not want to keep logs. Remove the `maya` entry from your client config.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format
```

The Maya-side handlers need a real Maya. Check them headless with Maya's Python:

```bash
"C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe" tests/maya/check_handlers.py
```

In batch mayapy the bridge itself cannot run (`maya.utils.executeDeferred` never fires), so that script calls the handlers directly. Read [AGENTS.md](AGENTS.md) for the architecture, wire protocol and code rules.

## License

MIT, see [LICENSE](LICENSE).
