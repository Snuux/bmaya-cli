import json
import logging
import os
import socket
import struct
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 55.0
MAX_FRAME = 256 * 1024 * 1024


class BridgeCommandError(Exception):
    def __init__(self, type: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.type = type
        self.message = message
        self.hint = hint


class BridgeConnectionError(Exception):
    def __init__(self, message: str, refused: bool = False, timed_out: bool = False) -> None:
        super().__init__(message)
        self.refused = refused
        self.timed_out = timed_out


def parent_process_name() -> str | None:
    ppid = os.getppid()
    try:
        if os.name == "nt":
            output = subprocess.run(
                ["tasklist", "/FI", f"PID eq {ppid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout.strip()
            if not output.startswith('"'):
                return None
            name = output.split('","')[0].strip('"')
        else:
            name = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(ppid)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    name = os.path.basename(name)
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower() or None


def default_client_label() -> str:
    return f"{parent_process_name() or 'agent'}-{os.getpid()}"


class BridgeClient:
    def __init__(
        self,
        port: int,
        token: str,
        label: str | None = None,
        host: str = HOST,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
    ) -> None:
        self.port = port
        self.token = token
        self.label = label or default_client_label()
        self.host = host
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.reconnects = 0
        self._sock: socket.socket | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._connected_before = False

    def request(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        read_timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            try:
                sock = self._connect()
                request_id = self._next_id
                self._next_id += 1
                payload = {
                    "id": request_id,
                    "token": self.token,
                    "client": self.label,
                    "command": command,
                    "params": params or {},
                }
                sock.settimeout(read_timeout or self.read_timeout)
                self._send(sock, payload)
                reply = self._receive(sock)
            except BridgeConnectionError:
                self._close()
                raise
        if reply.get("id") != request_id:
            self.close()
            raise BridgeConnectionError(
                f"Bridge answered request {reply.get('id')} instead of {request_id}"
            )
        if reply.get("ok"):
            return reply.get("result") or {}
        error = reply.get("error") or {}
        if error.get("type") == "Unauthorized":
            self.close()
        raise BridgeCommandError(
            str(error.get("type", "BridgeError")),
            str(error.get("message", "Unknown bridge error")),
            str(error.get("hint", "")),
        )

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except ConnectionRefusedError as exc:
            raise BridgeConnectionError(
                f"Connection refused on port {self.port}", refused=True
            ) from exc
        except TimeoutError as exc:
            raise BridgeConnectionError(
                f"Timed out connecting to port {self.port}", timed_out=True
            ) from exc
        except OSError as exc:
            raise BridgeConnectionError(f"Cannot connect to port {self.port}: {exc}") from exc
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._connected_before:
            self.reconnects += 1
            logger.info("reconnected to bridge on port %s", self.port)
        self._connected_before = True
        self._sock = sock
        return sock

    def _send(self, sock: socket.socket, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            sock.sendall(struct.pack(">I", len(body)) + body)
        except TimeoutError as exc:
            raise BridgeConnectionError("Timed out sending to Maya", timed_out=True) from exc
        except OSError as exc:
            raise BridgeConnectionError(f"Connection to Maya lost while sending: {exc}") from exc

    def _receive(self, sock: socket.socket) -> dict[str, Any]:
        (length,) = struct.unpack(">I", self._read_exactly(sock, 4))
        if length > MAX_FRAME:
            raise BridgeConnectionError(f"Bridge sent an oversized frame ({length} bytes)")
        body = self._read_exactly(sock, length)
        try:
            reply = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise BridgeConnectionError("Bridge sent a malformed reply") from exc
        if not isinstance(reply, dict):
            raise BridgeConnectionError("Bridge sent a malformed reply")
        return reply

    def _read_exactly(self, sock: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = sock.recv(min(remaining, 1 << 20))
            except TimeoutError as exc:
                raise BridgeConnectionError("No reply from Maya in time", timed_out=True) from exc
            except OSError as exc:
                raise BridgeConnectionError(f"Connection to Maya lost: {exc}") from exc
            if not chunk:
                raise BridgeConnectionError("Maya closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
