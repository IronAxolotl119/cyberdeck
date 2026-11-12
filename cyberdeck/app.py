"""app.py — start the deck: parse flags, open hardware, run UI or headless.

This is the traffic director. It:

  1. Reads command-line flags and a couple of environment variables.
  2. Opens the sensor pack and companion link (--mode sim or real).
  3. Loads theme, layout, built-in commands, and user plugins.
  4. Starts either the full-screen curses UI or headless/script mode.

`python -m cyberdeck` lands here via __main__.py.
"""

from __future__ import annotations

import argparse
import os
import sys

from cyberdeck import __app_name__, __version__
from cyberdeck.commands import Context, build_registry
from cyberdeck.companion import open_hardware
from cyberdeck.config import data_dir, load_layout, load_theme
from cyberdeck.headless import run_headless
from cyberdeck.tui import run_tui


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    mode = (args.mode or os.environ.get("CYBERDECK_MODE") or "sim").strip().lower()

    try:
        sensors, companion = open_hardware(mode, serial_port=args.serial)
    except ValueError as exc:
        print(f"cyberdeck: {exc}", file=sys.stderr)
        return 2

    # The sim link is up after connect(); the UART stub stays down
    # until that backend is implemented, which is expected.
    try:
        companion.connect()
    except Exception as exc:  # noqa: BLE001 — still start the UI and show the error
        print(f"cyberdeck: companion connect: {exc}", file=sys.stderr)

    ctx = Context(
        sensors=sensors,
        companion=companion,
        mode=mode,
        data_dir=data_dir(args.data_dir),
        config_dir=args.config_dir,
    )
    registry = build_registry(include_plugins=not args.no_plugins)
    theme = load_theme(args.config_dir)
    layout = load_layout(args.config_dir)

    try:
        if args.headless or args.script:
            scripts = list(args.script or [])
            commands = scripts if scripts else None
            return run_headless(ctx, registry, commands=commands)
        run_tui(ctx, registry, theme, layout)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        companion.close()


def build_parser() -> argparse.ArgumentParser:
    """Flags the user can pass to `python -m cyberdeck`."""
    parser = argparse.ArgumentParser(
        prog="cyberdeck",
        description=f"{__app_name__} main-board interface (v{__version__})",
    )
    parser.add_argument(
        "--mode",
        choices=("sim", "real"),
        default=None,
        help="hardware backend (default: sim, or CYBERDECK_MODE)",
    )
    parser.add_argument(
        "--serial",
        default=None,
        help="serial port for --mode real (default: CYBERDECK_SERIAL or /dev/ttyAMA0)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run commands without curses (reads stdin unless --script is given)",
    )
    parser.add_argument(
        "--script",
        action="append",
        metavar="CMD",
        help="run this command in headless mode (repeatable). Implies --headless.",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="folder with theme.ini and layout.ini (default: ./config)",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="folder for history, notes, scores, cache (default: ./data or CYBERDECK_DATA_DIR)",
    )
    parser.add_argument(
        "--no-plugins",
        action="store_true",
        help="do not auto-load commands from the plugins/ folder",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser
