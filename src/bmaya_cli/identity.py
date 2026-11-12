import os
import re
import subprocess
import sys

CLIENT_ENV = "BMAYA_CLIENT"
FALLBACK_LABEL = "cli"
MAX_LABEL = 48
MAX_DEPTH = 24
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# An agent session id from the environment names the agent better than any process does.
AGENT_SESSIONS = (("CLAUDE_CODE_SESSION_ID", "claude"),)

# Every command line starts a fresh one of these, so they cannot identify the caller.
TRANSIENT = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "fish",
        "dash",
        "ksh",
        "pwsh",
        "powershell",
        "cmd",
        "conhost",
        "env",
        "sudo",
        "uv",
        "uvx",
        "python",
        "python3",
        "pythonw",
        "py",
        "bmaya",
        "bmaya-cli",
    }
)

ProcessTable = dict[int, tuple[int, str]]


def sanitize(label: str) -> str:
    return UNSAFE.sub("-", label.strip()).strip("-")[:MAX_LABEL]


def program_name(executable: str) -> str:
    name = os.path.basename(executable.replace("\\", "/")).lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def client_label() -> str:
    """One label per agent, the same for every command it runs.

    The bridge keys undo ownership and the shared-scene guard on it, so a label that changed
    between two calls would make an agent a stranger to its own previous step.
    """
    explicit = sanitize(os.environ.get(CLIENT_ENV, ""))
    if explicit:
        return explicit
    for variable, prefix in AGENT_SESSIONS:
        session = sanitize(os.environ.get(variable, ""))
        if session:
            return f"{prefix}-{session[:8]}"
    host = host_process(process_table(), os.getpid())
    if host is None:
        return FALLBACK_LABEL
    pid, name = host
    return sanitize(f"{name}-{pid}") or FALLBACK_LABEL


def host_process(table: ProcessTable, pid: int) -> tuple[int, str] | None:
    """The nearest ancestor that outlives a single command: the agent or the terminal."""
    seen = {pid}
    current = table.get(pid, (0, ""))[0]
    for _ in range(MAX_DEPTH):
        if current in seen or current not in table:
            return None
        seen.add(current)
        parent, executable = table[current]
        name = program_name(executable)
        if name and name not in TRANSIENT:
            return current, name
        current = parent
    return None


def process_table() -> ProcessTable:
    try:
        if sys.platform == "win32":
            return windows_process_table()
        return posix_process_table()
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def windows_process_table() -> ProcessTable:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        )

    snapshot_processes = 0x00000002
    invalid_handle = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    snapshot = kernel32.CreateToolhelp32Snapshot(snapshot_processes, 0)
    if snapshot in (None, invalid_handle):
        return {}
    table: ProcessTable = {}
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    try:
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def posix_process_table() -> ProcessTable:
    output = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,comm="],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    ).stdout
    table: ProcessTable = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table
