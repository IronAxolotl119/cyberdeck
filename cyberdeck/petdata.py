"""petdata.py — make sense of whatever the companion says about the pet.

The companion board is a separate program (maybe written by someone
else), so the deck must not assume too much. Everything that reads a
pet payload goes through here:

  normalize_pet(raw)   a tidy dict: always has name/species/stage/needs/
                       mood (a word), plus the optional extras when sent
                       (level, xp, hp, max_hp, attack, defense, speed,
                       moves, care_mistakes, avatar)
  stats_line(pet)      'Lv 3 · HP 18/20 · ATK 6 DEF 5 SPD 7'
  event_notice(msg)    a pushed pet.event -> (kind, level, text)
  parse_avatar(reply)  a pet.avatar.ok payload -> Avatar (or None)
  nearest_256/8(rgb)   pick the closest terminal colour for a pixel

Nothing in here does I/O, so it is all unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyberdeck.protocol import STAGES

NEED_KEYS = ("hunger", "happiness", "energy")
STAT_KEYS = ("level", "xp", "hp", "max_hp", "attack", "defense", "speed", "care_mistakes")


# --- pet payloads -------------------------------------------------------------

def _int(value: Any) -> int | None:
    """Numbers (or numeric strings) -> int; anything else -> None."""
    if isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def mood_word(hunger: int | None, happiness: int | None, energy: int | None) -> str:
    """Same rules as the sim pet, for companions that only send numbers."""
    if hunger is not None and hunger >= 82:
        return "hungry"
    if energy is not None and energy <= 18:
        return "tired"
    if happiness is None:
        return "content"
    if happiness <= 28:
        return "grumpy"
    if happiness >= 85 and (hunger is None or hunger <= 45):
        return "joyful"
    return "playful" if happiness >= 70 else "content"


def normalize_pet(raw: Any) -> dict[str, Any]:
    """A safe, predictable pet dict from any payload (missing keys -> None)."""
    raw = raw if isinstance(raw, dict) else {}
    avatar = raw.get("avatar") if isinstance(raw.get("avatar"), dict) else None
    pet: dict[str, Any] = {
        "name": str(raw.get("name") or "PULSE"),
        # Show whatever the companion calls it: species, form, or the avatar's form.
        "species": str(raw.get("species") or raw.get("form") or (avatar or {}).get("form") or "?"),
        "stage": str(raw.get("stage") or "?"),
        "age_hours": raw.get("age_hours"),
        "gifts": _int(raw.get("gifts")),
        "avatar": avatar,
    }
    for key in NEED_KEYS:
        pet[key] = _int(raw.get(key))
    mood = raw.get("mood")
    if isinstance(mood, str) and mood.strip():
        pet["mood"] = mood.strip()
    else:
        # 'mood' may be a 0-100 score: treat it as happiness if that is missing.
        if pet["happiness"] is None and _int(mood) is not None:
            pet["happiness"] = _int(mood)
        pet["mood"] = mood_word(pet["hunger"], pet["happiness"], pet["energy"])
    for key in STAT_KEYS:
        pet[key] = _int(raw.get(key))
    moves = raw.get("moves")
    if isinstance(moves, list):
        names = [m.get("name") if isinstance(m, dict) else m for m in moves]
        pet["moves"] = [str(n) for n in names if n]
    else:
        pet["moves"] = []
    return pet


def stage_index(stage: str) -> int:
    """Position on the evolution ladder (-1 if unknown)."""
    return STAGES.index(stage) if stage in STAGES else -1


def stats_line(pet: dict[str, Any]) -> str:
    """Only the stats the companion actually sent."""
    parts = []
    if pet.get("level") is not None:
        xp = f" ({pet['xp']} xp)" if pet.get("xp") is not None else ""
        parts.append(f"Lv {pet['level']}{xp}")
    if pet.get("hp") is not None:
        parts.append(f"HP {pet['hp']}/{pet['max_hp']}" if pet.get("max_hp") else f"HP {pet['hp']}")
    combat = [f"{label} {pet[key]}" for key, label in (("attack", "ATK"), ("defense", "DEF"), ("speed", "SPD"))
              if pet.get(key) is not None]
    if combat:
        parts.append(" ".join(combat))
    return " · ".join(parts)


# --- pushed events --------------------------------------------------------------

def event_notice(payload: dict[str, Any]) -> tuple[str, str, str]:
    """pet.event payload -> (kind, notice level, one line of text)."""
    kind = str(payload.get("event") or "event")
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    pet = normalize_pet(payload.get("pet"))
    name = pet["name"]
    if kind == "hungry":
        return kind, "alert", f"{name} is hungry! — try `pet feed`"
    if kind == "tired":
        return kind, "warn", f"{name} is worn out — let it rest"
    if kind == "sad":
        return kind, "warn", f"{name} is sad — try `pet play`"
    if kind == "evolved":
        new = detail.get("form") or detail.get("to") or detail.get("stage") or pet["species"]
        return kind, "ok", f"{name} evolved into {new}!"
    if kind == "level_up":
        level = detail.get("level", pet.get("level"))
        return kind, "ok", f"{name} reached level {level}!"
    if kind == "learned_move":
        return kind, "ok", f"{name} learned {detail.get('move') or detail.get('name') or 'a new move'}!"
    if kind == "signal_found":
        what = detail.get("ssid") or detail.get("name") or detail.get("signal") or "something"
        return kind, "info", f"{name} sniffed out a signal: {what}"
    extra = detail.get("text") or detail.get("message") or ""
    return kind, "info", f"{name}: {kind.replace('_', ' ')} {extra}".rstrip()


# --- avatars ----------------------------------------------------------------------

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Avatar:
    """A small picture: rows of palette indexes, and the palette."""

    rows: list[list[int]]
    palette: list[RGB | None]   # None = transparent
    form: str = ""

    def pixel(self, x: int, y: int) -> RGB | None:
        """Colour at (x, y), or None for transparent / out of range."""
        if 0 <= y < len(self.rows) and 0 <= x < len(self.rows[y]):
            index = self.rows[y][x]
            if 0 <= index < len(self.palette):
                return self.palette[index]
        return None

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


def hex_to_rgb(text: Any) -> RGB | None:
    """'#ffcc00' / 'ffcc00' / '#fc0' -> (255, 204, 0). Anything else: None."""
    if not isinstance(text, str):
        return None
    h = text.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def parse_avatar(payload: Any, max_size: int = 32) -> Avatar | None:
    """Validate a pet.avatar.ok payload. Bad or missing data -> None."""
    if not isinstance(payload, dict):
        return None
    rows, palette = payload.get("rows"), payload.get("palette")
    if not isinstance(rows, list) or not isinstance(palette, list) or not rows or len(rows) > max_size:
        return None
    clean: list[list[int]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) > max_size:
            return None
        clean.append([v if isinstance(v, int) and not isinstance(v, bool) else -1 for v in row])
    return Avatar(clean, [hex_to_rgb(c) for c in palette], str(payload.get("form") or ""))


def half_block_cells(avatar: Avatar) -> list[list[tuple[RGB | None, RGB | None]]]:
    """Pair pixel rows: each text cell is '▀' with top=fg, bottom=bg colour."""
    out = []
    for y in range(0, len(avatar.rows), 2):
        out.append([(avatar.pixel(x, y), avatar.pixel(x, y + 1)) for x in range(avatar.width)])
    return out


# Standard xterm-256 colour cube levels.
_CUBE = (0, 95, 135, 175, 215, 255)


def nearest_256(rgb: RGB) -> int:
    """Closest xterm-256 colour index (6x6x6 cube or grey ramp)."""
    def closest_level(v: int) -> int:
        return min(range(6), key=lambda i: abs(_CUBE[i] - v))

    r, g, b = (closest_level(c) for c in rgb)
    cube_rgb = (_CUBE[r], _CUBE[g], _CUBE[b])
    cube = 16 + 36 * r + 6 * g + b
    grey_level = max(0, min(23, round((sum(rgb) / 3 - 8) / 10)))
    grey_value = 8 + 10 * grey_level
    if _dist(rgb, (grey_value,) * 3) < _dist(rgb, cube_rgb):
        return 232 + grey_level
    return cube


# The 8 basic curses colours, in curses' own numbering.
BASIC_8: dict[int, RGB] = {0: (0, 0, 0), 1: (205, 0, 0), 2: (0, 205, 0), 3: (205, 205, 0),
                           4: (0, 0, 238), 5: (205, 0, 205), 6: (0, 205, 205), 7: (229, 229, 229)}


def nearest_8(rgb: RGB) -> int:
    return min(BASIC_8, key=lambda k: _dist(rgb, BASIC_8[k]))


def _dist(a: RGB, b: RGB) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b))
