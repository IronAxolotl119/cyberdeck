"""Start the dashboard:  python3 -m cyberdeck_dash [options]

In plain English: parses the command line, builds the Dashboard, then
redraws it with Rich Live at [dash] refresh_hz while feeding it keys
(and GPIO buttons on the Pi). Ctrl+C or q quits and restores the
terminal. --snapshot renders a few simulated seconds without a
terminal and saves the screen as text/SVG (used for docs and tests).
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from .app import Dashboard
from .config import load_config


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python3 -m cyberdeck_dash",
                                description="CYBERDECK//DASH - awareness dashboard (sim-first).")
    p.add_argument("--mode", choices=("sim", "real"), help="sim (default from dash.ini) or real Pi hardware")
    p.add_argument("--config", help="path to a dash.ini (default config/dash.ini)")
    p.add_argument("--theme", help="theme name from dash.ini, e.g. neon, amber, matrix")
    p.add_argument("--no-companion", action="store_true", help="never try the TCP companion; local pet only")
    p.add_argument("--host", help="companion host (default from dash.ini)")
    p.add_argument("--port", type=int, help="companion TCP port (default 7777)")
    p.add_argument("--seed", type=int, help="random seed for repeatable simulations")
    p.add_argument("--snapshot", metavar="PREFIX",
                   help="headless: simulate --frames seconds, write PREFIX.txt and PREFIX.svg (docs use the .svg), exit")
    p.add_argument("--frames", type=int, default=40, help="simulated seconds for --snapshot (default 40)")
    p.add_argument("--size", default="100x30", help="snapshot size COLSxROWS (default 100x30)")
    return p.parse_args(argv)


def make_dashboard(args, threaded: bool = True, journal_path=None) -> Dashboard:
    cfg = load_config(args.config)
    if args.host:
        cfg.raw.set("companion", "host", args.host)
    if args.port:
        cfg.raw.set("companion", "port", str(args.port))
    dash = Dashboard(cfg, mode=args.mode, seed=args.seed, threaded=threaded,
                     companion=False if args.no_companion else None, journal_path=journal_path)
    if args.theme and args.theme in cfg.themes:
        dash.theme_name = args.theme
    return dash


def snapshot(args) -> int:
    """Render a simulated session to text + SVG with no terminal needed."""
    from rich.console import Console

    from .ui.screen import build_screen

    cols, rows = (int(x) for x in args.size.lower().split("x"))
    # In-memory journal so a docs snapshot never touches data/discoveries.jsonl.
    dash = make_dashboard(args, threaded=False, journal_path=False)
    start = time.time()
    dash.diag.run_now("dns", dash.cfg.get("diag", "dns_default"))
    dash.diag.run_now("ping", dash.cfg.get("diag", "ping_default"))
    for i in range(args.frames):
        keys = ["r"] if i == args.frames // 2 else []
        dash.step(start + i, keys)
    dash.screen_width = cols
    console = Console(record=True, width=cols, height=rows, force_terminal=True, color_system="truecolor",
                      file=io.StringIO())
    console.print(build_screen(dash, start + args.frames), height=rows)
    prefix = Path(args.snapshot)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".txt").write_text(console.export_text(clear=False), encoding="utf-8")
    console.save_svg(str(prefix.with_suffix(".svg")), title="CYBERDECK//DASH")
    print(f"wrote {prefix.with_suffix('.txt')} and {prefix.with_suffix('.svg')}", file=sys.stderr)
    return 0


def run(args) -> int:
    from rich.console import Console
    from rich.live import Live

    from .inputs import AlertLed, GpioButtons, KeyReader
    from .ui.screen import build_screen

    dash = make_dashboard(args)
    hz = max(0.5, dash.cfg.float("dash", "refresh_hz"))
    console = Console()
    with KeyReader() as keys:
        if dash.mode == "real" and dash.cfg.get("buttons", "backend") == "gpiozero":
            pins = {k: dash.cfg.get("buttons", k) for k in dash.cfg.raw.options("buttons") if k != "backend"}
            gpio = GpioButtons(pins, keys)
            if gpio.error:
                dash.log(gpio.error, "warn")
            dash.led = AlertLed(dash.cfg.get("buttons", "alert_led"))
        dash.start()
        try:
            with Live(build_screen(dash), console=console, screen=True, auto_refresh=False,
                      redirect_stdout=False, redirect_stderr=False) as live:
                frame = 1.0 / hz
                while dash.running:
                    pressed = keys.read(timeout=frame)
                    dash.step(keys=pressed)
                    dash.screen_width = console.size.width
                    live.update(build_screen(dash), refresh=True)
        except KeyboardInterrupt:
            pass
        finally:
            dash.stop()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    return snapshot(args) if args.snapshot else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
