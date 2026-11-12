"""Panels for the main deck's features: console, dungeon, notes, fun & tools.

In plain English: these are the tiles that bring the original deck
(cyberdeck/) into the dashboard. Like ui/panels.py, every function gets
the Dashboard and a `big` flag and returns something Rich can draw; they
only READ state. Keys for them are handled in app.py (deck_key()).

Most of them reuse the deck's own code:
  * the console shows DeckBridge's log and the line you are typing,
  * PULSE / sky / music / net reuse the deck's pane functions from
    cyberdeck/panes.py and cyberdeck/features/ through RowsView,
  * notes, todo and loot read the same files the commands use
    (data/notes.txt, data/todo.txt, data/loot.json).

Add your own: write `def render_mine(dash, big)`, add it to DECK_PANELS at
the bottom, and list "mine" in a [tab.*] panels line in config/dash.ini.
docs/DASHBOARD.md walks through it.
"""

from __future__ import annotations

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.table import Table
from rich.text import Text

from .rows import RowsView, role_style
from .theme import clip


def _no_deck(dash) -> Text:
    return Text("start with  python3 -m cyberdeck  to use the deck features here", style=dash.theme.dim)


def _hint(dash, text: str) -> Text:
    return Text(text, style=dash.theme.dim, no_wrap=True, overflow="ellipsis")


def _pad(rows: list, height: int, footer: list) -> list:
    """Rows, then blank lines, then the footer rows pinned to the bottom."""
    room = max(0, height - len(footer))
    rows = rows[:room]
    return rows + [[] for _ in range(room - len(rows))] + footer


def _window(count: int, selected: int, room: int) -> range:
    """Indexes to show so the selected row stays on screen."""
    room = max(1, room)
    top = 0 if selected < room else selected - room + 1
    return range(top, min(count, top + room))


# ---------------------------------------------------------------------------
# Console: the deck's command line (help, pet feed, weather, ssh, gpio…)
# ---------------------------------------------------------------------------
class ConsoleView:
    """Log on top, the command line at the bottom, sized to the tile."""

    def __init__(self, dash) -> None:
        self.dash = dash

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        dash, b, th = self.dash, self.dash.bridge, self.dash.theme
        width = max(10, options.max_width)
        height = options.height or options.max_height or 10
        log_h = max(1, height - 2)
        prompt = b.prompt
        window = b.log_window(width, log_h)
        for i in range(log_h):
            line = window[i] if i < len(window) else ""
            style = th.dim if prompt.strip() and line.startswith(prompt) else th.text
            t = Text(line, style=style, no_wrap=True, overflow="crop", end="\n")
            if i == 0 and b.scroll:
                tag = f" ↑{b.scroll} "
                t = Text(line[: max(0, width - len(tag))].ljust(width - len(tag)), style=style, end="\n")
                t.append(tag, style=f"bold {th.warn}")
            yield t
        # Mood line (from config/moods.ini) or key hints, then the prompt.
        mood = b.mood_line(dash.now)
        if mood:
            text, role = mood
            yield Text(clip(f"~ {text}", width), style=role_style({"info": "text"}.get(role, role), th),
                       no_wrap=True, end="\n")
        else:
            yield Text(clip("⏎ run · Tab complete · ↑↓ history · PgUp/PgDn scroll · Esc clear · [ ] tabs (empty line)",
                            width), style=th.dim, no_wrap=True, end="\n")
        line = Text(prompt, style=f"bold {th.accent2}", no_wrap=True, overflow="crop", end="\n")
        room = max(1, width - len(prompt) - 1)
        cur = b.cursor
        left = cur - (room - 1) if cur > room - 1 else 0
        shown = b.input[left: left + room]
        at = cur - left
        line.append(shown[:at], style=f"bold {th.text}")
        line.append(shown[at:at + 1] or " ", style=f"bold {th.title_bg} on {th.accent}")
        line.append(shown[at + 1:], style=f"bold {th.text}")
        yield line


def render_console(dash, big: bool):
    if dash.bridge is None:
        return _no_deck(dash)
    return ConsoleView(dash)


def render_output(dash, big: bool):
    """The newest command's output (handy on the Fun & Tools tab)."""
    if dash.bridge is None:
        return _no_deck(dash)
    th = dash.theme
    lines = dash.bridge.last_output or ["(run something from the list on the left)"]

    def rows(width, height):
        return [[(ln, "dim" if ln.startswith(dash.bridge.prompt) else "text")] for ln in lines[-height:]]

    return RowsView(rows, th)


# ---------------------------------------------------------------------------
# Deck panes reused as tiles: PULSE face, sky, music, net, todo summary
# ---------------------------------------------------------------------------
def deck_pane(name: str):
    """A tile that draws cyberdeck's own pane `name` (see cyberdeck/panes.py)."""

    def render(dash, big: bool):
        if dash.bridge is None:
            return _no_deck(dash)
        from cyberdeck.panes import PANES

        spec = PANES.get(name)
        if spec is None:
            return Text(f"the '{name}' feature is switched off (config/layout.ini [features])", style=dash.theme.dim)
        return RowsView(lambda w, h: spec.render(dash.bridge.ctx, w, h), dash.theme)

    return render


# ---------------------------------------------------------------------------
# Notes & Todo (same files as the `note` and `todo` commands)
# ---------------------------------------------------------------------------
def render_notes(dash, big: bool):
    if dash.bridge is None:
        return _no_deck(dash)
    from cyberdeck.features.notes import notes

    th = dash.theme
    items = notes(dash.bridge.ctx).read()
    sel = dash.clamp_sel("notes", len(items))
    focused = dash.panels[dash.focus] == "notes" if dash.panels else False

    def rows(width, height):
        out = []
        if not items:
            out.append([("(no notes yet: press a to add one)", "dim")])
        for i in _window(len(items), sel, height - 1):
            stamp, _, text = items[i].partition(" | ")
            mark = "▸" if (i == sel and focused) else " "
            out.append([(f"{mark}{i + 1:>2} ", "prompt" if i == sel and focused else "dim"),
                        (stamp[5:] + "  " if text else "", "dim"), (text or stamp, "text")])
        return _pad(out, height, [[("a add · ↑↓ pick · x delete · : console (note find …)", "dim")]])

    return RowsView(rows, th)


def render_todo(dash, big: bool):
    if dash.bridge is None:
        return _no_deck(dash)
    from cyberdeck.features.notes import todos

    th = dash.theme
    items = todos(dash.bridge.ctx).items()
    sel = dash.clamp_sel("todo", len(items))
    focused = dash.panels[dash.focus] == "todo" if dash.panels else False

    def rows(width, height):
        out = []
        if not items:
            out.append([("(nothing to do: press a to add a task)", "dim")])
        for i in _window(len(items), sel, height - 2):
            done, text = items[i]
            mark = "▸" if (i == sel and focused) else " "
            out.append([(f"{mark}{i + 1:>2} ", "prompt" if i == sel and focused else "dim"),
                        ("[x] " if done else "[ ] ", "ok" if done else "warn"), (text, "dim" if done else "text")])
        open_n = sum(1 for d, _ in items if not d)
        return _pad(out, height, [[(f"{open_n} open · {len(items) - open_n} done", "dim")],
                                  [("a add · space done/undo · x delete · c clear done", "dim")]])

    return RowsView(rows, th)


# ---------------------------------------------------------------------------
# Dungeon: start a run, PULSE's real combat sheet, the loot bag
# ---------------------------------------------------------------------------
def render_dungeon(dash, big: bool):
    if dash.bridge is None:
        return _no_deck(dash)
    th = dash.theme
    b = dash.bridge
    linked = b.link.is_linked()
    lines = [Text("ASCII roguelike · PULSE at your side", style=f"bold {th.accent}", no_wrap=True,
                  overflow="ellipsis")]
    status = Text("PULSE  ", style=th.dim, no_wrap=True, overflow="ellipsis")
    status.append("joins with real stats" if linked else "link down: solo run", style=th.good if linked else th.warn)
    lines.append(status)
    start = Text("\n ⏎ / space ", style=f"bold {th.title_bg} on {th.accent2}")
    start.append("  start a run", style=f"bold {th.text}")
    lines.append(start)
    keys = Table.grid(padding=(0, 1))
    keys.add_column(style=th.accent, no_wrap=True)
    keys.add_column(style=th.dim, no_wrap=True, overflow="ellipsis")
    for k, v in (("arrows/wasd", "move, bump = fight"), (">", "stairs down"), ("1-9", "PULSE's move"),
                 ("space / .", "auto / PULSE rests"), ("g / i", "gift loot / bag"), ("q / esc", "leave, keep loot")):
        keys.add_row(k, v)
    lines += [Text(""), keys]
    if dash.dungeon_last:
        lines.append(Text("\nlast run", style=f"bold {th.accent2}"))
        for line in dash.dungeon_last[-4:]:
            lines.append(Text(line, style=th.text, no_wrap=not big, overflow="ellipsis"))
    lines.append(Text("\n@ you  # wall  > stairs  * loot  ! kit", style=th.dim, no_wrap=True, overflow="ellipsis"))
    lines.append(Text("b g v w D  bug glitch virus worm daemon", style=th.dim, no_wrap=True, overflow="ellipsis"))
    return Group(*lines)


def render_combat(dash, big: bool):
    """PULSE's combat sheet from pet.combat.stats (asked when the tab opens)."""
    th = dash.theme
    stats = dash.pet.combat or {}
    pet = dash.pet.view()
    lines = []
    head = Text()
    head.append(f"{pet.get('name', 'PULSE')} ", style=f"bold {th.accent2}")
    head.append(f"Lv {stats.get('level', pet.get('level', '?'))}", style=th.good)
    if stats.get("hp") is not None:
        head.append(f"  HP {stats.get('hp')}/{stats.get('max_hp', '?')}", style=th.text)
    if pet.get("energy") is not None:
        head.append(f"  EN {pet.get('energy')}", style=th.text)
    lines.append(head)
    if any(k in stats for k in ("attack", "defense", "speed")):
        lines.append(Text(f"ATK {stats.get('attack', '?')}  DEF {stats.get('defense', '?')}  SPD {stats.get('speed', '?')}",
                          style=th.dim))
    moves = stats.get("moves") or []
    if moves:
        t = Table(box=None, show_header=True, header_style=f"bold {th.accent}", pad_edge=False, padding=(0, 1, 0, 0),
                  expand=True)
        for col in ("#", "move", "pow", "acc", "cost", "rank"):
            t.add_column(col, justify="left" if col == "move" else "right", no_wrap=True,
                         overflow="ellipsis" if col == "move" else "fold", ratio=1 if col == "move" else None)
        for i, m in enumerate(moves, 1):
            if not isinstance(m, dict):
                continue
            rank = m.get("rank")
            t.add_row(str(i), Text(clip(str(m.get("name", "?")), 16), style=th.text), str(m.get("power", "–")),
                      str(m.get("accuracy", "–")), str(m.get("energy_cost", m.get("cost", "–"))),
                      f"r{rank}" + (f" +{m['bonus']}" if m.get("bonus") else "") if rank is not None else "–")
        lines.append(t)
    elif dash.pet.online:
        lines.append(Text("asking PULSE for its combat sheet…", style=th.dim))
    else:
        lines.append(Text("no link: PULSE stays home, runs are solo", style=th.warn))
    if stats.get("fainted"):
        lines.append(Text("fainted: `pet rest` or a repair kit revives it", style=th.bad))
    if stats.get("asleep"):
        lines.append(Text("asleep: wake it with `pet sleep off`", style=th.warn))
    return Group(*lines)


def render_loot(dash, big: bool):
    if dash.bridge is None:
        return _no_deck(dash)
    from cyberdeck.features.dungeon import loot_bag

    th = dash.theme
    items = loot_bag(dash.bridge.ctx).items()
    sel = dash.clamp_sel("loot", len(items))
    focused = dash.panels[dash.focus] == "loot" if dash.panels else False

    def rows(width, height):
        out = []
        if not items:
            out.append([("bag is empty: go crawl!", "dim")])
        for i in _window(len(items), sel, height - 1):
            it = items[i]
            mark = "▸" if (i == sel and focused) else " "
            role = {"legendary": "alert", "rare": "prompt", "uncommon": "ok"}.get(str(it.get("rarity")), "text")
            out.append([(f"{mark}{i + 1:>2} ", "prompt" if i == sel and focused else "dim"),
                        (str(it.get("name", "?")), role), (f"  {it.get('rarity', '')} {it.get('kind', '')}", "dim")])
        return _pad(out, height, [[("↑↓ pick · g gift to PULSE · x drop", "dim")]])

    return RowsView(rows, th)


# ---------------------------------------------------------------------------
# Fun & Tools: every launcher entry (keys.ini [launcher]) one key away
# ---------------------------------------------------------------------------
def render_launcher(dash, big: bool):
    if dash.bridge is None:
        return _no_deck(dash)
    th = dash.theme
    items = dash.launcher_items()
    sel = dash.clamp_sel("launcher", len(items))
    focused = dash.panels[dash.focus] == "launcher" if dash.panels else False

    def rows(width, height):
        out = []
        for i in _window(len(items), sel, height - 1):
            it = items[i]
            here = i == sel and focused
            label = f"{'▸' if here else ' '} {it.label}"
            out.append([(label.ljust(min(width, 26)), "prompt" if here else "text"),
                        (it.action if width > 34 else "", "dim")])
        return _pad(out, height, [[("↑↓ pick · ⏎ run · … entries go to the console", "dim")]])

    return RowsView(rows, th)


# Registry: key -> (title, icon, render function, scanner name or None).
# app.py hides these when the deck is not attached (tests, old entry points).
DECK_PANELS = {
    "console": ("CONSOLE", "❯", render_console, None),
    "avatar": ("PULSE", "◆", deck_pane("pet"), None),
    "dungeon": ("DUNGEON", "⚔", render_dungeon, None),
    "combat": ("PULSE COMBAT", "✦", render_combat, None),
    "loot": ("LOOT BAG", "✚", render_loot, None),
    "notes": ("NOTES", "✎", render_notes, None),
    "todo": ("TODO", "☑", render_todo, None),
    "launcher": ("FUN & TOOLS", "★", render_launcher, None),
    "sky": ("SKY", "☾", deck_pane("sky"), None),
    "music": ("MUSIC", "♪", deck_pane("music"), None),
    "net": ("NET", "⇅", deck_pane("net"), None),
    "output": ("OUTPUT", "▤", render_output, None),
}
