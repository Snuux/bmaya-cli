import json
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

Handler = Callable[[dict[str, Any]], dict[str, Any]]
DROP: dict[str, Any] = {"__drop__": True}


class FakeError(Exception):
    def __init__(self, type: str, message: str, hint: str = "", stdout: str = "") -> None:
        super().__init__(message)
        self.type = type
        self.message = message
        self.hint = hint
        self.stdout = stdout


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def read_exactly(conn: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def read_frame(conn: socket.socket) -> dict[str, Any]:
    (length,) = struct.unpack(">I", read_exactly(conn, 4))
    return json.loads(read_exactly(conn, length).decode("utf-8"))


def write_frame(conn: socket.socket, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    conn.sendall(struct.pack(">I", len(body)) + body)


class FakeBridge:
    def __init__(
        self,
        handlers: dict[str, Handler] | None = None,
        token: str = "secret",
        port: int = 0,
        maya_version: str = "2027",
        scene: str = "C:/work/hero.ma",
    ) -> None:
        self.handlers = handlers or {}
        self.token = token
        self.maya_version = maya_version
        self.scene = scene
        self.requests: list[dict[str, Any]] = []
        self.silent = False
        self.clients = 0
        self._port = port
        self._listener: socket.socket | None = None
        self._connections: list[socket.socket] = []
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> "FakeBridge":
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", self._port))
        listener.listen(8)
        self._port = listener.getsockname()[1]
        self._listener = listener
        threading.Thread(target=self._accept_loop, args=(listener,), daemon=True).start()
        return self

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def write_registry(self, home: Path, pid: int) -> Path:
        sessions = home / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        path = sessions / f"{pid}.json"
        path.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "port": self._port,
                    "token": self.token,
                    "maya_version": self.maya_version,
                    "scene": self.scene,
                    "started": "2026-11-12T10:00:00",
                }
            ),
            encoding="utf-8",
        )
        return path

    def _accept_loop(self, listener: socket.socket) -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with self._lock:
                self._connections.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            while True:
                request = read_frame(conn)
                self.requests.append(request)
                if self.silent:
                    continue
                if request.get("token") != self.token:
                    error = {"type": "Unauthorized", "message": "Bad token", "hint": ""}
                    write_frame(conn, {"id": request["id"], "ok": False, "error": error})
                    return
                reply = self._answer(request)
                if reply is None:
                    return
                write_frame(conn, reply)
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            conn.close()

    def _answer(self, request: dict[str, Any]) -> dict[str, Any] | None:
        command = request["command"]
        if command == "ping":
            result = {
                "pid": 0,
                "maya_version": self.maya_version,
                "scene": self.scene,
                "busy": False,
                "clients": self.clients,
                "headless": False,
            }
            return {"id": request["id"], "ok": True, "result": result}
        handler = self.handlers.get(command)
        if handler is None:
            error = {"type": "UnknownCommand", "message": f"No command {command}", "hint": ""}
            return {"id": request["id"], "ok": False, "error": error}
        try:
            result = handler(request["params"])
        except FakeError as exc:
            error = {"type": exc.type, "message": exc.message, "hint": exc.hint}
            if exc.stdout:
                error["stdout"] = exc.stdout
            return {"id": request["id"], "ok": False, "error": error}
        if result is DROP:
            return None
        return {"id": request["id"], "ok": True, "result": result}
