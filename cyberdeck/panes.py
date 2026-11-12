"""panes.py — the side-by-side views (output, pet, system…) and tabs.

The body of the deck screen can be shown two ways (layout.ini [panes]):

  * split — the command output on the left, and a column on the right
            with small live panels stacked top to bottom (pet, system…)
  * tabs  — one view at a time, full width, with a tab bar; switch with
            F4 / Ctrl-T (see keys.ini) or the `view` command

A "pane" is just a function that returns rows of text for a given
width and height. Built-in panes are registered at the bottom of this
file; feature modules add more (net, todo, music…) with register_pane().
Your own plugin can call register_pane() too.

compute_layout() works out where everything goes for a screen size.
It is plain arithmetic (no curses), so it is unit-tested.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cyberdeck.activity import Row
from cyberdeck.config import Settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context

# A pane renderer: (ctx, width, height) -> rows of (text, role) pieces.
PaneFn = Callable[["Context", int, int], list[Row]]


@dataclass(frozen=True)
class PaneSpec:
    """A named view: its title on screen and the function that draws it."""

    name: str
    title: str
    render: PaneFn


# Every known pane, by name. "output" is special: the TUI draws the log.
PANES: dict[str, PaneSpec] = {}


def register_pane(name: str, title: str, render: PaneFn) -> None:
    """Add (or replace) a pane so layout.ini can list it by name."""
    PANES[name.lower()] = PaneSpec(name.lower(), title, render)


# --- settings ---------------------------------------------------------

@dataclass
class PaneSettings:
    """layout.ini [panes] values. See CUSTOMIZING.md for each one."""

    mode: str = "split"                 # split | tabs
    side: list[str] = field(default_factory=lambda: ["pet", "system"])
    side_width: int = 30
    min_split_cols: int = 72            # narrower screens fall back to tabs
    tabs: list[str] = field(default_factory=lambda: ["output", "pet", "system"])
    ticker: bool = True                 # the mood / notification line
    hints: bool = True                  # hotkey hints on the bottom frame
    pet_refresh: float = 3.0            # seconds between pet pane updates


def load_pane_settings(settings: Settings | None = None) -> PaneSettings:
    """Read [panes] from layout.ini, keeping defaults for anything missing."""
    s = settings or Settings.load("layout.ini")
    base = PaneSettings()
    mode = s.get_str("panes", "mode", base.mode).lower()
    return PaneSettings(
        mode=mode if mode in {"split", "tabs"} else base.mode,
        side=[p.lower() for p in s.get_list("panes", "side", base.side)],
        side_width=max(16, s.get_int("panes", "side_width", base.side_width)),
        min_split_cols=s.get_int("panes", "min_split_cols", base.min_split_cols),
        tabs=[p.lower() for p in s.get_list("panes", "tabs", base.tabs)],
        ticker=s.get_bool("panes", "ticker", base.ticker),
        hints=s.get_bool("panes", "hints", base.hints),
        pet_refresh=s.get_float("panes", "pet_refresh", base.pet_refresh),
    )


# --- geometry -----------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    """A box on screen: top row, left column, height, width."""

    top: int
    left: int
    height: int
    width: int


@dataclass
class ScreenLayout:
    """Where each part of the deck screen goes for one terminal size."""

    mode: str                       # the mode actually used (split may fall back)
    status_row: int
    ticker_row: int | None
    top_divider: int
    body: Rect                      # full body area inside the frame
    output: Rect                    # log area (split) or active view (tabs)
    divider_col: int | None = None  # vertical line between output and side panes
    side: list[tuple[str, int, Rect]] = field(default_factory=list)  # (pane, title row, content)
    bottom_divider: int = 0
    prompt_row: int = 0


def compute_layout(rows: int, cols: int, ps: PaneSettings, mode: str | None = None) -> ScreenLayout:
    """Work out every row/column for a rows x cols screen."""
    want = (mode or ps.mode).lower()
    side_names = [n for n in ps.side if n in PANES]
    if want == "split" and (cols < ps.min_split_cols or not side_names):
        want = "tabs"

    status_row = 1
    ticker_row = 2 if ps.ticker else None
    top_divider = 3 if ps.ticker else 2
    bottom_divider = rows - 3
    prompt_row = rows - 2
    body_top = top_divider + 1
    body_h = max(0, bottom_divider - body_top)
    body = Rect(body_top, 1, body_h, max(0, cols - 2))

    if want != "split":
        # Leave one blank column of padding on each side of the text.
        out = Rect(body_top, 2, body_h, max(0, cols - 4))
        return ScreenLayout("tabs", status_row, ticker_row, top_divider, body, out,
                            bottom_divider=bottom_divider, prompt_row=prompt_row)

    side_w = min(ps.side_width, max(16, cols // 2))
    divider_col = cols - 1 - side_w - 1
    out = Rect(body_top, 2, body_h, max(0, divider_col - 3))
    side: list[tuple[str, int, Rect]] = []
    # Share the right column between the side panes. Each pane gets a
    # title row; the first pane's title sits on the top divider line.
    count = len(side_names)
    avail = body_h + 1
    each = avail // count if count else 0
    row = top_divider
    for i, name in enumerate(side_names):
        h = each if i < count - 1 else avail - each * (count - 1)
        if h < 2:
            break
        side.append((name, row, Rect(row + 1, divider_col + 2, h - 1, max(0, side_w - 1))))
        row += h
    return ScreenLayout("split", status_row, ticker_row, top_divider, body, out,
                        divider_col=divider_col, side=side,
                        bottom_divider=bottom_divider, prompt_row=prompt_row)


# --- shared drawing helpers ---------------------------------------------

def bar(value: float | None, width: int = 10, lo: float = 0.0, hi: float = 100.0,
        fill: str = "#", empty: str = "-") -> str:
    """A [####----] bar for value between lo and hi."""
    if value is None:
        return "[" + "?" * width + "]"
    span = (hi - lo) or 1.0
    n = int(round((max(lo, min(hi, value)) - lo) / span * width))
    return "[" + fill * n + empty * (width - n) + "]"


def level_role(value: float | None, warn: float, alert: float, higher_is_worse: bool = True) -> str:
    """Pick a color role for a number: ok / warn / alert."""
    if value is None:
        return "dim"
    if higher_is_worse:
        return "alert" if value >= alert else "warn" if value >= warn else "ok"
    return "alert" if value <= alert else "warn" if value <= warn else "ok"


# --- built-in panes -------------------------------------------------------

def cached_pet(ctx: "Context", max_age: float = 3.0) -> dict[str, Any] | None:
    """Latest pet payload, asking the companion at most every max_age seconds.

    If a pet watcher service is running (pet alerts), its latest snapshot is
    used instead so the pane and the alerts agree.
    """
    watcher = ctx.services.get("petwatch")
    if watcher is not None and getattr(watcher, "latest", None):
        return watcher.latest
    cache = ctx.services.setdefault("pet_cache", {"at": 0.0, "pet": None})
    now = time.monotonic()
    if cache["pet"] is None or now - cache["at"] >= max_age:
        cache["at"] = now
        try:
            if ctx.companion.is_linked():
                from cyberdeck.protocol import TYPE_PET_STATUS

                reply = ctx.companion.request(TYPE_PET_STATUS, {})
                cache["pet"] = (reply.payload or {}).get("pet")
        except Exception:  # link trouble shows as "no signal" in the pane
            cache["pet"] = None
    return cache["pet"]


def pet_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    """PULSE at a glance: face, mood, and need bars."""
    refresh = getattr(ctx.services.get("pane_settings"), "pet_refresh", 3.0)
    pet = cached_pet(ctx, refresh)
    if not pet:
        return [[("no signal from companion", "warn")], [(ctx.companion.link_label(), "dim")]]
    bw = max(4, min(12, width - 12))
    face = PET_FACES.get(str(pet.get("mood")), "(•_•)")
    hunger = pet.get("hunger")
    happy = pet.get("happiness")
    energy = pet.get("energy")
    rows: list[Row] = [
        [(f"{face} ", "title"), (f"{pet.get('name', '?')} · {pet.get('stage', '?')}", "text")],
        [("mood  ", "dim"), (str(pet.get("mood", "?")), "prompt")],
        [("food  ", "dim"), (bar(100 - hunger if hunger is not None else None, bw), level_role(hunger, 65, 82)), (f" {hunger}", "dim")],
        [("happy ", "dim"), (bar(happy, bw), level_role(happy, 45, 28, higher_is_worse=False)), (f" {happy}", "dim")],
        [("nrg   ", "dim"), (bar(energy, bw), level_role(energy, 35, 18, higher_is_worse=False)), (f" {energy}", "dim")],
        [("age   ", "dim"), (f"{pet.get('age_hours', '?')}h", "text")],
    ]
    return rows[:height]


# Little faces for the pet pane, by mood word.
PET_FACES = {
    "joyful": "(^o^)",
    "playful": "(^_^)",
    "content": "(•_•)",
    "hungry": "(>_<)",
    "tired": "(-_-)",
    "grumpy": "(ò_ó)",
    "bored": "(._.)",
}


def system_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    """CPU, memory, temperature, battery, and wifi as small bars."""
    r = ctx.sensors.read()
    bw = max(4, min(12, width - 14))
    rows: list[Row] = [
        [("temp ", "dim"), (bar(r.cpu_temp_c, bw, 30, 85), level_role(r.cpu_temp_c, 60, 70)), (f" {r.temp_label()}", "text")],
        [("cpu  ", "dim"), (bar(r.cpu_percent, bw), level_role(r.cpu_percent, 60, 85)), (f" {_pct(r.cpu_percent)}", "text")],
        [("mem  ", "dim"), (bar(r.mem_percent, bw), level_role(r.mem_percent, 70, 90)), (f" {_pct(r.mem_percent)}", "text")],
        [("bat  ", "dim"), (bar(r.battery_percent, bw), level_role(r.battery_percent, 30, 15, False)),
         (f" {_pct(r.battery_percent)}{'+' if r.charging else ''}", "text")],
        [("wifi ", "dim"), (bar(r.wifi_dbm, bw, -90, -30), level_role(r.wifi_dbm, -70, -80, False)),
         (f" {r.wifi_dbm if r.wifi_dbm is not None else '--'}dBm", "text")],
        [("up   ", "dim"), (_uptime(r.uptime_s), "text")],
        [("link ", "dim"), (ctx.companion.link_label(), "ok" if ctx.companion.is_linked() else "warn")],
    ]
    return rows[:height]


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:.0f}%"


def _uptime(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


register_pane("pet", "PULSE", pet_pane)
register_pane("system", "SYSTEM", system_pane)
