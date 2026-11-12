"""The LCD tile: a picture of the 16x2 screen inside the Rich interface.

In plain English: whatever the little LCD shows (the real one on the Pi,
or the pretend one on your computer) is drawn here as a lit 16x2 window,
blue when the backlight is on and dark when it is off. Below it: which
backend is running, how many notifications are waiting, and (zoomed in
with Enter) the last things it showed and what it skipped.

It only READS the shared LcdService (ctx.services["lcd"]); the
console's `lcd` command changes things.
"""

from __future__ import annotations

import time

from rich.console import Group
from rich.text import Text

from cyberdeck.lcd.text import sim_view

# Screen colours: a classic blue LCD1602, lit and unlit.
LIT = "bold #eaf4ff on #1f4fd8"
UNLIT = "#33405e on #0a0f22"


def lcd_screen_text(lcd, bezel: str) -> Text:
    """The 16x2 window with a frame and a backlight lamp."""
    lines, light = lcd.screen.snapshot()
    style = LIT if light else UNLIT
    cols = lcd.s.cols
    out = Text()
    out.append("╭" + "─" * (cols + 2) + "╮", style=bezel)
    out.append("  ☀ BL on" if light else "  ☾ BL off", style="bold #ffd23f" if light else "#5a6688")
    out.append("\n")
    for line in lines:
        out.append("│ ", style=bezel)
        out.append(sim_view(line), style=style)
        out.append(" │\n", style=bezel)
    out.append("╰" + "─" * (cols + 2) + "╯", style=bezel)
    return out


def render_lcd(dash, big: bool):
    th = dash.theme
    lcd = getattr(dash, "lcd", None)
    if lcd is None:
        return Text("LCD needs the main deck (python3 -m cyberdeck)", style=th.dim)
    if not lcd.enabled:
        return Text("LCD switched off · set backend = sim or i2c in config/lcd.ini", style=th.dim)
    parts = [lcd_screen_text(lcd, th.border)]
    info = Text()
    if lcd.s.backend == "i2c" and lcd.hw is None:
        info.append("i2c FAILED · sim only · `lcd status`", style=th.bad)
    else:
        info.append(lcd.s.backend, style=th.accent)
    info.append(f" · {len(lcd.queue)} waiting", style=th.dim)
    if lcd.queue.field_mode:
        info.append(" · FIELD: alerts only", style=th.good)
    if lcd.queue.quiet(dash.now):
        info.append(" · quiet hours", style=th.warn)
    info.no_wrap, info.overflow = True, "ellipsis"
    parts.append(info)
    if big:
        parts.append(Text("\nlast shown", style=f"bold {th.accent2}"))
        for when, kind, text in list(lcd.shown)[:5]:
            parts.append(Text(f" {time.strftime('%H:%M:%S', time.localtime(when))} {kind:<6} {text}",
                              style=th.text, no_wrap=True, overflow="ellipsis"))
        skipped = [(t, d, x) for t, d, x in list(lcd.queue.history) if d != "ok"][:3]
        if skipped:
            parts.append(Text("skipped (dup / rate / field / quiet)", style=f"bold {th.dim}"))
            for when, why, text in skipped:
                parts.append(Text(f" {why:<5} {text}", style=th.dim, no_wrap=True, overflow="ellipsis"))
        parts.append(Text("\nconsole: lcd test · lcd say <text> · lcd backlight on|off|auto", style=th.dim))
    return Group(*parts)
