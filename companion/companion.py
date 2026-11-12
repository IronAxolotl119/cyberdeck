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

from avatar.faces import evolution_banner, life_banner, live_frames, speech_line
from discovery.simulated import SimulatedScanner
from link.handler import PET_NAME, CompanionHandler, mood_word, need_hunger
from pet.evolution import apply_evolution_config
from link.transport import (
    DEFAULT_SERIAL,
    DEFAULT_TCP_HOST,
    DEFAULT_TCP_PORT,
    TcpServer,
    UartServer,
    parse_host_port,
)
from pet.creature import Creature
from pet.world import World
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
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Start a brand-new pet even if --load is given (the old --save file is kept as .bak).",
    )
    parser.add_argument(
        "--weather",
        metavar="LAT,LON",
        default=None,
        help="Optional: fetch real weather from Open-Meteo for this location (needs internet).",
    )
    parser.add_argument(
        "--life-pace",
        type=float,
        default=1.0,
        help="Speed up idle life / chatter / adventures / rivals for demos (2.0 = twice as often).",
    )
    parser.add_argument("--avatar", action="store_true", help="Draw the text face on each tick.")
    parser.add_argument("--ascii", action="store_true", help="Plain-ASCII face accents.")
    parser.add_argument(
        "--evolution-speed",
        type=float,
        default=1.0,
        help="Multiply evolution rate (2.0 = gates halved / twice as fast; default 1.0).",
    )
    parser.add_argument(
        "--evolution-config",
        type=str,
        default=None,
        help="Optional evolution.json that overlays the default age/level/XP gates.",
    )
    parser.add_argument("--source", type=str, default="sim", help="source field in hello.ok (default sim).")
    parser.add_argument(
        "--event-interval",
        type=float,
        default=0.5,
        help="Wall-clock seconds between outgoing pet.event pushes (default 0.5). 0 sends immediately.",
    )
    return parser.parse_args(argv)


def serial_path(cli_value: str | None) -> str:
    """Resolve the tty path: flag, then env, then /dev/serial0."""
    if cli_value:
        return cli_value
    return os.environ.get("CYBERDECK_SERIAL") or DEFAULT_SERIAL


def print_avatar(creature: Creature, frame: int, ascii_mode: bool = False) -> None:
    _word, frames, _anim, _costume = live_frames(creature, ascii_mode=ascii_mode)
    text = frames[frame % len(frames)] if frames else ""
    bubble = creature.life.current_bubble()
    if bubble:
        print(f"  {speech_line(bubble['text'])}")
    for line in text.splitlines():
        print(f"  {line}")


def print_popups(handler: CompanionHandler, name: str) -> None:
    """Boxed banners when the pet is close to, or ready for, evolution.

    This is a prompt, not a blocking stdin question — the server thread
    has to keep listening. To evolve, send pet.evolve {species} from the
    deck (or any protocol client).
    """
    for kind, detail in handler.take_popups():
        if kind in {"evolve_soon", "evolve_choice", "evolving", "evolved", "woke"}:
            banner = evolution_banner(kind, detail, name=name)
        else:
            banner = life_banner(kind, detail, name=name)
        for line in banner.splitlines():
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
    if args.event_interval < 0:
        print("--event-interval must be >= 0", file=sys.stderr)
        return 2
    if args.evolution_speed <= 0:
        print("--evolution-speed must be > 0", file=sys.stderr)
        return 2
    apply_evolution_config(speed=args.evolution_speed, path=args.evolution_config)

    use_serial = args.serial is not None
    if use_serial:
        port_path = serial_path(args.serial)
        endpoint = f"UART {port_path} 115200 8N1"
    else:
        host, port = parse_host_port(args.tcp or f"{DEFAULT_TCP_HOST}:{DEFAULT_TCP_PORT}")
        endpoint = f"TCP {host}:{port}"

    weather = None
    if args.weather:
        try:
            lat_s, lon_s = args.weather.split(",", 1)
            weather = (float(lat_s), float(lon_s))
        except ValueError:
            print("--weather must look like LAT,LON (for example 33.45,-112.07)", file=sys.stderr)
            return 2
    if args.life_pace <= 0:
        print("--life-pace must be > 0", file=sys.stderr)
        return 2

    if args.reset:
        if args.save and Path(args.save).exists():
            backup = Path(args.save).with_suffix(Path(args.save).suffix + ".bak")
            backup.write_bytes(Path(args.save).read_bytes())
            print(f"--reset: old save copied to {backup}")
        creature = Creature(name=PET_NAME)
        origin = "new hatchling (--reset)"
    elif args.load:
        creature = load_creature(args.load)
        origin = f"loaded from {args.load}"
    else:
        creature = Creature(name=PET_NAME)
        origin = "new hatchling"

    scanner = SimulatedScanner(seed=args.seed)
    world = World(lat=weather[0], lon=weather[1]) if weather else World()
    handler = CompanionHandler(creature, scanner=scanner, source=args.source, world=world)
    creature.life.pace = args.life_pace
    lock = threading.Lock()
    stop = threading.Event()

    save_path = Path(args.save) if args.save else None

    def autosave() -> None:
        if save_path is None:
            return
        save_creature(creature, save_path)

    if save_path is not None:
        autosave()

    def ticker() -> None:
        while not stop.wait(args.tick_seconds):
            with lock:
                handler.tick(args.dt)
                speech_log = list(handler.take_speech())
                autosave()
                snapshot = creature.summary()
                frame = handler.frame
                need = need_hunger(creature)
                word = mood_word(creature)
            print(f"[tick] need_hunger={need:.0f} mood={word}  {snapshot}")
            if args.avatar:
                for line in speech_log:
                    print(f"  [{line[0]}] {speech_line(line[1], 90)}")
                print_avatar(creature, frame, ascii_mode=args.ascii)
                print_popups(handler, creature.name)

    tick_thread = threading.Thread(target=ticker, name="pet-tick", daemon=True)
    server: TcpServer | UartServer
    if use_serial:
        server = UartServer(
            handler, lock, path=port_path, event_interval=args.event_interval
        )
    else:
        server = TcpServer(
            handler, lock, host=host, port=port, event_interval=args.event_interval
        )
        server.start()
        endpoint = f"TCP {host}:{server.port}"

    print("Cyberdeck Pet — companion link")
    print(f"{endpoint}  ({origin})")
    print(
        f"tick every {args.tick_seconds:g}s, dt={args.dt:g} sim hours, "
        f"event interval {args.event_interval:g}s, "
        f"evolution-speed={args.evolution_speed:g}"
    )
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
