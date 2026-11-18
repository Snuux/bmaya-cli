import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path

from bmaya_cli import __version__

LOG_TAIL = 20

DESCRIPTION = """\
bmaya drives Autodesk Maya from the command line, for AI agents and scripts.

Each command talks to a running Maya, prints one line of JSON on stdout and exits.
Errors go to stderr as "error: ..." and "hint: ..." lines. With no Maya running,
the first command starts one without a window and says so in a "note: ..." line
on stderr; that Maya stays up until `bmaya stop`.
"""

EPILOG = """\
exit codes:
  0  done
  1  the command failed; stderr says why and what to do next
  2  bad arguments
  3  not ready: Maya is still starting, or still busy with a command that was
     sent. Do not resend that command; wait and check `bmaya sessions`.

a typical loop:
  bmaya scene                               # what is in the scene
  bmaya exec "cmds.polyCube(name='crate')"  # change it with maya.cmds
  bmaya shot -o shot.png                    # look at the result
  bmaya undo                                # take your last step back
  bmaya stop                                # end a Maya you started, after saving

`bmaya COMMAND --help` explains one command. `bmaya guide` prints the manual
for agents: the working loop, a Blender-to-Maya map and recipes.
"""

Runner = Callable[[argparse.Namespace], int]


def lazy(module: str, name: str) -> Runner:
    """Commands load their module on use, so `bmaya scene` does not pay for `bmaya demo`."""

    def run(args: argparse.Namespace) -> int:
        return getattr(importlib.import_module(f"bmaya_cli.{module}"), name)(args)

    return run


def examples(*lines: str) -> str:
    return "examples:\n" + "\n".join(f"  {line}" for line in lines)


def add_frames(command: argparse.ArgumentParser, what: str) -> None:
    group = command.add_mutually_exclusive_group()
    group.add_argument(
        "--frames",
        default="",
        metavar="SPEC",
        help=f"{what}: 1,12,24 or a range 0:30 or 0:30:2 (both ends included); "
        "negative frames as --frames=-10:20",
    )
    group.add_argument(
        "--clip",
        default="",
        metavar="NAME",
        help="the frames of a Game Exporter clip or time slider bookmark (`bmaya anim` lists them)",
    )


def add_target(group: argparse._ArgumentGroup, top_level: bool) -> None:
    default = "" if top_level else argparse.SUPPRESS
    group.add_argument(
        "--session",
        default=default,
        metavar="ID",
        help="Maya session to talk to for this one command (see `bmaya sessions`)",
    )
    group.add_argument(
        "--client",
        default=default,
        metavar="LABEL",
        help="name this caller; by default it is derived from the agent that runs the command "
        "(env BMAYA_CLIENT sets it too)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bmaya",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"bmaya {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    # Accepted before and after the command name. The copy on a command has no default, so
    # it does not wipe a value given before the name.
    add_target(parser.add_argument_group("which Maya, and who is asking"), top_level=True)
    maya = argparse.ArgumentParser(add_help=False)
    add_target(maya.add_argument_group("which Maya, and who is asking"), top_level=False)

    def add(
        name: str,
        runner: str,
        summary: str,
        details: str = "",
        epilog: str = "",
        module: str = "commands",
        for_maya: bool = True,
    ) -> argparse.ArgumentParser:
        command = commands.add_parser(
            name,
            help=summary,
            description=details or summary,
            epilog=epilog,
            parents=[maya] if for_maya else [],
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.set_defaults(run=lazy(module, runner))
        return command

    scene = add(
        "scene",
        "cmd_scene",
        "list the scene: file, units, up axis, fps, selection, nodes",
        "Prints the scene file, unit, up axis, fps, frame range, the selection and the nodes.\n"
        "Without filters the nodes are the top-level ones. `shown` and `total` say how many\n"
        "matched.",
        examples(
            "bmaya scene",
            "bmaya scene --query barrel            # names containing 'barrel'; * and ? work",
            "bmaya scene --type joint --limit 200  # every joint",
        ),
    )
    scene.add_argument("-q", "--query", default="", help="name filter, substring or wildcard")
    scene.add_argument("-t", "--type", default="", help="Maya node type: mesh, joint, camera, ...")
    scene.add_argument("-n", "--limit", type=int, default=0, help="most nodes to list (default 50)")

    obj = add(
        "object",
        "cmd_object",
        "describe one node: transform, bbox, mesh counts, materials, keys",
        "Prints the world transform, bounding box, shape, vertex and triangle counts, UV sets,\n"
        "materials, construction history and animated attributes of one node.",
        examples(
            "bmaya object crate", 'bmaya object "|props|crate"   # a full path when names clash'
        ),
    )
    obj.add_argument("name", help="node name or full DAG path")

    execute = add(
        "exec",
        "cmd_exec",
        "run Python inside Maya (cmds, mel and om are preloaded)",
        "Runs Python on Maya's main thread as one undo step. `cmds`, `mel` and `om`\n"
        "(OpenMaya 2) are already imported. Assign to a variable named `result` to get a value\n"
        "back; what the code prints comes back as `stdout`.\n\n"
        "The code comes from the argument, from -f FILE, or from stdin. Use stdin or a file for\n"
        "anything with quotes or several lines, so the shell does not mangle it.",
        examples(
            'bmaya exec "result = cmds.ls(selection=True)"',
            "bmaya exec -f build_barrel.py",
            "bmaya exec <<'PY'                         # bash, zsh",
            'cube = cmds.polyCube(name="crate")[0]',
            "result = cmds.ls(cube, long=True)",
            "PY",
            "Get-Content build.py -Raw | bmaya exec     # PowerShell",
        ),
    )
    execute.add_argument(
        "code", nargs="?", default="", help="Python source; '-' or nothing reads stdin"
    )
    execute.add_argument("-f", "--file", default="", help="read the code from this file")

    shot = add(
        "shot",
        "cmd_shot",
        "save a viewport screenshot (or a contact sheet of frames) as PNG",
        "Writes a PNG of the viewport and prints its path, width and height. Open that file to\n"
        "look at the scene. With --frames the frames are laid out in one contact sheet, which\n"
        "is the cheap way to review an animation. A range takes both ends (0:30 is 31\n"
        "frames) and is thinned evenly to the 12 tiles of a sheet.\n\n"
        "A Maya without a window renders offscreen from `persp` or the camera you name, in\n"
        "the shape of the render resolution; frame the subject first with\n"
        "cmds.viewFit('persp', all=True). --light changes the lighting for this shot only:\n"
        "unlit shows texture and base colors as they are, soft adds an even studio light,\n"
        "scene uses the scene's lights.",
        examples(
            "bmaya shot -o shot.png",
            "bmaya shot --camera front --size 1200 -o front.png",
            "bmaya shot --camera front --aspect 1:1 --light unlit -o front.png",
            "bmaya shot --frames 1,6,12,18,24 -o walk.png",
            "bmaya shot --frames 0:32 -o walk.png        # 12 evenly spaced frames of 0..32",
            "bmaya shot --clip zombie_walk -o walk.png   # a clip from `bmaya anim`",
        ),
    )
    shot.add_argument("-o", "--output", default="", metavar="FILE", help="where to write the PNG")
    shot.add_argument("--camera", default="", help="camera to look through (default: the view)")
    add_frames(shot, "frames for a contact sheet")
    shot.add_argument("--size", type=int, default=0, help="longest side in pixels (default 800)")
    shot.add_argument(
        "--aspect",
        default="",
        metavar="W:H",
        help="frame shape like 1:1 or 16:9, or 'render' for the render resolution "
        "(default: the viewport, or the render resolution without a window)",
    )
    shot.add_argument(
        "--light",
        default="",
        choices=("unlit", "soft", "scene"),
        help="lighting for this shot (default: as the viewport has it)",
    )

    audit = add(
        "audit",
        "cmd_audit",
        "check meshes for game export: n-gons, history, transforms, UVs",
        "Reports vertex and triangle counts and the issues that matter for a game asset:\n"
        "n-gons, non-manifold geometry, unfrozen transforms, construction history, missing\n"
        "UVs. With no names it audits the selection, or every mesh when nothing is selected.",
        examples("bmaya audit", "bmaya audit barrel_geo crate"),
    )
    audit.add_argument("names", nargs="*", help="mesh or group names")

    anim = add(
        "anim",
        "cmd_anim",
        "report animation: curves, key ranges, fps, skeleton, skin",
        "Without a name it summarises the animation in the scene: clips (Game Exporter clips\n"
        "and time slider bookmarks), animated nodes, skeletons, skin clusters, blend shapes\n"
        "and layers. With a name it lists that node's curves, key ranges and tangents, and\n"
        "its skeleton and skin clusters.",
        examples("bmaya anim", "bmaya anim root"),
    )
    anim.add_argument("name", nargs="?", default="", help="node to describe")

    export = add(
        "export",
        "cmd_export",
        "export FBX with a Unity, Unreal or Godot preset",
        "Writes an FBX file. Units are not converted: the scene unit is what the importer\n"
        "sees. The unreal preset triangulates and writes smoothing groups.",
        examples(
            "bmaya export hero.fbx",
            "bmaya export out/walk.fbx --preset unreal --selection --animation --start 1 --end 24",
        ),
    )
    export.add_argument("path", metavar="FILE.fbx", help="target file")
    export.add_argument("--preset", default="", help="unity (default), unreal or godot")
    export.add_argument("--selection", action="store_true", help="export only the selection")
    export.add_argument("--animation", action="store_true", help="bake and export animation")
    export.add_argument(
        "--start", type=int, default=0, help="first frame (default: playback range)"
    )
    export.add_argument("--end", type=int, default=0, help="last frame (default: playback range)")

    file = add(
        "file",
        "cmd_file",
        "new, open, save or import a scene file",
        "new and open replace the scene. They are refused when the scene has unsaved changes,\n"
        "and when another client used this Maya in the last 10 minutes. --force overrides\n"
        "both; use it only when the user asked for it.\n"
        "save without a path saves the current file; with a path it saves as .ma or .mb.\n"
        "import adds a .ma, .mb, .fbx or .obj file to the scene.",
        examples(
            "bmaya file save",
            "bmaya file save scenes/barrel.ma",
            "bmaya file open scenes/barrel.ma",
            "bmaya file import props/crate.fbx",
            "bmaya file new",
        ),
    )
    file.add_argument("action", choices=["new", "open", "save", "import"])
    file.add_argument("path", nargs="?", default="", help="scene or model file")
    file.add_argument("--force", action="store_true", help="discard unsaved changes")

    undo = add(
        "undo",
        "cmd_undo",
        "undo your own last steps, never someone else's",
        "Undoes your last `bmaya exec` steps, one per step. It stops at a change made by\n"
        "another client or by the user in the Maya window: the result then carries\n"
        "`blocked_by`, and with nothing undone it is an error. Ask the user before going\n"
        "on. --force also undoes changes the user made in the Maya window on top of your\n"
        "step; it never touches another client's steps.",
        examples("bmaya undo", "bmaya undo 3"),
    )
    undo.add_argument("steps", nargs="?", type=int, default=1, help="how many steps (default 1)")
    undo.add_argument("--force", action="store_true", help="also undo the user's UI changes")

    add(
        "sessions",
        "cmd_sessions",
        "list running Mayas: id, scene, busy, clients",
        "Lists every running Maya with the bridge. `clients` counts the callers that ran a\n"
        "command there in the last 10 minutes: more than one means you share that Maya.\n"
        "`busy` is true while a command runs. `client` is the name you act under.",
        examples("bmaya sessions"),
    )

    use = add(
        "use",
        "cmd_use",
        "pick the Maya that your next commands talk to",
        "Needed only when several Mayas run. The choice is remembered for this caller.",
        examples("bmaya use 12345"),
    )
    use.add_argument("id", help="session id from `bmaya sessions`")

    start = add(
        "start",
        "cmd_start",
        "start a Maya (without a window unless --gui) and wait for it",
        "Starts one more Maya, waits until it answers and makes it your current session. It\n"
        "keeps running after this command returns, with no timer: stop it with `bmaya stop`.\n"
        "You rarely need this: with no Maya running, any command starts one by itself.",
        examples("bmaya start", "bmaya start --scene scenes/barrel.ma", "bmaya start --gui"),
    )
    start.add_argument("--gui", action="store_true", help="start Maya with its window")
    start.add_argument("--scene", default="", metavar="FILE", help="scene file to open")

    stop = add(
        "stop",
        "cmd_stop",
        "stop a Maya that runs without a window",
        "Ends a Maya that runs without a window. It is refused when the scene has unsaved\n"
        "changes or another client used that Maya in the last 10 minutes; save first, or pass\n"
        "--force. A Maya with a window is the user's and is never stopped from here.",
        examples("bmaya file save scenes/barrel.ma", "bmaya stop", "bmaya stop 12345 --force"),
    )
    stop.add_argument("id", nargs="?", default="", help="session id (default: your current Maya)")
    stop.add_argument("--force", action="store_true", help="discard unsaved changes")

    add(
        "guide",
        "cmd_guide",
        "print the manual for agents (plain text, not JSON)",
        for_maya=False,
    )

    install = add(
        "install",
        "cmd_install",
        "install the Maya module, so a Maya you open has the bridge",
        module="admin",
        for_maya=False,
    )
    install.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    uninstall = add(
        "uninstall", "cmd_uninstall", "remove the Maya module", module="admin", for_maya=False
    )
    uninstall.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    doctor = add(
        "doctor",
        "cmd_doctor",
        "check the install and the running Mayas (plain text)",
        module="admin",
        for_maya=False,
    )
    doctor.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    logs = add(
        "logs",
        "cmd_logs",
        "show what was sent to a Maya, and what it crashed on (plain text)",
        module="admin",
        for_maya=False,
    )
    logs.add_argument("--session", type=int, help="pid of the Maya session (default: newest)")
    logs.add_argument("--tail", type=int, default=LOG_TAIL, help="records to show (default 20)")
    logs.add_argument("--crashes", action="store_true", help="list crashed sessions instead")

    demo = add(
        "demo",
        "cmd_demo",
        "build, check and export a prop in a running Maya (plain text)",
        module="demo",
        for_maya=False,
    )
    demo.add_argument("--out", type=Path, default=Path("demo"), help="output folder")
    demo.add_argument("--session", default="", help="session id when several Mayas run")
    return parser


def use_utf8() -> None:
    """The Windows code page would otherwise mangle node names and piped code."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        from bmaya_cli.commands import report_failure

        code = report_failure(error)
        if code is None:
            raise
        return code
