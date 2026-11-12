"""Run the ONE combined interface: the Rich dashboard + the whole main deck.

In plain English: cyberdeck/app.py builds the deck's brain (sensors, the
companion link, the command registry with every feature and plugin) and
then calls run_unified() here. This file:

  1. wraps that brain in a DeckBridge (deck_bridge.py),
  2. builds the Dashboard with it, so the awareness tabs and the deck
     tabs (Console, Dungeon, Notes & Todo, Fun & Tools) share one screen
     and one companion link,
  3. runs the Rich Live loop: read keys (and GPIO buttons), step, redraw.
     It redraws faster while something animates (dungeon, visualizer),
     and steps aside for programs that need the real terminal (ssh).

snapshot_unified() renders a few simulated seconds to .txt/.svg without
a terminal (docs/dashboard.svg, tests). --tab and --keys pick what is on
screen, e.g. --tab dungeon --keys enter,right,right.
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

from .app import Dashboard
from .config import load_config
from .deck_bridge import DeckBridge

# Key names --keys accepts besides single characters.
NAMED_KEYS = {"enter", "esc", "tab", "shift+tab", "up", "down", "left", "right", "backspace", "delete",
              "home", "end", "pgup", "pgdn", "insert", "space"} | {f"f{n}" for n in range(1, 13)}


def build_dashboard(args, ctx, registry, threaded: bool = True, journal_path=None,
                    start_services: bool = True, open_tab: bool = True) -> Dashboard:
    """The Dashboard with the deck attached (also used by tests)."""
    from cyberdeck.config import load_layout

    cfg = load_config(getattr(args, "dash_config", None))
    if getattr(args, "dash_mode", None):
        cfg.raw.set("dash", "mode", args.dash_mode)
    layout = load_layout(ctx.config_dir)
    ctx.services.setdefault("unicode", (layout.use_unicode or "auto").lower() not in {"no", "false", "0", "off", "ascii"})
    bridge = DeckBridge(ctx, registry, prompt=layout.prompt, max_lines=layout.max_output_lines,
                        start_services=start_services)
    dash = Dashboard(cfg, mode=getattr(args, "dash_mode", None), seed=getattr(args, "seed", None),
                     threaded=threaded, journal_path=journal_path, bridge=bridge)
    theme = getattr(args, "theme", None)
    if theme and theme in cfg.themes:
        dash.theme_name = theme
    tab = getattr(args, "tab", None)
    if tab and open_tab:
        dash.deck_ui_action(f":tab {tab}")
    return dash


def parse_key_script(text: str | None) -> list[str]:
    """'enter,right,note add milk,enter' -> key names (words are typed letter by letter)."""
    keys: list[str] = []
    for token in (text or "").split(","):
        if not token:
            continue
        low = token.strip().lower()
        if low in NAMED_KEYS or low.startswith("ctrl+"):
            keys.append(" " if low == "space" else low)
        else:
            keys.extend(token)
    return keys


def snapshot_unified(args, ctx, registry) -> int:
    """Render a simulated session to PREFIX.txt + PREFIX.svg with no terminal."""
    from rich.console import Console

    from .ui.screen import build_screen

    cols, rows = (int(x) for x in str(getattr(args, "size", "100x30")).lower().split("x"))
    # A scratch data folder unless one was given, so docs never touch data/.
    if getattr(args, "data_dir", None) is None:
        ctx.data_dir = Path(tempfile.mkdtemp(prefix="cyberdeck-snap-"))
    dash = build_dashboard(args, ctx, registry, threaded=False, journal_path=False, start_services=False,
                           open_tab=False)
    b = dash.bridge
    if getattr(args, "data_dir", None) is None:
        for line in ("note add solder the joystick header", "note add MFRC522 on SPI0 CE0",
                     "todo add 3D-print the case lid", "todo add flash the companion Pi", "todo done 2",
                     "loot add power cell", "loot add evo chip", "loot add data cookie"):
            b.run_line(line, remember=False)
        for line in ("status", "pet status", "moon"):
            b.run_line(line)
    start = 1_800_000_000.0
    dash.diag.run_now("dns", dash.cfg.get("diag", "dns_default"))
    dash.diag.run_now("ping", dash.cfg.get("diag", "ping_default"))
    frames = int(getattr(args, "frames", 40))
    for i in range(frames):
        dash.step(start + i, ["r"] if i == frames // 2 else [])
    t = start + frames
    tab = getattr(args, "tab", None)
    if tab:
        dash.deck_ui_action(f":tab {tab}")
    for key in parse_key_script(getattr(args, "keys", None)):
        t += 0.5
        dash.step(t, [key])
    for _ in range(2):
        t += 0.5
        dash.step(t)
    dash.screen_width = cols
    console = Console(record=True, width=cols, height=rows, force_terminal=True, color_system="truecolor",
                      file=io.StringIO())
    console.print(build_screen(dash, t), height=rows)
    prefix = Path(args.snapshot)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".txt").write_text(console.export_text(clear=False), encoding="utf-8")
    console.save_svg(str(prefix.with_suffix(".svg")), title=dash.cfg.get("dash", "title") or "CYBERDECK")
    print(f"wrote {prefix.with_suffix('.txt')} and {prefix.with_suffix('.svg')}", file=sys.stderr)
    return 0


def _run_program(argv: list[str]) -> int | str:
    """Run ssh (or similar) in the real terminal; keep errors on screen."""
    import subprocess

    print(f"\n[cyberdeck] running: {' '.join(argv)}\n", flush=True)
    code = subprocess.call(argv)
    if code != 0:
        try:
            input(f"\n[cyberdeck] {argv[0]} exited with {code} - press Enter to return to the deck ")
        except EOFError:
            pass
    return code


def run_unified(args, ctx, registry, startup=None) -> int:
    """The interactive loop. Returns when you quit (q, `quit`, Ctrl+C)."""
    from rich.console import Console
    from rich.live import Live

    from .inputs import AlertLed, GpioButtons, KeyReader
    from .ui.screen import build_screen

    dash = build_dashboard(args, ctx, registry)
    b = dash.bridge
    for activity in startup or []:          # e.g. the boot animation
        b.open_activity(activity)
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
            live = Live(build_screen(dash), console=console, screen=True, auto_refresh=False,
                        redirect_stdout=False, redirect_stderr=False)
            live.start()
            try:
                while dash.running:
                    pressed = keys.read(timeout=b.frame_seconds(1.0 / hz))
                    dash.step(keys=pressed)
                    if b.external:                       # ssh: hand over the terminal
                        argv, b.external = b.external, None
                        live.stop()
                        with keys.paused():
                            b.run_external(argv, runner=_run_program)
                        live.start()
                    dash.screen_width = console.size.width
                    live.update(build_screen(dash), refresh=True)
            finally:
                live.stop()
        except KeyboardInterrupt:
            pass
        finally:
            dash.stop()
            time.sleep(0)
    return 0
