"""Keep one SwiftCut window: later launches hand their file to the running instance.

The first instance listens on a per-user local socket. A second launch (e.g. "Trim with
SwiftCut" from Explorer) connects, sends {"open": path}, and exits immediately.
"""
from __future__ import annotations

import ctypes
import getpass
import json
import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"SwiftCut-{user}"


def send_to_running(payload: dict, name: str | None = None, timeout_ms: int = 400) -> bool:
    """Deliver `payload` to a running instance. Returns False if none is running."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    if sys.platform == "win32":
        try:  # let the running instance bring its window to the front
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass
    sock.write(json.dumps(payload).encode("utf-8") + b"\n")
    ok = sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return ok


class InstanceServer(QObject):
    """Receives messages from later launches."""

    message = Signal(dict)

    def __init__(self, name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.name = name or server_name()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._clients: dict[QLocalSocket, bytearray] = {}

    def listen(self) -> bool:
        if not self._server.listen(self.name):
            # A crashed instance can leave a stale endpoint behind; clear it and retry once.
            QLocalServer.removeServer(self.name)
            if not self._server.listen(self.name):
                log.warning("single-instance server unavailable: %s", self._server.errorString())
                return False
        return True

    def close(self) -> None:
        """Stop listening and dispose of any open client connections cleanly."""
        for sock in list(self._clients):
            self._drop(sock)
        self._server.close()

    def _on_connection(self) -> None:
        while True:
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            # Own the socket ourselves (not as a child of the server) so its lifetime is explicit.
            sock.setParent(None)
            self._clients[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: QTimer.singleShot(0, lambda: self._drop(s)))
            if sock.bytesAvailable():
                self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        buf = self._clients.get(sock)
        if buf is None:
            return
        buf.extend(bytes(sock.readAll()))
        if b"\n" not in buf:
            return
        line = bytes(buf).split(b"\n", 1)[0]
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            msg = None
        # Act on the message after this signal handler returns, never from inside it.
        QTimer.singleShot(0, lambda: self._drop(sock))
        if isinstance(msg, dict):
            QTimer.singleShot(0, lambda m=msg: self.message.emit(m))

    def _drop(self, sock: QLocalSocket) -> None:
        if self._clients.pop(sock, None) is None:
            return
        try:
            sock.readyRead.disconnect()
            sock.disconnected.disconnect()
        except (RuntimeError, TypeError):
            pass
        sock.abort()
        sock.deleteLater()
