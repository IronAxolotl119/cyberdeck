#!/usr/bin/env python3
"""Live companion process: tick the pet and serve the main-deck protocol.

This is what you run on the pet board (or on a regular computer, for
testing). It keeps a Creature alive, advances time on a wall-clock
interval, and speaks NDJSON to the main cyberdeck over TCP or UART.

    python3 companion.py --tcp 127.0.0.1:7777
    python3 companion.py --serial /dev/serial0
    python3 companion.py --tcp 127.0.0.1:7777 --avatar --ascii --save ./save.json
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from avatar.renderer import get_renderer, rich_is_available
from avatar.sprites import mood_for_creature, sprite_pixels
from discovery.simulated import SimulatedScanner
from link.handler import PET_NAME, CompanionHandler, mood_word, need_hunger
from link.transport import (
    DEFAULT_SERIAL,
    DEFAULT_TCP_HOST,
    DEFAULT_TCP_PORT,
    TcpServer,
    UartServer,
    parse_host_port,
)
from pet.creature import Creature
from save import load_creature, save_creature


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the cyberdeck pet and talk to the main deck (TCP or UART)."
    )
    parser.add_argument(
        "--tcp",
        metavar="HOST:PORT",
        nargs="?",
        const=f"{DEFAULT_TCP_HOST}:{DEFAULT_TCP_PORT}",
        default=None,
        help=f"Listen on TCP (default {DEFAULT_TCP_HOST}:{DEFAULT_TCP_PORT} if no path given).",
    )
    parser.add_argument(
        "--serial",
        metavar="PATH",
        nargs="?",
        const="",
        default=None,
        help=f"Serve on a tty (default $CYBERDECK_SERIAL or {DEFAULT_SERIAL}).",
    )
    parser.add_argument(
        "--tick-seconds",
        type=float,
        default=1.0,
        help="Wall-clock seconds between creature ticks (default 1).",
    )
    parser.add_argument(
        "--dt",
        type=float,
        default=0.05,
        help="Simulated hours advanced on each tick (default 0.05 = 3 minutes).",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for fake Wi-Fi / BT scans.")
    parser.add_argument("--save", type=str, default=None, help="Autosave JSON path.")
    parser.add_argument("--load", type=str, default=None, help="Load a JSON save at start.")
    parser.add_argument("--avatar", action="store_true", help="Draw the sprite on each tick.")
    parser.add_argument("--ascii", action="store_true", help="Plain-ASCII avatar (no ANSI color).")
    parser.add_argument("--rich", action="store_true", help="Use the optional 'rich' package if installed.")
    parser.add_argument("--source", type=str, default="sim", help="source field in hello.ok (default sim).")
    return parser.parse_args(argv)


def serial_path(cli_value: str | None) -> str:
    """Resolve the tty path: flag, then env, then /dev/serial0."""
    if cli_value:
        return cli_value
    return os.environ.get("CYBERDECK_SERIAL") or DEFAULT_SERIAL


def print_avatar(creature: Creature, renderer, frame: int) -> None:
    mood = mood_for_creature(creature)
    picture = renderer.render(sprite_pixels(creature.species_id, frame=frame, mood=mood))
    for line in picture.splitlines():
        print(f"  {line}")


def run(args: argparse.Namespace) -> int:
    # Line-buffer stdout so status lines appear immediately when piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    if args.tcp is not None and args.serial is not None:
        print("Choose one of --tcp or --serial, not both.", file=sys.stderr)
        return 2
    if args.tick_seconds <= 0 or args.dt < 0:
        print("--tick-seconds must be > 0 and --dt must be >= 0", file=sys.stderr)
        return 2

    use_serial = args.serial is not None
    if use_serial:
        port_path = serial_path(args.serial)
        endpoint = f"UART {port_path} 115200 8N1"
    else:
        host, port = parse_host_port(args.tcp or f"{DEFAULT_TCP_HOST}:{DEFAULT_TCP_PORT}")
        endpoint = f"TCP {host}:{port}"

    if args.load:
        creature = load_creature(args.load)
        origin = f"loaded from {args.load}"
    else:
        creature = Creature(name=PET_NAME)
        origin = "new hatchling"

    scanner = SimulatedScanner(seed=args.seed)
    handler = CompanionHandler(creature, scanner=scanner, source=args.source)
    lock = threading.Lock()
    stop = threading.Event()

    save_path = Path(args.save) if args.save else None

    def autosave() -> None:
        if save_path is None:
            return
        save_creature(creature, save_path)

    if save_path is not None:
        autosave()

    renderer = None
    if args.avatar:
        if args.rich and not args.ascii and not rich_is_available():
            print("Note: 'rich' is not installed; using the built-in renderer.", file=sys.stderr)
        renderer = get_renderer(ascii_mode=args.ascii, use_rich=args.rich)

    def ticker() -> None:
        while not stop.wait(args.tick_seconds):
            with lock:
                handler.tick(args.dt)
                autosave()
                snapshot = creature.summary()
                frame = handler.frame
                need = need_hunger(creature)
                word = mood_word(creature)
            print(f"[tick] need_hunger={need:.0f} mood={word}  {snapshot}")
            if renderer is not None:
                print_avatar(creature, renderer, frame)

    tick_thread = threading.Thread(target=ticker, name="pet-tick", daemon=True)
    server: TcpServer | UartServer
    if use_serial:
        server = UartServer(handler, lock, path=port_path)
    else:
        server = TcpServer(handler, lock, host=host, port=port)
        server.start()
        endpoint = f"TCP {host}:{server.port}"

    print("Cyberdeck Pet — companion link")
    print(f"{endpoint}  ({origin})")
    print(f"tick every {args.tick_seconds:g}s, dt={args.dt:g} sim hours")
    print(creature.summary())
    print("Waiting for the main deck. Ctrl-C to stop.")

    tick_thread.start()
    try:
        server.serve_forever(stop)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop.set()
        if isinstance(server, TcpServer):
            server.close()
        with lock:
            autosave()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
