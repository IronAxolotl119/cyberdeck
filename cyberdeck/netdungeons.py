"""netdungeons.py — network dungeons: the shared rules, and the deck's maps.

In plain English: every Wi-Fi network, Bluetooth device and LAN device
PULSE discovers unlocks a dungeon named after it. The companion owns the
list of dungeons, their seeds, the floor numbers, progress and rewards
(companion/docs/DUNGEON_INTEGRATION.md, "Network dungeons"). The deck
owns the map, the walking and the fights.

This file has three parts:

  1. The companion's generation rules, ported line for line from
     companion/pet/dungeons.py (ids, seeds, difficulty, layout numbers,
     themes, enemy mix, bosses, loot, floor XP). The deck's sim pet
     (cyberdeck/companion.py) uses them, so it answers exactly like the
     real companion; tests/test_netdungeons.py checks that they match.
  2. The deck's map builder: random.Random(f"{seed}:{floor}") plus the
     floor's `layout` numbers -> rooms, corridors, stairs, traps, pickups
     and enemy spots. Bad or missing numbers are clamped or replaced by
     defaults, so a confused companion can never crash the deck.
  3. Text helpers for the 48-column screens: the picker rows and the
     floor / boss banners (the mockups in the spec).

Privacy: raw BSSIDs and MACs never reach this file's output. Ids are
8-hex-digit hashes and names are display-safe (a MAC-only name becomes
"hidden-<id4>"). Nothing here does I/O, so it is all unit-tested.
"""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from cyberdeck.battledata import ALL_ROSTER, ITEMS, SAY_MAX, SCREEN_W, TYPES, clamp_say

# =====================================================================
# 1. The companion's rules (a port of companion/pet/dungeons.py)
# =====================================================================

DUNGEON_KINDS = ("wifi", "bt", "lan")
KIND_TYPE = {"wifi": "NET", "bt": "VOLT", "lan": "DATA"}
RARE_TYPE = "VOID"
THEME_TYPES = ("NET", "VOLT", "DATA", "VOID")

IN_RANGE_WINDOW = 300.0        # seconds a sighting keeps a dungeon "in range"
IN_RANGE_XP_MULT = 1.5
BOSS_EVERY = 5                 # boss every 5th floor; the floor after is a checkpoint
NAME_MAX = 24
FOUND_COOLDOWN = 30.0          # at most one dungeon_found / dungeons_found per 30 s
HOME_HEAL_SHARE = 0.15
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

_ASCII_SWAPS = {"\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                "\u2026": "...", "\u00e9": "e"}


def to_ascii(text: str) -> str:
    """Plain ASCII, single spaces (the companion's to_ascii)."""
    for a, b in _ASCII_SWAPS.items():
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.split())


def roster_rows() -> list[dict[str, Any]]:
    """The enemy list as the companion's roster.json rows (id, name, type, depth, boss)."""
    return [{"id": k.id, "name": k.name, "type": k.type, "depth": k.depth, "boss": k.boss} for k in ALL_ROSTER]


def _hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def dungeon_id(kind: str, ident: str | None, name: str | None) -> str:
    """Short stable hash of the BSSID / MAC (falls back to the name)."""
    key = str(ident or "").strip().lower() or str(name or "").strip().lower()
    return _hex(f"{kind}:{key}")[:8]


def seed_for(did: str) -> int:
    """The dungeon seed. Each floor's map uses random.Random(f"{seed}:{floor}")."""
    return int(_hex(f"dungeon:{did}")[:8], 16)


def looks_like_mac(text: Any) -> bool:
    """True for 'aa:bb:cc:dd:ee:ff' style text (never shown by the deck)."""
    return bool(_MAC.search(str(text or "")))


def scrub_ids(text: Any) -> str:
    """Hide any MAC / BSSID inside a line before it is shown or logged."""
    return _MAC.sub("<hidden>", str(text or ""))


def safe_name(did: str, ident: str | None, name: str | None) -> tuple[str, bool]:
    """Display-safe ASCII name (<= 24) and whether the signal was hidden."""
    raw = str(name or "")
    text = to_ascii(_MAC.sub("", raw)).strip(" -_:")
    if not text or raw.strip().lower() == str(ident or "").strip().lower() or _MAC.fullmatch(raw.strip() or "x"):
        return f"hidden-{did[:4]}", True
    return text[:NAME_MAX].rstrip(), False


def difficulty_for(rssi: int | None, secured: bool, rare: bool) -> int:
    """1-10. Strong signal = easy, weak = hard; secured and rare add one each."""
    base = 4 if rssi is None else max(1, min(8, 1 + round((-40 - int(rssi)) / 8)))
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
    return did, {
        "name": shown, "kind": kind, "type": primary, "type2": secondary, "rare": rare, "hidden": hidden,
        "secured": bool(secured), "difficulty": difficulty_for(rssi, bool(secured), rare),
        "ssid": (str(name) if kind == "wifi" and not hidden else ""),
        "first_seen": float(now), "last_seen": float(now),
        "depth": 0, "clears": 0, "boss_kills": 0, "runs": 0, "entered": False, "home": False,
        "battles": {"wins": 0, "losses": 0, "flees": 0},
    }


def checkpoint_for(depth: int) -> int:
    """The deepest start floor unlocked: 1, then 6 after floor 5, 11 after 10..."""
    return 1 + BOSS_EVERY * (max(0, int(depth)) // BOSS_EVERY)


def start_floors(depth: int) -> list[int]:
    return list(range(1, checkpoint_for(depth) + 1, BOSS_EVERY))


def snap_start(depth: int, floor: Any) -> int:
    """floor? -> the highest unlocked start floor <= floor (default: the last checkpoint)."""
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


def theme(did: str, rec: dict[str, Any]) -> dict[str, Any]:
    seed = seed_for(did)
    kind = rec.get("type", "NET")
    wall, floor_glyph = GLYPHS[kind][seed % len(GLYPHS[kind])]
    palette = PALETTES[kind][(seed // 7) % len(PALETTES[kind])]
    return {"type": kind, "type2": rec.get("type2"), "wall_glyph": wall, "floor_glyph": floor_glyph,
            "palette": palette}


def layout(did: str, rec: dict[str, Any], floor: int) -> dict[str, Any]:
    """Map numbers for one floor: deeper means bigger, darker, more traps."""
    r = random.Random(f"{seed_for(did)}:{floor}:layout")
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
    return {"width": width, "height": height, "rooms_min": rooms_min, "rooms_max": rooms_max,
            "room_w": room_w, "room_h": room_h, "corridor": corridor, "loops": loops, "dark": bool(dark),
            "traps": traps, "items": items}


def enemy_weights(rec: dict[str, Any], floor: int) -> dict[str, float]:
    """Roster weights for this dungeon's theme at this depth (no bosses)."""
    gate = min(5, 1 + (int(floor) - 1) // 2)
    depth = min(1.0, int(floor) / 10.0)
    out: dict[str, float] = {}
    for entry in roster_rows():
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
    by_id = {e["id"]: e for e in roster_rows()}
    ids = sorted(weights)
    kinds = 1 if is_boss_floor(floor) else 1 + (floor >= 3) + (floor >= 8)
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
        out.append({"name": entry["name"], "id": eid, "type": entry["type"], "level": base + r.randint(0, 1),
                    "count": count})
    return out


def boss_for(did: str, rec: dict[str, Any], floor: int) -> dict[str, Any] | None:
    if not is_boss_floor(floor):
        return None
    boss = next(e for e in roster_rows() if e["id"] == BOSS_ID)
    titles = BOSS_TITLES[rec.get("type", "NET")]
    level = enemy_level(rec, floor) + 2 + (1 if rec.get("secured") else 0)
    return {"name": boss["name"], "id": BOSS_ID, "level": level, "type": boss["type"],
            "theme": rec.get("type", "NET"), "title": titles[(seed_for(did) + int(floor) // BOSS_EVERY) % len(titles)]}


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
    """pet.stats "dungeons": {count, in_range, deepest, deepest_name, floors, boss_kills, home}."""
    if not dungeons:
        return {"count": 0, "in_range": 0, "deepest": 0, "deepest_name": None, "floors": 0, "boss_kills": 0,
                "home": None}
    deep_id = max(dungeons, key=lambda k: (int(dungeons[k].get("depth", 0)), dungeons[k].get("name", "")))
    deepest = int(dungeons[deep_id].get("depth", 0))
    return {
        "count": len(dungeons),
        "in_range": sum(1 for r in dungeons.values() if in_range(r, now)),
        "deepest": deepest,
        "deepest_name": dungeons[deep_id].get("name") if deepest else None,
        "floors": sum(int(r.get("clears", 0)) for r in dungeons.values()),
        "boss_kills": sum(int(r.get("boss_kills", 0)) for r in dungeons.values()),
        "home": next((r["name"] for r in dungeons.values() if r.get("home")), None),
    }


# =====================================================================
# 2. The deck's map builder
# =====================================================================

# Used for any layout number the companion leaves out (or sends broken).
DEFAULT_LAYOUT: dict[str, Any] = {"width": 48, "height": 15, "rooms_min": 2, "rooms_max": 4,
                                  "room_w": [4, 8], "room_h": [3, 5], "corridor": "straight", "loops": 0,
                                  "dark": False, "traps": 0, "items": 2}
DEFAULT_THEME: dict[str, Any] = {"type": "NET", "type2": None, "wall_glyph": "#", "floor_glyph": ".",
                                 "palette": ""}
# Map letters the deck already uses; a theme glyph can't be one of these.
RESERVED_GLYPHS = set("@>*!^$ ") | {k.glyph for k in ALL_ROSTER}


@dataclass(frozen=True)
class MapLimits:
    """The biggest floor the deck will build (config/dungeons.ini [map])."""
    max_width: int = 80
    max_height: int = 24
    max_rooms: int = 12
    max_traps: int = 8
    max_items: int = 8
    max_loops: int = 4


def _num(value: Any, default: int, lo: int, hi: int) -> int:
    """An int in [lo, hi]; anything that isn't a number becomes the default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = default
    return max(lo, min(hi, int(value)))


def _pair(value: Any, default: list[int], lo: int, hi: int) -> tuple[int, int]:
    """[min, max] clamped to [lo, hi], with min <= max."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        value = default
    a, b = (_num(v, d, lo, hi) for v, d in zip(value, default))
    return (a, b) if a <= b else (b, a)


def clean_layout(raw: Any, limits: MapLimits = MapLimits()) -> dict[str, Any]:
    """The companion's layout numbers, clamped to what the deck can draw."""
    d = DEFAULT_LAYOUT
    raw = raw if isinstance(raw, dict) else {}
    width = _num(raw.get("width"), d["width"], 24, max(24, limits.max_width))
    height = _num(raw.get("height"), d["height"], 10, max(10, limits.max_height))
    rooms_max = _num(raw.get("rooms_max"), d["rooms_max"], 1, max(1, limits.max_rooms))
    rooms_min = _num(raw.get("rooms_min"), d["rooms_min"], 1, rooms_max)
    room_w = _pair(raw.get("room_w"), d["room_w"], 3, max(3, min(16, width // 2 - 2)))
    room_h = _pair(raw.get("room_h"), d["room_h"], 3, max(3, min(10, height // 2 - 1)))
    corridor = raw.get("corridor") if raw.get("corridor") in ("winding", "straight") else d["corridor"]
    return {"width": width, "height": height, "rooms_min": rooms_min, "rooms_max": rooms_max,
            "room_w": list(room_w), "room_h": list(room_h), "corridor": corridor,
            "loops": _num(raw.get("loops"), d["loops"], 0, limits.max_loops),
            "dark": raw.get("dark") is True,
            "traps": _num(raw.get("traps"), d["traps"], 0, limits.max_traps),
            "items": _num(raw.get("items"), d["items"], 0, limits.max_items)}


def _glyph(value: Any, default: str) -> str:
    text = str(value) if isinstance(value, str) else ""
    return text if len(text) == 1 and 33 <= ord(text) < 127 and text not in RESERVED_GLYPHS else default


def clean_theme(raw: Any) -> dict[str, Any]:
    """Wall / floor glyphs (one printable ASCII char, not a map letter) and the palette name."""
    raw = raw if isinstance(raw, dict) else {}
    wall = _glyph(raw.get("wall_glyph"), "#")
    floor = _glyph(raw.get("floor_glyph"), ".")
    if floor == wall:
        wall, floor = "#", "."
    kind = str(raw.get("type") or "NET").upper()
    return {"type": kind if kind in TYPES else "NET", "type2": raw.get("type2") if raw.get("type2") in TYPES else None,
            "wall_glyph": wall, "floor_glyph": floor,
            "palette": str(raw.get("palette") or "")[:24]}


@dataclass
class FloorMap:
    """One built floor. grid uses '#' walls, '.' floor, '>' stairs (themes are drawn on top)."""
    width: int
    height: int
    grid: list[list[str]]
    rooms: list[tuple[int, int, int, int]]
    start: tuple[int, int]
    stairs: tuple[int, int]
    traps: list[tuple[int, int]] = field(default_factory=list)
    items: list[tuple[int, int]] = field(default_factory=list)
    enemies: list[tuple[dict[str, Any], tuple[int, int]]] = field(default_factory=list)   # (entry, spot)
    boss: tuple[dict[str, Any], tuple[int, int]] | None = None
    free: list[tuple[int, int]] = field(default_factory=list)                            # spare floor cells
    rng: random.Random | None = None                                                     # continues the floor's rng

    def text(self) -> list[str]:
        """The bare map (tests and the headless preview)."""
        rows = [row[:] for row in self.grid]
        for x, y in self.traps:
            rows[y][x] = "^"
        for x, y in self.items:
            rows[y][x] = "*"
        for entry, (x, y) in self.enemies:
            rows[y][x] = entry.get("glyph", "e")
        if self.boss:
            x, y = self.boss[1]
            rows[y][x] = "K"
        sx, sy = self.start
        rows[sy][sx] = "@"
        return ["".join(r) for r in rows]


def _carve(grid: list[list[str]], x: int, y: int) -> None:
    if 0 < y < len(grid) - 1 and 0 < x < len(grid[0]) - 1:
        grid[y][x] = "."


def _line(grid: list[list[str]], a: tuple[int, int], b: tuple[int, int], rng: random.Random) -> None:
    """An L-shaped tunnel from a to b (which leg first is a coin flip)."""
    (x1, y1), (x2, y2) = a, b
    if rng.random() < 0.5:
        for x in range(min(x1, x2), max(x1, x2) + 1):
            _carve(grid, x, y1)
        for y in range(min(y1, y2), max(y1, y2) + 1):
            _carve(grid, x2, y)
    else:
        for y in range(min(y1, y2), max(y1, y2) + 1):
            _carve(grid, x1, y)
        for x in range(min(x1, x2), max(x1, x2) + 1):
            _carve(grid, x, y2)


def _corridor(grid: list[list[str]], a: tuple[int, int], b: tuple[int, int], winding: bool,
              rng: random.Random) -> None:
    """Straight = one L; winding = a few L's through random waypoints."""
    if not winding:
        _line(grid, a, b, rng)
        return
    h, w = len(grid), len(grid[0])
    points = [a]
    for _ in range(rng.randint(1, 2)):
        points.append((rng.randint(1, w - 2), rng.randint(1, h - 2)))
    points.append(b)
    for p, q in zip(points, points[1:]):
        _line(grid, p, q, rng)


def build_floor(seed: Any, floor: Any, layout_raw: Any = None, enemies: Any = None, boss: Any = None,
                limits: MapLimits = MapLimits()) -> FloorMap:
    """Build one floor from random.Random(f"{seed}:{floor}") and the layout numbers.

    The same (seed, floor, layout) always gives the same map.
    """
    lay = clean_layout(layout_raw, limits)
    rng = random.Random(f"{seed}:{floor}")
    w, h = lay["width"], lay["height"]
    grid = [["#"] * w for _ in range(h)]
    want = rng.randint(lay["rooms_min"], lay["rooms_max"])
    rooms: list[tuple[int, int, int, int]] = []
    for _ in range(300):
        if len(rooms) >= want:
            break
        rw = rng.randint(*lay["room_w"])
        rh = rng.randint(*lay["room_h"])
        rw, rh = min(rw, w - 4), min(rh, h - 4)
        x, y = rng.randint(1, w - rw - 2), rng.randint(1, h - rh - 2)
        if any(x - 1 < rx + rw2 and x + rw + 1 > rx and y - 1 < ry + rh2 and y + rh + 1 > ry
               for rx, ry, rw2, rh2 in rooms):
            continue
        rooms.append((x, y, rw, rh))
    if not rooms:                                   # never happens with sane numbers; be safe anyway
        rooms.append((2, 2, min(6, w - 4), min(3, h - 4)))
    rooms.sort(key=lambda r: (r[0] + r[2] / 2, r[1]))     # left to right: a journey across the map
    for x, y, rw, rh in rooms:
        for yy in range(y, y + rh):
            for xx in range(x, x + rw):
                grid[yy][xx] = "."
    centres = [(x + rw // 2, y + rh // 2) for x, y, rw, rh in rooms]
    winding = lay["corridor"] == "winding"
    for a, b in zip(centres, centres[1:]):
        _corridor(grid, a, b, winding, rng)
    if len(centres) > 2:
        for _ in range(lay["loops"]):              # extra links make loops (more ways round)
            i, j = sorted(rng.sample(range(len(centres)), 2))
            _corridor(grid, centres[i], centres[j], winding, rng)
    start = centres[0]
    if len(rooms) > 1:
        stairs = centres[-1]
    else:                                           # one room: the stairs go in its far corner
        x, y, rw, rh = rooms[0]
        stairs = (x + rw - 1, y + rh - 1) if (x + rw - 1, y + rh - 1) != start else (x, y)
    grid[stairs[1]][stairs[0]] = ">"
    # Spare floor cells, far from you first (pop() takes from the end); cells
    # near the start are only used when a small map runs out of room.
    cells = [(x, y) for y in range(h) for x in range(w) if grid[y][x] == "." and (x, y) != start]
    far = [c for c in cells if abs(c[0] - start[0]) + abs(c[1] - start[1]) > 3]
    near = [c for c in cells if 1 < abs(c[0] - start[0]) + abs(c[1] - start[1]) <= 3]
    rng.shuffle(far)
    rng.shuffle(near)
    free = near + far
    out = FloorMap(w, h, grid, rooms, start, stairs, free=free, rng=rng)
    # The boss waits next to the stairs.
    boss_entry = boss if isinstance(boss, dict) else None
    if boss_entry is not None and free:
        spot = min(free, key=lambda p: (abs(p[0] - stairs[0]) + abs(p[1] - stairs[1]), p))
        free.remove(spot)
        out.boss = (boss_entry, spot)
    for entry in clean_enemies(enemies):
        for _ in range(entry["count"]):
            if free:
                out.enemies.append((entry, free.pop()))
    for _ in range(lay["traps"]):
        if free:
            out.traps.append(free.pop())
    for _ in range(lay["items"]):
        if free:
            out.items.append(free.pop())
    return out


def clean_enemies(raw: Any) -> list[dict[str, Any]]:
    """The floor's enemy list: [{id, name, type, level, count}], bad rows dropped, counts 1-3."""
    out = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict) or not (row.get("id") or row.get("name")):
            continue
        name = to_ascii(str(row.get("name") or row.get("id")))[:16] or "enemy"
        etype = str(row.get("type") or "").upper()
        out.append({"id": str(row.get("id") or name), "name": name, "type": etype if etype in TYPES else "NORM",
                    "level": _num(row.get("level"), 1, 1, 99), "count": _num(row.get("count"), 1, 1, 3)})
    return out[:6]


def clean_boss(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = to_ascii(str(raw.get("name") or "kernel panic"))[:16] or "kernel panic"
    title = to_ascii(str(raw.get("title") or name))[:20] or name
    etype = str(raw.get("type") or "VOID").upper()
    return {"id": str(raw.get("id") or BOSS_ID), "name": name, "title": title,
            "type": etype if etype in TYPES else "VOID", "level": _num(raw.get("level"), 5, 1, 99)}


# =====================================================================
# 3. 48-column text: picker rows and banners (the spec's mockups)
# =====================================================================

INNER = SCREEN_W - 4          # text between "| " and " |"


def box_line(text: str = "") -> str:
    return "| " + text[:INNER].ljust(INNER) + " |"


def box_rule() -> str:
    return "+" + "-" * (SCREEN_W - 2) + "+"


def two_ends(left: str, right: str) -> str:
    """'left ..... right' in INNER columns (the left side is cut first)."""
    room = INNER - len(right) - 1
    return left[:max(0, room)].ljust(max(0, room)) + " " + right if right else left


def quoted(say: Any) -> str:
    text = clamp_say(say, SAY_MAX - 2)
    return f'"{text}"' if text else ""


def picker_row(row: dict[str, Any], selected: bool, start: int | None = None) -> str:
    """'> HomeNet         NET  D3  B2   HOME         ' (44 columns)."""
    mark = ">" if selected else " "
    rare = "*" if row.get("rare") else " "
    name = clamp_say(row.get("name") or "?", 15)
    tag = "HOME" if row.get("home") else "NEAR" if row.get("in_range") else ""
    text = (f"{mark}{rare}{name:<16}{str(row.get('type') or '?'):<5}D{_num(row.get('difficulty'), 4, 0, 99):<3}"
            f"B{_num(row.get('depth_record'), 0, 0, 999):<4}{tag:<5}")
    if selected and start is not None and start > 1:
        text += f" F{start}"           # the chosen start floor (a checkpoint)
    return text[:INNER]


def floor_tag(floor: int, boss: bool) -> str:
    return f"B{floor}F BOSS" if boss else f"B{floor}F"


def level_span(enemies: list[dict[str, Any]], boss: dict[str, Any] | None) -> str:
    levels = [e["level"] for e in enemies] + ([boss["level"]] if boss else [])
    return f"Lv {min(levels)}-{max(levels)}" if levels else "Lv ?"


def floor_banner(info: dict[str, Any], flavor: str = "") -> list[str]:
    """The floor-entry banner (or the boss banner on a boss floor), 48 columns."""
    floor = _num(info.get("floor"), 1, 1, 9999)
    name = clamp_say(info.get("name") or "dungeon", 24)
    boss = clean_boss(info.get("boss"))
    lines = [box_rule()]
    if boss:
        lines += [box_line(two_ends(name, floor_tag(floor, True))),
                  box_line(f"{boss['title'].upper()}  ({boss['name']} Lv {boss['level']})"),
                  box_rule(), box_line(quoted(info.get("say"))), box_rule()]
        return lines
    th = clean_theme(info.get("theme"))
    lay = clean_layout(info.get("layout"))
    enemies = clean_enemies(info.get("enemies"))
    types = th["type"] + (f"/{th['type2']}" if th["type2"] else "")
    mult = info.get("xp_mult") if isinstance(info.get("xp_mult"), (int, float)) else 1.0
    loot = info.get("loot_mult") if isinstance(info.get("loot_mult"), (int, float)) else 1.0
    top = f"{types}  {level_span(enemies, None)}  x{float(mult):g} XP  loot x{float(loot):g}"
    foes = ", ".join(f"{e['name']} x{e['count']}" for e in enemies) or "none"
    lines += [box_line(two_ends(name, floor_tag(floor, False))), box_line(top), box_rule(),
              box_line(clamp_say(flavor, INNER)), box_line(),
              box_line(clamp_say(f"Foes: {foes}", INNER)),
              box_line(f"Map {lay['width']}x{lay['height']}  rooms {lay['rooms_min']}-{lay['rooms_max']}  "
                       f"{lay['corridor']}"),
              box_line(f"Traps {lay['traps']}  items {lay['items']}  {'dark' if lay['dark'] else 'lit'}"
                       + ("  HOME" if info.get("home") else "")),
              box_line(), box_line(quoted(info.get("say"))), box_rule()]
    return lines
