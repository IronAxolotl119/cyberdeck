"""netdungeon.py — network dungeons: pick a dungeon, crawl its floors.

In plain English: every Wi-Fi network, Bluetooth device and LAN device
PULSE has discovered unlocks a dungeon named after it. `dungeon` opens a
picker of those dungeons (when the companion supports them); pick one and
the deck builds its floors, one at a time, from the companion's seed.

  dungeon                  the picker (or the classic dungeon, see below)
  dungeon list             the picker as text (scripts / headless)
  dungeon enter <n> [F]    enter dungeon n (or a name) at start floor F
  dungeon classic          the classic random dungeon, as before
  dungeons                 same as `dungeon list`

The picker (48 columns, the spec's mockup):
  up/down (or j/k)   choose a dungeon
  left/right         choose the start floor: 1 or any checkpoint unlocked
                     (6, 11, ...). Any floor snaps down to a checkpoint.
  enter              go in;  c = the classic dungeon;  esc / q = close

In a dungeon everything works like the classic crawl (arrows move, walk
into an enemy to battle, g gift, i bag), plus:
  >                  take the stairs. With every enemy beaten the floor
                     counts as "cleared" (floor XP + a loot roll);
                     otherwise "stairs" (half the floor XP, no roll).
  q / esc            leave the dungeon (the run ends: "fled")
  $ data chest       extra XP for PULSE, reported at the stairs
  * pickup           an item for PULSE's bag, reported at the stairs
  ^ trap             hidden until you are next to it or step on it
  ! repair kit       heals you (or PULSE, whoever is more hurt)

Floors: a boss (always kernel panic, with the dungeon's own title) every
5th floor; the floor after each boss is a checkpoint (1, 6, 11, ...).
If PULSE faints (or you do), the run ends; next time you can start from
the last checkpoint. Battle XP only ever comes from pet.combat.end
(which carries dungeon_id + floor, so the x1.5 in-range bonus applies);
pet.dungeon.floor's `xp` is only the extras (data chests).

Without a companion, or with one that doesn't know pet.dungeons, or
with no dungeons found yet, `dungeon` opens the classic dungeon exactly
as before. Settings: config/dungeons.ini. Spec: companion/docs/
DUNGEON_INTEGRATION.md "Network dungeons". Map rules: cyberdeck/
netdungeons.py.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cyberdeck import netdungeons as ndg
from cyberdeck.activity import Activity, Row
from cyberdeck.battledata import ITEMS, SCREEN_W, clamp_say
from cyberdeck.commands import CompanionError, run_pet_hooks
from cyberdeck.config import Settings
from cyberdeck.protocol import (
    TYPE_ERROR,
    TYPE_PET_DUNGEON_ENTER,
    TYPE_PET_DUNGEON_FLOOR,
    TYPE_PET_DUNGEON_LEAVE,
    TYPE_PET_DUNGEONS,
)

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

# --- settings (config/dungeons.ini) ----------------------------------------------

# palette name -> (wall colour, floor colour). The companion picks the palette
# name; [palettes] in config/dungeons.ini can change any of these.
PALETTE_COLOURS: dict[str, tuple[str, str]] = {
    "cyan_grid": ("2bd9e8", "1c7f88"), "teal_mesh": ("20b2aa", "16716c"),
    "sky_relay": ("6ec6ff", "3a7ca8"), "aqua_lattice": ("5ff0d8", "2f8f80"),
    "amber_spark": ("ffbf3f", "9a6f1e"), "yellow_coil": ("f5e14a", "8f8327"),
    "gold_arc": ("e0b040", "856826"), "orange_surge": ("ff8c3a", "9a5322"),
    "green_term": ("39ff7a", "1f8f45"), "mint_matrix": ("8affc1", "4a9a70"),
    "lime_ledger": ("b6f23a", "6a8f22"), "jade_index": ("3fbf8f", "246f54"),
    "violet_null": ("b07cff", "64459a"), "gray_static": ("a8a8b0", "5e5e66"),
    "indigo_abyss": ("7a6cff", "433b8f"), "black_glass": ("8a8aa8", "45455a"),
}


@dataclass
class NetSettings:
    """config/dungeons.ini — every knob for network dungeons."""

    enabled: bool = True          # [dungeons] enabled: no = `dungeon` is always the classic one
    classic_row: bool = True      # a "classic dungeon" row at the bottom of the picker
    picker_rows: int = 20         # most dungeon rows shown at once (fewer on a short screen; it scrolls)
    dark_sight: int = 3           # how far you see on a dark floor (lit floors: 7)
    trap_damage: int = 2          # a trap's hit (+1 every 4 floors)
    repair_kits: bool = True      # one ! repair kit per floor for you
    chest_chance: float = 0.3     # share of pickups that are data chests (XP) vs bag items
    chest_xp: int = 3             # a data chest's XP: chest_xp + the floor number
    colors: str = "palette"       # palette = the dungeon's colours | theme = your theme's | off
    glyphs: bool = True           # draw walls/floors with the dungeon's own characters
    palettes: dict[str, tuple[str, str]] = field(default_factory=lambda: dict(PALETTE_COLOURS))
    limits: ndg.MapLimits = field(default_factory=ndg.MapLimits)
    # [alerts]: "NEW DUNGEON: name" notices (the LCD has its own list in lcd.ini)
    alert_console: bool = True


def _hex6(text: str) -> str | None:
    text = text.strip().lstrip("#")
    return text.lower() if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text) else None


def load_settings(config_dir: Any = None) -> NetSettings:
    s = Settings.load("dungeons.ini", config_dir)
    d = NetSettings()
    palettes = dict(PALETTE_COLOURS)
    for name, raw in s.section("palettes").items():
        parts = [_hex6(p) for p in raw.split(",")]
        if len(parts) == 2 and all(parts):
            palettes[name.strip().lower()] = (parts[0], parts[1])   # type: ignore[assignment]
    lim = ndg.MapLimits()
    limits = ndg.MapLimits(
        max_width=max(24, min(120, s.get_int("map", "max_width", lim.max_width))),
        max_height=max(10, min(40, s.get_int("map", "max_height", lim.max_height))),
        max_rooms=max(1, min(20, s.get_int("map", "max_rooms", lim.max_rooms))),
        max_traps=max(0, min(20, s.get_int("map", "max_traps", lim.max_traps))),
        max_items=max(0, min(20, s.get_int("map", "max_items", lim.max_items))),
        max_loops=max(0, min(10, s.get_int("map", "max_loops", lim.max_loops))))
    colors = s.get_str("dungeons", "colors", d.colors).lower()
    return NetSettings(
        enabled=s.get_bool("dungeons", "enabled", d.enabled),
        classic_row=s.get_bool("dungeons", "classic_row", d.classic_row),
        picker_rows=max(3, min(40, s.get_int("dungeons", "picker_rows", d.picker_rows))),
        dark_sight=max(1, min(7, s.get_int("dungeons", "dark_sight", d.dark_sight))),
        trap_damage=max(0, min(20, s.get_int("dungeons", "trap_damage", d.trap_damage))),
        repair_kits=s.get_bool("dungeons", "repair_kits", d.repair_kits),
        chest_chance=max(0.0, min(1.0, s.get_float("dungeons", "chest_chance", d.chest_chance))),
        chest_xp=max(0, min(100, s.get_int("dungeons", "chest_xp", d.chest_xp))),
        colors=colors if colors in {"palette", "theme", "off"} else d.colors,
        glyphs=s.get_bool("dungeons", "glyphs", d.glyphs),
        palettes=palettes, limits=limits,
        alert_console=s.get_bool("alerts", "console", d.alert_console))


# --- talking to the companion ------------------------------------------------------

def ask(ctx: "Context", typ: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str, str]:
    """One request -> (body, "", "") or (None, error code, a line saying why)."""
    try:
        if not ctx.companion.is_linked():
            return None, "link_down", "no companion link"
        reply = ctx.companion.request(typ, payload)
    except CompanionError as exc:
        return None, "link_down", f"could not reach PULSE: {exc}"
    body = reply.payload if isinstance(reply.payload, dict) else {}
    if reply.type == TYPE_ERROR:
        code = str(body.get("code") or "error")
        if code == "unknown_type":
            return None, code, "this companion has no network dungeons yet"
        return None, code, f"PULSE says no: {body.get('message') or code}"
    return body, "", ""


def clean_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """pet.dungeons rows, tidied (bad rows dropped; names display-safe; no raw ids)."""
    rows = []
    for raw in body.get("dungeons") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"].strip():
            continue
        starts = [f for f in raw.get("starts") or [] if isinstance(f, int) and not isinstance(f, bool) and f >= 1]
        name = ndg.to_ascii(str(raw.get("name") or ""))
        if not name or ndg.looks_like_mac(name):
            name = f"hidden-{raw['id'][:4]}"
        rows.append(dict(raw, name=name[:ndg.NAME_MAX], starts=sorted(set(starts)) or [1],
                         home=raw.get("home") is True, in_range=raw.get("in_range") is True,
                         rare=raw.get("rare") is True))
    return rows


def picker_lines(rows: list[dict[str, Any]], sel: int = 0, starts: dict[str, int] | None = None,
                 classic: bool = False, top: int = 0, shown: int = 99, near: int | None = None) -> list[str]:
    """The picker box, 48 columns (the spec's mockup)."""
    starts = starts or {}
    near = sum(1 for r in rows if r["in_range"]) if near is None else near
    out = [ndg.box_rule(), ndg.box_line(ndg.two_ends("DUNGEONS", f"{len(rows)} found  {near} near")), ndg.box_rule()]
    total = len(rows) + (1 if classic else 0)
    for i in range(top, min(total, top + shown)):
        if i < len(rows):
            row = rows[i]
            out.append(ndg.box_line(ndg.picker_row(row, i == sel, starts.get(row["id"], row.get("checkpoint", 1)))))
        else:
            out.append(ndg.box_line(("> " if i == sel else "  ") + "classic dungeon (random every time)"))
    if not rows:
        out.append(ndg.box_line("No dungeons yet. Scan for networks and"))
        out.append(ndg.box_line("PULSE turns each one into a dungeon."))
    out += [ndg.box_rule(), ndg.box_line("near = in range (x1.5 XP)  * = rare"),
            ndg.box_line("[ent] enter  [<-/->] start floor  [esc]"), ndg.box_rule()]
    return out


# --- one run --------------------------------------------------------------------------

@dataclass
class NetRun:
    """One trip into one network dungeon: the floor info and what happened on this floor."""

    info: dict[str, Any]
    settings: NetSettings
    flavor: str = ""
    loot: list[str] = field(default_factory=list)       # item ids picked up on this floor
    chest_xp: int = 0                                   # data chest XP on this floor
    defeated: list[str] = field(default_factory=list)   # enemy ids beaten on this floor
    boss_beaten: bool = False
    fainted: bool = False
    floors_done: int = 0
    floor_xp: int = 0                                   # XP the companion granted for floors

    @property
    def id(self) -> str:
        return str(self.info.get("id") or "")

    @property
    def name(self) -> str:
        return clamp_say(self.info.get("name") or "dungeon", ndg.NAME_MAX)

    @property
    def floor(self) -> int:
        f = self.info.get("floor")
        return f if isinstance(f, int) and not isinstance(f, bool) and f >= 1 else 1

    @property
    def theme(self) -> dict[str, Any]:
        return ndg.clean_theme(self.info.get("theme"))

    @property
    def type(self) -> str:
        return self.theme["type"]

    @property
    def wall_glyph(self) -> str:
        return self.theme["wall_glyph"] if self.settings.glyphs else "#"

    @property
    def floor_glyph(self) -> str:
        return self.theme["floor_glyph"] if self.settings.glyphs else "."

    @property
    def roles(self) -> tuple[str, str]:
        """(wall role, floor role): exact rgb: colours, your theme's, or plain."""
        mode = self.settings.colors
        if mode == "palette":
            colours = self.settings.palettes.get(self.theme["palette"].lower())
            if colours:
                return f"rgb:{colours[0]}:-", f"rgb:{colours[1]}:-"
        if mode == "off":
            return "text", "text"
        return "title", "dim"

    @property
    def limits(self) -> ndg.MapLimits:
        return self.settings.limits

    @property
    def dark_sight(self) -> int:
        return self.settings.dark_sight

    @property
    def trap_damage(self) -> int:
        return self.settings.trap_damage

    @property
    def repair_kits(self) -> bool:
        return self.settings.repair_kits

    @property
    def xp_mult(self) -> float:
        m = self.info.get("xp_mult")
        return float(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else 1.0

    @property
    def boss(self) -> dict[str, Any] | None:
        return ndg.clean_boss(self.info.get("boss"))

    def hud(self) -> str:
        bonus = f"  x{self.xp_mult:g} XP" if self.xp_mult > 1 else ""
        return f"B{self.floor}F {self.name}{bonus}"

    def roll_pickup(self, rng: random.Random) -> dict[str, Any]:
        """A floor pickup: a data chest (XP) or an item for PULSE's bag (better deeper)."""
        if rng.random() < self.settings.chest_chance:
            return {"name": "data chest", "kind": "chest", "xp": self.settings.chest_xp + self.floor}
        mult = self.info.get("loot_mult") if isinstance(self.info.get("loot_mult"), (int, float)) else 1.0
        tier = min(3, int(self.floor * float(mult) // 5))
        pool = [k for k in ndg.LOOT_TIERS[tier] if k in ITEMS and k != "evo_chip"]
        item = rng.choice(pool)
        return {"name": ITEMS[item][0], "kind": "bagitem", "id": item}

    def pick_up(self, item: dict[str, Any]) -> str:
        if item["kind"] == "chest":
            self.chest_xp += int(item.get("xp", 0))
            return f"data chest: +{item.get('xp', 0)} XP for PULSE (counted at the stairs)"
        self.loot.append(str(item.get("id") or item["name"]))
        return f"found {item['name']} for PULSE's bag (sent at the stairs)"

    def beat(self, monster: Any) -> None:
        kind = getattr(monster, "kind", None)
        self.defeated.append(kind.id if kind is not None else str(monster.name))
        if kind is not None and kind.boss:
            self.boss_beaten = True

    def report(self, result: str, enemies_left: int) -> dict[str, Any]:
        """The pet.dungeon.floor request for this floor."""
        payload: dict[str, Any] = {"id": self.id, "floor": self.floor, "result": result,
                                   "xp": min(999, self.chest_xp), "enemies": list(self.defeated),
                                   "loot": list(self.loot)[:10]}
        if ndg.is_boss_floor(self.floor):
            payload["boss_beaten"] = self.boss_beaten
        del enemies_left
        return payload

    def next_floor(self, info: dict[str, Any]) -> None:
        self.info = info
        self.loot, self.chest_xp, self.defeated, self.boss_beaten = [], 0, [], False


# --- the screen -------------------------------------------------------------------------

def _box_rows(lines: list[str], width: int, roles: dict[int, str] | None = None) -> list[Row]:
    """Box text -> rows (frame lines dim, the rest text, with per-line overrides)."""
    roles = roles or {}
    out: list[Row] = []
    for i, line in enumerate(lines):
        role = roles.get(i) or ("dim" if line.startswith("+") else "text")
        out.append([(line[:width], role)])
    return out


def _new_dungeon_activity_base():
    from cyberdeck.features.dungeon import DungeonActivity

    return DungeonActivity


class NetDungeonActivity(Activity):
    """The picker, the banners and the floors of a network dungeon (one Activity)."""

    title = "DUNGEONS"
    tick = 0.5

    def __init__(self, ctx: "Context", rows: list[dict[str, Any]], settings: NetSettings | None = None,
                 unicode: bool = True, seed: int | None = None) -> None:
        super().__init__()
        self.ctx = ctx
        self.settings = settings or load_settings(ctx.config_dir)
        self.unicode = unicode
        self.seed = seed
        self.rows = rows
        self.sel = 0
        self.top = 0
        self.starts: dict[str, int] = {r["id"]: r.get("checkpoint", r["starts"][-1]) for r in rows}
        self.phase = "picker"          # picker | banner | play | result | end
        self.note = ""                 # one line under the picker (errors, hints)
        self.run: NetRun | None = None
        self.view: Any = None          # the DungeonActivity drawing the current floor
        self.lines: list[str] = []     # the banner / result box on screen
        self.line_roles: dict[int, str] = {}
        self.pending_next: dict[str, Any] | None = None
        self.shown = self.settings.picker_rows   # rows that fit (render() updates it)
        self.runs_done: list[str] = []

    # --- picker -----------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.rows) + (1 if self.settings.classic_row else 0)

    @property
    def subtitle(self) -> str:
        if self.phase in {"play", "classic"} and self.view is not None:
            return self.view.subtitle
        return {"picker": " enter go · ←/→ start floor · esc close ",
                "banner": " any key: start ", "result": " any key: next floor ",
                "end": " any key: back to the list "}.get(self.phase, "")

    def _move_sel(self, step: int) -> None:
        if not self.total:
            return
        self.sel = (self.sel + step) % self.total
        shown = self.shown
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + shown:
            self.top = self.sel - shown + 1

    def _change_start(self, step: int) -> None:
        if self.sel >= len(self.rows):
            return
        row = self.rows[self.sel]
        starts = row["starts"]
        now = self.starts.get(row["id"], starts[-1])
        i = max(0, min(len(starts) - 1, (starts.index(now) if now in starts else len(starts) - 1) + step))
        self.starts[row["id"]] = starts[i]
        self.note = f"start floor {starts[i]}" + ("" if len(starts) > 1 else " (beat floor 5 for a checkpoint)")

    def refresh(self) -> None:
        body, _code, why = ask(self.ctx, TYPE_PET_DUNGEONS, {})
        if body is None:
            self.note = why
            return
        self.rows = clean_rows(body)
        for r in self.rows:
            self.starts[r["id"]] = r.get("checkpoint", r["starts"][-1]) if r["id"] not in self.starts or \
                self.starts[r["id"]] not in r["starts"] else self.starts[r["id"]]
        self.sel = min(self.sel, max(0, self.total - 1))

    def enter(self, index: int | None = None, floor: int | None = None) -> bool:
        """Go into dungeon `index` (default: the selected one). False = it didn't work (see note)."""
        index = self.sel if index is None else index
        if index >= len(self.rows):
            self._classic()
            return False
        row = self.rows[index]
        want = self.starts.get(row["id"], row["starts"][-1]) if floor is None else floor
        body, _code, why = ask(self.ctx, TYPE_PET_DUNGEON_ENTER, {"id": row["id"], "floor": int(want)})
        if body is None:
            self.note = why
            return False
        from cyberdeck.features.dungeon import new_game

        self.run = NetRun(info=body, settings=self.settings, flavor=clamp_say(body.get("flavor") or ""))
        game = new_game(self.ctx, self.seed, net=self.run)
        DungeonActivity = _new_dungeon_activity_base()
        self.view = DungeonActivity(self.ctx, game, unicode=self.unicode)
        self.view.help_line = " arrows move · > stairs · $ chest · * item · ^ trap · i bag · q leave"
        self._show_banner(body, [str(x) for x in body.get("log") or []])
        return True

    def _classic(self) -> None:
        """The classic random dungeon, run inside this screen (closing it closes the picker)."""
        from cyberdeck.features.dungeon import DungeonActivity, new_game

        self.view = DungeonActivity(self.ctx, new_game(self.ctx, self.seed), unicode=self.unicode)
        self.phase = "classic"

    # --- banners ---------------------------------------------------------------
    def _show_banner(self, info: dict[str, Any], log: list[str]) -> None:
        run = self.run
        lines = ndg.floor_banner(info, run.flavor if run else "")
        boss = ndg.clean_boss(info.get("boss")) is not None
        self.line_roles = {1: "title", 2: "alert" if boss else "prompt"}
        extra = [clamp_say(x, ndg.INNER) for x in log if x][:4]
        if extra:
            lines = lines[:-1] + [ndg.box_line(x) for x in extra] + [lines[-1]]
        self.lines = lines
        self.phase = "banner"
        say = info.get("say")
        if say and self.view is not None and self.view.game.on_say is not None:
            self.view.game.on_say(clamp_say(say), run.name if run else "")

    def _result_box(self, title: str, body: dict[str, Any] | None, extra: list[str]) -> list[str]:
        lines = [ndg.box_rule(), ndg.box_line(ndg.two_ends(self.run.name if self.run else "", title)), ndg.box_rule()]
        for x in (body or {}).get("log") or []:
            lines.append(ndg.box_line(clamp_say(x, ndg.INNER)))
        for x in extra:
            lines.append(ndg.box_line(clamp_say(x, ndg.INNER)))
        say = (body or {}).get("say")
        if say:
            lines += [ndg.box_line(), ndg.box_line(ndg.quoted(say))]
        lines.append(ndg.box_rule())
        return lines

    # --- floors -------------------------------------------------------------------
    def take_stairs(self) -> None:
        g, run = self.view.game, self.run
        if (g.px, g.py) != g.stairs:
            g.say("no stairs here.")
            return
        result = "cleared" if not g.monsters else "stairs"
        body, _code, why = ask(self.ctx, TYPE_PET_DUNGEON_FLOOR, run.report(result, len(g.monsters)))
        if body is None:
            self._end("LINK LOST", None, [why, "The run is over. Your progress so far is kept."])
            return
        run.floors_done += 1
        run.floor_xp += int(body.get("xp") or 0) if isinstance(body.get("xp"), int) else 0
        g.score += 25 * g.depth
        g.hp = min(g.max_hp, g.hp + 3)
        nxt = body.get("next")
        self._after_report(body)
        if isinstance(nxt, dict) and isinstance(nxt.get("floor"), int):
            self.pending_next = nxt
            self.lines = self._result_box(f"B{run.floor}F {result.upper()}", body, [])
            self.line_roles = {1: "ok"}
            self.phase = "result"
        else:
            self._end("RUN OVER", body, [])

    def _after_report(self, body: dict[str, Any]) -> None:
        pet = body.get("pet")
        if isinstance(pet, dict):
            import time as _time

            self.ctx.services["pet_cache"] = {"at": _time.monotonic(), "pet": pet}
        say = body.get("say")
        g = self.view.game if self.view else None
        if say and g is not None and g.on_say is not None:
            g.on_say(clamp_say(say), self.run.name if self.run else "")

    def _next_floor(self) -> None:
        info, self.pending_next = self.pending_next, None
        if not info:
            self._end("RUN OVER", None, [])
            return
        self.run.next_floor(info)
        g = self.view.game
        g.new_level()
        self._show_banner(info, [])

    def end_run(self, result: str) -> None:
        """Faint or flee: tell the companion; the run ends (next = null)."""
        run = self.run
        if run is None:
            return
        body, _code, why = ask(self.ctx, TYPE_PET_DUNGEON_FLOOR, run.report(result, len(self.view.game.monsters)))
        if body is not None:
            self._after_report(body)
        ask(self.ctx, TYPE_PET_DUNGEON_LEAVE, {"id": run.id, "floor": run.floor})
        title = "FAINTED" if result == "fainted" else "LEFT"
        extra = [] if body is not None else [why]
        if result == "fainted" and body is None:
            extra.append("Next time: start from your last checkpoint.")
        self._end(title, body, extra)

    def _end(self, title: str, body: dict[str, Any] | None, extra: list[str]) -> None:
        run = self.run
        g = self.view.game if self.view else None
        if g is not None:
            if g.battle is not None:
                g.close_battle()
            g.finish()
            payload = getattr(g, "end_payload", None)
            if payload:
                run_pet_hooks(self.ctx, "combat", payload)
        if run is not None:
            self.runs_done.append(f"{run.name}: reached B{run.floor}F, {run.floors_done} floor"
                                  f"{'s' if run.floors_done != 1 else ''} done, +{run.floor_xp} floor XP"
                                  + (f", {g.battles_won} battles won (+{g.battle_xp} XP)" if g else ""))
        self.lines = self._result_box(title, body, extra)
        self.line_roles = {1: "alert" if title in {"FAINTED", "LINK LOST"} else "title"}
        self.phase = "end"
        self.run = None
        self.ctx.services.pop("pet_cache", None)

    # --- keys -----------------------------------------------------------------------
    def on_key(self, key: str) -> None:
        if self.phase == "picker":
            self._picker_key(key)
        elif self.phase == "banner":
            self.phase = "play"
        elif self.phase == "result":
            self._next_floor()
        elif self.phase == "end":
            self.phase = "picker"
            self.view = None
            self.refresh()
        elif self.phase == "play":
            self._play_key(key)
        elif self.phase == "classic":
            self.view.on_key(key)
            if self.view.done:
                self.done = True
                self.result_lines = list(self.view.result_lines)
        if self.done and not self.result_lines:
            self.result_lines = self.runs_done or ["dungeons: closed"]

    def _picker_key(self, key: str) -> None:
        if key in {"esc", "q"}:
            self.done = True
        elif key in {"up", "k", "w"}:
            self._move_sel(-1)
        elif key in {"down", "j", "s"}:
            self._move_sel(1)
        elif key in {"left", "h", "a"}:
            self._change_start(-1)
        elif key in {"right", "l", "d"}:
            self._change_start(1)
        elif key == "c":
            self._classic()
        elif key in {"enter", " ", "space"}:
            self.enter()

    def _play_key(self, key: str) -> None:
        g = self.view.game
        if g.battle is None and g.awaiting is None:
            if key in {"q", "esc"}:
                self.end_run("fled")
                return
            if key == ">":
                self.take_stairs()
                return
        if g.over:                     # you were derezzed: same as fainting
            self.end_run("fainted")
            return
        self.view.on_key(key)
        self.view.done = False
        if self.run is not None and (self.run.fainted or g.over) and g.battle is None:
            self.end_run("fainted")

    # --- drawing ------------------------------------------------------------------------
    def render(self, width: int, height: int) -> list[Row]:
        if self.phase in {"play", "classic"} and self.view is not None:
            return self.view.render(width, height)
        if self.phase == "picker":
            self.shown = max(1, min(self.settings.picker_rows, height - 9))
            self.top = max(0, min(self.top, self.sel, max(0, self.total - self.shown)))
            if self.sel >= self.top + self.shown:
                self.top = self.sel - self.shown + 1
            lines = picker_lines(self.rows, self.sel, self.starts, self.settings.classic_row, self.top, self.shown)
            roles = {1: "title"}
            for i in range(self.top, min(self.total, self.top + self.shown)):
                roles[3 + i - self.top] = "prompt" if i == self.sel else (
                    "warn" if i < len(self.rows) and self.rows[i]["rare"] else "text")
            rows = _box_rows(lines, width, roles)
            if self.note:
                rows.append([(" " + clamp_say(self.note, SCREEN_W - 2), "warn")])
            return rows[:height]
        return _box_rows(self.lines, width, self.line_roles)[:height]

    def headless(self) -> list[str]:
        return picker_lines(self.rows, self.sel, self.starts, self.settings.classic_row) + [
            "`dungeon enter <n> [floor]` to go in · `dungeon classic` for the classic dungeon"]


# --- commands -----------------------------------------------------------------------------

def find_row(rows: list[dict[str, Any]], ref: str) -> int | None:
    """A picker number (1-based), an id, or a name (case-insensitive, prefix ok)."""
    ref = ref.strip().lower()
    if ref.isdigit() and 1 <= int(ref) <= len(rows):
        return int(ref) - 1
    for i, r in enumerate(rows):
        if ref in (r["id"].lower(), r["name"].lower()):
            return i
    hits = [i for i, r in enumerate(rows) if r["name"].lower().startswith(ref)]
    return hits[0] if len(hits) == 1 else None


def load_rows(ctx: "Context") -> tuple[list[dict[str, Any]] | None, str]:
    """(rows, "") or (None, why) — None means: use the classic dungeon."""
    body, _code, why = ask(ctx, TYPE_PET_DUNGEONS, {})
    if body is None:
        return None, why
    return clean_rows(body), ""


def open_dungeon(ctx: "Context", args: list[str], seed: int | None = None) -> list[str]:
    """The `dungeon` command: network dungeons when possible, else the classic one."""
    from cyberdeck.features.dungeon import DungeonActivity, new_game

    settings = load_settings(ctx.config_dir)
    unicode = ctx.services.get("unicode", True)
    action = args[0].lower() if args else ""

    def classic(note: str = "") -> list[str]:
        ctx.activity = DungeonActivity(ctx, new_game(ctx, seed), unicode=unicode)
        return [note] if note else []

    if action == "classic" or not settings.enabled:
        return classic()
    rows, why = load_rows(ctx)
    if rows is None:
        if action in {"list", "enter"}:
            return [f"network dungeons: {why}"]
        return classic()                  # no companion / an older one: the classic dungeon, as before
    if action == "list":
        return picker_lines(rows, -1, None, False)
    activity = NetDungeonActivity(ctx, rows, settings, unicode=unicode, seed=seed)
    if action == "enter":
        if len(args) < 2:
            return ["usage: dungeon enter <number|name> [floor]"]
        floor = None
        if len(args) >= 3 and args[-1].isdigit():
            floor, ref = int(args[-1]), " ".join(args[1:-1])
        else:
            ref = " ".join(args[1:])
        index = find_row(rows, ref)
        if index is None:
            return [f"no dungeon '{ref}' — `dungeon list` shows them"]
        if not activity.enter(index, floor):
            return [activity.note]
        if not ctx.interactive:
            # Headless: show the banner and a map preview, then leave (nothing is played).
            run = activity.run
            out = list(activity.lines) + activity.view.game.map_text()
            ask(ctx, TYPE_PET_DUNGEON_LEAVE, {"id": run.id, "floor": run.floor})
            return out
        ctx.activity = activity
        return []
    if not rows and not settings.classic_row:
        return classic("no network dungeons yet — scan for networks to unlock some. classic dungeon:")
    ctx.activity = activity
    return []


def register(reg: "Registry") -> None:
    @reg.command("dungeons", "Network dungeons PULSE has found (one per wifi / bt / lan signal).")
    def cmd_dungeons(ctx: "Context", args: list[str]) -> list[str]:
        del args
        return open_dungeon(ctx, ["list"])
