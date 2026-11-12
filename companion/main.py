#!/usr/bin/env python3
"""Headless text simulation of the cyberdeck pet.

Run a fixed number of simulated hours, print a readable log, and optionally
save or load a JSON file. Fake radio scans are seeded, so the same command
always tells the same story:

    python3 main.py --ticks 50 --seed 42

Add --avatar to draw the creature's 16x16 sprite each tick (ANSI truecolor
half-blocks by default, or --ascii for a plain-text fallback). --show-sprites
prints every form and mood, writes docs/sprites/sheet.ansi and sheet.txt, and
exits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from avatar.renderer import format_catalog, get_renderer, rich_is_available, write_sprite_sheets
from avatar.sprites import mood_for_creature, sprite_pixels
from discovery.effects import apply_signals, describe_signal
from discovery.simulated import SimulatedScanner
from pet.creature import Creature
from save import load_creature, save_creature

ROOT = Path(__file__).resolve().parent
SPRITE_SHEET_DIR = ROOT / "docs" / "sprites"


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
        help="Draw the creature's sprite each tick (and at the start and end).",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="Draw sprites with plain ASCII instead of ANSI truecolor half-blocks.",
    )
    parser.add_argument(
        "--show-sprites",
        action="store_true",
        help="Print all forms and moods side by side, write docs/sprites/sheet.{ansi,txt}, and exit.",
    )
    parser.add_argument(
        "--rich",
        action="store_true",
        help="Use the optional 'rich' package to draw sprites, if it is installed.",
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


def make_renderer(ascii_mode: bool, use_rich: bool):
    if use_rich and not ascii_mode and not rich_is_available():
        print(
            "Note: the 'rich' package is not installed; using the built-in ANSI renderer.",
            file=sys.stderr,
        )
    return get_renderer(ascii_mode=ascii_mode, use_rich=use_rich)


def print_avatar(creature: Creature, renderer, frame: int = 0, indent: str = "  ") -> None:
    """Draw one sprite that matches the creature's current form and mood."""
    mood = mood_for_creature(creature)
    pixels = sprite_pixels(creature.species_id, frame=frame, mood=mood)
    picture = renderer.render(pixels)
    for line in picture.splitlines():
        print(f"{indent}{line}")


def show_sprites(ascii_mode: bool, use_rich: bool) -> int:
    """Print the catalog, write both sheet files, and return an exit code."""
    if use_rich and not ascii_mode and not rich_is_available():
        print(
            "Note: the 'rich' package is not installed; using the built-in ANSI renderer.",
            file=sys.stderr,
        )
    # What the user sees follows --ascii / --rich. The two files are always
    # written as raw ANSI and plain ASCII, regardless of those flags.
    print(format_catalog(ascii_mode=ascii_mode, use_rich=use_rich and not ascii_mode), end="")
    ansi_path, txt_path = write_sprite_sheets(SPRITE_SHEET_DIR)
    print(f"Wrote {ansi_path}")
    print(f"Wrote {txt_path}")
    return 0


def run_simulation(
    ticks: int,
    seed: int,
    name: str,
    load_path: str | None,
    save_path: str | None,
    show_avatar: bool = False,
    ascii_mode: bool = False,
    use_rich: bool = False,
) -> int:
    if ticks < 0:
        print("ticks must be >= 0", file=sys.stderr)
        return 2

    if load_path:
        creature = load_creature(load_path)
        origin = f"loaded from {load_path}"
    else:
        creature = Creature(name=name)
        origin = "new hatchling"

    scanner = SimulatedScanner(seed=seed)
    renderer = make_renderer(ascii_mode, use_rich) if show_avatar else None

    print("Cyberdeck Pet — headless simulation")
    print(f"seed={seed}  ticks={ticks}  ({origin})")
    print(creature.summary())
    if renderer is not None:
        print_avatar(creature, renderer, frame=0)
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

        for line in events:
            print(f"  * {line}")
        if renderer is not None:
            print_avatar(creature, renderer, frame=hour % 2)
        print(f"  {creature.summary()}")
        print()

    print("-" * 72)
    print("Simulation finished.")
    print(creature.summary())
    if renderer is not None:
        print_avatar(creature, renderer, frame=ticks % 2)

    if save_path:
        written = save_creature(creature, save_path)
        print(f"Saved to {written}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.show_sprites:
        return show_sprites(ascii_mode=args.ascii, use_rich=args.rich)
    return run_simulation(
        ticks=args.ticks,
        seed=args.seed,
        name=args.name,
        load_path=args.load,
        save_path=args.save,
        show_avatar=args.avatar,
        ascii_mode=args.ascii,
        use_rich=args.rich,
    )


if __name__ == "__main__":
    sys.exit(main())
