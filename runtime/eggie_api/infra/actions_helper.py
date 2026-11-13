"""Client for the root `eggie-actions` helper's unix socket: one JSON line each way,
then, after an attach, `type:u8 | length:u32 | payload` frames."""
from __future__ import annotations

import json
import socket
import struct
import threading
from pathlib import Path

FRAME_DATA, FRAME_RESIZE = 0x00, 0x01
MAX_FRAME = 1024 * 1024
WRITE_CHUNK = 64 * 1024
MAX_REPLY = 64 * 1024


class HelperUnavailable(Exception):
    """Nothing answered on the socket, or it stopped answering."""


class HelperError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Terminal:
    def __init__(self, sock: socket.socket, reader):
        self._sock = sock
        self._reader = reader
        self._send = threading.Lock()
        self._closed = False

    def read(self) -> bytes:
        """The next terminal bytes; `b""` once the helper has hung up."""
        while True:
            try:
                header = self._reader.read(5)
                if len(header) < 5:
                    return b""
                kind, length = struct.unpack("!BI", header)
                if length > MAX_FRAME:
                    return b""
                payload = self._reader.read(length)
            except (OSError, ValueError):
                return b""
            if len(payload) < length:
                return b""
            if kind == FRAME_DATA and payload:
                return payload

    def _frame(self, kind: int, payload: bytes) -> None:
        with self._send:
            self._sock.sendall(struct.pack("!BI", kind, len(payload)) + payload)

    def write(self, data: bytes) -> None:
        for at in range(0, len(data), WRITE_CHUNK):
            self._frame(FRAME_DATA, data[at:at + WRITE_CHUNK])

    def resize(self, cols: int, rows: int) -> None:
        self._frame(FRAME_RESIZE, struct.pack("!HH", cols, rows))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._reader.close()
        self._sock.close()


class ActionsHelper:
    def __init__(self, socket_path: Path, timeout: float = 10.0):
        self._path = str(socket_path)
        self._timeout = timeout

    def _open(self, request: dict):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        reader = sock.makefile("rb")
        try:
            try:
                sock.connect(self._path)
                sock.sendall(json.dumps(request).encode() + b"\n")
                line = reader.readline(MAX_REPLY)
            except OSError as e:
                raise HelperUnavailable(f"eggie-actions did not answer: {e}") from e
            if not line:
                raise HelperUnavailable("eggie-actions closed the connection without answering")
            try:
                reply = json.loads(line)
            except ValueError:
                reply = None
            if not isinstance(reply, dict):
                raise HelperError("failed", "eggie-actions sent an answer Eggie can't read")
            if reply.get("ok") is not True:
                raise HelperError(str(reply.get("code") or "failed"),
                                  str(reply.get("message") or "eggie-actions refused"))
        except BaseException:
            reader.close()
            sock.close()
            raise
        return sock, reader, reply

    def _ask(self, request: dict) -> dict:
        sock, reader, reply = self._open(request)
        reader.close()
        sock.close()
        return reply

    def start(self, project: str, action: str, command: str, *, log: bool) -> dict:
        return self._ask({"op": "start", "project": project, "action": action,
                          "command": command, "log": log}).get("run")

    def stop(self, project: str, action: str) -> dict | None:
        return self._ask({"op": "stop", "project": project, "action": action}).get("run")

    def status(self, project: str) -> dict[str, dict]:
        runs = self._ask({"op": "status", "project": project}).get("runs")
        return runs if isinstance(runs, dict) else {}

    def stop_project(self, project: str) -> None:
        self._ask({"op": "stop-project", "project": project})

    def attach(self, project: str, action: str, cols: int, rows: int) -> Terminal:
        sock, reader, _reply = self._open({"op": "attach", "project": project,
                                           "action": action, "cols": cols, "rows": rows})
        # A terminal sits idle for as long as its user does.
        sock.settimeout(None)
        return Terminal(sock, reader)
