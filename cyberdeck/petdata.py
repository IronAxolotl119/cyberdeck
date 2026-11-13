"""petdata.py — make sense of whatever the companion says about the pet.

The companion board is a separate program (maybe written by someone
else), so the deck must not assume too much. Everything that reads a
pet payload goes through here:

  normalize_pet(raw)   a tidy dict: always has name/species/stage/needs/
                       mood (a word), plus the optional extras when sent
                       (level, xp, hp, max_hp, attack, defense, speed,
                       moves, care_mistakes, avatar, asleep, evolution)
  stats_line(pet)      'Lv 3 · HP 18/20 · ATK 6 DEF 5 SPD 7'
  evolution_info(raw)  {next_stage, progress 0-1, ready} or None
  event_notice(msg)    a pushed pet.event -> (kind, level, text)
  parse_face(reply)    a pet.avatar.ok text-face payload -> Face (or None)
  pet_face(pet, now)   the face to show right now (animated, asleep-aware)
  parse_avatar(reply)  a sprite -> Avatar, a text face -> Face (or None)
  combat_stats(reply)  a pet.combat.stats.ok payload -> CombatStats
  nearest_256/8(rgb)   pick the closest terminal colour for a pixel

Stages and species are shown exactly as the companion names them, so a
new form (or a whole new ladder) never needs a deck update.

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
    asleep = raw.get("asleep")
    pet["asleep"] = asleep if isinstance(asleep, bool) else None
    pet["evolution"] = evolution_info(raw.get("evolution"))
    return pet


def evolution_info(raw: Any) -> dict[str, Any] | None:
    """{next_stage, progress (0-1), ready} from pet.status, or None if not sent."""
    if not isinstance(raw, dict):
        return None
    try:
        progress = float(raw.get("progress"))
    except (TypeError, ValueError):
        progress = None
    if progress is not None:
        progress = max(0.0, min(1.0, progress if progress == progress else 0.0))
    next_stage = raw.get("next_stage")
    return {"next_stage": str(next_stage) if next_stage else None, "progress": progress,
            "ready": raw.get("ready") is True, "choices": evolution_choices(raw.get("choices"))}


def evolution_choices(raw: Any) -> list[dict[str, Any]]:
    """[{species, stage, hint, available}] — branches for the next stage."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and str(item.get("species") or "").strip():
            out.append({"species": str(item["species"]).strip(), "stage": str(item.get("stage") or ""),
                        "hint": str(item.get("hint") or ""), "available": item.get("available") is not False})
    return out


def choice_line(choice: dict[str, Any]) -> str:
    """'Voltshard (champion)' plus '— locked: needs …' when it cannot be picked."""
    stage = f" ({choice['stage']})" if choice.get("stage") else ""
    if choice.get("available"):
        hint = f" — {choice['hint']}" if choice.get("hint") else ""
        return f"{choice['species']}{stage}{hint}"
    return f"{choice['species']}{stage} — locked: {choice.get('hint') or 'not available yet'}"


def evolution_line(evolution: dict[str, Any] | None, width: int = 10) -> str:
    """'→ champion [######----] 62%' (or 'final form' / '' when unknown)."""
    if not evolution:
        return ""
    if not evolution.get("next_stage"):
        return "final form"
    progress = evolution.get("progress")
    if progress is None:
        return f"→ {evolution['next_stage']}"
    filled = int(round(progress * width))
    ready = "  ready!" if evolution.get("ready") else ""
    return f"→ {evolution['next_stage']} [{'#' * filled}{'-' * (width - filled)}] {int(progress * 100)}%{ready}"


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
    if kind == "evolve_soon":
        nxt = detail.get("next_stage") or detail.get("to") or "its next form"
        return kind, "info", f"{name} is close to evolving into {nxt}{_eta(detail)}"
    if kind == "evolve_choice":
        choices = evolution_choices(detail.get("choices"))
        names = ", ".join(c["species"] for c in choices if c["available"]) or "?"
        return kind, "alert", f"{name} is ready to evolve! choose: {names} — `pet evolve`"
    if kind == "evolving":
        new = detail.get("to") or detail.get("form") or "something new"
        return kind, "ok", f"{name} is evolving: {detail.get('from') or pet['species']} → {new}…"
    if kind == "evolved":
        new = detail.get("form") or detail.get("to") or detail.get("stage") or pet["species"]
        return kind, "ok", f"{name} evolved into {new}!"
    if kind == "woke":
        why = detail.get("reason") or "rested"
        return kind, "ok", f"{name} woke up ({why}) — energy {pet['energy'] if pet['energy'] is not None else '?'}"
    if kind == "level_up":
        level = detail.get("level", pet.get("level"))
        return kind, "ok", f"{name} reached level {level}!"
    if kind == "learned_move":
        return kind, "ok", f"{name} learned {detail.get('move') or detail.get('name') or 'a new move'}!"
    if kind == "dungeon_found":
        # A discovery unlocked a network dungeon (names are display-safe; never a MAC).
        return kind, "ok", f"NEW DUNGEON: {dungeon_name(detail)}" + (" (rare!)" if detail.get("rare") is True else "") \
            + " — `dungeon` to explore"
    if kind == "dungeons_found":
        names = [dungeon_name({"name": n}) for n in (detail.get("names") or []) if isinstance(n, str)][:5]
        count = _int(detail.get("count")) or len(names)
        return kind, "ok", f"NEW DUNGEONS: {count} found" + (f" ({', '.join(names)})" if names else "") \
            + " — `dungeon` to explore"
    if kind == "signal_found":
        what = detail.get("ssid") or detail.get("name") or detail.get("signal") or "something"
        return kind, "info", f"{name} sniffed out a signal: {what}"
    extra = detail.get("text") or detail.get("message") or ""
    return kind, "info", f"{name}: {kind.replace('_', ' ')} {extra}".rstrip()


def dungeon_name(detail: dict[str, Any]) -> str:
    """A dungeon's name for alerts: ASCII, <= 24 chars, and never a raw MAC / BSSID."""
    from cyberdeck.netdungeons import NAME_MAX, looks_like_mac, to_ascii

    name = to_ascii(str(detail.get("name") or ""))[:NAME_MAX]
    if not name or looks_like_mac(name):
        did = str(detail.get("id") or "")
        return f"hidden-{did[:4]}" if did else "a hidden signal"
    return name


def _eta(detail: dict[str, Any]) -> str:
    """' (in about 3h)' from any eta-ish field the companion sends."""
    for key, unit in (("eta_hours", "h"), ("eta_minutes", "m"), ("eta_min", "m"), ("eta_seconds", "s"), ("eta_s", "s")):
        value = detail.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f" (in about {value:g}{unit})" if value > 0 else " (any moment now)"
    eta = detail.get("eta")
    return f" ({eta})" if isinstance(eta, str) and eta else ""


# --- text faces (current companions) ------------------------------------------------

# Used when the companion sends no face at all.
DEFAULT_FACES = {"joyful": "(^o^)", "playful": "(^_^)", "content": "(o_o)", "hungry": "(>_<)",
                 "tired": "(=_=)", "grumpy": "(>_>)", "sleep": "(-_-) zZ"}


@dataclass(frozen=True)
class Face:
    """An animated text face: frames shown in turn every interval_ms."""

    frames: tuple[str, ...]
    interval_ms: int = 500
    mood: str = ""

    def at(self, now: float) -> str:
        """The frame for this moment (now in seconds)."""
        if not self.frames:
            return ""
        step = int(now * 1000 // max(50, self.interval_ms))
        return self.frames[step % len(self.frames)]


def _clean_face(text: Any, limit: int = 24) -> str:
    """One printable line, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    text = text.strip()
    # Some companions send a quoted repr like "'(^_^)'": drop the outer quotes.
    if len(text) > 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return "".join(c for c in text.replace("\n", " ").replace("\t", " ") if c.isprintable())[:limit].strip()


def parse_face(payload: Any) -> Face | None:
    """A pet.avatar.ok text-face payload -> Face. Sprites (rows/palette) -> None."""
    if not isinstance(payload, dict):
        return None
    frames = payload.get("frames")
    if isinstance(frames, list):
        clean = tuple(f for f in (_clean_face(x) for x in frames[:16]) if f)
    else:
        clean = ()
    if not clean and _clean_face(payload.get("text")):
        clean = (_clean_face(payload.get("text")),)
    if not clean:
        return None
    interval = _int(payload.get("interval_ms")) or 500
    return Face(clean, max(80, min(10_000, interval)), str(payload.get("mood") or ""))


def is_face_avatar(avatar: Any) -> bool:
    """True when a pet object's avatar is the text-face kind."""
    return isinstance(avatar, dict) and (avatar.get("style") == "face" or isinstance(avatar.get("text"), str))


def pet_face(pet: dict[str, Any], now: float, face: Face | None = None) -> str:
    """The text face to draw: fetched frames, else avatar.text, else a default.

    Asleep pets always get a sleepy face with zZ, whatever was cached.
    """
    avatar = pet.get("avatar") if isinstance(pet.get("avatar"), dict) else {}
    mood = str(avatar.get("mood") or pet.get("mood") or "")
    if face is not None and face.frames and (not face.mood or face.mood == mood):
        text = face.at(now)
    else:
        text = _clean_face(avatar.get("text")) or DEFAULT_FACES.get(mood, "(o_o)")
    if pet.get("asleep") and "zz" not in text.lower():
        text = DEFAULT_FACES["sleep"] if face is None and not avatar.get("text") else f"{text} zZ"
    return text


# --- combat ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    name: str
    power: int = 0
    accuracy: float = 1.0      # 0-1
    energy_cost: int = 0


@dataclass(frozen=True)
class CombatStats:
    """PULSE's fighting numbers from pet.combat.stats.ok."""

    hp: int
    max_hp: int
    attack: int = 0
    defense: int = 0
    speed: int = 0
    level: int = 1
    energy: int | None = None
    asleep: bool = False
    moves: tuple[Move, ...] = ()


def combat_stats(payload: Any) -> CombatStats | None:
    """Validate pet.combat.stats.ok. Needs at least hp; everything else optional."""
    if not isinstance(payload, dict):
        return None
    src = payload.get("stats") if isinstance(payload.get("stats"), dict) else payload
    pet = payload.get("pet") if isinstance(payload.get("pet"), dict) else {}
    hp = _int(src.get("hp"))
    if hp is None:
        return None
    max_hp = _int(src.get("max_hp")) or max(1, hp)
    moves = []
    for raw in src.get("moves") or []:
        if isinstance(raw, str) and raw.strip():
            moves.append(Move(raw.strip()))
        elif isinstance(raw, dict) and str(raw.get("name") or "").strip():
            try:
                accuracy = float(raw.get("accuracy", 1.0))
            except (TypeError, ValueError):
                accuracy = 1.0
            if accuracy > 1.0:          # 0-100 style
                accuracy /= 100.0
            moves.append(Move(str(raw["name"]).strip(), _int(raw.get("power")) or 0,
                              max(0.0, min(1.0, accuracy)), max(0, _int(raw.get("energy_cost")) or 0)))
    energy = _int(src.get("energy"))
    if energy is None:
        energy = _int(pet.get("energy"))
    asleep = src.get("asleep", pet.get("asleep"))
    return CombatStats(hp=max(0, hp), max_hp=max_hp, attack=_int(src.get("attack")) or 0,
                       defense=_int(src.get("defense")) or 0, speed=_int(src.get("speed")) or 0,
                       level=_int(src.get("level")) or 1, energy=energy, asleep=asleep is True,
                       moves=tuple(moves))


# --- sprite avatars (older companions) ----------------------------------------------

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


def parse_avatar(payload: Any, max_size: int = 32) -> "Avatar | Face | None":
    """Validate a pet.avatar.ok payload. Bad or missing data -> None.

    Both kinds are accepted: an older companion's sprite (rows + palette)
    gives an Avatar, and a current companion's text face (style "face",
    with frames or text) gives a Face, the same as parse_face().
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("style") == "face" or (payload.get("rows") is None and
                                          ("frames" in payload or isinstance(payload.get("text"), str))):
        return parse_face(payload)
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
