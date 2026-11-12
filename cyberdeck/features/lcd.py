"""lcd.py (feature) — the 16x2 LCD inside the deck: commands, pane, hooks.

In plain English: the LCD itself lives in cyberdeck/lcd/. This file
plugs it into the deck:

  * starts it when the interface starts (Rich UI and --classic), so
    PULSE's blurbs rotate on the screen while you work;
  * forwards every PULSE event the pet watcher sees (hungry, level up,
    new move, evolve choice…) to the LCD as a notification;
  * adds an "lcd" pane (classic split / tab view) showing the sim screen;
  * adds the console command:

      lcd                   same as lcd status
      lcd status            backend, backlight, the screen, what was skipped
      lcd test              all 8 custom glyphs + a scrolling line, with a flash
      lcd say <text>        show your own text right now (scrolls if long)
      lcd backlight on|off|auto
      lcd next              skip to the next idle page
      lcd clear             drop waiting notifications
      lcd reload            re-read text/lcd_blurbs.txt after you edit it

Settings: config/lcd.ini. Wiring and setup: docs/LCD.md.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from cyberdeck.activity import Row

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

# The pet watcher's own (polled) event names -> the LCD's event names.
LOCAL_KINDS = {"evolve": "evolved"}


# ---------------------------------------------------------------------------
# Start-up and hooks
# ---------------------------------------------------------------------------
def start_lcd(ctx: "Context") -> None:
    """UI_STARTUP: build the shared LcdService (its thread starts too)."""
    from cyberdeck.lcd import get_lcd

    get_lcd(ctx)


def on_pet_event(ctx: "Context", event, now: float) -> None:
    """petwatch EVENT_HOOKS: PULSE news -> LCD notification + blurb."""
    lcd = ctx.services.get("lcd")
    if lcd is None:
        return
    kind = LOCAL_KINDS.get(event.kind, event.kind)
    lcd.pet_event(kind, event.text, event.payload, now=now)


def headless_start(ctx: "Context") -> None:
    """--headless/--script: run the LCD only if it is real or was asked for.

    A real I2C screen (config/lcd.ini backend = i2c) keeps working in
    headless mode; --lcd / --lcd-sim start it too. Otherwise it starts
    the first time you type an `lcd` command.
    """
    from cyberdeck.config import Settings
    from cyberdeck.lcd import get_lcd

    opts = ctx.services.get("lcd_options") or {}
    backend = opts.get("backend") or Settings.load("lcd.ini", ctx.config_dir).get_str("lcd", "backend", "sim")
    if opts or backend.lower() == "i2c":
        get_lcd(ctx)


def headless_lines(ctx: "Context") -> list[str]:
    """After each headless command: feed the LCD, print it if --lcd-sim."""
    lcd = ctx.services.get("lcd")
    if lcd is None or not lcd.enabled:
        return []
    lcd.ui_tick(ctx, time.time())
    if not (ctx.services.get("lcd_options") or {}).get("print"):
        return []
    shown = lcd.screen.render(ascii_only=True)
    if shown == ctx.services.get("lcd_printed"):
        return []
    ctx.services["lcd_printed"] = shown
    return ["[lcd] " + line for line in shown]


# ---------------------------------------------------------------------------
# The console command
# ---------------------------------------------------------------------------
USAGE = "usage: lcd [status | test | say <text> | backlight on|off|auto | next | clear | reload]"


def lcd_command(ctx: "Context", args: list[str]) -> list[str]:
    from cyberdeck.lcd import MANUAL, get_lcd

    lcd = get_lcd(ctx)
    sub = args[0].lower() if args else "status"
    now = time.time()
    if not lcd.enabled and sub not in ("status", "help"):
        return ["lcd is switched off: set backend = sim or i2c in config/lcd.ini"]
    if sub == "status":
        return lcd.status_lines(now)
    if sub == "test":
        from cyberdeck.lcd.text import G

        glyphs = "".join(G.values())
        lcd.interrupt_manual()
        lcd.notify("0123456789ABCDEF", glyphs + " TEST", MANUAL, "test", hold=4, now=now)
        lcd.notify("Hello from CYBERDECK//MAIN - long lines scroll like this ticker {heart}", "", MANUAL, "test",
                   now=now)
        return ["lcd test: 8 custom glyphs + a 16-char ruler, then a scrolling line (with a backlight flash)",
                "glyphs: heart, PULSE face, wifi bars, bell, bolt, diamond, note, food"]
    if sub == "say":
        text = " ".join(args[1:]).strip()
        if not text:
            return ["usage: lcd say <text>"]
        lcd.say(text, now=now)
        return [f"lcd: {text}"]
    if sub in ("backlight", "light", "bl"):
        mode = args[1].lower() if len(args) > 1 else ""
        if mode not in ("on", "off", "auto"):
            return ["usage: lcd backlight on|off|auto"]
        lcd.set_backlight(mode)
        return [f"lcd backlight {mode}"]
    if sub == "next":
        with lcd._lock:
            lcd.current = None
            lcd.page_until = 0.0
        return ["lcd: next page"]
    if sub == "clear":
        with lcd._lock:
            n = lcd.queue.clear()
            lcd.current = None
            lcd.page_until = 0.0
        return [f"lcd: dropped {n} waiting notification(s)"]
    if sub == "reload":
        from pathlib import Path

        from cyberdeck.config import repo_root
        from cyberdeck.lcd.pages import load_blurbs

        path = Path(lcd.s.blurb_file)
        path = path if path.is_absolute() else repo_root() / path
        lcd.blurbs.blurbs = load_blurbs(path)
        total = sum(len(v) for v in lcd.blurbs.blurbs.values())
        return [f"lcd: {total} blurbs loaded from {lcd.s.blurb_file} ({', '.join(lcd.blurbs.blurbs)})"]
    return [USAGE]


# ---------------------------------------------------------------------------
# The "lcd" pane (classic split / tabs; the Rich UI has its own tile)
# ---------------------------------------------------------------------------
def lcd_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    lcd = ctx.services.get("lcd")
    if lcd is None:
        return [[("LCD not started", "dim")]]
    if not lcd.enabled:
        return [[("LCD off (config/lcd.ini)", "dim")]]
    unicode = ctx.services.get("unicode", True)
    role = "accent" if lcd.screen.backlight else "dim"
    rows: list[Row] = [[(line[:width], role)] for line in lcd.screen.render(ascii_only=not unicode)]
    note = lcd.s.backend if not lcd.hw_error else "i2c error - see `lcd status`"
    rows.append([(f"{note} · {len(lcd.queue)} waiting"[:width], "dim")])
    return rows[:height]


def register(reg: "Registry") -> None:
    from cyberdeck.commands import Command
    from cyberdeck.features import UI_STARTUP
    from cyberdeck.features.petwatch import EVENT_HOOKS
    from cyberdeck.panes import register_pane

    reg.register(Command(
        name="lcd",
        handler=lcd_command,
        summary="the 16x2 LCD: status, test, say <text>, backlight",
        usage="lcd [status|test|say|backlight]",
        subcommands=("status", "test", "say", "backlight", "next", "clear", "reload"),
        completer=lambda ctx, words: ["on", "off", "auto"] if words and words[0] in ("backlight", "light", "bl") else [],
    ))
    register_pane("lcd", "LCD", lcd_pane)
    if start_lcd not in UI_STARTUP:
        UI_STARTUP.append(start_lcd)
    if on_pet_event not in EVENT_HOOKS:
        EVENT_HOOKS.append(on_pet_event)
