"""statsheet.py — PULSE's full stat sheet: the data, the pages, the paging.

In plain English: everything about PULSE on one pageable 48-column
screen (Overview, Combat, Care, Traits & Evolution, Moves & Loadout, Bag,
Battle Record, Discoveries). The companion does the hard part: it answers
`pet.stats` with the raw numbers AND the finished pages
(companion/docs/DUNGEON_INTEGRATION.md, "Full stats"). This file:

  * fetch_sheet()       asks for the sheet, with fallbacks for older
                        companions (pet.stats -> pet.status {full:true}
                        -> plain pet.status, which gets basic pages)
  * build_pages()       the companion's page layout, line for line. The
                        sim pet (companion.py) uses it so it answers
                        pet.stats exactly like the real companion.
  * StatsView           paging and drawing: left/right flip pages, 1-8
                        jump, up/down scroll a long page, esc/q close.
                        The XP / HP / EN / evolution bars are redrawn
                        from the raw fields, in colour.
  * load_stats_settings config/pet.ini [stats] (rows, wrap, start page)

No curses or Rich in here: render() returns rows of (text, role), so the
unified Rich UI, classic curses and headless --script all show the same
screen. Every row is exactly 48 columns of plain ASCII.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cyberdeck.protocol import TYPE_ERROR, TYPE_PET_STATS, TYPE_PET_STATUS

# ---------------------------------------------------------------------------
# The fixed page order and size limits (same as the companion's fullstats.py)
# ---------------------------------------------------------------------------
PAGE_TITLES: tuple[str, ...] = (
    "Overview",
    "Combat",
    "Care",
    "Traits & Evolution",
    "Moves & Loadout",
    "Bag",
    "Battle Record",
    "Discoveries",
)
TITLE_MAX = 20        # page titles
LINE_MAX = 44         # page lines (the inside of the 48-column box)
SCREEN_W = 48         # the stats screen is always exactly this wide

# Labels for PULSE's likes and dislikes; undiscovered ones show as ???.
PREF_LABELS = {
    "fav_food": "Fav food", "hated_food": "Hates food",
    "fav_signal": "Fav signal", "hated_signal": "Hates signal",
}

# Typographic characters the companion swaps for plain ASCII.
_ASCII_SWAPS = {"\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                "\u2026": "...", "\u00e9": "e"}


# ---------------------------------------------------------------------------
# Your settings: config/pet.ini [stats]
# ---------------------------------------------------------------------------
@dataclass
class StatsSettings:
    rows: int = 13                 # body rows on screen (a longer page scrolls)
    wrap: bool = True              # right on the last page goes back to page 1
    remember_page: bool = True     # reopen on the page you left
    start_page: int = 1            # first page when nothing is remembered (1-8)
    color_bars: bool = True        # redraw the XP/HP/EN/evolution bars in colour


def load_stats_settings(config_dir: str | Path | None = None) -> StatsSettings:
    from cyberdeck.config import Settings

    s = Settings.load("pet.ini", config_dir)
    base = StatsSettings()
    return StatsSettings(
        rows=max(4, min(30, s.get_int("stats", "rows", base.rows))),
        wrap=s.get_bool("stats", "wrap", base.wrap),
        remember_page=s.get_bool("stats", "remember_page", base.remember_page),
        start_page=max(1, s.get_int("stats", "start_page", base.start_page)),
        color_bars=s.get_bool("stats", "color_bars", base.color_bars),
    )


# ---------------------------------------------------------------------------
# Small text helpers (match the companion's so the pages look the same)
# ---------------------------------------------------------------------------
def fit_row(text: Any, limit: int = LINE_MAX) -> str:
    """Plain ASCII, inner spacing kept (for aligned rows), at most `limit` chars."""
    text = str(text if text is not None else "")
    for a, b in _ASCII_SWAPS.items():
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = "".join(ch if ch.isprintable() else " " for ch in text).rstrip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def bar(value: Any, maximum: Any, width: int = 10) -> str:
    """[####......] the companion's way (rounded to the nearest block)."""
    try:
        v, top = float(value), float(maximum)
    except (TypeError, ValueError):
        return "[" + "?" * width + "]"
    filled = int(round(width * max(0.0, min(1.0, v / top)))) if top > 0 else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


def pct(have: Any, need: Any) -> int:
    try:
        h, n = float(have), float(need)
    except (TypeError, ValueError):
        return 0
    return 100 if n <= 0 else int(max(0, min(100, round(100 * h / n))))


def _row(label: str, value: Any) -> str:
    return fit_row(f"{label:<13}{value}")


def obedience_tier(value: float) -> str:
    return "wild" if value < 25 else "willful" if value < 50 else "good" if value < 75 else "loyal"


# ---------------------------------------------------------------------------
# The eight pages from the raw fields (a port of companion/pet/fullstats.py)
# ---------------------------------------------------------------------------
def build_pages(r: dict[str, Any]) -> list[dict[str, Any]]:
    """[{title, lines}] in PAGE_TITLES order, built from a full raw sheet."""
    gen = f"  Gen {r['generation']}" if r["generation"] > 1 else ""
    overview = [
        f"{r['name']}" + (f"  ({r['species']})" if r["nickname"] else ""),
        f"{r['species']}  {r['stage']}  {r['type']}{gen}",
        f"Lv {r['level']}  XP {r['xp']}/{r['xp_next_level']}",
        f"XP {bar(r['xp_pct'], 100)} {r['xp_to_next']} to go",
        f"HP {bar(r['hp'], r['max_hp'])} {r['hp']}/{r['max_hp']}",
        f"EN {bar(r['energy'], r['max_energy'])} {r['energy']}/{r['max_energy']}",
        f"Mood {r['mood']}  Bond {r['bond']}",
        f"Status {r['status']}" + ("  TANTRUM" if r["tantrum"] else ""),
        f"Age {r['age_days']}d  Born {r['birthday']}",
    ]
    combat = [
        _row("HP", f"{r['hp']}/{r['max_hp']}"),
        _row("EN", f"{r['energy']}/{r['max_energy']}"),
        _row("Attack", r["attack"]),
        _row("Defense", r["defense"]),
        _row("Speed", f"{r['speed']}" + (f" (+{r['speed_bonus']} buff)" if r["speed_bonus"] else "")),
        _row("Type", r["type"]),
        _row("Crit", f"{round(r['crit_chance'] * 100, 1)}%"),
        _row("Accuracy", f"{r['accuracy_mod']:+d}"),
        _row("Damage", f"x{r['damage_mult']:g}"),
        _row("Disobey", f"{int(r['disobey_chance'] * 100)}%"),
    ]
    care = [
        _row("Hunger", f"{bar(r['hunger'], 100)} {r['hunger']}"),
        _row("Happiness", f"{bar(r['happiness'], 100)} {r['happiness']}"),
        _row("Energy", f"{bar(r['energy'], 100)} {r['energy']}"),
        _row("Obedience", f"{r['discipline']} ({r['obedience']})"),
        _row("Mistakes", r["care_mistakes"]),
        _row("Mood", f"{r['mood']} ({r['mood_offset']:+g})"),
        _row("Bond", f"{r['bond']} " + "<3" * r["bond_hearts"]),
        _row("Sick", r["sick"] or "no"),
    ]
    for mod in r["modifiers"][:4]:
        care.append(fit_row(f"  {mod.get('kind', '?')} {float(mod.get('value', 0)):+.0f}"))
    q = r["quirks"]
    traits = [
        _row("Catchphrase", q.get("catchphrase", "-")),
        _row("Dance", q.get("dance", "-")),
        _row("Sneeze", q.get("sneeze", "-")),
        _row("Fav idle", str(q.get("fav_idle", "-")).replace("_", " ")),
    ]
    for key, label in PREF_LABELS.items():
        traits.append(_row(label, r["favorites"].get(key, "???")))
    evo = r["evolution"]
    if evo["next_stage"]:
        traits.append(fit_row(f"Next: {evo['next_stage']}  {evo['progress_pct']}%" + ("  READY" if evo["ready"] else "")))
        for gate in ("age", "level", "xp"):
            g = evo["gates"].get(gate)
            if g:
                traits.append(fit_row(f"{gate.upper():<6}{bar(g['pct'], 100)} {g['pct']}%"))
        names = [c.get("species", "?") + ("" if c.get("available", True) else "(locked)") for c in evo["choices"]]
        if names:
            traits.append(fit_row("Can become: " + ", ".join(names)))
    else:
        traits.append("Final form reached.")
    moves = []
    for m in r["moves"]:
        star = "*" if m["name"] in r["loadout"] else " "
        moves.append(fit_row(f"{star}{m['name'][:15]:<15} {m['type']:<4} P{m['power']:<3} EN{m['energy_cost']:<3} R{m['rank']}"))
    moves.append(fit_row("* = loadout" + (" (auto)" if r["loadout_auto"] else "")))
    bag = [fit_row(f"{row['name'][:22]:<22} x{row['qty']:<3}" + ("" if row["usable_in_battle"] else " (no battle)"))
           for row in r["bag"]]
    bag = bag or ["(empty)"]
    bag.append(fit_row(f"{r['bag_items']} kinds, {r['bag_total']} items"))
    rec, riv = r["record"], r["rival"]
    record = [
        _row("Fights", rec["fights"]),
        _row("Wins", rec["wins"]),
        _row("Losses", rec["losses"]),
        _row("Fled", rec["flees"]),
        _row("Bosses", rec["bosses"]),
        _row("Fainted", rec["faints"]),
        _row("Rival", riv["name"] or "-"),
        _row("vs Rival", f"{riv['losses']}W {riv['wins']}L"),
    ]
    found = [
        _row("Discoveries", r["discoveries"]),
        _row("Networks", r["networks_known"]),
        _row("Treasures", r["treasures"]),
        _row("Trinkets", r["trinkets"]),
        _row("Favorite", r["favorite_trinket"] or "-"),
        _row("Adventures", r["adventures"]),
        _row("Diary", f"{r['diary_entries']} entries"),
    ]
    bodies = [overview, combat, care, traits, moves, bag, record, found]
    return [{"title": title, "lines": [fit_row(line) for line in body]}
            for title, body in zip(PAGE_TITLES, bodies)]


def flat_lines(pages: list[dict[str, Any]]) -> list[str]:
    """The same pages as one list, with `== Title ==` rows as page breaks."""
    out: list[str] = []
    for page in pages:
        out.append(fit_row(f"== {page['title']} =="))
        out.extend(page["lines"])
    return out


# ---------------------------------------------------------------------------
# Older companions: basic pages from plain pet.status
# ---------------------------------------------------------------------------
def _num(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    return None


def basic_sheet(pet: dict[str, Any], evolution: dict[str, Any] | None = None) -> dict[str, Any]:
    """A sheet from a plain pet object: the fields it has, pages for those only."""
    p = pet if isinstance(pet, dict) else {}
    evo = evolution if isinstance(evolution, dict) else p.get("evolution") if isinstance(p.get("evolution"), dict) else {}
    name = str(p.get("name") or "PULSE")
    raw: dict[str, Any] = {"name": name, "species": p.get("species"), "stage": p.get("stage"),
                           "type": p.get("type"), "mood": p.get("mood"), "basic": True}
    for key in ("level", "xp", "hp", "max_hp", "energy", "attack", "defense", "speed", "hunger", "happiness",
                "care_mistakes"):
        raw[key] = _num(p.get(key))
    raw["max_energy"] = _num(p.get("max_energy")) or 100
    raw["age_hours"] = p.get("age_hours") if isinstance(p.get("age_hours"), (int, float)) else None
    moves = [m.get("name") if isinstance(m, dict) else m for m in (p.get("moves") or [])]
    raw["moves"] = [{"name": str(m)} for m in moves if m]
    choices = [c for c in (evo.get("choices") or []) if isinstance(c, dict)] if evo else []
    progress = evo.get("progress") if evo else None
    raw["evolution"] = {"next_stage": (evo or {}).get("next_stage"), "ready": bool((evo or {}).get("ready")),
                        "progress_pct": int(round(100 * float(progress))) if isinstance(progress, (int, float)) else None,
                        "gates": {}, "choices": choices}

    def has(*keys: str) -> bool:
        return all(raw.get(k) is not None for k in keys)

    overview = [name + (f"  ({raw['species']})" if raw["species"] else "")]
    kind = "  ".join(str(raw[k]) for k in ("stage", "type") if raw.get(k))
    if kind:
        overview.append(kind)
    if has("level"):
        overview.append(f"Lv {raw['level']}" + (f"  XP {raw['xp']}" if has("xp") else ""))
    if has("hp", "max_hp"):
        overview.append(f"HP {bar(raw['hp'], raw['max_hp'])} {raw['hp']}/{raw['max_hp']}")
    if has("energy"):
        overview.append(f"EN {bar(raw['energy'], raw['max_energy'])} {raw['energy']}/{raw['max_energy']}")
    if raw["mood"]:
        overview.append(f"Mood {raw['mood']}" + ("  zZ asleep" if p.get("asleep") else ""))
    if raw["age_hours"] is not None:
        overview.append(f"Age {round(float(raw['age_hours']), 1)}h")
    combat = [_row(label, raw[key]) for label, key in (("HP", "hp"), ("Attack", "attack"), ("Defense", "defense"),
                                                       ("Speed", "speed")) if has(key)]
    if combat and has("hp", "max_hp"):
        combat[0] = _row("HP", f"{raw['hp']}/{raw['max_hp']}")
    care = []
    for label, key in (("Hunger", "hunger"), ("Happiness", "happiness"), ("Energy", "energy")):
        if has(key):
            care.append(_row(label, f"{bar(raw[key], 100)} {raw[key]}"))
    if has("care_mistakes"):
        care.append(_row("Mistakes", raw["care_mistakes"]))
    traits = []
    e = raw["evolution"]
    if e["next_stage"]:
        traits.append(f"Next: {e['next_stage']}" + (f"  {e['progress_pct']}%" if e["progress_pct"] is not None else "")
                      + ("  READY" if e["ready"] else ""))
        names = [str(c.get("species", "?")) + ("" if c.get("available", True) else "(locked)") for c in choices]
        if names:
            traits.append("Can become: " + ", ".join(names))
    moves_page = [" " + str(m["name"]) for m in raw["moves"]]
    pages = []
    for title, body in zip(PAGE_TITLES, (overview, combat, care, traits, moves_page)):
        if body:
            pages.append({"title": title, "lines": [fit_row(x) for x in body]})
    pages[-1]["lines"].append("")
    pages[-1]["lines"].append("(older companion: basic stats only)")
    raw["pages"] = pages
    raw["lines"] = flat_lines(pages)
    return raw


# ---------------------------------------------------------------------------
# Asking the companion (pet.stats -> pet.status {full:true} -> pet.status)
# ---------------------------------------------------------------------------
def clean_pages(raw: Any) -> list[dict[str, Any]]:
    """The companion's pages, made safe: ASCII, <= 20 / <= 44 chars, no junk."""
    pages = []
    for page in raw if isinstance(raw, list) else []:
        if not isinstance(page, dict):
            continue
        title = fit_row(page.get("title") or f"Page {len(pages) + 1}", TITLE_MAX)
        lines = [fit_row(x) for x in page.get("lines") or [] if isinstance(x, (str, int, float))]
        pages.append({"title": title, "lines": lines})
    return pages


def pages_from_lines(lines: Any) -> list[dict[str, Any]]:
    """Split a flat `lines` list on its `== Title ==` rows (if `pages` is missing)."""
    pages: list[dict[str, Any]] = []
    for line in lines if isinstance(lines, list) else []:
        m = re.match(r"^==\s*(.+?)\s*==$", str(line).strip())
        if m:
            pages.append({"title": m.group(1), "lines": []})
        elif pages:
            pages[-1]["lines"].append(line)
    return clean_pages(pages)


def fetch_sheet(request: Callable[..., Any]) -> tuple[dict[str, Any] | None, str, str]:
    """(sheet, source, note). source: pet.stats | pet.status full | pet.status | "".

    `request` is ctx.companion.request (or a fake in tests). A sheet always
    has `pages`; None means nothing could be fetched and note says why.
    """
    def ask(typ: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        try:
            reply = request(typ, payload)
        except Exception as exc:        # CompanionError, timeouts, link down
            return None, f"could not reach PULSE: {exc}"
        body = reply.payload or {}
        if reply.type == TYPE_ERROR:
            return None, str(body.get("code") or "error")
        return body, ""

    def usable(sheet: Any) -> dict[str, Any] | None:
        if not isinstance(sheet, dict):
            return None
        pages = clean_pages(sheet.get("pages")) or pages_from_lines(sheet.get("lines"))
        return dict(sheet, pages=pages) if pages else None

    body, why = ask(TYPE_PET_STATS, {})
    if body is not None and usable(body):
        return usable(body), "pet.stats", ""
    if why.startswith("could not reach"):
        return None, "", why
    body, why2 = ask(TYPE_PET_STATUS, {"full": True})
    if body is not None:
        if usable(body.get("full")):
            return usable(body["full"]), "pet.status full", ""
        if isinstance(body.get("pet"), dict):
            return basic_sheet(body["pet"], body.get("evolution")), "pet.status", "basic stats"
    body, why3 = ask(TYPE_PET_STATUS, {})
    if body is not None and isinstance(body.get("pet"), dict):
        return basic_sheet(body["pet"], body.get("evolution")), "pet.status", "basic stats"
    return None, "", why3 or why2 or why or "no stats"


# ---------------------------------------------------------------------------
# The screen: paging and drawing (rows of (text, role), 48 columns)
# ---------------------------------------------------------------------------
Row = list[tuple[str, str]]

# Bars the deck redraws from the raw fields: (page, label) -> how to read them.
# Each reader returns (value, maximum, higher_is_worse) or None. higher_is_worse
# None = a progress bar (XP, evolution gates): one colour, green when full.
BAR_SOURCES: dict[tuple[str, str], Callable[[dict[str, Any]], tuple[float, float, bool | None] | None]] = {
    ("Overview", "XP"): lambda r: (r["xp_pct"], 100, None) if _has(r, "xp_pct") else None,
    ("Overview", "HP"): lambda r: (r["hp"], r["max_hp"], False) if _has(r, "hp", "max_hp") else None,
    ("Overview", "EN"): lambda r: (r["energy"], r.get("max_energy") or 100, False) if _has(r, "energy") else None,
    ("Care", "Hunger"): lambda r: (r["hunger"], 100, True) if _has(r, "hunger") else None,
    ("Care", "Happiness"): lambda r: (r["happiness"], 100, False) if _has(r, "happiness") else None,
    ("Care", "Energy"): lambda r: (r["energy"], 100, False) if _has(r, "energy") else None,
    ("Traits & Evolution", "AGE"): lambda r: _gate(r, "age"),
    ("Traits & Evolution", "LEVEL"): lambda r: _gate(r, "level"),
    ("Traits & Evolution", "XP"): lambda r: _gate(r, "xp"),
}
_BAR_RE = re.compile(r"^(?P<label>[A-Za-z]+)(?P<gap>\s*)\[(?P<bar>[#.?]{3,})\](?P<rest>.*)$")


def _has(r: dict[str, Any], *keys: str) -> bool:
    return all(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) for k in keys)


def _gate(r: dict[str, Any], name: str) -> tuple[float, float, bool | None] | None:
    g = ((r.get("evolution") or {}).get("gates") or {}).get(name) if isinstance(r.get("evolution"), dict) else None
    return (g["pct"], 100, None) if isinstance(g, dict) and _has(g, "pct") else None


def bar_role(value: float, top: float, higher_is_worse: bool | None = False) -> str:
    """Colour of a bar: green / yellow / red by how good it is; progress bars are one colour."""
    frac = max(0.0, min(1.0, value / top)) if top else 0.0
    if higher_is_worse is None:
        return "ok" if frac >= 1.0 else "prompt"
    if higher_is_worse:
        frac = 1.0 - frac
    return "ok" if frac > 0.5 else "warn" if frac > 0.25 else "alert"


class StatsView:
    """One open stats screen: which page, how far scrolled, and the drawing."""

    def __init__(self, sheet: dict[str, Any], source: str = "pet.stats", page: int = 0,
                 settings: StatsSettings | None = None, note: str = "") -> None:
        self.sheet = sheet
        self.source = source
        self.note = note
        self.settings = settings or StatsSettings()
        self.pages: list[dict[str, Any]] = sheet.get("pages") or [{"title": "Stats", "lines": ["(no stats)"]}]
        self.page = max(0, min(page, len(self.pages) - 1))
        self.scroll = 0

    @property
    def name(self) -> str:
        return fit_row(str(self.sheet.get("name") or "PULSE").upper(), 16)

    # --- keys ---------------------------------------------------------------------
    def key(self, key: str) -> bool:
        """One key. True = close the screen (esc / q)."""
        k = key.lower() if len(key) == 1 else key
        n = len(self.pages)
        if k in ("esc", "q", "backspace"):
            return True
        if k in ("right", "l"):
            self.flip(1)
        elif k in ("left", "h"):
            self.flip(-1)
        elif k.isdigit() and 1 <= int(k) <= n:
            self.go(int(k) - 1)
        elif k in ("down", "j"):
            self.scroll = min(self.scroll + 1, self.max_scroll())
        elif k in ("up", "k"):
            self.scroll = max(0, self.scroll - 1)
        elif k in ("home",):
            self.go(0)
        elif k in ("end",):
            self.go(n - 1)
        return False

    def flip(self, step: int) -> None:
        n = len(self.pages)
        target = self.page + step
        if self.settings.wrap:
            target %= n
        self.go(max(0, min(n - 1, target)))

    def go(self, index: int) -> None:
        self.page = max(0, min(index, len(self.pages) - 1))
        self.scroll = 0

    def rows_room(self, height: int) -> int:
        return max(1, min(self.settings.rows, height - 6))   # 6 = rules, header, footer

    def max_scroll(self, height: int = 99) -> int:
        return max(0, len(self.pages[self.page]["lines"]) - self.rows_room(height))

    # --- drawing ------------------------------------------------------------------
    def _bar_line(self, title: str, line: str) -> list[tuple[str, str]]:
        """A page line; a bar on it is redrawn from the raw numbers, in colour."""
        m = _BAR_RE.match(line)
        if not m:
            return [(line, "text")]
        width = len(m.group("bar"))
        reader = BAR_SOURCES.get((title, m.group("label")))
        found = reader(self.sheet) if reader else None
        if found is None:
            return [(line, "text")]
        value, top, worse = found
        text = bar(value, top, width) if self.settings.color_bars else "[" + m.group("bar") + "]"
        role = bar_role(float(value), float(top), worse) if self.settings.color_bars else "text"
        return [(m.group("label") + m.group("gap"), "dim"), (text, role), (m.group("rest"), "text")]

    def render(self, width: int = SCREEN_W, height: int = 30) -> list[Row]:
        n = len(self.pages)
        page = self.pages[self.page]
        title = page["title"]
        room = self.rows_room(height)
        self.scroll = max(0, min(self.scroll, self.max_scroll(height)))
        inner = SCREEN_W - 4

        rows: list[Row] = []

        def rule() -> None:
            rows.append([("+" + "-" * (SCREEN_W - 2) + "+", "dim")])

        def line(*pieces: tuple[str, str]) -> None:
            out: Row = [("| ", "dim")]
            used = 0
            for text, role in pieces:
                text = text[: inner - used]
                if text:
                    out.append((text, role))
                    used += len(text)
            out.append((" " * (inner - used) + " |", "dim"))
            rows.append(out)

        # Header: "PULSE STATS          Traits & Evolution  4/8"
        right = f"{title}  {self.page + 1}/{n}"
        left = f"{self.name} STATS"
        left = left[: max(0, inner - len(right) - 1)]
        rule()
        line((left, "title"), (" " * (inner - len(left) - len(right)), "text"), (right, "prompt"))
        rule()
        # Body: the page's lines, scrolled when the page is longer than the room.
        body = page["lines"][self.scroll: self.scroll + room]
        for i in range(room):
            text = body[i] if i < len(body) else ""
            if title == "Moves & Loadout" and text.startswith("*"):
                line(("*", "ok"), *self._bar_line(title, text[1:]))
            else:
                line(*self._bar_line(title, text))
        rule()
        more = self.max_scroll(height) > 0
        hint = "[<-/->] page  [1-%d] jump  [esc] back" % n if n > 1 else "[esc] back"
        if more:
            hint = ("[<-/->] page [up/dn] more [esc] back" if n > 1 else "[up/dn] more  [esc] back")
            hint += " v" if self.scroll < self.max_scroll(height) else " ^"
        line((hint, "dim"))
        rule()
        pad = " " * max(0, (width - SCREEN_W) // 2)
        return [[(pad, "text")] + r if pad else r for r in rows][:height]

    def text(self, height: int = 40) -> list[str]:
        """The current page as plain strings."""
        return ["".join(t for t, _r in row) for row in self.render(SCREEN_W, height)]

    def all_text(self) -> list[str]:
        """Every page, one box after another (headless `pet stats`)."""
        out: list[str] = []
        keep, keep_rows = self.page, self.settings.rows
        self.settings.rows = max(keep_rows, max(len(p["lines"]) for p in self.pages))
        for i in range(len(self.pages)):
            self.go(i)
            out += self.text(self.settings.rows + 6)
        self.settings.rows = keep_rows
        self.go(keep)
        return out

    def find_page(self, ref: str) -> int | None:
        """'3' or 'bag' or 'battle' -> a page index."""
        ref = ref.strip().lower()
        if ref.isdigit():
            i = int(ref) - 1
            return i if 0 <= i < len(self.pages) else None
        for i, p in enumerate(self.pages):
            if p["title"].lower().startswith(ref) or ref in p["title"].lower():
                return i
        return None
