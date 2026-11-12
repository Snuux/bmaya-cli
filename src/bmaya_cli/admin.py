import argparse
import json
import sys
import time
from pathlib import Path

from bmaya_cli import __version__
from bmaya_cli.client import BridgeClient, BridgeCommandError, BridgeConnectionError
from bmaya_cli.install import (
    InstallError,
    default_maya_app_dir,
    install,
    installed_version,
    module_paths,
    uninstall,
)
from bmaya_cli.journal import (
    describe_pending,
    find_crashes,
    find_session_log,
    format_record,
    pid_alive,
    read_records,
    session_logs,
)
from bmaya_cli.launch import MAYA_ENV, MODE_ENV, OFF, autostart_mode, find_maya
from bmaya_cli.sessions import bmaya_home

DOCTOR_LABEL = "bmaya-doctor"


def resolve_app_dir(app_dir: Path | None) -> Path:
    return (app_dir or default_maya_app_dir()).expanduser().resolve()


def cmd_install(args: argparse.Namespace) -> int:
    target = resolve_app_dir(args.maya_app_dir)
    try:
        paths = install(target)
    except (InstallError, OSError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed bmaya {__version__} for Maya in {target}")
    print(f"  module folder  {paths.module_dir}")
    print(f"  module file    {paths.mod_file}")
    print()
    print("Next: restart Maya. The Script Editor should then show")
    print("  bmaya bridge listening on 127.0.0.1:<port> (session <pid>)")
    print("and `bmaya sessions` should list it. If it does not, load the fallback plug-in")
    print("once in Maya's Script Editor (Python) and mark it to auto-load:")
    print('  cmds.loadPlugin("bmaya_plugin.py")')
    print('  cmds.pluginInfo("bmaya_plugin.py", edit=True, autoload=True)')
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = resolve_app_dir(args.maya_app_dir)
    try:
        removed = uninstall(target)
    except (InstallError, OSError) as exc:
        print(f"Uninstall failed: {exc}", file=sys.stderr)
        return 1
    if not removed:
        print(f"bmaya is not installed in {target}")
        return 0
    for path in removed:
        print(f"Removed {path}")
    print("Restart Maya to stop the bridge.")
    return 0


def report_autostart() -> bool:
    mode = autostart_mode()
    found = find_maya()
    if found is None:
        where = f"no Maya installation found (set {MAYA_ENV})"
    else:
        where = f"Maya {found.version} in {found.bin_dir}"
    if mode == OFF:
        print(f"Autostart: off ({MODE_ENV}); {where}")
    else:
        print(f"Autostart: {mode} when no Maya is running; {where}")
    return found is not None


def cmd_doctor(args: argparse.Namespace) -> int:
    target = resolve_app_dir(args.maya_app_dir)
    paths = module_paths(target)
    home = bmaya_home()
    logs = home / "logs"

    print(f"bmaya {__version__}")
    print(f"Maya user folder: {target}")
    version = installed_version(target)
    if version is None or not paths.module_dir.is_dir():
        print("Module: not installed. A Maya you open yourself has no bridge until you run")
        print("  `bmaya install` and restart it. `bmaya start` works without it.")
    elif version != __version__:
        print(f"Module: version {version} is installed but this is {__version__}.")
        print("  Run `bmaya install` again, then restart Maya.")
    else:
        print(f"Module: installed, version {version}, in {paths.module_dir}")

    can_start = report_autostart()
    live = report_sessions(home)

    crashes = find_crashes(logs)
    if crashes:
        crash = crashes[-1]
        doing = crash.pending.get("command") if crash.pending else "no command"
        print(
            f"Last crash: session {crash.log.pid}, last record {crash.last_time}, "
            f"while running {doing}. Details: `bmaya logs --crashes`."
        )
    bootstrap_log = logs / "bootstrap-error.log"
    if bootstrap_log.is_file():
        print(f"Bridge startup errors were logged in {bootstrap_log}")
    print(f"Logs: {logs}")
    if live:
        return 0
    if can_start:
        print("No Maya is running. `bmaya start`, or any command, starts one without a window.")
        return 0
    print("No Maya is running and none can be started.")
    return 1


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
            client = BridgeClient(int(entry["port"]), str(entry["token"]), DOCTOR_LABEL)
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
        kind = "Maya without a window" if info.get("headless") else "Maya"
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


def cmd_logs(args: argparse.Namespace) -> int:
    logs = bmaya_home() / "logs"
    session = args.session
    if args.crashes:
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
    shown = records[-args.tail :] if args.tail > 0 else records
    print(f"Session {log.pid}: {log.path} ({len(shown)} of {len(records)} records)")
    for record in shown:
        print(f"  {format_record(record)}")
    return 0
