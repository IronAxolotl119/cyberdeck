#!/usr/bin/env python3
"""fake_companion.py — a stand-in pet board you can run in another terminal.

It serves the deck's built-in simulated pet (cyberdeck.companion.PetState)
over a real wire, speaking docs/PROTOCOL.md exactly like the real
companion program will:

    python3 tools/fake_companion.py                      # TCP 127.0.0.1:7777
    python3 tools/fake_companion.py --tcp 0.0.0.0:7777   # reachable from other machines
    python3 tools/fake_companion.py --serial /dev/pts/5  # a serial device / socat pty
    python3 tools/fake_companion.py --speed 30           # pet lives 30x faster (gets hungry soon)

Then start the deck with  python3 -m cyberdeck --link tcp  (or --link serial).

What it does:
  * prints every line it receives (<-) and sends (->), so you can watch
    the conversation
  * answers hello, ping, pet.status, pet.feed, pet.play, pet.gift, pet.avatar;
    anything else gets an `error` reply with code unknown_type
  * pushes pet.event messages (hungry, tired, sad, evolved, level_up,
    learned_move, signal_found) — but only to a deck whose hello said
    "events": true
  * every --demo-every seconds it does something on purpose so you can
    see events arrive: finds a fake wifi signal, or gets peckish

It is also meant as a readable example for whoever writes the real
companion program: the whole protocol side is the Server class below.
Standard library only.
"""

from __future__ import annotations

import argparse
import itertools
import os
import selectors
import socket
import sys
import time
from pathlib import Path

# Let this script import the deck's package when run from the repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cyberdeck.companion import PetState, SimCompanionLink  # noqa: E402
from cyberdeck.protocol import (  # noqa: E402
    TYPE_HELLO,
    TYPE_PET_EVENT,
    Message,
    ProtocolError,
    decode,
    new_id,
)

DEMO_SIGNALS = ["FBI-Surveillance-Van", "Pretty Fly for a WiFi", "linksys", "NETGEAR42", "deadbeef-iot"]


class Client:
    """One connected deck (a TCP socket or the serial device)."""

    def __init__(self, name: str, write, fileno: int) -> None:
        self.name = name
        self.write = write          # function(bytes)
        self.fileno = fileno
        self.buffer = b""
        self.events = False         # pushed events agreed in hello?


class Server:
    """Turns lines from a deck into replies from the simulated pet."""

    def __init__(self, link: SimCompanionLink, quiet: bool = False, log=print) -> None:
        self.link = link
        self.quiet = quiet
        self.log = log
        self.clients: dict[int, Client] = {}

    def say(self, arrow: str, client: Client, text: str) -> None:
        if not self.quiet:
            stamp = time.strftime("%H:%M:%S")
            text = text.rstrip("\n")
            self.log(f"{stamp} {arrow} {client.name:<10} {text[:180] + ('…' if len(text) > 180 else '')}")

    def receive(self, client: Client, data: bytes) -> None:
        """Bytes arrived from a deck: split into lines and answer each."""
        client.buffer += data
        *lines, client.buffer = client.buffer.split(b"\n")
        if len(client.buffer) > 65536:
            client.buffer = b""
        for raw in lines:
            if not raw.strip(b"\r\x00 \t"):
                continue
            self.say("<-", client, raw.decode("utf-8", "replace"))
            try:
                message = decode(raw)
            except ProtocolError as exc:
                # No id to reply to, so just skip it (see PROTOCOL.md).
                self.say("!!", client, f"ignored bad line: {exc}")
                continue
            self.answer(client, message)

    def answer(self, client: Client, message: Message) -> None:
        with self.link._lock:            # the pet is shared by all clients
            self.link._tick()
            reply = self.link.handle(message)
            if message.type == TYPE_HELLO:
                client.events = (message.payload or {}).get("events") is True
            # Keep the sim producing events; each client decides if it gets them.
            self.link.events_enabled = True
        self.send(client, reply)

    def send(self, client: Client, message: Message) -> None:
        line = message.encode_bytes()
        try:
            client.write(line)
            self.say("->", client, line.decode("utf-8"))
        except OSError as exc:
            self.say("!!", client, f"send failed: {exc}")

    def push_events(self) -> None:
        """Let time pass for the pet and push whatever happened."""
        with self.link._lock:
            self.link._tick()
            self.link.events_enabled = True
            events = self.link.take_events()
        self.broadcast(events)

    def broadcast(self, events: list[Message]) -> None:
        for event in events:
            for client in list(self.clients.values()):
                if client.events:
                    self.send(client, event)

    def demo(self, step: int) -> None:
        """Something to look at: a found signal, or a sudden appetite."""
        pet = self.link.pet
        if step % 2 == 0:
            ssid = DEMO_SIGNALS[(step // 2) % len(DEMO_SIGNALS)]
            event = Message(TYPE_PET_EVENT, new_id(), {
                "event": "signal_found",
                "detail": {"ssid": ssid, "rssi": -48 - (step * 7) % 40},
                "pet": pet.as_payload(),
            })
            self.broadcast([event])
        else:
            with self.link._lock:
                pet.hunger = max(pet.hunger, 85.0)   # crosses the line -> a real "hungry" event
            self.push_events()


# --- wiring it to TCP or a serial device ---------------------------------------------------

def serve_tcp(server: Server, host: str, port: int, args) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen()
    listener.setblocking(False)
    sel = selectors.DefaultSelector()
    sel.register(listener, selectors.EVENT_READ, "listen")
    print(f"fake companion: {server.link.pet.name} the {server.link.pet.species} listening on TCP {host}:{port}"
          f"  (speed x{args.speed:g}, demo event every {args.demo_every:g}s; Ctrl-C to stop)", flush=True)
    loop(server, sel, args, accept_from=listener)


def serve_serial(server: Server, device: str, baud: int, args) -> None:
    from cyberdeck.link import SerialTransport

    sel = selectors.DefaultSelector()
    print(f"fake companion: {server.link.pet.name} on UART {device} {baud} 8N1 (Ctrl-C to stop)", flush=True)
    while True:
        transport = SerialTransport(device, baud, args.serial_driver)
        try:
            transport.open()
        except OSError as exc:
            print(f"cannot open {device}: {exc}; retrying in 2s", flush=True)
            time.sleep(2)
            continue
        client = Client(os.path.basename(device), transport.write, transport.fileno())
        server.clients[client.fileno] = client
        sel.register(client.fileno, selectors.EVENT_READ, ("serial", transport))
        try:
            loop(server, sel, args)
        except _SerialGone as exc:
            print(f"serial device closed ({exc}); reopening", flush=True)
        finally:
            server.clients.pop(client.fileno, None)
            try:
                sel.unregister(client.fileno)
            except (KeyError, ValueError):
                pass
            transport.close()
            time.sleep(0.5)


class _SerialGone(Exception):
    pass


def loop(server: Server, sel, args, accept_from: socket.socket | None = None) -> None:
    demo_steps = itertools.count()
    next_tick = time.monotonic() + 1.0
    next_demo = time.monotonic() + args.demo_every if args.demo_every > 0 else float("inf")
    while True:
        for key, _ in sel.select(timeout=0.25):
            if key.data == "listen":
                conn, addr = accept_from.accept()
                conn.setblocking(True)
                conn.settimeout(5.0)
                client = Client(f"{addr[0]}:{addr[1]}", conn.sendall, conn.fileno())
                server.clients[client.fileno] = client
                sel.register(conn, selectors.EVENT_READ, ("tcp", conn))
                print(f"{time.strftime('%H:%M:%S')} ++ deck connected from {client.name}", flush=True)
                continue
            kind, handle = key.data
            client = server.clients.get(key.fd)
            if kind == "tcp":
                try:
                    data = handle.recv(4096)
                except OSError:
                    data = b""
                if not data:
                    print(f"{time.strftime('%H:%M:%S')} -- deck {client.name if client else '?'} disconnected", flush=True)
                    sel.unregister(handle)
                    server.clients.pop(key.fd, None)
                    handle.close()
                    continue
            else:
                try:
                    data = handle.read()
                except OSError as exc:
                    raise _SerialGone(exc)
                if data == b"":
                    raise _SerialGone("hang-up")
                if not data:
                    continue
            if client:
                server.receive(client, data)
        now = time.monotonic()
        if now >= next_tick:
            next_tick = now + 1.0
            server.push_events()
        if now >= next_demo:
            next_demo = now + args.demo_every
            if server.clients:
                server.demo(next(demo_steps))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Serve a simulated PULSE pet over TCP or serial.")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--tcp", default="127.0.0.1:7777", metavar="HOST:PORT",
                       help="listen here (default 127.0.0.1:7777; use 0.0.0.0 for other machines)")
    where.add_argument("--serial", metavar="DEVICE", help="use a serial device instead, e.g. /dev/serial0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--serial-driver", default="auto", choices=("auto", "stdlib", "pyserial"))
    p.add_argument("--speed", type=float, default=1.0, help="pet time runs this many times faster")
    p.add_argument("--demo-every", type=float, default=45.0, help="seconds between demo events (0 = off)")
    p.add_argument("--stage", default="rookie", help="starting stage (egg … mega)")
    p.add_argument("--name", default="PULSE")
    p.add_argument("--quiet", action="store_true", help="do not print every message")
    return p


def make_link(args) -> SimCompanionLink:
    start = time.monotonic()
    pet = PetState(name=args.name, stage=args.stage)
    link = SimCompanionLink(pet=pet, clock=lambda: start + (time.monotonic() - start) * args.speed,
                            source="fake_companion")
    link._linked = True
    return link


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = Server(make_link(args), quiet=args.quiet, log=lambda line: print(line, flush=True))
    try:
        if args.serial:
            serve_serial(server, args.serial, args.baud, args)
        else:
            host, _, port = args.tcp.rpartition(":")
            serve_tcp(server, host or "127.0.0.1", int(port), args)
    except KeyboardInterrupt:
        print("\nfake companion: bye", flush=True)
    except OSError as exc:
        print(f"fake companion: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
