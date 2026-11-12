import argparse
import json
import sys
import time
from pathlib import Path

from bmaya_mcp import __version__
from bmaya_mcp.client import BridgeClient, BridgeCommandError, BridgeConnectionError
from bmaya_mcp.demo import run_demo
from bmaya_mcp.install import (
    InstallError,
    default_maya_app_dir,
    install,
    installed_version,
    module_paths,
    uninstall,
)
from bmaya_mcp.journal import (
    describe_pending,
    find_crashes,
    find_session_log,
    format_record,
    pid_alive,
    read_records,
    session_logs,
)
from bmaya_mcp.launch import MAYA_ENV, MODE_ENV, OFF, autostart_mode, find_maya, run_headless
from bmaya_mcp.sessions import bmaya_home

LOG_TAIL = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bmaya-mcp",
        description="MCP server for Autodesk Maya. Without a command it serves MCP over stdio.",
    )
    parser.add_argument("--version", action="version", version=f"bmaya-mcp {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    commands.add_parser("serve", help="run the MCP stdio server (the default)")

    install = commands.add_parser("install", help="install the Maya module that starts the bridge")
    install.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    uninstall = commands.add_parser("uninstall", help="remove the installed Maya module")
    uninstall.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    doctor = commands.add_parser("doctor", help="check the install and the running sessions")
    doctor.add_argument("--maya-app-dir", type=Path, help="Maya user folder (MAYA_APP_DIR)")

    logs = commands.add_parser("logs", help="show bridge journals")
    logs.add_argument("--session", type=int, help="pid of the Maya session (default: newest)")
    logs.add_argument("--tail", type=int, default=LOG_TAIL, help="records to show (default 20)")
    logs.add_argument("--crashes", action="store_true", help="list crashed sessions instead")

    headless = commands.add_parser(
        "headless", help="run a Maya without a UI and the bridge in it, until Ctrl+C"
    )
    headless.add_argument("--maya", default="", help="Maya install folder (default: newest)")
    headless.add_argument("--scene", default="", help="scene file to open at start")
    headless.add_argument(
        "--idle-exit",
        type=int,
        default=0,
        metavar="SECONDS",
        help="exit after this long without a connected client (default 0: never)",
    )

    demo = commands.add_parser("demo", help="build, check and export a prop in the live Maya")
    demo.add_argument("--out", type=Path, default=Path("demo"), help="output folder")
    demo.add_argument("--session", default="", help="session id when several Maya run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "serve"):
        from bmaya_mcp.server import main as serve

        serve()
        return 0
    if args.command == "install":
        return cmd_install(args.maya_app_dir)
    if args.command == "uninstall":
        return cmd_uninstall(args.maya_app_dir)
    if args.command == "doctor":
        return cmd_doctor(args.maya_app_dir)
    if args.command == "logs":
        return cmd_logs(args.session, args.tail, args.crashes)
    if args.command == "headless":
        return cmd_headless(args.maya, args.scene, args.idle_exit)
    return run_demo(args.out, args.session)


def resolve_app_dir(app_dir: Path | None) -> Path:
    return (app_dir or default_maya_app_dir()).expanduser().resolve()


def cmd_install(app_dir: Path | None) -> int:
    target = resolve_app_dir(app_dir)
    try:
        paths = install(target)
    except (InstallError, OSError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed bmaya-mcp {__version__} for Maya in {target}")
    print(f"  module folder  {paths.module_dir}")
    print(f"  module file    {paths.mod_file}")
    print()
    print("Next: restart Maya. The Script Editor should then show")
    print("  bmaya-mcp bridge listening on 127.0.0.1:<port> (session <pid>)")
    print("and `bmaya-mcp doctor` should list the session. If it does not, load the")
    print("fallback plug-in once in Maya's Script Editor (Python) and mark it to auto-load:")
    print('  cmds.loadPlugin("bmaya_mcp_plugin.py")')
    print('  cmds.pluginInfo("bmaya_mcp_plugin.py", edit=True, autoload=True)')
    return 0


def cmd_uninstall(app_dir: Path | None) -> int:
    target = resolve_app_dir(app_dir)
    try:
        removed = uninstall(target)
    except (InstallError, OSError) as exc:
        print(f"Uninstall failed: {exc}", file=sys.stderr)
        return 1
    if not removed:
        print(f"bmaya-mcp is not installed in {target}")
        return 0
    for path in removed:
        print(f"Removed {path}")
    print("Restart Maya to stop the bridge.")
    return 0


def cmd_headless(maya: str, scene: str, idle_exit: int) -> int:
    install = find_maya(override=maya)
    if install is None:
        where = maya or "the default install folders"
        print(f"No Maya installation found in {where}.", file=sys.stderr)
        print(f"Pass --maya or set {MAYA_ENV} to the Maya install folder.", file=sys.stderr)
        return 1
    print(f"Starting Maya {install.version} without a UI ({install.mayapy}). Ctrl+C stops it.")
    sys.stdout.flush()
    return run_headless(install, idle_exit, scene)


def report_autostart() -> None:
    mode = autostart_mode()
    install = find_maya()
    if install is None:
        found = f"no Maya installation found (set {MAYA_ENV})"
    else:
        found = f"Maya {install.version} in {install.bin_dir}"
    if mode == OFF:
        print(f"Autostart: off ({MODE_ENV}); {found}")
    else:
        print(f"Autostart: {mode} when no Maya is running; {found}")


def cmd_doctor(app_dir: Path | None) -> int:
    target = resolve_app_dir(app_dir)
    paths = module_paths(target)
    home = bmaya_home()
    logs = home / "logs"

    print(f"bmaya-mcp {__version__}")
    print(f"Maya user folder: {target}")
    version = installed_version(target)
    if version is None or not paths.module_dir.is_dir():
        print("Module: not installed. Run `bmaya-mcp install`, then restart Maya.")
    elif version != __version__:
        print(f"Module: version {version} is installed but this is {__version__}.")
        print("  Run `bmaya-mcp install` again, then restart Maya.")
    else:
        print(f"Module: installed, version {version}, in {paths.module_dir}")

    report_autostart()

    live = report_sessions(home)

    crashes = find_crashes(logs)
    if crashes:
        crash = crashes[-1]
        doing = crash.pending.get("command") if crash.pending else "no command"
        print(
            f"Last crash: session {crash.log.pid}, last record {crash.last_time}, "
            f"while running {doing}. Details: `bmaya-mcp logs --crashes`."
        )
    bootstrap_log = logs / "bootstrap-error.log"
    if bootstrap_log.is_file():
        print(f"Bridge startup errors were logged in {bootstrap_log}")
    print(f"Logs: {logs}")
    if not live:
        print(
            "No live Maya session. Start Maya with the module installed, or run "
            "`bmaya-mcp headless`."
        )
        return 1
    return 0


def report_sessions(home: Path) -> int:
    registry = sorted((home / "sessions").glob("*.json"))
    live = 0
    print(f"Sessions ({home / 'sessions'}):")
    if not registry:
        print("  none registered")
    for path in registry:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            pid = int(entry["pid"])
            client = BridgeClient(int(entry["port"]), str(entry["token"]), "bmaya-mcp-doctor")
        except (OSError, ValueError, KeyError, TypeError):
            print(f"  {path.name}: unreadable registry file")
            continue
        if not pid_alive(pid):
            remove_stale(path)
            print(f"  {pid}: stale entry, Maya is gone; removed {path.name}")
            continue
        started = time.perf_counter()
        try:
            info = client.request("ping", read_timeout=3.0)
        except BridgeConnectionError as exc:
            print(f"  {pid}: Maya is running but the bridge did not answer ({exc})")
            continue
        except BridgeCommandError as exc:
            print(f"  {pid}: the bridge refused the registry token ({exc.type})")
            continue
        finally:
            client.close()
        latency = (time.perf_counter() - started) * 1000
        live += 1
        scene = info.get("scene") or "untitled"
        state = "busy" if info.get("busy") else "idle"
        kind = "headless Maya" if info.get("headless") else "Maya"
        print(
            f"  {pid}: {kind} {info.get('maya_version')}, {scene}, {state}, "
            f"{info.get('clients', 0)} clients, ping {latency:.0f} ms"
        )
    return live


def remove_stale(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def cmd_logs(session: int | None, tail: int, crashes: bool) -> int:
    logs = bmaya_home() / "logs"
    if crashes:
        found = [crash for crash in find_crashes(logs) if session in (None, crash.log.pid)]
        if not found:
            print(f"No crashed sessions in {logs}")
            return 0
        for crash in found:
            print(f"Session {crash.log.pid} crashed after {crash.last_time}")
            print(f"  journal {crash.log.path}")
            if crash.pending:
                print("  unfinished command:")
                for line in describe_pending(crash.pending):
                    print(f"    {line}")
            else:
                print("  no command was running")
            if crash.crash_tail:
                print(f"  crash log {crash.log.crash_path}:")
                for line in crash.crash_tail:
                    print(f"    {line}")
            print()
        return 0

    if session is None:
        available = session_logs(logs)
        log = available[-1] if available else None
    else:
        log = find_session_log(logs, session)
    if log is None:
        which = f"session {session}" if session is not None else "any session"
        print(f"No journal for {which} in {logs}")
        return 1
    records = read_records(log.path)
    shown = records[-tail:] if tail > 0 else records
    print(f"Session {log.pid}: {log.path} ({len(shown)} of {len(records)} records)")
    for record in shown:
        print(f"  {format_record(record)}")
    return 0
