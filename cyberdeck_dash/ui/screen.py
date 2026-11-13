"""The full-screen layout: header, panel grid (or one zoomed panel), footer.

In plain English: build_screen(dash) returns one Rich Layout that is
the whole dashboard for this frame. The header has the glitchy logo,
status chips and the clock. The body is a grid of panel tiles (the
focused one glows), or one panel full-screen when zoomed. The footer
shows the latest alert/log line and key hints. The main menu and help
are drawn over the body when open.

With the main deck attached (python3 -m cyberdeck), two more things can
take over the body: a deck "activity" (dungeon, typing hack, hack show,
boot, launcher) and PULSE's evolution pop-up. Both are drawn from the
deck's own rows through ui/rows.py, in the dashboard theme.
"""

from __future__ import annotations

import time
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .panels import PANELS
from .theme import glitch_title

DECK_HELP = [
    (":", "command console (type any deck command)"), ("F1-F12, ^O…", "hotkeys from config/keys.ini (F2 launcher)"),
    ("console", "⏎ run · Tab complete · ↑↓ history · PgUp/Dn scroll"), ("notes/todo", "a add · ↑↓ pick · space done · x del"),
    ("dungeon", "⏎ start · loot: g gift · x drop"), ("fun & tools", "↑↓ pick · ⏎ run"),
]

HELP = [
    ("[ ] / g", "switch group (tab)"), ("1-9", "focus panel in group"), ("←→ / tab", "move focus"), ("↑↓", "move focus / pick item"),
    ("enter / z", "full-screen panel"), ("esc", "back"), ("m", "main menu"), ("s", "scan (focused or all); on PULSE: full stats"),
    ("u", "PULSE uses move → fresh scan"), ("v", "next move"), ("f", "field mode on/off"),
    ("p / d", "ping a host / DNS lookup"), ("r", "tap a tag (sim)"), ("n", "note for last tag"), ("space", "tick checklist item"),
    ("t", "next theme"), ("q", "quit"),
]


def _chip(text: str, fg: str, bg: str) -> Text:
    return Text(f" {text} ", style=f"bold {fg} on {bg}")


def header(dash, now: float) -> Panel:
    th = dash.theme
    logo = Text("▓▒░ ", style=th.accent2) + glitch_title(dash.cfg.get("dash", "title") or "CYBERDECK//MAIN", th, dash.rng,
                                                          dash.cfg.int("dash", "glitch")) + Text(" ░▒▓", style=th.accent)
    chips = Text()
    chips.append_text(_chip(dash.mode.upper(), th.title_bg, th.accent))
    chips.append(" ")
    chips.append_text(_chip("FIELD ●" if dash.filter.field_mode else "FIELD ○",
                            th.title_bg if dash.filter.field_mode else th.dim,
                            th.good if dash.filter.field_mode else th.title_bg))
    chips.append(" ")
    if dash.bridge is not None:
        link_text = ("LINK ◆ SIM" if dash.bridge.link.is_sim else "LINK ◆") if dash.pet.online else "LINK DOWN"
    else:
        link_text = "LINK ◆" if dash.pet.online else "PET LOCAL"
    chips.append_text(_chip(link_text, th.title_bg if dash.pet.online else th.dim,
                            th.accent2 if dash.pet.online else th.title_bg))
    chips.append(" ")
    n_alerts = len(dash.alerts)
    chips.append_text(_chip(f"⚠ {n_alerts}", th.title_bg if n_alerts else th.dim, th.bad if n_alerts else th.title_bg))
    if dash.bridge is not None:
        # Deck badges, e.g. "! HUNGRY" from pet alerts or "♪ playing" from music.
        for text, role in dash.bridge.badges()[:1]:
            colour = {"alert": th.bad, "warn": th.warn, "ok": th.good}.get(role, th.accent)
            chips.append(" ")
            chips.append_text(_chip(text.strip(), th.title_bg, colour))
    clock = Text(datetime.fromtimestamp(now).strftime("%H:%M:%S"), style=f"bold {th.accent}")
    if (dash.screen_width or 100) >= 90:
        clock.append(datetime.fromtimestamp(now).strftime(" %a %d %b"), style=th.dim)
    grid = Table.grid(expand=True)
    # Logo and clock never wrap; the status chips get what is left and are
    # cropped first on a tiny screen (e.g. a 3.5" Pi display).
    grid.add_column(justify="left", no_wrap=True)
    grid.add_column(justify="center", ratio=1, no_wrap=True, overflow="crop")
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(logo, chips, clock)
    return Panel(grid, box=box.HEAVY, border_style=th.accent, style=f"on {th.title_bg}", padding=(0, 1))


def tile(dash, key: str, index: int, big: bool) -> Panel:
    th = dash.theme
    title, icon, render, scanner = PANELS[key]
    focused = index == dash.focus
    label = Text()
    label.append(f" {index + 1} ", style=f"bold {th.title_bg} on {th.focus if focused else th.border}")
    label.append(f" {icon} {title} ", style=f"bold {th.focus if focused else th.accent}")
    sub = None
    if scanner and scanner in dash.scanners:
        sc = dash.scanners[scanner]
        err = dash.scan_errors.get(scanner)
        sub = Text(f" {sc.describe()} {'✖' if err else '●'} ", style=th.bad if err else th.dim)
    try:
        body = render(dash, big)
    except Exception as exc:  # one broken panel must not kill the screen
        body = Text(f"panel error: {exc}", style=th.bad)
    return Panel(body, title=label, title_align="left", subtitle=sub, subtitle_align="right",
                 box=box.HEAVY if focused else box.ROUNDED, border_style=th.focus if focused else th.border,
                 padding=(0, 1))


def grid(dash) -> Layout:
    cols = dash.columns
    keys = [k for k in dash.panels if k in PANELS]
    detail = dash.cfg.tab_detail(dash.tab_title)
    ratios = dash.cfg.tab_ratios(dash.tab_title)      # e.g. "ratios = 7, 3" in [tab.console]
    body = Layout(name="body")
    rows = []
    for r in range(0, len(keys), cols):
        # A short last row (fewer tiles than columns) gets half the height.
        row = Layout(name=f"row{r}", ratio=2 if len(keys[r:r + cols]) == cols else 1)
        row.split_row(*[Layout(tile(dash, k, dash.panels.index(k), detail),
                               ratio=ratios[i] if i < len(ratios) else 1)
                        for i, k in enumerate(keys[r:r + cols])])
        rows.append(row)
    body.split_column(*rows)
    return body


def overlay_menu(dash) -> Panel:
    th = dash.theme
    t = Text()
    for i, (label, action) in enumerate(dash.menu_items()):
        sel = i == dash.menu_index
        if not action:   # group heading
            t.append(label + "\n", style=f"bold {th.accent2}")
            continue
        t.append(("▸" if sel else " ") + label + "\n", style=f"bold {th.title_bg} on {th.accent}" if sel else th.text)
    return Panel(t, title=Text(" ◢ MAIN MENU ◣ ", style=f"bold {th.accent2}"), box=box.DOUBLE,
                 border_style=th.accent2, width=40, style=f"on {th.title_bg}")


def overlay_help(dash) -> Panel:
    th = dash.theme
    t = Table.grid(padding=(0, 2))
    t.add_column(style=f"bold {th.accent}")
    t.add_column(style=th.text)
    for k, v in HELP:
        t.add_row(k, v)
    if dash.bridge is not None:
        t.add_row("", "")
        for k, v in DECK_HELP:
            t.add_row(k, v)
    return Panel(t, title=Text(" KEYS ", style=f"bold {th.accent2}"), box=box.DOUBLE, border_style=th.accent2,
                 width=64 if dash.bridge is not None else 48, style=f"on {th.title_bg}")


def footer(dash, now: float) -> Group:
    th = dash.theme
    line = Text(" ⟫ ", style=f"bold {th.accent2}")
    if dash.prompt is not None:
        line.append(f"{dash.prompt['label']}: ", style=th.accent)
        line.append(dash.prompt["text"] + "█", style=f"bold {th.text}")
        line.append("   enter save · esc cancel", style=th.dim)
    elif dash.ticker:
        ts, style, text = dash.ticker[0]
        line.append(datetime.fromtimestamp(ts).strftime("%H:%M:%S "), style=th.dim)
        line.append(text, style=getattr(th, style, th.text))
    if dash.bridge is not None:
        viz = dash.bridge.visualizer_text(now)
        if viz:
            line.truncate(max(0, (dash.screen_width or 100) - len(viz) - 2), pad=True)
            line.append(" " + viz, style=th.good)
    hints = Text(no_wrap=True, overflow="crop")
    if dash.bridge is not None and dash.bridge.activity is not None:
        act = dash.bridge.activity
        battle = getattr(act, "battle", None) or getattr(getattr(act, "game", None), "battle", None)
        in_battle = act.title == "BATTLE" or battle is not None
        in_stats = act.title == "STATS" or getattr(battle, "phase", "") == "stats"
        if in_stats:            # PULSE STATS (from the PULSE tile, `pet stats`, or a battle's PULSE card)
            keys = (("←→ h l", "page"), ("1-8", "jump"), ("↑↓", "scroll"), ("esc/q", "back"), ("^C", "close"))
        elif in_battle:
            keys = (("arrows/⏎", "pick"), ("f b p r", "fight bag pulse run"), ("esc", "back"), ("^C", "close"))
        else:
            keys = (("q/esc", "leave"), ("^C", "close"), ("keys", f"go to {act.title.lower()}"))
    elif dash.console_typing():
        keys = (("⏎", "run"), ("Tab", "complete"), ("↑↓", "history"), ("PgUp", "scroll"), ("Esc", "clear"),
                ("[ ]", "tabs (empty line)"), ("F2", "launcher"))
    elif dash.bridge is not None:
        on_pet = bool(dash.panels) and dash.panels[dash.focus] == "pet"
        keys = (("[ ]", "tab"), ("1-9", "panel"), ("⏎", "zoom/run"), (":", "console"), ("m", "menu"),
                ("s", "stats" if on_pet else "scan"),
                ("u", "move"), ("?", "keys"), ("q", "quit"))
    else:
        keys = (("[ ]", "group"), ("1-9", "panel"), ("⏎", "zoom"), ("m", "menu"), ("s", "scan"), ("u", "move"),
                ("f", "field"), ("?", "keys"), ("q", "quit"))
    for k, v in keys:
        hints.append(f" {k} ", style=f"bold {th.title_bg} on {th.accent}")
        hints.append(f" {v} ", style=th.dim)
    return Group(line, hints)


def tab_bar(dash) -> Text:
    """One line naming every group; the current one is lit up."""
    th = dash.theme
    t = Text(" ", no_wrap=True, overflow="crop")
    many = len(dash.tabs) > 3
    for i, (title, keys, _) in enumerate(dash.tabs):
        if i == dash.tab:
            t.append(f" ◢ {title.upper()} ◣ ", style=f"bold {th.title_bg} on {th.accent2}")
        else:
            # With many tabs, the others use their short names so all fit.
            t.append(f" {dash.cfg.tab_short(title) if many else title} ", style=f"{th.dim}")
        t.append(" ")
    t.append(f" [ ] switch · {len(dash.panels)} panels", style=th.dim)
    return t


def activity_panel(dash, now: float):
    """A deck activity (dungeon, typehack, launcher…) drawn in the body."""
    from .rows import RowsView

    act = dash.bridge.activity
    th = dash.theme
    title = Text(f" ▶ {act.title} ", style=f"bold {th.title_bg} on {th.accent2}")
    view = RowsView(lambda w, h: act.render(w, h), th)
    if getattr(act, "overlay", False):
        w, h = getattr(act, "overlay_size", (40, 12))
        return Align.center(Panel(view, title=title, title_align="left", box=box.DOUBLE, border_style=th.accent2,
                                  width=w + 2, height=h + 2, style=f"on {th.title_bg}"), vertical="middle")
    hint = str(getattr(act, "subtitle", "") or " q / esc leave ")
    return Panel(view, title=title, title_align="left", subtitle=Text(hint, style=th.dim),
                 subtitle_align="right", box=box.HEAVY, border_style=th.focus, padding=(0, 1))


def popup_panel(dash, popup, now: float):
    """PULSE's evolution / level-up pop-up (cyberdeck/popups.py) over the body."""
    from cyberdeck.popups import popup_size, render_popup

    from .rows import rows_text

    th = dash.theme
    w, h = popup_size(popup)
    w = min(w, max(20, (dash.screen_width or 100) - 6))
    rows = render_popup(popup, time.time(), w - 4, bool(dash.bridge.ctx.services.get("unicode", True)))
    return Align.center(Panel(rows_text(rows, th, w - 4), title=Text(f" {popup.title} ", style=f"bold {th.accent2}"),
                              box=box.DOUBLE, border_style=th.bad, width=w, height=len(rows) + 2,
                              style=f"on {th.title_bg}"), vertical="middle")


def build_screen(dash, now: float | None = None) -> Layout:
    now = time.time() if now is None else now
    root = Layout(name="root")
    root.split_column(Layout(header(dash, now), name="header", size=3), Layout(tab_bar(dash), name="tabs", size=1),
                      Layout(name="main"),
                      Layout(footer(dash, now), name="footer", size=2))
    if dash.bridge is not None and dash.bridge.activity is not None:
        root["main"].update(activity_panel(dash, now))   # popups (launcher) are centred
        return root
    if dash.zoomed:
        key = dash.panels[dash.focus]
        root["main"].update(tile(dash, key, dash.focus, True))
    else:
        root["main"].update(grid(dash))
    popup = dash.bridge.popup() if dash.bridge is not None else None
    if popup is not None and not (dash.menu_open or dash.show_help):
        root["main"].update(popup_panel(dash, popup, now))
    if dash.menu_open or dash.show_help:
        overlay = overlay_menu(dash) if dash.menu_open else overlay_help(dash)
        root["main"].update(Align.center(overlay, vertical="middle"))
    return root
