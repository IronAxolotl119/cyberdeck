"""TCP and UART servers that speak the companion protocol.

The TCP server accepts several clients at once (a selector watches the
listening socket and every connected socket). Each client has its own
byte buffer and its own events flag. Replies go only to the client that
sent the request; pet.event is pushed to every client that opted in
during hello, at most one event per --event-interval (default 0.5 s).

When a client disconnects, the others stay up. Incoming bytes are split
on newlines; a line that will not decode is discarded so the link stays
up.

UART still serves one tty at a time (there is only one serial cable)
and uses the same pacing. It uses only the standard library (os,
termios, selectors) — 115200 8N1 raw, no flow control — so it can run
on a Pi Zero 2 W without pyserial.
"""

from __future__ import annotations

import os
import selectors
import socket
import termios
import threading
import time
from typing import Callable

from link.events import DEFAULT_EVENT_INTERVAL, EventPacer, enqueue_event
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
TCP_BACKLOG = 8

_BAUD_MAP = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}

Clock = Callable[[], float]


class LinkClosed(Exception):
    """The current client disconnected or the tty disappeared."""


class ClientSession:
    """Per-connection state for the multi-client TCP server."""

    def __init__(
        self,
        sock: socket.socket,
        event_interval: float,
        clock: Clock,
    ) -> None:
        self.sock = sock
        self.buffer = b""
        self.events_enabled = False
        self.event_queue: list[Message] = []
        self.pacer = EventPacer(event_interval, clock=clock)

    def fileno(self) -> int:
        return self.sock.fileno()


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


def _sendall_bytes(sock: socket.socket, data: bytes) -> None:
    """sendall that tolerates a non-blocking socket."""
    view = memoryview(data)
    while view:
        try:
            written = sock.send(view)
        except BlockingIOError:
            writable = selectors.DefaultSelector()
            writable.register(sock, selectors.EVENT_WRITE)
            ready = writable.select(0.5)
            writable.close()
            if not ready:
                raise LinkClosed
            continue
        if written == 0:
            raise LinkClosed
        view = view[written:]


def _write_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except BlockingIOError:
            time.sleep(0.01)
            continue
        if written == 0:
            raise LinkClosed
        view = view[written:]


def _sync_events_flag(handler: CompanionHandler, any_opted_in: bool) -> None:
    """Keep the handler generating events iff at least one client asked for them."""
    if any_opted_in:
        handler.events_enabled = True
    else:
        handler.events_enabled = False
        handler.drain_events()


def _link_changed(handler: CompanionHandler, active: bool) -> None:
    """Tell the pet the link went quiet (it gets lonely; hello = reunion)."""
    hook = getattr(handler, "link_changed", None)
    if hook is not None:
        hook(active)


def _fan_out(sessions: list[ClientSession], events: list[Message]) -> None:
    for event in events:
        for session in sessions:
            if session.events_enabled:
                enqueue_event(session.event_queue, event)


def _flush_paced(sessions: list[ClientSession], write_fn) -> None:
    """write_fn(session, message) sends one message to that session."""
    for session in sessions:
        for message in session.pacer.take(session.event_queue):
            write_fn(session, message)


def _select_timeout(sessions: list[ClientSession], default: float = SELECT_TIMEOUT) -> float:
    waits = [default]
    for session in sessions:
        if session.events_enabled and session.event_queue:
            waits.append(session.pacer.wait_time())
    return max(0.0, min(waits))


def _session_loop(
    read_fd: int,
    write_fn: Callable[[bytes], None],
    handler: CompanionHandler,
    lock: threading.Lock,
    stop: threading.Event,
    *,
    is_socket: bool = False,
    sock: socket.socket | None = None,
    event_interval: float = DEFAULT_EVENT_INTERVAL,
    clock: Clock | None = None,
) -> None:
    """Read lines from a single client (UART, or a lone TCP socket in tests)."""
    clock = clock or time.monotonic
    selector = selectors.DefaultSelector()
    selector.register(read_fd, selectors.EVENT_READ)
    buffer = b""
    events_enabled = False
    event_queue: list[Message] = []
    pacer = EventPacer(event_interval, clock=clock)
    try:
        while not stop.is_set():
            wait = default_wait = SELECT_TIMEOUT
            if events_enabled and event_queue:
                wait = min(default_wait, pacer.wait_time())
            ready = selector.select(wait)
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
            fresh: list[Message] = []
            with lock:
                for message in incoming:
                    reply = handler.handle(message)
                    if message.type == "hello":
                        events_enabled = (
                            reply.type == "hello.ok"
                            and bool((reply.payload or {}).get("events"))
                        )
                    outgoing.append(reply)
                    fresh.extend(handler.drain_events())
                fresh.extend(handler.drain_events())
                _sync_events_flag(handler, events_enabled)
            for reply in outgoing:
                write_fn(reply.encode_bytes())
            if events_enabled:
                for event in fresh:
                    enqueue_event(event_queue, event)
            else:
                event_queue.clear()
            for event in pacer.take(event_queue):
                write_fn(event.encode_bytes())
    finally:
        selector.close()
        with lock:
            # A lone UART client dropping should turn events off. The TCP
            # multi-client loop handles this itself and does not use this path.
            _sync_events_flag(handler, False)
            _link_changed(handler, False)


class TcpServer:
    """Accept many TCP clients at once on host:port (0 = ephemeral)."""

    def __init__(
        self,
        handler: CompanionHandler,
        lock: threading.Lock,
        host: str = DEFAULT_TCP_HOST,
        port: int = DEFAULT_TCP_PORT,
        event_interval: float = DEFAULT_EVENT_INTERVAL,
        clock: Clock | None = None,
    ) -> None:
        self.handler = handler
        self.lock = lock
        self.host = host
        self._requested_port = port
        self._sock: socket.socket | None = None
        self.port = port
        self.event_interval = max(0.0, float(event_interval))
        self.clock: Clock = clock or time.monotonic

    def start(self) -> socket.socket:
        """Bind and listen. Returns the listening socket; fills in self.port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self._requested_port))
        sock.listen(TCP_BACKLOG)
        sock.setblocking(False)
        self._sock = sock
        self.port = sock.getsockname()[1]
        return sock

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        """Accept and serve clients until stop is set. Safe to call after start()."""
        stop = stop or threading.Event()
        if self._sock is None:
            self.start()
        assert self._sock is not None
        listen = self._sock
        selector = selectors.DefaultSelector()
        selector.register(listen, selectors.EVENT_READ, data=None)
        sessions: dict[int, ClientSession] = {}
        try:
            while not stop.is_set():
                timeout = _select_timeout(list(sessions.values()))
                try:
                    ready = selector.select(timeout)
                except OSError:
                    if stop.is_set():
                        break
                    raise
                for key, _mask in ready:
                    if key.data is None:
                        self._accept(selector, sessions)
                    else:
                        session: ClientSession = key.data
                        try:
                            self._read_client(session, sessions)
                        except (LinkClosed, OSError, BrokenPipeError, ConnectionResetError):
                            self._drop_client(selector, sessions, session)
                # Tick-thread events, plus any still waiting on the pacer.
                self._drain_and_flush(selector, sessions)
        finally:
            selector.close()
            for session in list(sessions.values()):
                self._close_socket(session.sock)
            sessions.clear()
            with self.lock:
                _sync_events_flag(self.handler, False)

    def _accept(self, selector: selectors.DefaultSelector, sessions: dict[int, ClientSession]) -> None:
        assert self._sock is not None
        try:
            client, _addr = self._sock.accept()
        except (BlockingIOError, OSError):
            return
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.setblocking(False)
            session = ClientSession(client, self.event_interval, self.clock)
            selector.register(client, selectors.EVENT_READ, data=session)
            sessions[client.fileno()] = session
        except OSError:
            self._close_socket(client)

    def _read_client(self, session: ClientSession, sessions: dict[int, ClientSession]) -> None:
        try:
            chunk = session.sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            raise LinkClosed from None
        if not chunk:
            raise LinkClosed
        session.buffer += chunk
        lines, session.buffer = split_complete_lines(session.buffer)
        incoming: list[Message] = []
        for line in lines:
            message = try_decode_line(line)
            if message is not None:
                incoming.append(message)
        if not incoming:
            return
        replies: list[Message] = []
        fresh: list[Message] = []
        with self.lock:
            for message in incoming:
                reply = self.handler.handle(message)
                if message.type == "hello":
                    session.events_enabled = (
                        reply.type == "hello.ok"
                        and bool((reply.payload or {}).get("events"))
                    )
                replies.append(reply)
                fresh.extend(self.handler.drain_events())
            fresh.extend(self.handler.drain_events())
            _sync_events_flag(self.handler, any(s.events_enabled for s in sessions.values()))
        for reply in replies:
            _sendall_bytes(session.sock, reply.encode_bytes())
        _fan_out(list(sessions.values()), fresh)

    def _drain_and_flush(
        self,
        selector: selectors.DefaultSelector,
        sessions: dict[int, ClientSession],
    ) -> None:
        with self.lock:
            fresh = self.handler.drain_events()
        _fan_out(list(sessions.values()), fresh)
        dead: list[ClientSession] = []
        for session in list(sessions.values()):
            try:
                for message in session.pacer.take(session.event_queue):
                    _sendall_bytes(session.sock, message.encode_bytes())
            except (LinkClosed, OSError, BrokenPipeError, ConnectionResetError):
                dead.append(session)
        for session in dead:
            self._drop_client(selector, sessions, session)

    def _drop_client(
        self,
        selector: selectors.DefaultSelector,
        sessions: dict[int, ClientSession],
        session: ClientSession,
    ) -> None:
        fd = -1
        try:
            fd = session.sock.fileno()
        except OSError:
            fd = -1
        try:
            selector.unregister(session.sock)
        except (OSError, KeyError, ValueError):
            pass
        self._close_socket(session.sock)
        sessions.pop(fd, None)
        # Also drop by identity if fileno was already invalid.
        for key, item in list(sessions.items()):
            if item is session:
                sessions.pop(key, None)
        with self.lock:
            _sync_events_flag(self.handler, any(s.events_enabled for s in sessions.values()))
            if not sessions:
                _link_changed(self.handler, False)

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

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
        event_interval: float = DEFAULT_EVENT_INTERVAL,
        clock: Clock | None = None,
    ) -> None:
        self.handler = handler
        self.lock = lock
        self.path = path
        self.baud = baud
        self.event_interval = max(0.0, float(event_interval))
        self.clock: Clock = clock or time.monotonic

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            fd = None
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                configure_tty(fd, self.baud)

                def _write(data: bytes, _fd: int = fd) -> None:
                    _write_fd(_fd, data)

                _session_loop(
                    fd,
                    _write,
                    self.handler,
                    self.lock,
                    stop,
                    event_interval=self.event_interval,
                    clock=self.clock,
                )
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
