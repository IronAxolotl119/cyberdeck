"""petstats.py — the PULSE STATS screen: every stat, eight pages, 48 columns.

In plain English: `pet stats` opens a pageable sheet with everything
about PULSE (Overview, Combat, Care, Traits & Evolution, Moves & Loadout,
Bag, Battle Record, Discoveries). The companion writes the pages; the
deck pages through them and draws the bars in colour (statsheet.py).

Where it opens:
  * `pet stats [page]`      console, launcher, any full-screen UI
  * `s` on the PULSE tile   unified Rich UI (Deck & PULSE tab)
  * `s` on the PULSE tab    classic curses (tabs mode), or F3
  * STATS on the PULSE card in a battle (no turn used)

Keys: left/right (or h/l) flip pages, 1-8 jump to a page, up/down scroll
a long page, esc or q goes back. Headless (`--script "pet stats"`): every
page is printed, or just one with `pet stats 3` / `pet stats bag`.

Settings: config/pet.ini [stats] (rows, wrap, remember_page, start_page,
color_bars).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cyberdeck.activity import Activity, Row
from cyberdeck.commands import PET_ACTIONS, PET_COMPLETERS
from cyberdeck.statsheet import PAGE_TITLES, StatsView, fetch_sheet, load_stats_settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


# ---------------------------------------------------------------------------
# Opening a sheet (shared by the command, the tiles and the battle menu)
# ---------------------------------------------------------------------------
def open_view(ctx: "Context | None", request: Any, page: str | int | None = None,
              config_dir: Any = None) -> tuple[StatsView | None, str]:
    """(view, "") or (None, why). Starts on the remembered page unless one is given."""
    sheet, source, note = fetch_sheet(request)
    if sheet is None:
        return None, f"no stats from PULSE: {note}"
    settings = load_stats_settings(config_dir if config_dir is not None else getattr(ctx, "config_dir", None))
    start = settings.start_page - 1
    if settings.remember_page and ctx is not None:
        start = int(ctx.services.get("stats_page", start))
    view = StatsView(sheet, source, start, settings, note)
    if page is not None:
        index = view.find_page(str(page))
        if index is None:
            return None, f"no stats page '{page}' — pages: " + ", ".join(
                f"{i}. {p['title']}" for i, p in enumerate(view.pages, 1))
        view.go(index)
    return view, ""


def remember(ctx: "Context | None", view: StatsView) -> None:
    if ctx is not None and view.settings.remember_page:
        ctx.services["stats_page"] = view.page


# ---------------------------------------------------------------------------
# The full-screen activity (unified Rich UI and classic curses)
# ---------------------------------------------------------------------------
class StatsActivity(Activity):
    title = "STATS"
    tick = 0.5
    subtitle = " ←→ page · 1-8 jump · esc back "

    def __init__(self, ctx: "Context", view: StatsView, show_all: bool = True) -> None:
        super().__init__()
        self.ctx = ctx
        self.view = view
        self.show_all = show_all      # headless: every page, or only the one asked for

    def on_key(self, key: str) -> None:
        if key == "ctrl+c" or self.view.key(key):
            self.done = True
            remember(self.ctx, self.view)

    def render(self, width: int, height: int) -> list[Row]:
        return self.view.render(width, height)

    def headless(self) -> list[str]:
        out = self.view.all_text() if self.show_all else self.view.text()
        if self.view.note:
            out.append(f"({self.view.note}: this companion has no pet.stats)")
        return out


# ---------------------------------------------------------------------------
# `pet stats [page|all|lines]`
# ---------------------------------------------------------------------------
def pet_stats(ctx: "Context", args: list[str]) -> list[str]:
    """pet stats [page]: the PULSE STATS screen (page = 1-8 or a title word)."""
    rest = " ".join(args[1:]).strip()
    if rest.lower() == "lines":                     # the flat list, for scripts and grep
        view, why = open_view(ctx, ctx.companion.request)
        return (view.sheet.get("lines") or []) if view else [why]
    view, why = open_view(ctx, ctx.companion.request, rest if rest and rest.lower() != "all" else None)
    if view is None:
        return [why]
    ctx.activity = StatsActivity(ctx, view, show_all=not rest or rest.lower() == "all")
    return []


def page_completer(ctx: "Context", rest: list[str]) -> list[str]:
    del ctx
    if rest:
        return []
    return [t.split()[0].lower() for t in PAGE_TITLES] + ["all", "lines"]


def register(reg: "Registry") -> None:
    """Hook `stats` into `pet` (no new top-level command)."""
    del reg
    PET_ACTIONS["stats"] = pet_stats
    PET_COMPLETERS["stats"] = page_completer
