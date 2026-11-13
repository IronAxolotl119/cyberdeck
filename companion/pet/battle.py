"""Pokemon-style battle data: types, the type chart, the enemy roster, the
companion's item bag, and short battle text.

Everything a deck shows from here is plain ASCII and at most 44 characters
(SAY_MAX), so it fits a 48-column screen with a 2-column border.

Type chart (attacker -> defender multiplier; anything not listed is 1x):

    NORM -> VOID 0.5, FIRE 0.5
    VOLT -> DATA 2, ICE 2, VOLT 0.5, VOID 0.5
    DATA -> VOID 2, DATA 0.5, FIRE 0.5
    NET  -> VOLT 2, NET 0.5, FIRE 0.5, VOID 0 (immune)
    FIRE -> DATA 2, NET 2, ICE 0.5, FIRE 0 (immune)
    ICE  -> FIRE 2, NET 2, ICE 0.5, VOLT 0.5
    VOID -> NORM 2, VOID 0.5, DATA 0.5
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from pet.moves import GUARD_MOVE, MOVE_LIBRARY, Move

TYPES: tuple[str, ...] = ("NORM", "VOLT", "DATA", "NET", "FIRE", "ICE", "VOID")

CHART: dict[str, dict[str, float]] = {
    "NORM": {"VOID": 0.5, "FIRE": 0.5},
    "VOLT": {"DATA": 2.0, "ICE": 2.0, "VOLT": 0.5, "VOID": 0.5},
    "DATA": {"VOID": 2.0, "DATA": 0.5, "FIRE": 0.5},
    "NET": {"VOLT": 2.0, "NET": 0.5, "FIRE": 0.5, "VOID": 0.0},
    "FIRE": {"DATA": 2.0, "NET": 2.0, "ICE": 0.5, "FIRE": 0.0},
    "ICE": {"FIRE": 2.0, "NET": 2.0, "ICE": 0.5, "VOLT": 0.5},
    "VOID": {"NORM": 2.0, "VOID": 0.5, "DATA": 0.5},
}

# PULSE's own (defending) type by species.
SPECIES_TYPES: dict[str, str] = {
    "chirplet": "NORM",
    "wavling": "NORM",
    "aethling": "FIRE",
    "luminode": "FIRE",
    "aurorion": "FIRE",
    "aethernova": "FIRE",
    "hexwave": "NET",
    "resonarch": "NET",
    "signumega": "NET",
    "staticore": "VOLT",
    "voltshard": "VOLT",
    "stormbit": "VOLT",
    "nullstorm": "VOLT",
    "jammeron": "VOID",
    "blackout": "VOID",
    "voidstatic": "VOID",
}

SAY_MAX = 44
MAX_LOADOUT = 4
CRIT_BASE = 1.0 / 16.0
CRIT_MULT = 1.5
MAX_ENERGY = 100

DATA_DIR = Path(__file__).resolve().parent / "data"


# ----------------------------------------------------------------- types
def norm_type(raw: Any, default: str = "NORM") -> str:
    text = str(raw or "").strip().upper()
    return text if text in TYPES else default


def effectiveness(attack_type: str, defend_type: str) -> float:
    return CHART.get(norm_type(attack_type), {}).get(norm_type(defend_type), 1.0)


def effectiveness_label(mult: float) -> str:
    if mult == 0:
        return "immune"
    if mult > 1:
        return "super"
    if mult < 1:
        return "weak"
    return "normal"


def weaknesses(defend_type: str) -> list[str]:
    """Attack types that do 2x to this type."""
    return [t for t in TYPES if effectiveness(t, defend_type) > 1]


def resistances(defend_type: str) -> list[str]:
    return [t for t in TYPES if 0 < effectiveness(t, defend_type) < 1]


def immunities(defend_type: str) -> list[str]:
    return [t for t in TYPES if effectiveness(t, defend_type) == 0]


def chart_payload() -> dict[str, Any]:
    return {
        "types": list(TYPES),
        "chart": {a: {d: effectiveness(a, d) for d in TYPES} for a in TYPES},
    }


def move_type(move: Move) -> str:
    return norm_type(getattr(move, "type", "NORM"))


def pet_type(creature: Any) -> str:
    return SPECIES_TYPES.get(getattr(creature, "species_id", ""), "NORM")


# ---------------------------------------------------------------- roster
@lru_cache(maxsize=1)
def load_roster() -> tuple[dict[str, Any], ...]:
    data = json.loads((DATA_DIR / "roster.json").read_text(encoding="utf-8"))
    return tuple(data["enemies"])


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def roster_entry(ref: Any) -> dict[str, Any] | None:
    key = _slug(ref)
    if not key:
        return None
    for entry in load_roster():
        if key in (entry["id"], _slug(entry["name"])):
            return entry
    return None


def roster_stats(entry: dict[str, Any], level: int) -> dict[str, int]:
    grow = entry.get("per_level", {})
    extra = max(0, int(level) - 1)
    return {k: int(entry[k]) + int(grow.get(k, 0)) * extra for k in ("hp", "attack", "defense", "speed")}


def roster_payload() -> list[dict[str, Any]]:
    out = []
    for entry in load_roster():
        row = {k: v for k, v in entry.items() if k != "per_level"}
        row["per_level"] = dict(entry.get("per_level", {}))
        row["weak_to"] = weaknesses(entry["type"])
        row["resists"] = resistances(entry["type"])
        row["immune_to"] = immunities(entry["type"])
        row["boss"] = bool(entry.get("boss"))
        row["extra"] = bool(entry.get("extra"))
        out.append(row)
    return out


def normalize_enemy(raw: Any) -> dict[str, Any]:
    """The deck's enemy object, filled in from the roster where missing."""
    data = raw if isinstance(raw, dict) else {}
    entry = roster_entry(data.get("id")) or roster_entry(data.get("name"))

    def num(key: str, default: int) -> int:
        try:
            return int(data[key])
        except (KeyError, TypeError, ValueError):
            return default

    level = max(1, num("level", 1))
    base = roster_stats(entry, level) if entry else {"hp": 1, "attack": 1, "defense": 0, "speed": 1}
    hp = num("hp", base["hp"])
    return {
        "id": str(data.get("id") or (entry["id"] if entry else _slug(data.get("name")) or "foe")),
        "name": str(data.get("name") or (entry["name"] if entry else "foe")),
        "type": norm_type(data.get("type"), entry["type"] if entry else "NORM"),
        "level": level,
        "hp": hp,
        "max_hp": num("max_hp", max(hp, base["hp"])),
        "attack": num("attack", base["attack"]),
        "defense": num("defense", base["defense"]),
        "speed": num("speed", base["speed"]),
        "face": str(data.get("face") or (entry["face"] if entry else "(o_o)")),
        "boss": bool(data.get("boss", entry.get("boss") if entry else False)),
    }


# ------------------------------------------------------------------ items
# id -> (name, kind, amount, usable_in_battle)
ITEMS: dict[str, tuple[str, str, int, bool]] = {
    "cache_cookie": ("cache cookie", "food", 20, True),
    "glitch_berry": ("glitch berry", "food", 15, True),
    "data_cookie": ("data cookie", "food", 25, True),
    "byte_burger": ("byte burger", "food", 40, True),
    "battery": ("battery", "battery", 30, True),
    "power_cell": ("power cell", "battery", 25, True),
    "overclock_chip": ("overclock chip", "battery", 50, True),
    "medicine": ("medicine", "medicine", 5, True),
    "patch_kit": ("patch kit", "medicine", 10, True),
    "evo_chip": ("evo chip", "evolve", 0, False),
}
ALIASES = {"cookie": "cache_cookie", "evo": "evo_chip", "evolve_chip": "evo_chip", "med": "medicine", "meds": "medicine"}
STARTER_BAG = {"cache_cookie": 3, "battery": 1, "medicine": 1}
BAG_KINDS = ("food", "battery", "medicine", "evolve", "toy", "trinket")


def item_key(ref: Any) -> str:
    key = _slug(ref)
    return ALIASES.get(key, key)


def item_info(key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Definition for a bag id: built-ins, else what was stored with it."""
    if key in ITEMS:
        name, kind, amount, usable = ITEMS[key]
        return {"id": key, "name": name, "kind": kind, "amount": amount, "usable_in_battle": usable}
    extra = extra or {}
    kind = str(extra.get("kind") or "trinket")
    amount = int(extra.get("amount") or extra.get("power") or 0)
    if kind == "food":
        amount = max(5, amount)
    elif kind == "battery":
        amount = max(10, amount)
    return {
        "id": key,
        "name": str(extra.get("name") or key.replace("_", " ")),
        "kind": kind,
        "amount": amount,
        "usable_in_battle": kind in {"food", "battery", "medicine"},
    }


def item_desc(info: dict[str, Any]) -> str:
    from pet.speech import load_pools

    pools = load_pools()
    lines = pools.get(f"item.{info['id']}")
    if lines:
        return clamp_say(lines[0])
    kind, amount = info["kind"], info["amount"]
    if kind == "food":
        return clamp_say(f"Restores {amount} HP.")
    if kind == "battery":
        return clamp_say(f"Restores {amount} EN.")
    if kind == "medicine":
        return clamp_say(f"Cures glitch. Restores {amount} HP.")
    if kind == "evolve":
        return "Evolves PULSE. Not for battle."
    return "A keepsake. Not for battle."


class Bag:
    """The companion's items: {id: {"qty": n, "name", "kind", "amount"}} on the creature."""

    def __init__(self, creature: Any) -> None:
        if not isinstance(getattr(creature, "bag", None), dict):
            creature.bag = {}
        self.data: dict[str, dict[str, Any]] = creature.bag

    def qty(self, key: str) -> int:
        row = self.data.get(key)
        return int(row.get("qty", 0)) if row else 0

    def info(self, key: str) -> dict[str, Any]:
        return item_info(key, self.data.get(key))

    def find(self, ref: Any) -> str | None:
        key = item_key(ref)
        if key in self.data:
            return key
        for k, row in self.data.items():
            if _slug(row.get("name")) == key:
                return k
        return key if key in ITEMS else None

    def add(self, item: Any, qty: int = 1) -> dict[str, Any]:
        """Add an item (an id/name string, or {name, kind, power}). Returns its info."""
        if isinstance(item, dict):
            key = item_key(item.get("id") or item.get("name"))
            kind = str(item.get("kind") or "").lower()
            extra = {"name": item.get("name"), "kind": kind if kind in BAG_KINDS else "trinket",
                     "amount": item.get("amount", item.get("power"))}
        else:
            key, extra = item_key(item), {"name": str(item)}
        if not key:
            raise ValueError("item needs a name")
        if key not in ITEMS and "kind" not in extra:
            extra["kind"] = "trinket"
        row = self.data.setdefault(key, {"qty": 0})
        if key not in ITEMS:
            info = item_info(key, extra)
            row.update(name=info["name"], kind=info["kind"], amount=info["amount"])
        row["qty"] = min(99, int(row.get("qty", 0)) + max(1, int(qty)))
        return self.info(key)

    def take(self, key: str) -> bool:
        row = self.data.get(key)
        if not row or int(row.get("qty", 0)) <= 0:
            return False
        row["qty"] = int(row["qty"]) - 1
        if row["qty"] <= 0:
            del self.data[key]
        return True

    def payload(self) -> list[dict[str, Any]]:
        out = []
        for key in sorted(self.data, key=lambda k: (not self.info(k)["usable_in_battle"], k)):
            info = self.info(key)
            out.append({
                "id": key,
                "name": info["name"],
                "kind": info["kind"],
                "qty": self.qty(key),
                "usable_in_battle": info["usable_in_battle"],
                "desc": item_desc(info),
            })
        return out


# ------------------------------------------------------------------- text
_ASCII_SWAPS = {"\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00e9": "e"}


def to_ascii(text: str) -> str:
    for a, b in _ASCII_SWAPS.items():
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.split())


def clamp_say(text: str, limit: int = SAY_MAX) -> str:
    """Plain ASCII, at most `limit` characters, cut at a word boundary."""
    text = to_ascii(text or "")
    if len(text) <= limit:
        return text
    cut = text[: limit - 3]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-") + "..."


def battle_say(creature: Any, key: str, rng: random.Random, slots: dict[str, Any] | None = None) -> str:
    """One battle line in the pet's stage voice, ASCII and <= 44 chars."""
    life = getattr(creature, "life", None)
    slots = dict(slots or {})
    if life is None:
        from pet.speech import LinePicker, fill

        return clamp_say(fill(LinePicker().pick(key, rng), slots))
    text = life.say(creature, key, rng, life.last_now, slots, decorate=False)
    return clamp_say(text)


def display_name(creature: Any) -> str:
    life = getattr(creature, "life", None)
    name = (life.nickname if life is not None and life.nickname else None) or getattr(creature, "name", "PULSE") or "PULSE"
    return to_ascii(str(name)).upper()[:16] or "PULSE"


def move_desc(creature: Any, move: Move, rng: random.Random | None = None) -> str:
    from pet.speech import load_pools

    lines = load_pools().get(f"desc.{move.id}")
    if not lines:
        return clamp_say(move.description)
    # Stable per pet (so the menu does not flicker), different between pets.
    seed = getattr(getattr(creature, "life", None), "seed", 0) or 0
    return clamp_say(lines[(int(seed) + len(move.id)) % len(lines)])


def loadout(creature: Any) -> list[Move]:
    """Up to 4 battle moves: the saved loadout, else the strongest of different types."""
    known = [MOVE_LIBRARY[m] for m in getattr(creature, "known_move_ids", []) if m in MOVE_LIBRARY]
    saved = [MOVE_LIBRARY[m] for m in (getattr(creature, "battle_moves", None) or []) if m in MOVE_LIBRARY and m in creature.known_move_ids]
    if saved:
        return saved[:MAX_LOADOUT]
    ranked = sorted(known, key=lambda m: (-creature.effective_power(m), m.energy_cost, m.id))
    picked: list[Move] = []
    for move in ranked:  # one per type first
        if len(picked) < MAX_LOADOUT and move_type(move) not in {move_type(p) for p in picked}:
            picked.append(move)
    for move in ranked:
        if len(picked) < MAX_LOADOUT and move not in picked:
            picked.append(move)
    # Keep the cheap fallback (Chirp) reachable when the pet knows it.
    if "chirp" in creature.known_move_ids and all(m.id != "chirp" for m in picked) and len(picked) == MAX_LOADOUT:
        picked[-1] = MOVE_LIBRARY["chirp"]
    return picked


def battle_move_payload(creature: Any, move: Move) -> dict[str, Any]:
    power = creature.effective_power(move)
    return {
        "id": move.id,
        "name": move.name,
        "type": move_type(move),
        "power": power,
        "energy_cost": move.energy_cost,
        "accuracy": move.accuracy,
        "rank": creature.move_rank(move.id),
        "effect": move.effect,
        "kind": move.kind,
        "bonus": power - int(move.power),
        "desc": move_desc(creature, move),
    }


def best_move(creature: Any, enemy: dict[str, Any]) -> Move | None:
    """What a smart pet would pick vs this enemy (used when the deck sends no move)."""
    energy = creature.stats.energy
    best, best_score = None, -1.0
    for move in loadout(creature):
        if move.energy_cost > energy or move.effect == "guard":
            continue
        mult = effectiveness(move_type(move), enemy.get("type", "NORM"))
        score = (creature.effective_power(move) + creature.stats.attack) * mult * move.accuracy / 100
        if score > best_score:
            best, best_score = move, score
    return best


__all__ = [
    "TYPES", "CHART", "SPECIES_TYPES", "SAY_MAX", "Bag", "ITEMS", "STARTER_BAG", "GUARD_MOVE",
    "effectiveness", "effectiveness_label", "weaknesses", "resistances", "immunities", "chart_payload",
    "move_type", "pet_type", "load_roster", "roster_entry", "roster_stats", "roster_payload",
    "normalize_enemy", "item_key", "item_info", "item_desc", "to_ascii", "clamp_say", "battle_say",
    "display_name", "move_desc", "loadout", "battle_move_payload", "best_move",
]
