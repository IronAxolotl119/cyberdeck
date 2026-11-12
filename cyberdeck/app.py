"""app.py — start the deck: parse flags, open hardware, run UI or headless.

This is the traffic director. It:

  1. Reads command-line flags and a couple of environment variables.
  2. Opens the sensor pack (--mode sim|real) and the companion link
     (--link sim|tcp|serial, or [link] in config/hardware.ini).
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
from cyberdeck.companion import open_sensors
from cyberdeck.config import Settings, data_dir, load_layout, load_theme
from cyberdeck.link import load_link_settings, open_link, with_overrides
from cyberdeck.headless import run_headless
from cyberdeck.tui import run_tui, wants_unicode


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    mode = (args.mode or os.environ.get("CYBERDECK_MODE") or "sim").strip().lower()

    # Link settings: hardware.ini [link], then environment, then flags.
    link_settings = with_overrides(
        load_link_settings(Settings.load("hardware.ini", args.config_dir)),
        mode=args.link or os.environ.get("CYBERDECK_LINK"),
        host=args.host, port=args.port,
        device=args.device or os.environ.get("CYBERDECK_SERIAL"),
        baud=args.baud,
    )
    try:
        sensors = open_sensors(mode)
        companion = open_link(link_settings)
    except ValueError as exc:
        print(f"cyberdeck: {exc}", file=sys.stderr)
        return 2

    # tcp/serial links connect in the background and keep retrying, so a
    # companion that is not running yet is fine: the UI shows LINK DOWN.
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
        # Effects pick Unicode blocks or plain ASCII to match the frame style.
        ctx.services["unicode"] = wants_unicode(layout)
        startup = []
        if not args.no_boot:
            from cyberdeck.features.boot import make_boot

            boot = make_boot(ctx)
            if boot is not None:
                startup.append(boot)
        run_tui(ctx, registry, theme, layout, startup=startup)
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
        help="sensors: sim (pretend numbers) or real (Pi sysfs). Default sim, or CYBERDECK_MODE",
    )
    parser.add_argument(
        "--link",
        choices=("sim", "tcp", "serial"),
        default=None,
        help="how to reach the companion pet (default: hardware.ini [link] mode, or CYBERDECK_LINK)",
    )
    parser.add_argument("--host", default=None, help="companion TCP host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="companion TCP port (default 7777)")
    parser.add_argument(
        "--device", "--serial",
        dest="device",
        default=None,
        help="serial device for --link serial (default: CYBERDECK_SERIAL or /dev/serial0)",
    )
    parser.add_argument("--baud", type=int, default=None, help="serial speed (default 115200)")
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
        "--no-boot",
        action="store_true",
        help="skip the boot animation this time (config/fx.ini [boot] turns it off for good)",
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
