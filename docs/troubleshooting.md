# Troubleshooting

## Common messages

- `bmaya` is not found by the agent. Its shell does not have the uv tool folder on PATH. `uv tool dir --bin` prints that folder; add it to PATH or give the agent the full path.
- "No Maya with the bmaya bridge is running." The command is one that never starts Maya (`sessions`, `use`, `stop`, `demo`), or `BMAYA_AUTOSTART` is `off`. Run `bmaya start`. For a Maya you opened yourself, run `bmaya doctor`; if the module is missing, run `bmaya install` and restart Maya. Bridge startup errors show up in the Script Editor.
- "No Maya installation was found to start." Maya is not in the standard install folder. Set `BMAYA_MAYA` to your Maya folder.
- "Maya is starting ... and is not ready yet", again and again. Look at the newest `~/.bmaya/logs/launch-*.log`: it holds the output of the Maya that was started. After three minutes the message changes to say that Maya is still running but its bridge has not come up, for example because it waits on a license dialog. Close that Maya and run the command again.
- "Nothing was undone." The latest change in that Maya is not the caller's. The message names it. Maya's own startup scripts can leave such an entry while Maya is still loading; repeat with `--force`. See [Sharing one Maya](sessions.md#sharing-one-maya).
- Undo is blocked by a label that looks like your own agent. The label changed between two calls. `bmaya sessions` shows the current one as `client`; set `BMAYA_CLIENT` to keep it fixed.
- "Several Maya sessions are running." Run `bmaya sessions`, then `bmaya use ID`, or pass `--session ID`.
- Exit code 3 with "did not reply in time" or "still running". Maya runs one command at a time on its main thread, and the bridge waits 50 seconds for it. Usually a modal dialog is open (save prompt, plug-in warning, file dialog) or the operation is long. Close the dialog or wait; `bmaya sessions` shows `busy: true` meanwhile. The command may still finish, but its result is lost, so check the scene and do not send it again blindly. Keep `bmaya exec` calls short.
- "The viewport has not been drawn yet." Maya is still starting or its window is minimized. Wait a few seconds or restore the window and retry.
- "Maya session ... exited or crashed." See [Crash journal](#crash-journal). Unsaved work in that Maya is gone.
- `bmaya shot` fails. In a Maya with a window it needs a visible 3D viewport. Without a window it needs a GPU that Maya's hardware renderer can use. `bmaya` must run on the same machine as Maya, because it moves the PNG out of Maya's temp folder.
- Other tools that drive the same Maya. The bridge uses a free port and its own folder under `~/.bmaya`, so it shouldn't collide with other Maya plug-ins. Two plug-ins that run commands in one Maya still share one undo queue; `bmaya undo` treats the other tool's steps like edits made in the Maya window.

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
