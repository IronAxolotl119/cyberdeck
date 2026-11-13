"""Network dungeons: every discovered wifi / bt / lan signal unlocks one.

A dungeon is keyed by a short stable hash of the signal's BSSID / MAC (or
its name when there is no id). Everything about it -- layout, look, enemy
mix, boss title, flavor text -- comes from ``seed = hash(id)``, so the same
network always makes the same dungeon. Raw identifiers are hashed on the
way in: they are never saved here, sent to the deck, or written to logs.

Save state lives on ``Life.dungeons`` ({id: record}); see ``new_record``.
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from pet.battle import ITEMS, TYPES, clamp_say, load_roster, to_ascii

DUNGEON_KINDS = ("wifi", "bt", "lan")
KIND_TYPE = {"wifi": "NET", "bt": "VOLT", "lan": "DATA"}
RARE_TYPE = "VOID"
THEME_TYPES = ("NET", "VOLT", "DATA", "VOID")

IN_RANGE_WINDOW = 300.0        # seconds; the same window as the environment mood
IN_RANGE_XP_MULT = 1.5
BOSS_EVERY = 5                 # boss floor every 5; the floor after it is a checkpoint
NAME_MAX = 24
MAX_DUNGEONS = 300
FOUND_COOLDOWN = 30.0          # at most one dungeon_found / dungeons_found per 30 s
HOME_HEAL_SHARE = 0.15         # home turf: heal 15% max HP between floors
BOSS_ID = "kernel_panic"
RESULTS = ("cleared", "stairs", "fainted", "fled")

_MAC = re.compile(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.I)

GLYPHS: dict[str, list[tuple[str, str]]] = {
    "NET": [("#", "."), ("+", ":"), ("%", "."), ("H", "'")],
    "VOLT": [("=", "."), ("|", ":"), ("Z", "."), ("#", "~")],
    "DATA": [("#", ","), ("0", "."), ("1", ":"), ("[", ".")],
    "VOID": [("%", "."), ("&", "."), ("@", ":"), ("?", ".")],
}
PALETTES: dict[str, list[str]] = {
    "NET": ["cyan_grid", "teal_mesh", "sky_relay", "aqua_lattice"],
    "VOLT": ["amber_spark", "yellow_coil", "gold_arc", "orange_surge"],
    "DATA": ["green_term", "mint_matrix", "lime_ledger", "jade_index"],
    "VOID": ["violet_null", "gray_static", "indigo_abyss", "black_glass"],
}
WINDING: dict[str, float] = {"NET": 0.7, "VOLT": 0.5, "DATA": 0.25, "VOID": 0.85}
BOSS_TITLES: dict[str, list[str]] = {
    "NET": ["Router King", "Packet Storm", "Mesh Tyrant", "Beacon Lord"],
    "VOLT": ["Static Titan", "Coil Lord", "Pairing Phantom", "Surge Queen"],
    "DATA": ["Root Daemon", "Table Smasher", "Index Golem", "Cache Hydra"],
    "VOID": ["Null Wraith", "Ghost SSID", "Blackhole", "The Unlisted"],
}
LOOT_TIERS: list[list[str]] = [
    ["cache_cookie", "glitch_berry", "battery"],
    ["data_cookie", "power_cell", "medicine"],
    ["byte_burger", "patch_kit", "overclock_chip"],
    ["byte_burger", "overclock_chip", "patch_kit", "evo_chip"],
]


# ------------------------------------------------------------------ identity
def _hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def dungeon_id(kind: str, ident: str | None, name: str | None) -> str:
    """Short stable hash of BSSID / MAC (falls back to the name)."""
    key = str(ident or "").strip().lower() or str(name or "").strip().lower()
    return _hex(f"{kind}:{key}")[:8]


def seed_for(did: str) -> int:
    """The dungeon seed. The deck uses random.Random(f"{seed}:{floor}")."""
    return int(_hex(f"dungeon:{did}")[:8], 16)


def is_raw_id(name: str | None, ident: str | None) -> bool:
    """True when a 'name' is really a MAC / BSSID / the raw id."""
    text = str(name or "").strip()
    return bool(text) and (text.lower() == str(ident or "").strip().lower() or bool(_MAC.fullmatch(text)))


def safe_name(did: str, ident: str | None, name: str | None) -> tuple[str, bool]:
    """Display-safe ASCII name (<= 24) and whether the signal was hidden."""
    raw = str(name or "")
    text = to_ascii(_MAC.sub("", raw)).strip(" -_:")
    if not text or raw.strip().lower() == str(ident or "").strip().lower() or _MAC.fullmatch(raw.strip() or "x"):
        return f"hidden-{did[:4]}", True
    return text[:NAME_MAX].rstrip(), False


def keep_name(text: str, name: str) -> str:
    """Undo sentence-casing of a lowercase dungeon name at the start of a line."""
    if name and name[0].islower() and text.startswith(name[0].upper() + name[1:]):
        return name + text[len(name):]
    return text


def difficulty_for(rssi: int | None, secured: bool, rare: bool) -> int:
    """1-10. Strong signal = easy, weak = hard; secured and rare add one each."""
    if rssi is None:
        base = 4
    else:
        base = max(1, min(8, 1 + round((-40 - int(rssi)) / 8)))
    return max(1, min(10, base + (1 if secured else 0) + (1 if rare else 0)))


def theme_for(did: str, kind: str, rare: bool) -> tuple[str, str]:
    primary = RARE_TYPE if rare else KIND_TYPE.get(kind, "NET")
    options = [t for t in TYPES if t not in (primary, "NORM")]
    return primary, options[seed_for(did) % len(options)]


def new_record(kind: str, ident: str, name: str | None, rssi: int | None, now: float,
               secured: bool = False, rare: bool = False) -> tuple[str, dict[str, Any]]:
    did = dungeon_id(kind, ident, name)
    shown, hidden = safe_name(did, ident, name)
    rare = bool(rare or hidden)
    primary, secondary = theme_for(did, kind, rare)
    rec = {
        "name": shown,
        "kind": kind,
        "type": primary,
        "type2": secondary,
        "rare": rare,
        "hidden": hidden,
        "secured": bool(secured),
        "difficulty": difficulty_for(rssi, bool(secured), rare),
        # The SSID (a display name, never the BSSID) so territory can spot home.
        "ssid": (str(name) if kind == "wifi" and not hidden else ""),
        "first_seen": float(now),
        "last_seen": float(now),
        "depth": 0,
        "clears": 0,
        "boss_kills": 0,
        "runs": 0,
        "entered": False,
        "home": False,
        "battles": {"wins": 0, "losses": 0, "flees": 0},
    }
    return did, rec


def normalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Fill fields an older save may not have."""
    out = dict(rec)
    out.setdefault("type", "NET")
    out.setdefault("type2", "FIRE")
    for key, default in (("depth", 0), ("clears", 0), ("boss_kills", 0), ("runs", 0), ("difficulty", 4)):
        out[key] = int(out.get(key, default) or 0) or (default if key == "difficulty" else 0)
    for key in ("rare", "hidden", "secured", "entered", "home"):
        out[key] = bool(out.get(key, False))
    out["battles"] = {k: int((out.get("battles") or {}).get(k, 0)) for k in ("wins", "losses", "flees")}
    out.setdefault("ssid", "")
    out["first_seen"] = float(out.get("first_seen", 0.0))
    out["last_seen"] = float(out.get("last_seen", out["first_seen"]))
    return out


# ------------------------------------------------------------------ progress
def checkpoint_for(depth: int) -> int:
    """The deepest start floor unlocked: 1, then 6 after floor 5, 11 after 10..."""
    return 1 + BOSS_EVERY * (max(0, int(depth)) // BOSS_EVERY)


def start_floors(depth: int) -> list[int]:
    return list(range(1, checkpoint_for(depth) + 1, BOSS_EVERY))


def snap_start(depth: int, floor: Any) -> int:
    """floor? -> the highest unlocked start floor <= floor (default: last checkpoint)."""
    starts = start_floors(depth)
    try:
        want = int(floor)
    except (TypeError, ValueError):
        return starts[-1]
    ok = [f for f in starts if f <= want]
    return ok[-1] if ok else 1


def is_boss_floor(floor: int) -> bool:
    return int(floor) % BOSS_EVERY == 0


def is_checkpoint_floor(floor: int) -> bool:
    return int(floor) % BOSS_EVERY == 1


def in_range(rec: dict[str, Any], now: float) -> bool:
    return now - float(rec.get("last_seen", 0.0)) <= IN_RANGE_WINDOW


def xp_mult(rec: dict[str, Any], now: float) -> float:
    return IN_RANGE_XP_MULT if in_range(rec, now) else 1.0


def loot_mult(rec: dict[str, Any], floor: int) -> float:
    mult = min(3.0, 1.0 + 0.08 * (int(floor) - 1))
    if rec.get("rare"):
        mult *= 1.25
    return round(mult, 2)


def enemy_level(rec: dict[str, Any], floor: int) -> int:
    level = int(rec.get("difficulty", 4)) + (int(floor) - 1) // 2
    return max(1, level - (1 if rec.get("home") else 0))


def level_range(rec: dict[str, Any]) -> list[int]:
    floor = int(rec.get("depth", 0)) + 1
    return [enemy_level(rec, checkpoint_for(rec.get("depth", 0))), enemy_level(rec, floor) + 2]


# ------------------------------------------------------------------ generation
def theme(did: str, rec: dict[str, Any]) -> dict[str, Any]:
    seed = seed_for(did)
    kind = rec.get("type", "NET")
    wall, floor_glyph = GLYPHS[kind][seed % len(GLYPHS[kind])]
    palette = PALETTES[kind][(seed // 7) % len(PALETTES[kind])]
    return {"type": kind, "type2": rec.get("type2"), "wall_glyph": wall, "floor_glyph": floor_glyph, "palette": palette}


def layout(did: str, rec: dict[str, Any], floor: int) -> dict[str, Any]:
    """Map parameters for one floor: deeper means bigger, darker, more traps."""
    seed = seed_for(did)
    r = random.Random(f"{seed}:{floor}:layout")
    depth = min(1.0, (int(floor) - 1) / 24.0)
    kind = rec.get("type", "NET")
    home = bool(rec.get("home"))
    width = min(72, 40 + int(depth * 20) + r.randint(0, 12))
    height = min(21, 13 + int(depth * 5) + r.randint(0, 3))
    rooms_max = max(1, min(10, 3 + int(floor) // 4 + r.randint(0, 2)))
    rooms_min = max(1, rooms_max - 2 - r.randint(0, 1))
    room_w = [3 + r.randint(0, 1), min(14, 6 + r.randint(0, 4) + int(depth * 4))]
    room_h = [3, max(3, min(height // 2 - 1, 4 + r.randint(0, 2) + int(depth * 2)))]
    corridor = "winding" if r.random() < WINDING.get(kind, 0.5) else "straight"
    loops = max(0, min(3, r.randint(0, 1) + int(depth * 2) + (1 if kind == "NET" else 0)))
    dark = not home and ((kind == "VOID" and floor >= 3) or (floor >= 6 and r.random() < 0.15 + depth * 0.6))
    traps = max(0, min(5, int(floor) // 3 + r.randint(0, 1) + (1 if rec.get("secured") else 0) - (2 if home else 0)))
    items = max(0, min(5, 1 + r.randint(0, 2) + int(floor) // 8))
    return {
        "width": width, "height": height,
        "rooms_min": rooms_min, "rooms_max": rooms_max,
        "room_w": room_w, "room_h": room_h,
        "corridor": corridor, "loops": loops, "dark": bool(dark),
        "traps": traps, "items": items,
    }


def enemy_weights(rec: dict[str, Any], floor: int) -> dict[str, float]:
    """Roster weights for this dungeon's theme at this depth (no bosses)."""
    gate = min(5, 1 + (int(floor) - 1) // 2)
    depth = min(1.0, int(floor) / 10.0)
    out: dict[str, float] = {}
    for entry in load_roster():
        if entry.get("boss") or int(entry.get("depth", 1)) > gate:
            continue
        weight = 1.0
        if entry["type"] == rec.get("type"):
            weight = 4.0
        elif entry["type"] == rec.get("type2"):
            weight = 2.0
        weight *= 1.0 + 0.5 * (int(entry.get("depth", 1)) - 1) * depth
        out[entry["id"]] = round(weight, 3)
    return out


def enemies_for(did: str, rec: dict[str, Any], floor: int) -> list[dict[str, Any]]:
    r = random.Random(f"{seed_for(did)}:{floor}:enemies")
    weights = enemy_weights(rec, floor)
    by_id = {e["id"]: e for e in load_roster()}
    ids = sorted(weights)
    kinds = 1 + (floor >= 3) + (floor >= 8)
    if is_boss_floor(floor):
        kinds = 1
    picked: list[str] = []
    while len(picked) < min(kinds, len(ids)):
        choice = r.choices(ids, weights=[weights[i] for i in ids])[0]
        if choice not in picked:
            picked.append(choice)
    base = enemy_level(rec, floor)
    out = []
    for eid in picked:
        count = r.randint(1, min(3, 1 + int(floor) // 6))
        if rec.get("home"):
            count = max(1, count - 1)
        entry = by_id[eid]
        out.append({"name": entry["name"], "id": eid, "type": entry["type"], "level": base + r.randint(0, 1), "count": count})
    return out


def boss_for(did: str, rec: dict[str, Any], floor: int) -> dict[str, Any] | None:
    if not is_boss_floor(floor):
        return None
    boss = next(e for e in load_roster() if e["id"] == BOSS_ID)
    titles = BOSS_TITLES[rec.get("type", "NET")]
    level = enemy_level(rec, floor) + 2 + (1 if rec.get("secured") else 0)
    return {"name": boss["name"], "id": BOSS_ID, "level": level, "type": boss["type"], "theme": rec.get("type", "NET"),
            "title": titles[(seed_for(did) + int(floor) // BOSS_EVERY) % len(titles)]}


def flavor(did: str, rec: dict[str, Any], pools: dict[str, list[str]]) -> str:
    """A fixed one-line description for this dungeon (picked by seed, not random)."""
    lines = pools.get(f"dungeon.flavor.{rec.get('type', 'NET').lower()}") or pools.get("dungeon.flavor") or ["A strange place."]
    text = lines[seed_for(did) % len(lines)]
    return clamp_say(text.replace("{dungeon}", rec.get("name", "")))


def roll_loot(did: str, rec: dict[str, Any], floor: int, boss: bool, salt: int) -> list[str]:
    """Item ids for clearing a floor. Quality scales with depth (and rarity)."""
    r = random.Random(f"{seed_for(did)}:{floor}:loot:{salt}")
    quality = int(floor) * loot_mult(rec, floor)
    tier = min(3, int(quality // 5))
    count = 1 + (1 if boss else 0) + (1 if quality > 8 and r.random() < 0.5 else 0)
    out = []
    for i in range(count):
        t = tier if (boss and i == 0) or r.random() < 0.6 else max(0, tier - 1)
        pool = [k for k in LOOT_TIERS[t] if k in ITEMS]
        if "evo_chip" in pool and not (boss and floor >= 15 and r.random() < 0.25):
            pool.remove("evo_chip")
        out.append(r.choice(pool))
    return out


def floor_xp(floor: int, result: str, boss: bool) -> int:
    if result not in ("cleared", "stairs"):
        return 0
    xp = 3 + 2 * int(floor) + ((10 + 2 * int(floor)) if boss and result == "cleared" else 0)
    return xp // 2 if result == "stairs" else xp


def summary(dungeons: dict[str, dict[str, Any]], now: float) -> dict[str, Any]:
    if not dungeons:
        return {"count": 0, "in_range": 0, "deepest": 0, "deepest_name": None, "floors": 0, "boss_kills": 0, "home": None}
    deep_id = max(dungeons, key=lambda k: (int(dungeons[k].get("depth", 0)), dungeons[k].get("name", "")))
    home = next((r["name"] for r in dungeons.values() if r.get("home")), None)
    return {
        "count": len(dungeons),
        "in_range": sum(1 for r in dungeons.values() if in_range(r, now)),
        "deepest": int(dungeons[deep_id].get("depth", 0)),
        "deepest_name": dungeons[deep_id].get("name") if int(dungeons[deep_id].get("depth", 0)) else None,
        "floors": sum(int(r.get("clears", 0)) for r in dungeons.values()),
        "boss_kills": sum(int(r.get("boss_kills", 0)) for r in dungeons.values()),
        "home": home,
    }
