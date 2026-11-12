from __future__ import annotations

import atexit
import hmac
import json
import os
import secrets
import socket
import struct
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from bmaya_bridge import registry
from bmaya_bridge.dispatch import (
    DEFAULT_TIMEOUT,
    Dispatcher,
    Handler,
    Scheduler,
    UndoChunk,
    maya_schedule,
    maya_undo_chunk,
)
from bmaya_bridge.errors import error_payload
from bmaya_bridge.journal import Journal

HOST = "127.0.0.1"
MAX_FRAME = 16 * 1024 * 1024
HEADER = struct.Struct(">I")
ACCEPT_POLL_SECONDS = 0.5

CommandSource = Callable[[], Mapping[str, Handler]]


class FrameError(Exception):
    pass


def encode_frame(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise FrameError(f"Frame of {len(body)} bytes exceeds the {MAX_FRAME} byte limit.")
    return HEADER.pack(len(body)) + body


def read_frame(sock: socket.socket) -> bytes | None:
    header = _read_exact(sock, HEADER.size)
    if header is None:
        return None
    (length,) = HEADER.unpack(header)
    if length > MAX_FRAME:
        raise FrameError(f"Frame of {length} bytes exceeds the {MAX_FRAME} byte limit.")
    body = _read_exact(sock, length)
    if body is None:
        raise FrameError("Connection closed in the middle of a frame.")
    return body


def _read_exact(sock: socket.socket, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(min(remaining, 1024 * 1024))
        if not chunk:
            if remaining == size:
                return None
            raise FrameError("Connection closed in the middle of a frame.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def load_commands() -> Mapping[str, Handler]:
    from bmaya_bridge.handlers import COMMANDS

    return COMMANDS


def error_response(request_id: object, kind: str, message: str, hint: str = "") -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "error": {"type": kind, "message": message, "hint": hint},
    }


class BridgeServer:
    def __init__(
        self,
        journal: Journal,
        commands: CommandSource = load_commands,
        schedule: Scheduler = maya_schedule,
        undo_chunk: UndoChunk = maya_undo_chunk,
        timeout: float = DEFAULT_TIMEOUT,
        maya_version: str = "",
        scene: str = "",
    ) -> None:
        self.journal = journal
        self.dispatcher = Dispatcher(journal, schedule, undo_chunk, timeout)
        self.maya_version = maya_version
        self.scene = scene
        self.pid = os.getpid()
        self.port = 0
        self.token = ""
        self._commands = commands
        self._started = ""
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._listener is not None

    def start(self) -> int:
        with self._lock:
            if self._listener is not None:
                return self.port
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind((HOST, 0))
            listener.listen()
            listener.settimeout(ACCEPT_POLL_SECONDS)
            self._listener = listener
            self.port = listener.getsockname()[1]
            self.token = secrets.token_hex(16)
            self._started = datetime.now().isoformat(timespec="seconds")
            self._write_registry()
            self.journal.event(
                "bridge_start",
                pid=self.pid,
                port=self.port,
                maya_version=self.maya_version,
                scene=self.scene,
            )
            self._accept_thread = threading.Thread(
                target=self._accept_loop,
                args=(listener,),
                name="bmaya-bridge-accept",
                daemon=True,
            )
            self._accept_thread.start()
            return self.port

    def stop(self) -> None:
        with self._lock:
            listener = self._listener
            if listener is None:
                return
            self._listener = None
            listener.close()
            for client in list(self._clients):
                _close_quietly(client)
            registry.remove_session(self.pid)
            self.journal.event("bridge_stop", pid=self.pid, port=self.port)
        thread = self._accept_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(ACCEPT_POLL_SECONDS * 4)

    def set_scene(self, scene: str) -> None:
        self.scene = scene
        with self._lock:
            if self._listener is not None:
                self._write_registry()

    def ping(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "maya_version": self.maya_version,
            "scene": self.scene,
            "busy": self.dispatcher.busy,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        command = request.get("command")
        client = str(request.get("client") or "")
        if command == "ping":
            return {"id": request_id, "ok": True, "result": self.ping()}
        try:
            commands = self._commands()
        except Exception as exc:
            traceback.print_exc()
            return error_response(
                request_id,
                type(exc).__name__,
                f"The bmaya_bridge command handlers failed to import: {exc}",
                "Check the Maya Script Editor for the traceback and reinstall bmaya-mcp.",
            )
        handler = commands.get(command) if isinstance(command, str) else None
        if handler is None:
            valid = ", ".join(sorted(["ping", *commands]))
            return error_response(
                request_id,
                "UnknownCommand",
                f"Unknown command {command!r}.",
                f"Valid commands: {valid}.",
            )
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return error_response(
                request_id,
                "BadRequest",
                "'params' must be a JSON object.",
                "Send the command parameters as an object, for example {}.",
            )
        job = self.dispatcher.submit(request_id, client, command, handler, params)
        if job.error is not None:
            return {"id": request_id, "ok": False, "error": job.error}
        return {"id": request_id, "ok": True, "result": job.result}

    def _write_registry(self) -> None:
        registry.write_session(
            {
                "pid": self.pid,
                "port": self.port,
                "token": self.token,
                "maya_version": self.maya_version,
                "scene": self.scene,
                "started": self._started,
            }
        )

    def _accept_loop(self, listener: socket.socket) -> None:
        while self._listener is listener:
            try:
                connection, address = listener.accept()
            except OSError:
                if self._listener is listener:
                    continue
                break
            connection.settimeout(None)
            with self._lock:
                if self._listener is not listener:
                    _close_quietly(connection)
                    break
                self._clients.add(connection)
            threading.Thread(
                target=self._serve_client,
                args=(connection, address[1]),
                name=f"bmaya-bridge-client-{address[1]}",
                daemon=True,
            ).start()

    def _serve_client(self, connection: socket.socket, peer_port: int) -> None:
        self.journal.event("client_connect", peer=peer_port)
        client = ""
        try:
            while True:
                try:
                    body = read_frame(connection)
                except FrameError as exc:
                    self._send(connection, error_response(None, "BadRequest", str(exc)))
                    break
                if body is None:
                    break
                try:
                    request = json.loads(body.decode("utf-8"))
                except ValueError as exc:
                    message = f"Request is not valid UTF-8 JSON: {exc}"
                    self._send(connection, error_response(None, "BadRequest", message))
                    continue
                if not isinstance(request, dict):
                    message = "Request must be a JSON object."
                    self._send(connection, error_response(None, "BadRequest", message))
                    continue
                if not self._authorized(request.get("token")):
                    response = error_response(
                        request.get("id"),
                        "Unauthorized",
                        "The session token does not match.",
                        "Reload the session registry; Maya may have restarted the bridge.",
                    )
                    self._send(connection, response)
                    break
                client = str(request.get("client") or client)
                try:
                    response = self.handle(request)
                except Exception as exc:
                    traceback.print_exc()
                    response = {"id": request.get("id"), "ok": False, "error": error_payload(exc)}
                self._send(connection, response)
        except OSError:
            pass
        finally:
            with self._lock:
                self._clients.discard(connection)
            _close_quietly(connection)
            self.journal.event("client_disconnect", peer=peer_port, client=client)

    def _authorized(self, token: object) -> bool:
        if not isinstance(token, str):
            return False
        return hmac.compare_digest(token.encode("utf-8"), self.token.encode("utf-8"))

    def _send(self, connection: socket.socket, response: dict[str, Any]) -> None:
        try:
            frame = encode_frame(response)
        except (TypeError, ValueError, FrameError) as exc:
            frame = encode_frame(
                error_response(
                    response.get("id"),
                    type(exc).__name__,
                    f"The command result could not be sent: {exc}",
                    "Return less data, or only JSON-serializable values.",
                )
            )
        connection.sendall(frame)


def _close_quietly(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    connection.close()


_lock = threading.RLock()
_bridge: BridgeServer | None = None
_journal: Journal | None = None
_callback_ids: list[Any] = []
_atexit_registered = False


def start() -> int:
    global _bridge, _journal, _atexit_registered
    with _lock:
        if _bridge is not None and _bridge.running:
            return _bridge.port
        if _journal is None:
            _journal = Journal(registry.logs_dir())
        maya_version, scene = _maya_info()
        if maya_version:
            _import_handlers_early()
        _bridge = BridgeServer(_journal, maya_version=maya_version, scene=scene)
        port = _bridge.start()
        _add_maya_callbacks()
        if not _atexit_registered:
            atexit.register(_shutdown)
            _atexit_registered = True
        return port


def stop() -> None:
    with _lock:
        _remove_maya_callbacks()
        if _bridge is not None:
            _bridge.stop()


def is_running() -> bool:
    return _bridge is not None and _bridge.running


def _shutdown() -> None:
    with _lock:
        if _bridge is not None:
            _bridge.stop()


def _import_handlers_early() -> None:
    try:
        load_commands()
    except Exception:
        traceback.print_exc()


def _maya_info() -> tuple[str, str]:
    try:
        from maya import cmds
    except ImportError:
        return "", ""
    try:
        return str(cmds.about(version=True)), _scene_name()
    except Exception:
        return "", ""


def _scene_name() -> str:
    from maya import cmds

    return str(cmds.file(query=True, sceneName=True) or "")


def _add_maya_callbacks() -> None:
    if _callback_ids:
        return
    try:
        from maya.api import OpenMaya
    except ImportError:
        return
    messages = OpenMaya.MSceneMessage
    try:
        _callback_ids.append(messages.addCallback(messages.kAfterOpen, _on_scene_opened))
        _callback_ids.append(messages.addCallback(messages.kAfterNew, _on_scene_renamed))
        _callback_ids.append(messages.addCallback(messages.kAfterSave, _on_scene_renamed))
        _callback_ids.append(messages.addCallback(messages.kMayaExiting, _on_maya_exiting))
    except Exception:
        traceback.print_exc()


def _remove_maya_callbacks() -> None:
    if not _callback_ids:
        return
    try:
        from maya.api import OpenMaya

        OpenMaya.MMessage.removeCallbacks(_callback_ids)
    except Exception:
        traceback.print_exc()
    _callback_ids.clear()


def _on_scene_opened(*_: object) -> None:
    bridge = _bridge
    if bridge is None or not bridge.running:
        return
    scene = _scene_name()
    bridge.set_scene(scene)
    bridge.journal.event("scene_opened", scene=scene)


def _on_scene_renamed(*_: object) -> None:
    bridge = _bridge
    if bridge is not None and bridge.running:
        bridge.set_scene(_scene_name())


def _on_maya_exiting(*_: object) -> None:
    try:
        _shutdown()
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
