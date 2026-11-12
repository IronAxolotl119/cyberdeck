"""link.py — real connections to the companion pet program.

The deck and the pet talk with newline-delimited JSON (docs/PROTOCOL.md).
This file carries those lines over two kinds of wire:

  * TcpTransport     — a TCP socket. The companion is the server
                       (default 127.0.0.1:7777). Use this to run both
                       programs on one computer.
  * SerialTransport  — a UART / USB-serial device (default /dev/serial0,
                       115200 8N1). Plain stdlib termios + os, or
                       pyserial if it is installed and you ask for it.

StreamCompanionLink sits on top of either one and does the hard parts on
a background thread, so the screen never freezes waiting for the pet:

  * connect, say hello (asking for pushed events), and if the companion
    goes away, keep retrying with a growing wait (0.5 s, 1 s, 2 s … max)
  * match every reply to its request by id; give up after `timeout`
  * collect pet.event messages the companion pushes, for the UI to show
  * poll pet.status every few seconds, which keeps a fresh copy of the
    pet for the panels (pet_snapshot) and doubles as a heartbeat

Settings come from config/hardware.ini [link]; app.py lets command-line
flags override them (--link tcp --port 7777, --link serial --device …).
"""

from __future__ import annotations

import collections
import errno
import os
import select
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, Callable

from cyberdeck.companion import CompanionError, CompanionLink, SimCompanionLink, os_serial_default
from cyberdeck.config import Settings
from cyberdeck.protocol import (
    PROTO_VERSION,
    TYPE_HELLO,
    TYPE_HELLO_OK,
    TYPE_PET_EVENT,
    TYPE_PET_STATUS,
    Message,
    ProtocolError,
    decode,
)

MAX_LINE = 64 * 1024   # a line longer than this is garbage; throw it away


# --- settings -----------------------------------------------------------------

@dataclass(frozen=True)
class LinkSettings:
    """config/hardware.ini [link], after command-line overrides."""

    mode: str = "sim"              # sim | tcp | serial
    host: str = "127.0.0.1"
    port: int = 7777
    device: str = "/dev/serial0"
    baud: int = 115200
    timeout: float = 1.5           # seconds to wait for a reply
    connect_wait: float = 2.0      # at startup, wait this long for the first hello
    retry_max: float = 10.0        # longest wait between reconnect attempts
    poll_seconds: float = 3.0      # pet.status poll / heartbeat while linked
    events: bool = True            # ask the companion to push pet.event
    serial_driver: str = "auto"    # auto | stdlib | pyserial

    def describe(self) -> str:
        if self.mode == "tcp":
            return f"TCP {self.host}:{self.port}"
        if self.mode == "serial":
            return f"UART {self.device} {self.baud} 8N1"
        return "in-process sim"


def load_link_settings(s: Settings) -> LinkSettings:
    d = LinkSettings()
    return LinkSettings(
        mode=s.get_str("link", "mode", d.mode).lower(),
        host=s.get_str("link", "host", d.host),
        port=s.get_int("link", "port", d.port),
        device=s.get_str("link", "device", d.device),
        baud=s.get_int("link", "baud", d.baud),
        timeout=max(0.1, s.get_float("link", "timeout", d.timeout)),
        connect_wait=max(0.0, s.get_float("link", "connect_wait", d.connect_wait)),
        retry_max=max(0.5, s.get_float("link", "retry_max", d.retry_max)),
        poll_seconds=max(0.5, s.get_float("link", "poll_seconds", d.poll_seconds)),
        events=s.get_bool("link", "events", d.events),
        serial_driver=s.get_str("link", "serial_driver", d.serial_driver).lower(),
    )


def with_overrides(ls: LinkSettings, mode: str | None = None, host: str | None = None,
                   port: int | None = None, device: str | None = None, baud: int | None = None) -> LinkSettings:
    """Apply command-line flags / environment on top of the config file."""
    changes: dict[str, Any] = {}
    if mode:
        changes["mode"] = mode.lower()
    if host:
        changes["host"] = host
    if port:
        changes["port"] = int(port)
    if device:
        changes["device"] = device
    if baud:
        changes["baud"] = int(baud)
    return replace(ls, **changes)


# --- transports -------------------------------------------------------------------

class Transport(ABC):
    """A byte pipe the link thread can select() on."""

    label = "?"

    @abstractmethod
    def open(self) -> None:
        """Connect / open the device. Raises OSError on failure."""

    @abstractmethod
    def close(self) -> None:
        """Release it. Safe to call twice."""

    @abstractmethod
    def fileno(self) -> int:
        """For select()."""

    @abstractmethod
    def read(self) -> bytes | None:
        """Bytes available now; None = nothing yet; b'' = the other end closed."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Send all of data (raises OSError if the wire is gone)."""


class TcpTransport(Transport):
    def __init__(self, host: str, port: int, connect_timeout: float = 3.0) -> None:
        self.host, self.port, self.connect_timeout = host, port, connect_timeout
        self.sock: socket.socket | None = None
        self.label = f"TCP {host}:{port}"

    def open(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(5.0)  # bounds sendall(); reads only happen after select()
        self.sock = sock

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def fileno(self) -> int:
        return self.sock.fileno() if self.sock else -1

    def read(self) -> bytes | None:
        assert self.sock is not None
        try:
            return self.sock.recv(4096)
        except (BlockingIOError, socket.timeout):
            return None

    def write(self, data: bytes) -> None:
        if self.sock is None:
            raise OSError("not connected")
        self.sock.sendall(data)


def configure_tty(fd: int, baud: int) -> None:
    """Raw mode, 8 data bits, no parity, 1 stop bit, no flow control."""
    import termios

    speed = getattr(termios, f"B{baud}", None)
    if speed is None:
        raise OSError(f"unsupported baud rate {baud}")
    iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = termios.tcgetattr(fd)
    iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR
               | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF | termios.IXANY)
    oflag &= ~termios.OPOST                                  # send bytes exactly as written
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
    cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
    cc = list(cc)
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])
    termios.tcflush(fd, termios.TCIOFLUSH)


class SerialTransport(Transport):
    """A tty device. stdlib by default; pyserial when driver says so (or auto + installed)."""

    def __init__(self, device: str, baud: int = 115200, driver: str = "auto") -> None:
        self.device, self.baud, self.driver = device, baud, driver
        self.fd: int | None = None
        self.port: Any = None      # pyserial object, if used
        self.label = f"UART {device}"

    def _want_pyserial(self) -> bool:
        if self.driver == "stdlib":
            return False
        try:
            import serial  # noqa: F401  (optional dependency)
            return True
        except ImportError:
            if self.driver == "pyserial":
                raise OSError("serial_driver = pyserial but pyserial is not installed")
            return False

    def open(self) -> None:
        if self._want_pyserial():
            import serial

            self.port = serial.Serial(self.device, self.baud, bytesize=8, parity="N", stopbits=1,
                                      timeout=0, write_timeout=2, exclusive=True)
            self.fd = self.port.fileno()
            return
        fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            configure_tty(fd, self.baud)
        except Exception:
            os.close(fd)
            raise
        self.fd = fd

    def close(self) -> None:
        try:
            if self.port is not None:
                self.port.close()
            elif self.fd is not None:
                os.close(self.fd)
        except OSError:
            pass
        self.port, self.fd = None, None

    def fileno(self) -> int:
        return -1 if self.fd is None else self.fd

    def read(self) -> bytes | None:
        if self.port is not None:
            return self.port.read(4096) or None
        assert self.fd is not None
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return None
        # A tty read of 0 bytes after select() said "readable" means hang-up.
        return data if data else b""

    def write(self, data: bytes) -> None:
        if self.port is not None:
            self.port.write(data)
            return
        if self.fd is None:
            raise OSError("device not open")
        view = memoryview(data)
        deadline = time.monotonic() + 2.0
        while view:
            try:
                sent = os.write(self.fd, view)
                view = view[sent:]
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise OSError(errno.ETIMEDOUT, "serial write timed out")
                select.select([], [self.fd], [], 0.1)


# --- the link -------------------------------------------------------------------------

class _Waiter:
    """One caller waiting for the reply to its request."""

    __slots__ = ("done", "reply", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.reply: Message | None = None
        self.error = ""


class StreamCompanionLink(CompanionLink):
    """Protocol over any Transport, with a background reader thread."""

    def __init__(self, make_transport: Callable[[], Transport], settings: LinkSettings,
                 short_label: str = "") -> None:
        self.make_transport = make_transport
        self.settings = settings
        self.short_label = short_label or settings.describe()
        self.state = "down"            # down | handshake | up
        self.reason = "not started"
        self.hello: dict[str, Any] = {}
        self.events_enabled = False
        self.stats = collections.Counter()   # sent, received, bad_lines, stray, reconnects, timeouts
        self._transport: Transport | None = None
        self._pending: dict[str, _Waiter] = {}
        self._events: collections.deque[Message] = collections.deque(maxlen=200)
        self._snapshot: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._up = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = 0.5
        self._next_attempt = 0.0
        self._hello_id = ""
        self._hello_deadline = 0.0
        self._poll_id = ""
        self._last_poll = 0.0
        self._last_rx = 0.0

    # -- CompanionLink API --------------------------------------------------------

    def connect(self) -> None:
        """Start the background thread; wait briefly for the first hello."""
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="companion-link", daemon=True)
            self._thread.start()
        if self.settings.connect_wait > 0:
            self._up.wait(self.settings.connect_wait)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._drop("closed", retry=False)

    def is_linked(self) -> bool:
        return self.state == "up"

    def link_label(self) -> str:
        return f"LINK {'UP' if self.is_linked() else 'DOWN'} · {self.short_label}"

    def link_detail(self) -> str:
        """One line for `status` / `link`: where, and why it is down."""
        if self.is_linked():
            src = self.hello.get("source", "companion")
            return f"{self.settings.describe()} · {src} · events {'on' if self.events_enabled else 'off'}"
        wait = max(0.0, self._next_attempt - time.monotonic())
        return f"{self.settings.describe()} · down: {self.reason} · retry in {wait:.0f}s"

    def request(self, typ: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> Message:
        """Send and wait for the reply with the same id (raises CompanionError)."""
        if not self.is_linked():
            raise CompanionError(f"link down ({self.reason})")
        message = Message.request(typ, payload)
        waiter = _Waiter()
        with self._lock:
            self._pending[message.id] = waiter
        try:
            self._send(message)
        except OSError as exc:
            with self._lock:
                self._pending.pop(message.id, None)
            self._drop(f"write failed: {exc}")
            raise CompanionError(f"link lost while sending ({exc})") from exc
        wait = self.settings.timeout if timeout is None else timeout
        if not waiter.done.wait(wait):
            with self._lock:
                self._pending.pop(message.id, None)
            self.stats["timeouts"] += 1
            raise CompanionError(f"no reply to {typ} within {wait:.1f}s")
        if waiter.reply is None:
            raise CompanionError(waiter.error or "link lost")
        return waiter.reply

    def drain_events(self) -> list[Message]:
        out = []
        while self._events:
            out.append(self._events.popleft())
        return out

    def pet_snapshot(self) -> dict[str, Any] | None:
        snap = self._snapshot
        if snap and self.is_linked() and time.monotonic() - snap[0] < self.settings.poll_seconds * 3:
            return snap[1]
        return None

    # -- background thread ---------------------------------------------------------

    def _run(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            now = time.monotonic()
            if self._transport is None:
                if now < self._next_attempt:
                    self._stop.wait(min(0.2, self._next_attempt - now))
                    continue
                buffer = b""
                self._open_and_hello()
                continue
            fd = self._transport.fileno()
            try:
                readable, _, _ = select.select([fd], [], [], 0.2)
                data = self._transport.read() if readable else None
            except (OSError, ValueError) as exc:
                self._drop(f"read failed: {exc}")
                continue
            if data == b"":
                self._drop("companion closed the connection")
                continue
            if data:
                buffer += data
                *lines, buffer = buffer.split(b"\n")
                if len(buffer) > MAX_LINE:
                    buffer = b""
                    self.stats["bad_lines"] += 1
                for line in lines:
                    self._handle_line(line)
            self._housekeeping(time.monotonic())

    def _open_and_hello(self) -> None:
        transport = self.make_transport()
        try:
            transport.open()
        except (OSError, ValueError) as exc:
            transport.close()
            self.reason = _short_error(exc)
            self._schedule_retry()
            return
        self._transport = transport
        self.state = "handshake"
        self.stats["connects"] += 1
        hello = Message.request(TYPE_HELLO, {"role": "main", "proto": PROTO_VERSION, "events": self.settings.events})
        self._hello_id = hello.id
        self._hello_deadline = time.monotonic() + max(2.0, self.settings.timeout * 2)
        try:
            self._send(hello)
        except OSError as exc:
            self._drop(f"hello failed: {exc}")

    def _handle_line(self, line: bytes) -> None:
        if not line.strip(b"\r\x00 \t"):
            return
        try:
            message = decode(line)
        except ProtocolError:
            self.stats["bad_lines"] += 1   # noise on the wire: skip it, keep listening
            return
        self.stats["received"] += 1
        self._last_rx = time.monotonic()
        pet = (message.payload or {}).get("pet")
        if isinstance(pet, dict):
            # pet.status sends evolution beside the pet; keep the newest one with it.
            evolution = (message.payload or {}).get("evolution")
            if not isinstance(evolution, dict) and "evolution" not in pet and self._snapshot:
                evolution = self._snapshot[1].get("evolution")
            if isinstance(evolution, dict):
                pet = dict(pet, evolution=evolution)
            self._snapshot = (time.monotonic(), pet)
        if message.id == self._hello_id and self.state == "handshake":
            if message.type == TYPE_HELLO_OK:
                self.hello = dict(message.payload or {})
                self.events_enabled = self.settings.events and self.hello.get("events") is True
                self.state, self.reason, self._backoff = "up", "", 0.5
                self._last_poll = 0.0
                self._up.set()
            else:
                err = (message.payload or {}).get("message", message.type)
                self._drop(f"hello refused: {err}")
            return
        if message.type == TYPE_PET_EVENT:
            self._events.append(message)
            return
        if message.id == self._poll_id:
            self._poll_id = ""
            return
        with self._lock:
            waiter = self._pending.pop(message.id, None)
        if waiter is None:
            self.stats["stray"] += 1   # a late reply after its caller gave up
            return
        waiter.reply = message
        waiter.done.set()

    def _housekeeping(self, now: float) -> None:
        """Handshake timeout, pet.status poll, and the 'silent companion' check."""
        if self.state == "handshake" and now > self._hello_deadline:
            self._drop("no answer to hello")
            return
        if self.state != "up":
            return
        if self._poll_id and now - self._last_poll > max(self.settings.timeout * 2, self.settings.poll_seconds * 2):
            self._drop("companion stopped answering")
            return
        if not self._poll_id and now - self._last_poll >= self.settings.poll_seconds:
            poll = Message.request(TYPE_PET_STATUS, {})
            self._poll_id, self._last_poll = poll.id, now
            try:
                self._send(poll)
            except OSError as exc:
                self._drop(f"write failed: {exc}")

    def _send(self, message: Message) -> None:
        transport = self._transport
        if transport is None:
            raise OSError("not connected")
        with self._write_lock:
            transport.write(message.encode_bytes())
        self.stats["sent"] += 1

    def _drop(self, reason: str, retry: bool = True) -> None:
        """Close the wire, fail everyone waiting, and plan the next attempt."""
        was_up = self.state == "up"
        self.state, self.reason = "down", reason
        self._up.clear()
        self._poll_id = ""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        with self._lock:
            pending, self._pending = self._pending, {}
        for waiter in pending.values():
            waiter.error = f"link lost ({reason})"
            waiter.done.set()
        if was_up:
            self.stats["reconnects"] += 1
        if retry:
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        self._next_attempt = time.monotonic() + self._backoff
        self._backoff = min(self.settings.retry_max, self._backoff * 2)


def _short_error(exc: BaseException) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused (is the companion running?)"
    if isinstance(exc, FileNotFoundError):
        return f"no such device {exc.filename or ''}".strip()
    if isinstance(exc, PermissionError):
        return "permission denied (add yourself to the dialout group)"
    if isinstance(exc, socket.timeout):
        return "connect timed out"
    return str(exc) or exc.__class__.__name__


# --- factory ----------------------------------------------------------------------------

def open_link(ls: LinkSettings) -> CompanionLink:
    """Build the link chosen in hardware.ini / on the command line."""
    if ls.mode == "sim":
        return SimCompanionLink()
    if ls.mode == "tcp":
        return StreamCompanionLink(lambda: TcpTransport(ls.host, ls.port, max(1.0, ls.timeout * 2)), ls,
                                   f"TCP {ls.port}")
    if ls.mode == "serial":
        device = ls.device or os_serial_default()
        return StreamCompanionLink(lambda: SerialTransport(device, ls.baud, ls.serial_driver), ls,
                                   f"UART {os.path.basename(device)}")
    raise ValueError(f"unknown link mode {ls.mode!r}; expected sim, tcp, or serial")


__all__ = ["LinkSettings", "load_link_settings", "with_overrides", "Transport", "TcpTransport",
           "SerialTransport", "configure_tty", "StreamCompanionLink", "open_link"]
