"""TCP and UART servers that speak the companion protocol.

One client is served at a time. When that client disconnects, the server
accepts the next one (reconnect-friendly). Incoming bytes are buffered
and split on newlines; a line that will not decode is discarded so the
link stays up.

UART uses only the standard library (os, termios, selectors) — 115200 8N1
raw, no flow control — so it can run on a Pi Zero 2 W without pyserial.
"""

from __future__ import annotations

import os
import selectors
import socket
import termios
import threading
import time
from typing import Callable

from link.handler import CompanionHandler
from link.protocol import Message, split_complete_lines, try_decode_line

# Planned serial settings from PROTOCOL.md.
UART_BAUD = 115200
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 7777
DEFAULT_SERIAL = "/dev/serial0"

# How long a session loop waits for socket/tty activity before flushing
# events queued by the tick thread.
SELECT_TIMEOUT = 0.1

_BAUD_MAP = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


class LinkClosed(Exception):
    """The current client disconnected or the tty disappeared."""


def configure_tty(fd: int, baud: int = UART_BAUD) -> None:
    """Put a file descriptor into raw 8N1 mode at the given baud, no flow control."""
    if baud not in _BAUD_MAP:
        raise ValueError(f"unsupported baud rate {baud}")
    flag = _BAUD_MAP[baud]
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = attrs
    iflag &= ~(
        termios.IGNBRK
        | termios.BRKINT
        | termios.PARMRK
        | termios.ISTRIP
        | termios.INLCR
        | termios.IGNCR
        | termios.ICRNL
        | termios.IXON
        | termios.IXOFF
        | termios.IXANY
    )
    oflag &= ~termios.OPOST
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
    if hasattr(termios, "CRTSCTS"):
        cflag &= ~termios.CRTSCTS
    cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
    cc[termios.VMIN] = 1
    cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, flag, flag, cc])
    termios.tcflush(fd, termios.TCIOFLUSH)


def _session_loop(
    read_fd: int,
    write_fn: Callable[[bytes], None],
    handler: CompanionHandler,
    lock: threading.Lock,
    stop: threading.Event,
    *,
    is_socket: bool = False,
    sock: socket.socket | None = None,
) -> None:
    """Read lines from read_fd, dispatch, write replies and queued events."""
    selector = selectors.DefaultSelector()
    selector.register(read_fd, selectors.EVENT_READ)
    buffer = b""
    try:
        while not stop.is_set():
            ready = selector.select(SELECT_TIMEOUT)
            incoming: list[Message] = []
            if ready:
                try:
                    chunk = os.read(read_fd, 4096) if not is_socket else sock.recv(4096)
                except (BlockingIOError, InterruptedError):
                    chunk = b""
                except OSError:
                    raise LinkClosed from None
                if not chunk:
                    raise LinkClosed
                buffer += chunk
                lines, buffer = split_complete_lines(buffer)
                for line in lines:
                    message = try_decode_line(line)
                    if message is not None:
                        incoming.append(message)
            outgoing: list[Message] = []
            with lock:
                for message in incoming:
                    outgoing.append(handler.handle(message))
                    outgoing.extend(handler.drain_events())
                # Tick thread may have queued events while we were waiting.
                outgoing.extend(handler.drain_events())
            for message in outgoing:
                write_fn(message.encode_bytes())
    finally:
        selector.close()
        with lock:
            handler.events_enabled = False
            handler.drain_events()


class TcpServer:
    """Accept one TCP client at a time on host:port (0 = ephemeral)."""

    def __init__(
        self,
        handler: CompanionHandler,
        lock: threading.Lock,
        host: str = DEFAULT_TCP_HOST,
        port: int = DEFAULT_TCP_PORT,
    ) -> None:
        self.handler = handler
        self.lock = lock
        self.host = host
        self._requested_port = port
        self._sock: socket.socket | None = None
        self.port = port

    def start(self) -> socket.socket:
        """Bind and listen. Returns the listening socket; fills in self.port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self._requested_port))
        sock.listen(1)
        sock.settimeout(0.5)
        self._sock = sock
        self.port = sock.getsockname()[1]
        return sock

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        """Accept clients until stop is set. Safe to call after start()."""
        stop = stop or threading.Event()
        if self._sock is None:
            self.start()
        assert self._sock is not None
        listen = self._sock
        while not stop.is_set():
            try:
                client, addr = listen.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop.is_set():
                    break
                raise
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                fd = client.fileno()

                def _write(data: bytes, _c: socket.socket = client) -> None:
                    _c.sendall(data)

                _session_loop(
                    fd,
                    _write,
                    self.handler,
                    self.lock,
                    stop,
                    is_socket=True,
                    sock=client,
                )
            except (LinkClosed, OSError, BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                client.close()
                # Address kept only so the accept loop is obviously using it.
                del addr

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class UartServer:
    """Serve the protocol on a serial tty (raw 115200 8N1, no flow control)."""

    def __init__(
        self,
        handler: CompanionHandler,
        lock: threading.Lock,
        path: str = DEFAULT_SERIAL,
        baud: int = UART_BAUD,
    ) -> None:
        self.handler = handler
        self.lock = lock
        self.path = path
        self.baud = baud

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            fd = None
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                configure_tty(fd, self.baud)

                def _write(data: bytes, _fd: int = fd) -> None:
                    view = memoryview(data)
                    while view:
                        try:
                            written = os.write(_fd, view)
                        except BlockingIOError:
                            time.sleep(0.01)
                            continue
                        view = view[written:]

                _session_loop(fd, _write, self.handler, self.lock, stop)
            except (LinkClosed, OSError):
                if stop.is_set():
                    break
                # Reconnect-friendly: if the tty disappears (USB unplug, hangup)
                # wait a moment and try to reopen it.
                time.sleep(0.5)
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass


def parse_host_port(value: str, default_host: str = DEFAULT_TCP_HOST, default_port: int = DEFAULT_TCP_PORT) -> tuple[str, int]:
    """Parse 'HOST:PORT', ':PORT', 'HOST', or 'PORT' into a (host, port) pair."""
    raw = (value or "").strip()
    if not raw:
        return default_host, default_port
    if raw.isdigit():
        return default_host, int(raw)
    if raw.startswith("["):
        # Optional IPv6 [host]:port — kept simple for the Pi's usual IPv4 use.
        host, _, port_s = raw[1:].partition("]")
        if port_s.startswith(":"):
            return host, int(port_s[1:])
        return host, default_port
    if ":" in raw:
        host, _, port_s = raw.rpartition(":")
        host = host or default_host
        return host, int(port_s) if port_s else default_port
    return raw, default_port
