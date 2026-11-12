#!/usr/bin/env python3
"""Headless text simulation of the cyberdeck pet.

Run a fixed number of simulated hours, print a readable log, and optionally
save or load a JSON file. Fake radio scans are seeded, so the same command
always tells the same story:

    python3 main.py --ticks 50 --seed 42

Add --avatar to draw the creature's text face each tick. --show-faces prints
every mood and stage accent, writes docs/faces/sheet.txt, and exits.

The pet never auto-evolves on its own; this headless demo auto-picks the
first available evolution choice once the gates are met so the story still
moves forward. Use --evolution-speed to scale the time gates (2.0 is twice
as fast).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from avatar.faces import evolution_banner, face_text, format_face_sheet, write_face_sheet
from discovery.effects import apply_signals, describe_signal
from discovery.simulated import SimulatedScanner
from pet.creature import Creature
from pet.evolution import DEFAULT_EVOLUTION_GATES, apply_evolution_config, evolution_speed
from save import load_creature, save_creature

ROOT = Path(__file__).resolve().parent
FACE_SHEET_DIR = ROOT / "docs" / "faces"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate a cyberdeck pet in the terminal (no hardware)."
    )
    parser.add_argument("--ticks", type=int, default=50, help="Simulated hours to run (default 50).")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for fake Wi-Fi / Bluetooth scans (default 42).",
    )
    parser.add_argument("--name", type=str, default="Pulse", help="Nickname for a new creature.")
    parser.add_argument("--save", type=str, default=None, help="Write a JSON save to this path at the end.")
    parser.add_argument("--load", type=str, default=None, help="Load a JSON save from this path first.")
    parser.add_argument(
        "--avatar",
        action="store_true",
        help="Draw the creature's text face each tick (and at the start and end).",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="Draw faces with a plain-ASCII accent (mega uses * instead of «»).",
    )
    parser.add_argument(
        "--show-faces",
        action="store_true",
        help="Print every mood and stage accent, write docs/faces/sheet.txt, and exit.",
    )
    parser.add_argument(
        "--show-sprites",
        action="store_true",
        help=argparse.SUPPRESS,  # old flag; same as --show-faces
    )
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
    return parser.parse_args(argv)


def caretaker_action(creature: Creature, tick_number: int) -> list[str]:
    """A tiny automatic caretaker so a 50-tick demo is not a starvation log.

    Priority: feed when hungry, rest when tired, otherwise train every few
    hours so stats and XP keep moving. This is only for the headless demo;
    a later UI or hardware buttons will let a human decide.
    """
    if creature.stats.hunger < 40:
        return creature.feed()
    if creature.stats.energy < 35:
        return creature.rest()
    if tick_number % 4 == 0:
        # Cycle attack -> defense -> speed so one stat is not the only winner.
        stat = ("attack", "defense", "speed")[(tick_number // 4) % 3]
        return creature.train(stat)
    return []


def print_avatar(creature: Creature, frame: int = 0, indent: str = "  ", ascii_mode: bool = False) -> None:
    """Draw the current text face."""
    mood = "sleeping" if creature.asleep else None
    # face_text needs a mood; let face_mood decide when mood is None via helper
    from avatar.faces import face_mood

    word = face_mood(creature) if mood is None else mood
    text = face_text(word, creature.species.stage, frame=frame, ascii_mode=ascii_mode)
    for line in text.splitlines():
        print(f"{indent}{line}")


def print_popup(kind: str, detail: dict, name: str) -> None:
    banner = evolution_banner(kind, detail, name=name)
    for line in banner.splitlines():
        print(f"  {line}")


def maybe_auto_evolve(creature: Creature) -> list[str]:
    """Headless-only: pick the first available choice when the gates are met."""
    lines: list[str] = []
    target = creature.first_available_choice()
    if not target:
        return lines
    result = creature.evolve_to(target)
    if result["accepted"]:
        print_popup("evolving", {"from": result["from"], "to": result["to"]}, creature.name)
        print_popup("evolved", {"from": result["from"], "to": result["to"]}, creature.name)
        lines.extend(result.get("events") or [])
        lines.append(f"(auto-picked {result['to']})")
    return lines


def show_faces(ascii_mode: bool) -> int:
    print(format_face_sheet(ascii_mode=ascii_mode), end="")
    path = write_face_sheet(FACE_SHEET_DIR, ascii_mode=False)
    print(f"Wrote {path}")
    return 0


def run_simulation(
    ticks: int,
    seed: int,
    name: str,
    load_path: str | None,
    save_path: str | None,
    show_avatar: bool = False,
    ascii_mode: bool = False,
    evo_speed: float = 1.0,
    evo_config: str | None = None,
) -> int:
    if ticks < 0:
        print("ticks must be >= 0", file=sys.stderr)
        return 2
    if evo_speed <= 0:
        print("--evolution-speed must be > 0", file=sys.stderr)
        return 2

    apply_evolution_config(speed=evo_speed, path=evo_config)

    if load_path:
        creature = load_creature(load_path)
        origin = f"loaded from {load_path}"
    else:
        creature = Creature(name=name)
        origin = "new hatchling"

    scanner = SimulatedScanner(seed=seed)

    print("Cyberdeck Pet — headless simulation")
    print(
        f"seed={seed}  ticks={ticks}  evolution-speed={evolution_speed():g}  ({origin})"
    )
    print(creature.summary())
    if show_avatar:
        print_avatar(creature, frame=0, ascii_mode=ascii_mode)
    print("-" * 72)

    for hour in range(1, ticks + 1):
        print(f"=== Tick {hour}  (age {creature.stats.age:.1f}h -> {creature.stats.age + 1:.1f}h) ===")

        signals = scanner.scan()
        if signals:
            spotted = ", ".join(describe_signal(s) for s in signals)
            print(f"  scan: {spotted}")
        else:
            print("  scan: (nothing nearby)")

        events = []
        events.extend(apply_signals(creature, signals))
        events.extend(caretaker_action(creature, hour))
        events.extend(creature.tick(dt=1.0))

        for kind, detail in creature.take_evolution_notices():
            print_popup(kind, detail, creature.name)
        events.extend(maybe_auto_evolve(creature))

        for line in events:
            print(f"  * {line}")
        if show_avatar:
            print_avatar(creature, frame=hour, ascii_mode=ascii_mode)
        print(f"  {creature.summary()}")
        print()

    print("-" * 72)
    print("Simulation finished.")
    print(creature.summary())
    if show_avatar:
        print_avatar(creature, frame=ticks, ascii_mode=ascii_mode)

    if save_path:
        written = save_creature(creature, save_path)
        print(f"Saved to {written}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.show_faces or args.show_sprites:
        return show_faces(ascii_mode=args.ascii)
    return run_simulation(
        ticks=args.ticks,
        seed=args.seed,
        name=args.name,
        load_path=args.load,
        save_path=args.save,
        show_avatar=args.avatar,
        ascii_mode=args.ascii,
        evo_speed=args.evolution_speed,
        evo_config=args.evolution_config,
    )


if __name__ == "__main__":
    # DEFAULT_EVOLUTION_GATES is imported so --help text and docs stay in sync.
    _ = DEFAULT_EVOLUTION_GATES
    sys.exit(main())
