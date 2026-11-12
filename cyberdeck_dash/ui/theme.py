"""Look-and-feel helpers: colours, the glitch title, sparklines, bars.

In plain English: small functions that turn numbers into pretty text.
Colours come from the active theme in config/dash.ini, so changing a
hex value there recolours the whole dashboard.
"""

from __future__ import annotations

import random

from rich.text import Text

from ..config import Theme

SPARK = "▁▂▃▄▅▆▇█"
GLITCH_CHARS = "▓▒░█#@%&$¥Ξ╳/\\"


def sparkline(values, width: int = 12, lo: float = -95, hi: float = -30) -> str:
    """RSSI history -> '▂▃▅▆▅▃' (last `width` samples, stronger = taller)."""
    vals = list(values)[-width:]
    out = []
    for v in vals:
        frac = (min(hi, max(lo, v)) - lo) / (hi - lo)
        out.append(SPARK[min(len(SPARK) - 1, int(frac * len(SPARK)))])
    return "".join(out).rjust(width, " ")


def bar(frac: float, width: int, theme: Theme, invert: bool = False) -> Text:
    """A filled bar like ■■■■■□□□. Colour shifts good->warn->bad as it fills
    (or the other way round when invert=True, e.g. for energy)."""
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    level = (1 - frac) if invert else frac
    colour = theme.good if level < 0.6 else theme.warn if level < 0.85 else theme.bad
    t = Text("■" * filled, style=colour)
    t.append("·" * (width - filled), style=theme.dim)
    return t


def rssi_style(rssi: int | None, theme: Theme) -> str:
    if rssi is None:
        return theme.dim
    return theme.good if rssi >= -60 else theme.warn if rssi >= -75 else theme.dim


def interest_style(level: int, theme: Theme) -> str:
    return {1: theme.dim, 2: theme.warn, 3: f"bold {theme.bad}"}.get(level, theme.text)


def glitch_title(text: str, theme: Theme, rng: random.Random, amount: int = 1) -> Text:
    """The header logo: each frame a few letters flicker into noise and the
    colours split like a bad VHS tape. amount 0 = steady."""
    out = Text()
    for i, ch in enumerate(text):
        style = f"bold {theme.accent}" if i % 2 == 0 else f"bold {theme.accent2}"
        if amount and ch != " " and rng.random() < 0.035 * amount:
            out.append(rng.choice(GLITCH_CHARS), style=f"bold {theme.bad}")
        else:
            out.append(ch, style=style)
    return out


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"
