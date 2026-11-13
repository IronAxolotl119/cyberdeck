"""battledata.py — the numbers and words behind dungeon battles.

In plain English: this is the "rule book" for the Pokemon-style battles
in the dungeon. It has no screen code and no link code, just tables:

  * TYPES and CHART      the seven types and who beats whom (same chart
                         as the companion, companion/docs/DUNGEON_INTEGRATION.md)
  * ROSTER               every dungeon enemy: map letter, type, weakness,
                         face, stats, its attacks, and the first floor
                         it shows up on
  * ODDS                 flee chance, crits, loot drops, XP per battle
  * ITEMS                the battle bag (cookies, batteries, medicine)
  * SIM_LINES            what the built-in sim PULSE says in battle

Both the deck's battle screen (features/battle.py) and the sim pet
(companion.py) read this file, so they always agree.

Customize without touching code: config/battle.ini overrides ODDS
([odds]) and any enemy ([enemy.bug] face = (O_O), hp = 30, …). See the
comments in that file. Every line PULSE says must be plain ASCII and at
most 44 characters (SAY_MAX) so it fits the 48-column battle screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Types and the type chart (attacker row -> defender column; missing = 1x)
# ---------------------------------------------------------------------------
TYPES: tuple[str, ...] = ("NORM", "VOLT", "DATA", "NET", "FIRE", "ICE", "VOID")

CHART: dict[str, dict[str, float]] = {
    "NORM": {"VOID": 0.5, "FIRE": 0.5},
    "VOLT": {"DATA": 2.0, "ICE": 2.0, "VOLT": 0.5, "VOID": 0.5},
    "DATA": {"VOID": 2.0, "DATA": 0.5, "FIRE": 0.5},
    "NET": {"VOLT": 2.0, "NET": 0.5, "FIRE": 0.5, "VOID": 0.0},     # packets vanish into VOID
    "FIRE": {"DATA": 2.0, "NET": 2.0, "ICE": 0.5, "FIRE": 0.0},     # a firewall cannot burn
    "ICE": {"FIRE": 2.0, "NET": 2.0, "ICE": 0.5, "VOLT": 0.5},
    "VOID": {"NORM": 2.0, "VOID": 0.5, "DATA": 0.5},
}

SAY_MAX = 44          # inner width of the 48-column battle box
SCREEN_W = 48         # the battle screen is always exactly this wide
MAX_LOADOUT = 4       # moves in the FIGHT menu


def norm_type(raw: Any, default: str = "NORM") -> str:
    """'volt' -> 'VOLT'; anything unknown -> default (older companions send no type)."""
    text = str(raw or "").strip().upper()
    return text if text in TYPES else default


def effectiveness(attack: Any, defend: Any, chart: dict[str, dict[str, float]] | None = None) -> float:
    """The damage multiplier: 2, 1, 0.5 or 0."""
    table = chart or CHART
    return float(table.get(norm_type(attack), {}).get(norm_type(defend), 1.0))


def effectiveness_label(mult: float) -> str:
    if mult == 0:
        return "immune"
    if mult > 1:
        return "super"
    if mult < 1:
        return "weak"
    return "normal"


def chart_from_payload(payload: Any) -> dict[str, dict[str, float]] | None:
    """pet.combat.roster's {"chart": {atk: {def: mult}}} -> a chart, or None."""
    raw = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return None
    out: dict[str, dict[str, float]] = {}
    for atk, row in raw.items():
        if norm_type(atk, "") and isinstance(row, dict):
            out[norm_type(atk)] = {norm_type(d): float(v) for d, v in row.items()
                                   if norm_type(d, "") and isinstance(v, (int, float))}
    return out or None


def clamp_say(text: Any, limit: int = SAY_MAX) -> str:
    """Plain ASCII, one line, at most `limit` chars (cut at a word with '...')."""
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    line = line.encode("ascii", "replace").decode("ascii")
    if len(line) <= limit:
        return line
    cut = line[: limit - 3].rsplit(" ", 1)[0] or line[: limit - 3]
    return cut.rstrip(" ,.;:") + "..."


def slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


# ---------------------------------------------------------------------------
# The enemy roster. Change faces, stats or attacks here, or in
# config/battle.ini. hp/attack/defense/speed are level-1 numbers; each
# level above 1 adds per_level. solo_hp / solo_attack are the small
# numbers used when you fight with your own fists (no PULSE).
# attacks: (name, type, power multiplier). The enemy AI picks one.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EnemyKind:
    id: str
    name: str
    glyph: str
    type: str
    weak: str
    depth: int                 # first floor it can appear on
    face: str
    hp: int
    attack: int
    defense: int
    speed: int
    per_level: dict[str, int] = field(default_factory=dict)
    attacks: tuple[tuple[str, str, float], ...] = ()
    solo_hp: int = 5
    solo_attack: int = 2
    boss: bool = False
    every: int = 0             # bosses: appear on every Nth floor
    desc: str = ""

    def stats(self, level: int) -> dict[str, int]:
        extra = max(0, int(level) - 1)
        return {k: int(getattr(self, k)) + int(self.per_level.get(k, 0)) * extra
                for k in ("hp", "attack", "defense", "speed")}


def _grow(hp: int, atk: int, df: int, spd: int) -> dict[str, int]:
    return {"hp": hp, "attack": atk, "defense": df, "speed": spd}


ROSTER: tuple[EnemyKind, ...] = (
    EnemyKind("bug", "bug", "b", "DATA", "VOLT", 1, "(o_o)", 18, 4, 1, 4, _grow(4, 1, 1, 1),
              (("Byte Nibble", "DATA", 1.0), ("Scuttle", "NORM", 0.8)), 3, 1,
              desc="A crawling typo. Hates a good jolt."),
    EnemyKind("glitch", "glitch", "g", "VOID", "DATA", 1, "(#_#)", 20, 5, 1, 6, _grow(4, 1, 1, 1),
              (("Frame Skip", "VOID", 1.0), ("Flicker", "NORM", 0.8)), 5, 2,
              desc="A flicker in the frame. Clean DATA fixes it."),
    EnemyKind("spam", "spam", "s", "NET", "FIRE", 1, "(^o^)", 16, 4, 0, 7, _grow(4, 1, 0, 1),
              (("Inbox Flood", "NET", 1.0), ("Reply All", "NORM", 0.8)), 4, 1,
              desc="Never stops talking. Burn it or freeze it."),
    EnemyKind("virus", "virus", "v", "DATA", "FIRE", 2, "(>_<)", 26, 6, 2, 5, _grow(5, 1, 1, 1),
              (("Infect", "DATA", 1.0), ("Replicate", "DATA", 0.8)), 8, 3,
              desc="Copies itself. FIRE melts its code."),
    EnemyKind("lag", "lag", "l", "ICE", "VOLT", 2, "(-_-)", 30, 5, 3, 2, _grow(5, 1, 1, 0),
              (("Freeze Frame", "ICE", 1.0), ("Buffer", "NORM", 0.7)), 9, 2,
              desc="Slow and frozen. A jolt thaws it."),
    EnemyKind("worm", "worm", "w", "NET", "ICE", 3, "(~o~)", 34, 7, 2, 6, _grow(6, 1, 1, 1),
              (("Tunnel", "NET", 1.0), ("Burrow Bite", "NORM", 0.8)), 12, 4,
              desc="Tunnels between hosts. Freeze it in place."),
    EnemyKind("firewall", "firewall", "F", "FIRE", "ICE", 3, "[=_=]", 40, 6, 5, 3, _grow(6, 1, 1, 0),
              (("Port Burn", "FIRE", 1.0), ("Block", "NORM", 0.6)), 14, 4,
              desc="A wall of heat. Immune to FIRE. Use ICE."),
    EnemyKind("daemon", "daemon", "D", "VOID", "DATA", 4, "(`_')", 44, 9, 3, 6, _grow(6, 2, 1, 1),
              (("Fork Bomb", "VOID", 1.0), ("Zombie Proc", "DATA", 0.8)), 18, 5,
              desc="Runs in the background. DATA exposes it."),
    EnemyKind("kernel_panic", "kernel panic", "K", "VOID", "DATA", 5, "(X_X)", 90, 12, 5, 6, _grow(10, 2, 1, 1),
              (("Core Dump", "VOID", 1.0), ("Stack Smash", "NORM", 1.2), ("Oops", "DATA", 0.7)), 30, 6,
              boss=True, every=5, desc="Boss of every 5th floor. Fatal error."),
)


def roster_entry(ref: Any, roster: tuple[EnemyKind, ...] = ROSTER) -> EnemyKind | None:
    """Find an enemy by id, name or map letter ('kernel panic', 'kernel_panic', 'K')."""
    key = slug(ref)
    for kind in roster:
        if key and key in (kind.id, slug(kind.name)):
            return kind
    raw = str(ref or "")
    return next((k for k in roster if len(raw) == 1 and raw == k.glyph), None)


def enemy_type(name: Any, default: str = "NORM") -> str:
    """The roster type for an enemy name (how the sim fills in a missing type)."""
    kind = roster_entry(name)
    return kind.type if kind else default


# ---------------------------------------------------------------------------
# Odds and rewards. Everything here can be overridden in config/battle.ini [odds].
# ---------------------------------------------------------------------------
ODDS: dict[str, float] = {
    # RUN: chance = base + (pet.speed - foe.speed) * speed + (pet.level - foe.level) * level,
    # clamped to [flee_min, flee_max], then times flee_boss against a boss.
    "flee_base": 0.5, "flee_speed": 0.05, "flee_level": 0.05,
    "flee_min": 0.10, "flee_max": 0.95, "flee_boss": 0.5,
    # Enemy AI: base damage is a roll from attack * dmg_min .. attack (times the
    # attack's power), plus 1 per dmg_depth floors. smart = chance it picks the
    # attack that hurts PULSE's type most (otherwise a random one).
    "dmg_min": 0.5, "dmg_depth": 2, "smart": 0.6,
    # XP per win sent in pet.combat.end: xp_base + xp_depth * floor (x xp_boss for a boss).
    "xp_base": 2, "xp_depth": 3, "xp_boss": 3,
    # Chance a beaten enemy drops loot for PULSE's bag (bosses: boss_loot).
    "loot": 0.4, "boss_loot": 1.0,
    # The sim pet's crits (the real companion uses its own numbers).
    "crit": 1.0 / 16.0, "crit_mult": 1.5,
    # After you flee (or PULSE loses) the enemy stays put for this many turns.
    "calm_turns": 3,
}


def flee_chance(pet_speed: int, pet_level: int, foe_speed: int, foe_level: int, boss: bool = False,
                odds: dict[str, float] | None = None) -> float:
    """The RUN roll's odds (same formula as the companion's pet.combat.run)."""
    o = odds or ODDS
    chance = o["flee_base"] + (pet_speed - foe_speed) * o["flee_speed"] + (pet_level - foe_level) * o["flee_level"]
    chance = max(o["flee_min"], min(o["flee_max"], chance))
    return round(chance * (o["flee_boss"] if boss else 1.0), 3)


def win_xp(depth: int, boss: bool = False, odds: dict[str, float] | None = None) -> int:
    o = odds or ODDS
    return int((o["xp_base"] + o["xp_depth"] * max(1, depth)) * (o["xp_boss"] if boss else 1))


# ---------------------------------------------------------------------------
# Battle bag items (the sim pet's bag; a real companion sends its own).
# id -> (name, kind, amount, usable in battle, description)
# ---------------------------------------------------------------------------
ITEMS: dict[str, tuple[str, str, int, bool, str]] = {
    "cache_cookie": ("cache cookie", "food", 20, True, "Restores 20 HP. Crunchy."),
    "glitch_berry": ("glitch berry", "food", 15, True, "Restores 15 HP. Tastes like static."),
    "data_cookie": ("data cookie", "food", 25, True, "Restores 25 HP."),
    "byte_burger": ("byte burger", "food", 40, True, "Restores 40 HP. A full meal."),
    "battery": ("battery", "battery", 30, True, "Restores 30 EN."),
    "power_cell": ("power cell", "battery", 25, True, "Restores 25 EN."),
    "overclock_chip": ("overclock chip", "battery", 50, True, "Restores 50 EN. Runs hot."),
    "medicine": ("medicine", "medicine", 5, True, "Cures glitch. Restores 5 HP."),
    "patch_kit": ("patch kit", "medicine", 10, True, "Cures glitch. Restores 10 HP."),
    "evo_chip": ("evo chip", "evolve", 0, False, "Evolves PULSE. Not for battle."),
}
ITEM_ALIASES = {"cookie": "cache_cookie", "evo": "evo_chip", "med": "medicine", "meds": "medicine"}
STARTER_BAG = {"cache_cookie": 3, "battery": 1, "medicine": 1}


def item_key(ref: Any) -> str:
    key = slug(ref)
    return ITEM_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# The sim pet's moves: name -> (type, description). Power / accuracy / cost
# live in companion.py MOVE_INFO. A real companion sends its own.
# ---------------------------------------------------------------------------
SIM_MOVE_TYPES: dict[str, tuple[str, str]] = {
    "peck": ("NORM", "A quick peck. Free to use."),
    "chirp": ("NORM", "A cheap chirp. The fallback move."),
    "static zap": ("VOLT", "A sharp VOLT zap. Great vs DATA and ICE."),
    "wave dash": ("NET", "Rides the wave. Great vs VOLT."),
    "signal burst": ("DATA", "A dense DATA burst. Great vs VOID."),
    "nova flare": ("FIRE", "Big FIRE flare. Great vs DATA and NET."),
    "signal sniff": ("DATA", "Sniffs out a weak spot. Good vs VOID."),
    "beacon burst": ("FIRE", "A hot beacon flash. Great vs NET."),
    "channel hop": ("NET", "Hops channels to strike. Good vs VOLT."),
    "blue pulse": ("ICE", "A cold blue pulse. Great vs FIRE and NET."),
    "tooth echo": ("ICE", "An icy echo bite. Good vs FIRE."),
    "ping echo": ("DATA", "Pings and pokes. Good vs VOID."),
    "arp whisper": ("VOID", "A spooky whisper. Great vs NORM."),
    "tag bite": ("VOLT", "An RFID chomp. Great vs DATA."),
    "near field": ("VOLT", "A close-range jolt. Good vs ICE."),
    "guard": ("VOID", "Braces. Halves the next hit."),
}

# The sim pet's own type by species (the real companion decides its own).
SIM_SPECIES_TYPES = {"chirplet": "NORM", "wavling": "NORM", "aethling": "FIRE", "aethlance": "FIRE",
                     "aethgeist": "FIRE", "aethcrown": "FIRE", "staticore": "VOLT", "voltshard": "VOLT",
                     "stormcore": "VOLT", "glitchwyrm": "VOID", "nullking": "VOID"}

# ---------------------------------------------------------------------------
# What the sim PULSE says in battle. {foe} = the enemy's name. Keep each
# line plain ASCII and at most 44 characters (tests check this).
# ---------------------------------------------------------------------------
SIM_LINES: dict[str, tuple[str, ...]] = {
    "start": ("Hi {foe}. Bye {foe}. Soon.", "Let's go! I've got this!", "A {foe}? Easy."),
    "start.boss": ("That's a big one. Stay close!", "Boss fight! Deep breaths."),
    "super": ("Weakness found!", "That one hurt it!"),
    "weak": ("Hmm, it shrugged that off.", "Not my best angle."),
    "immune": ("It didn't even notice...",),
    "crit": ("Right in the core!", "Critical!"),
    "miss": ("Missed! Next one lands.", "Whoops!"),
    "guard": ("Shields up!",),
    "move": ("Take that!", "Hyah!", "Zap zap!"),
    "hurt": ("Ouch!", "Hey, that stung!"),
    "resist": ("Barely felt it.",),
    "immune_hit": ("Ha! Can't touch this.",),
    "lowhp": ("I'm running low...", "Help me out here!"),
    "faint": ("I... need... a reboot...",),
    "win": ("We did it!", "Deleted! High five!"),
    "win.boss": ("We beat the boss! Legends!",),
    "level_up": ("Level up! Stronger already!",),
    "learned": ("I learned a new move!",),
    "lose": ("Sorry... I'll train harder.",),
    "flee": ("Phew, we got away.",),
    "flee.fail": ("It won't let us go!",),
    "item": ("Thanks! Much better.",),
    "no_effect": ("I don't need that right now.",),
}


# ---------------------------------------------------------------------------
# config/battle.ini: your overrides for ODDS and the ROSTER
# ---------------------------------------------------------------------------
def load_battle_config(config_dir: str | Path | None = None) -> tuple[tuple[EnemyKind, ...], dict[str, float]]:
    """(roster, odds) with config/battle.ini applied. A missing file = the defaults."""
    from cyberdeck.config import Settings

    s = Settings.load("battle.ini", config_dir)
    odds = dict(ODDS)
    for key, default in ODDS.items():
        odds[key] = s.get_float("odds", key, float(default))
    roster = []
    for kind in ROSTER:
        sec = f"enemy.{kind.id}"
        if not s.has_section(sec):
            roster.append(kind)
            continue
        if not s.get_bool(sec, "enabled", True):
            continue
        changes: dict[str, Any] = {}
        for key in ("name", "glyph", "face", "desc"):
            value = s.get_str(sec, key, "")
            if value:
                changes[key] = value[:1] if key == "glyph" else value
        for key in ("type", "weak"):
            value = s.get_str(sec, key, "")
            if value:
                changes[key] = norm_type(value, getattr(kind, key))
        for key in ("depth", "hp", "attack", "defense", "speed", "solo_hp", "solo_attack", "every"):
            changes[key] = s.get_int(sec, key, getattr(kind, key))
        roster.append(replace(kind, **changes))
    return tuple(roster), odds
