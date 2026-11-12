"""TCP round-trip on an ephemeral port, plus a socat PTY test when available."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from link.events import event_kind
from link.handler import CompanionHandler, need_hunger
from link.protocol import Message, split_complete_lines, try_decode_line
from link.transport import TcpServer, UartServer
from pet.creature import Creature
from pet.stats import Stats
from save import load_creature, save_creature


def _send_recv(sock: socket.socket, typ: str, payload: dict | None = None, timeout: float = 2.0) -> Message:
    req = Message.request(typ, payload or {})
    sock.settimeout(timeout)
    sock.sendall(req.encode_bytes())
    buffer = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buffer += chunk
        lines, buffer = split_complete_lines(buffer)
        for line in lines:
            msg = try_decode_line(line)
            if msg is not None and msg.id == req.id:
                return msg
    raise AssertionError(f"no reply for {typ} id={req.id}")



class OpenptyUartTests(unittest.TestCase):
    """Exercise the termios UART server over a stdlib PTY (no socat required)."""

    def test_uart_round_trip_over_openpty(self) -> None:
        master_fd, slave_fd = os.openpty()
        slave_path = os.ttyname(slave_fd)
        # The server will reopen the slave path; we can close our extra fd.
        os.close(slave_fd)
        stop = threading.Event()
        handler = CompanionHandler(Creature(stats=Stats(hunger=50, mood=60, energy=80)))
        lock = threading.Lock()
        server = UartServer(handler, lock, path=slave_path)
        thread = threading.Thread(target=server.serve_forever, args=(stop,), daemon=True)
        thread.start()
        time.sleep(0.2)
        try:
            from link.transport import configure_tty

            configure_tty(master_fd, 115200)

            def converse(typ: str, payload: dict | None = None) -> Message:
                req = Message.request(typ, payload or {})
                os.write(master_fd, req.encode_bytes())
                buffer = b""
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    import selectors

                    sel = selectors.DefaultSelector()
                    sel.register(master_fd, selectors.EVENT_READ)
                    ready = sel.select(0.3)
                    sel.close()
                    if not ready:
                        continue
                    try:
                        chunk = os.read(master_fd, 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        continue
                    buffer += chunk
                    lines, buffer = split_complete_lines(buffer)
                    for line in lines:
                        msg = try_decode_line(line)
                        if msg is not None and msg.id == req.id:
                            return msg
                raise AssertionError(f"no PTY reply for {typ}")

            hello = converse("hello", {"role": "main", "proto": 1})
            self.assertEqual(hello.type, "hello.ok")
            self.assertEqual(hello.payload["source"], "sim")
            pong = converse("ping", {})
            self.assertEqual(pong.type, "pong")
            feed = converse("pet.feed", {"item": "ration"})
            self.assertEqual(feed.type, "pet.feed.ok")
            self.assertEqual(feed.payload["item"], "ration")
        finally:
            stop.set()
            try:
                os.close(master_fd)
            except OSError:
                pass
            thread.join(timeout=2)


class TcpRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = CompanionHandler(Creature(name="Pulse", stats=Stats(hunger=50, mood=60, energy=80)))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.server = TcpServer(self.handler, self.lock, host="127.0.0.1", port=0)
        self.server.start()
        self.port = self.server.port
        self.thread = threading.Thread(target=self.server.serve_forever, args=(self.stop,), daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.stop.set()
        self.server.close()
        self.thread.join(timeout=2)

    def test_hello_ping_status_feed_over_tcp(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as sock:
            hello = _send_recv(sock, "hello", {"role": "main", "proto": 1})
            self.assertEqual(hello.type, "hello.ok")
            self.assertEqual(hello.payload["pet"], "PULSE")
            self.assertEqual(hello.payload["proto"], 1)

            pong = _send_recv(sock, "ping", {})
            self.assertEqual(pong.type, "pong")
            self.assertTrue(pong.payload["ok"])

            status = _send_recv(sock, "pet.status", {})
            self.assertEqual(status.type, "pet.status.ok")
            self.assertEqual(status.payload["pet"]["name"], "PULSE")

            before = status.payload["pet"]["hunger"]
            feed = _send_recv(sock, "pet.feed", {"item": "ration"})
            self.assertEqual(feed.type, "pet.feed.ok")
            self.assertLess(feed.payload["pet"]["hunger"], before)

    def test_unknown_type_and_reconnect(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as sock:
            err = _send_recv(sock, "explode", {})
            self.assertEqual(err.type, "error")
            self.assertEqual(err.payload["code"], "unknown_type")
        # Reconnect-friendly: a new client is accepted after the first hangs up.
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as sock:
            hello = _send_recv(sock, "hello", {"role": "main", "proto": 1})
            self.assertEqual(hello.type, "hello.ok")

    def test_events_arrive_after_opt_in(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as sock:
            hello = _send_recv(sock, "hello", {"role": "main", "proto": 1, "events": True})
            self.assertTrue(hello.payload.get("events") is True)
            gift = Message.request("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}})
            sock.sendall(gift.encode_bytes())
            sock.settimeout(2)
            buffer = b""
            seen = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk
                lines, buffer = split_complete_lines(buffer)
                for line in lines:
                    msg = try_decode_line(line)
                    if msg is not None:
                        seen.append(msg)
                kinds = [m.payload.get("event") for m in seen if m.type == "pet.event"]
                if any(k in {"evolve_soon", "evolve_choice"} for k in kinds) and any(m.id == gift.id for m in seen):
                    break
            self.assertTrue(any(m.id == gift.id and m.type == "pet.gift.ok" for m in seen))
            kinds = [m.payload.get("event") for m in seen if m.type == "pet.event"]
            self.assertTrue(
                any(k in {"evolve_soon", "evolve_choice"} for k in kinds),
                f"expected evolve_soon/evolve_choice, got {kinds}",
            )
            # Confirming the choice actually evolves and pushes evolving + evolved.
            evoreq = Message.request("pet.evolve", {"species": "Wavling"})
            sock.sendall(evoreq.encode_bytes())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk
                lines, buffer = split_complete_lines(buffer)
                for line in lines:
                    msg = try_decode_line(line)
                    if msg is not None:
                        seen.append(msg)
                if any(m.type == "pet.event" and m.payload.get("event") == "evolved" for m in seen):
                    break
            self.assertTrue(any(m.type == "pet.event" and m.payload.get("event") == "evolved" for m in seen))


def _parse_socat_ptys(stderr: str) -> tuple[str, str]:
    paths = []
    for line in stderr.splitlines():
        # socat -d -d prints: "N PTY is /dev/pts/N"
        if "PTY is " in line:
            paths.append(line.rsplit(" ", 1)[-1].strip())
    if len(paths) < 2:
        raise RuntimeError(f"could not parse socat PTY paths from:\n{stderr}")
    return paths[0], paths[1]


@unittest.skipUnless(shutil.which("socat"), "socat is not installed")
class SocatPtyTests(unittest.TestCase):
    def test_uart_round_trip_over_pty_pair(self) -> None:
        proc = subprocess.Popen(
            ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        server_thread = None
        stop = threading.Event()
        client_fd = None
        try:
            # socat writes the PTY paths to stderr and then runs; we need to
            # read those two lines without waiting for the process to exit.
            stderr_lines = ""
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and stderr_lines.count("PTY is ") < 2:
                # The pipe is not line-buffered from our side; read incrementally
                # by setting the fd non-blocking via a short select in a thread
                # is messy, so poll the file in small sleeps after a blocking
                # peek is not available. Use os.read on the stderr fd.
                if proc.stderr is None:
                    break
                import selectors

                sel = selectors.DefaultSelector()
                sel.register(proc.stderr, selectors.EVENT_READ)
                ready = sel.select(0.2)
                sel.close()
                if not ready:
                    continue
                chunk = proc.stderr.read(256)
                if not chunk:
                    continue
                stderr_lines += chunk
            server_pty, client_pty = _parse_socat_ptys(stderr_lines)

            handler = CompanionHandler(Creature(stats=Stats(hunger=50, mood=60, energy=80)))
            lock = threading.Lock()
            server = UartServer(handler, lock, path=server_pty)
            server_thread = threading.Thread(target=server.serve_forever, args=(stop,), daemon=True)
            server_thread.start()
            # Give the server a moment to open its end.
            time.sleep(0.2)

            client_fd = os.open(client_pty, os.O_RDWR | os.O_NOCTTY)
            # Termios raw so our writes are not cooked on the way out.
            from link.transport import configure_tty

            configure_tty(client_fd, 115200)

            def converse(typ: str, payload: dict | None = None) -> Message:
                req = Message.request(typ, payload or {})
                os.write(client_fd, req.encode_bytes())
                buffer = b""
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    import selectors

                    sel = selectors.DefaultSelector()
                    sel.register(client_fd, selectors.EVENT_READ)
                    ready = sel.select(0.3)
                    sel.close()
                    if not ready:
                        continue
                    try:
                        chunk = os.read(client_fd, 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        continue
                    buffer += chunk
                    lines, buffer = split_complete_lines(buffer)
                    for line in lines:
                        msg = try_decode_line(line)
                        if msg is not None and msg.id == req.id:
                            return msg
                raise AssertionError(f"no UART reply for {typ}")

            hello = converse("hello", {"role": "main", "proto": 1})
            self.assertEqual(hello.type, "hello.ok")
            self.assertEqual(hello.payload["pet"], "PULSE")
            pong = converse("ping", {})
            self.assertEqual(pong.type, "pong")
            status = converse("pet.status", {})
            self.assertEqual(status.type, "pet.status.ok")
            self.assertEqual(status.payload["pet"]["species"], "Chirplet")
        finally:
            stop.set()
            if client_fd is not None:
                try:
                    os.close(client_fd)
                except OSError:
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            if server_thread is not None:
                server_thread.join(timeout=2)



def _collect_until(sock: socket.socket, predicate, timeout: float = 2.0):
    """Read messages from sock until predicate(messages) is true or we time out."""
    sock.settimeout(timeout)
    buffer = b""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buffer += chunk
        lines, buffer = split_complete_lines(buffer)
        for line in lines:
            msg = try_decode_line(line)
            if msg is not None:
                seen.append(msg)
        if predicate(seen):
            break
    return seen


class MultiClientTcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = CompanionHandler(Creature(name="Pulse", stats=Stats(hunger=50, mood=70, energy=80)))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        # interval 0 so events flush in the same select turn as the reply.
        self.server = TcpServer(self.handler, self.lock, host="127.0.0.1", port=0, event_interval=0.0)
        self.server.start()
        self.port = self.server.port
        self.thread = threading.Thread(target=self.server.serve_forever, args=(self.stop,), daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.stop.set()
        self.server.close()
        self.thread.join(timeout=2)

    def test_two_clients_at_once_and_replies_stay_private(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as alpha, \
             socket.create_connection(("127.0.0.1", self.port), timeout=2) as beta:
            hello_a = _send_recv(alpha, "hello", {"role": "main", "proto": 1, "events": True})
            hello_b = _send_recv(beta, "hello", {"role": "main", "proto": 1, "events": False})
            self.assertEqual(hello_a.type, "hello.ok")
            self.assertTrue(hello_a.payload.get("events") is True)
            self.assertEqual(hello_b.type, "hello.ok")
            self.assertFalse(bool(hello_b.payload.get("events")))

            pong = _send_recv(beta, "ping", {})
            self.assertEqual(pong.type, "pong")
            # Alpha must not receive beta's pong (or any reply).
            alpha.settimeout(0.2)
            try:
                stray = alpha.recv(4096)
            except socket.timeout:
                stray = b""
            self.assertEqual(stray, b"")

            # Both can ask for status at the same time.
            status_a = _send_recv(alpha, "pet.status", {})
            status_b = _send_recv(beta, "pet.status", {})
            self.assertEqual(status_a.payload["pet"]["species"], "Chirplet")
            self.assertEqual(status_b.payload["pet"]["species"], "Chirplet")

    def test_events_go_to_every_opted_in_client_only(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as alpha, \
             socket.create_connection(("127.0.0.1", self.port), timeout=2) as beta, \
             socket.create_connection(("127.0.0.1", self.port), timeout=2) as gamma:
            _send_recv(alpha, "hello", {"role": "main", "proto": 1, "events": True})
            _send_recv(beta, "hello", {"role": "main", "proto": 1, "events": True})
            _send_recv(gamma, "hello", {"role": "main", "proto": 1, "events": False})

            gift = Message.request("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}})
            alpha.sendall(gift.encode_bytes())
            from_alpha = _collect_until(
                alpha,
                lambda msgs: any(m.id == gift.id for m in msgs)
                and any(m.type == "pet.event" and event_kind(m) == "evolve_choice" for m in msgs),
            )
            from_beta = _collect_until(
                beta,
                lambda msgs: any(m.type == "pet.event" and event_kind(m) == "evolve_choice" for m in msgs),
            )
            gamma.settimeout(0.3)
            try:
                from_gamma = gamma.recv(4096)
            except socket.timeout:
                from_gamma = b""

            self.assertTrue(any(m.id == gift.id and m.type == "pet.gift.ok" for m in from_alpha))
            self.assertTrue(any(m.type == "pet.event" and event_kind(m) == "evolve_choice" for m in from_alpha))
            self.assertTrue(any(m.type == "pet.event" and event_kind(m) == "evolve_choice" for m in from_beta))
            # Beta did not send the gift, so it must not see the gift.ok reply.
            self.assertFalse(any(m.id == gift.id for m in from_beta))
            self.assertEqual(from_gamma, b"")

            evoreq = Message.request("pet.evolve", {"species": "Wavling"})
            alpha.sendall(evoreq.encode_bytes())
            more_a = _collect_until(
                alpha,
                lambda msgs: any(m.type == "pet.event" and event_kind(m) == "evolved" for m in msgs),
            )
            more_b = _collect_until(
                beta,
                lambda msgs: any(m.type == "pet.event" and event_kind(m) == "evolved" for m in msgs),
            )
            self.assertTrue(any(event_kind(m) == "evolved" for m in more_a))
            self.assertTrue(any(event_kind(m) == "evolved" for m in more_b))


class InventorySaveTests(unittest.TestCase):
    def test_gifts_survive_save_round_trip(self) -> None:
        handler = CompanionHandler(Creature(stats=Stats(hunger=50, mood=50, energy=80)))
        handler.handle(Message.request("pet.gift", {"item": {"name": "token", "kind": "trinket"}}))
        self.assertEqual(handler.creature.inventory, ["token"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pet.json")
            save_creature(handler.creature, path)
            loaded = load_creature(path)
        self.assertEqual(loaded.inventory, ["token"])
        self.assertEqual(need_hunger(loaded), need_hunger(handler.creature))


if __name__ == "__main__":
    unittest.main()
