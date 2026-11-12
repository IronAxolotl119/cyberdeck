"""cyberdeck/lcd — the little 16x2 character LCD on the main deck.

In plain English: this package drives an LCD1602 screen (16 characters,
2 lines, I2C backpack) so the deck can show PULSE's blurbs and short
notifications without you looking at the main display. Out of the box
it is a pretend screen ("sim") you can see in the Deck & PULSE tab; set
`backend = i2c` in config/lcd.ini on the Pi to drive the real one.

Files:
  text.py      ASCII mapping, the 8 custom glyphs, word-wrap, ticker
  messages.py  notification priorities, de-dupe, rate limit, quiet hours
  pages.py     idle pages (PULSE, blurbs, clock) and line layout
  backends.py  the sim screen and the real I2C screen (RPLCD / smbus2)
  service.py   LcdService: the background thread that runs it all
  settings.py  config/lcd.ini
  __main__.py  python3 -m cyberdeck.lcd --demo (test the screen alone)

The deck side (console commands, the classic pane, PULSE event hooks)
is cyberdeck/features/lcd.py. Wiring and setup: docs/LCD.md.

get_lcd(ctx) returns the one shared LcdService for a running deck.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .messages import ALERT, INFO, MANUAL, NOTICE, LcdMessage, NotifyQueue
from .service import LcdService
from .settings import LcdSettings, load_lcd_settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context

__all__ = ["ALERT", "INFO", "MANUAL", "NOTICE", "LcdMessage", "LcdService", "LcdSettings", "NotifyQueue",
           "get_lcd", "load_lcd_settings"]


def get_lcd(ctx: "Context", threaded: bool | None = None) -> LcdService:
    """The deck's shared LCD service, built (and started) on first use.

    ctx.services["lcd_options"] (set by app.py from --lcd / --lcd-sim)
    may override the backend and add a text mirror file.
    """
    existing = ctx.services.get("lcd")
    if existing is not None:
        return existing
    from dataclasses import replace

    from cyberdeck.config import Settings, repo_root

    opts = ctx.services.get("lcd_options") or {}
    settings = load_lcd_settings(Settings.load("lcd.ini", ctx.config_dir))
    if opts.get("backend"):
        settings = replace(settings, backend=opts["backend"])
    if threaded is None:
        threaded = opts.get("threaded", True)

    def chirp() -> None:
        """Short buzzer chirp on the deck's buzzer pin (hardware.ini)."""
        import threading

        from cyberdeck.features.hardware import get_hardware

        threading.Thread(target=get_hardware(ctx).beep, args=(1760.0, 60.0), daemon=True).start()

    lcd = LcdService(settings, threaded=threaded, base_dir=repo_root(), chirp=chirp, mirror=opts.get("mirror"))
    ctx.services["lcd"] = lcd
    lcd.start()
    return lcd
