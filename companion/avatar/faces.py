"""Small kaomoji-style text faces for the pet.

Each mood has 2–4 short frames (about 1–3 lines) that blink, glance, or
bounce a little. A stage accent (brackets, tildes, stars, ...) wraps the
face so later forms look a bit more dressed up. Everything is plain
Unicode (or ASCII when --ascii is set). There are no pixel grids or images.
"""

from __future__ import annotations

from typing import Any

# How long the deck / terminal should wait between animation frames.
DEFAULT_INTERVAL_MS = 450

# Face moods the avatar payload uses. Protocol mood words (joyful, playful,
# content, ...) are mapped onto one of these.
FACE_MOODS = (
    "happy",
    "content",
    "hungry",
    "tired",
    "sleeping",
    "sad",
    "angry",
    "hurt",
    "fainted",
    "excited",
    "evolving",
)

# 2–4 frames each. Multi-line faces use "\n". Eyes blink, glance, or bounce.
_FACES: dict[str, list[str]] = {
    "happy": [
        "(^_^)",
        "(^o^)",
        "(^_^)",
        "(^v^)",
    ],
    "content": [
        "(-_-)",
        "(-.-)",
        "(-_-)",
        "(o_o)",
    ],
    "hungry": [
        "(o_o)\n  ...",
        "(o_O)\n  ...",
        "(O_o)\n  ...",
        "(o_o)\n  nom?",
    ],
    "tired": [
        "(-_- )",
        "( -_-)",
        "(-_-;)",
        "(-_- )",
    ],
    "sleeping": [
        "(-_-) zZ",
        "(_-_) zZz",
        "(-_-) zZ",
        "(u_u) zZz",
    ],
    "sad": [
        "(T_T)",
        "(;_;)",
        "(T_T)",
        "(._.)",
    ],
    "angry": [
        "(>_<)",
        "(>o<)",
        "(>_<)",
        "(;-;)",
    ],
    "hurt": [
        "(x_x)",
        "(+_+)",
        "(x_x)",
        "(>_<)",
    ],
    "fainted": [
        "(x_x)\n  ...",
        "(X_X)\n  ...",
        "(x_x)\n  ...",
        "(_ _)\n  ...",
    ],
    "excited": [
        "(*_*)",
        "(^o^)",
        "(*o*)",
        "(^_^)/",
    ],
    "evolving": [
        "(*_*)",
        "(o*o)",
        "(*o*)",
        "(+_+)",
    ],
}

# Plain ASCII variants used with --ascii (no curly punctuation).
_ASCII_FACES: dict[str, list[str]] = {
    "happy": ["(^_^)", "(^o^)", "(^_^)", "(^v^)"],
    "content": ["(-_-)", "(-.-)", "(-_-)", "(o_o)"],
    "hungry": ["(o_o)\n  ...", "(o_O)\n  ...", "(O_o)\n  ...", "(o_o)\n  nom?"],
    "tired": ["(-_- )", "( -_-)", "(-_-;)", "(-_- )"],
    "sleeping": ["(-_-) zZ", "(_-_) zZz", "(-_-) zZ", "(u_u) zZz"],
    "sad": ["(T_T)", "(;_;)", "(T_T)", "(._.)"],
    "angry": ["(>_<)", "(>o<)", "(>_<)", "(;-;)"],
    "hurt": ["(x_x)", "(+_+)", "(x_x)", "(>_<)"],
    "fainted": ["(x_x)\n  ...", "(X_X)\n  ...", "(x_x)\n  ...", "(_ _)\n  ..."],
    "excited": ["(*_*)", "(^o^)", "(*o*)", "(^_^)/"],
    "evolving": ["(*_*)", "(o*o)", "(*o*)", "(+_+)"],
}

# (left, right) wrappers per evolution stage.
_ACCENTS: dict[str, tuple[str, str]] = {
    "baby": ("", ""),  # no wrapper — quotes looked like part of the face
    "in-training": ("~", "~"),
    "rookie": ("[", "]"),
    "champion": ("{", "}"),
    "ultimate": ("<", ">"),
    "mega": ("«", "»"),
}

_ASCII_ACCENTS: dict[str, tuple[str, str]] = {
    "baby": ("", ""),  # no wrapper — quotes looked like part of the face
    "in-training": ("~", "~"),
    "rookie": ("[", "]"),
    "champion": ("{", "}"),
    "ultimate": ("<", ">"),
    "mega": ("*", "*"),
}

# Map protocol / request mood words onto a face mood.
_MOOD_ALIAS = {
    "happy": "happy",
    "joyful": "happy",
    "playful": "happy",
    "content": "content",
    "neutral": "content",
    "idle": "content",
    "hungry": "hungry",
    "tired": "tired",
    "sleeping": "sleeping",
    "asleep": "sleeping",
    "sad": "sad",
    "grumpy": "angry",
    "angry": "angry",
    "hurt": "hurt",
    "fainted": "fainted",
    "faint": "fainted",
    "excited": "excited",
    "evolving": "evolving",
}


def normalize_mood(word: str) -> str:
    """Return a FACE_MOODS value, or raise ValueError."""
    key = word.strip().lower().replace(" ", "")
    mood = _MOOD_ALIAS.get(key)
    if mood is None:
        raise ValueError(f"unknown mood {word!r}")
    return mood


def face_mood(creature: Any, override: str | None = None) -> str:
    """Pick a face mood from the live pet (or an explicit override)."""
    if override is not None:
        return normalize_mood(override)
    if getattr(creature, "fainted", False):
        return "fainted"
    if getattr(creature, "asleep", False):
        return "sleeping"
    stats = creature.stats
    if stats.hp < max(1, stats.max_hp * 0.35) and stats.hp > 0:
        return "hurt"
    status = creature.evolution_status() if hasattr(creature, "evolution_status") else {}
    if status.get("ready") or float(status.get("progress") or 0) >= 0.90:
        return "excited"
    hunger = float(stats.hunger)
    energy = float(stats.energy)
    mood = float(stats.mood)
    if hunger <= 20:
        return "hungry"
    if energy <= 20:
        return "tired"
    if mood <= 28:
        return "angry" if mood <= 18 else "sad"
    if mood >= 70:
        return "happy"
    return "content"


def accent_for(stage: str, ascii_mode: bool = False) -> tuple[str, str]:
    table = _ASCII_ACCENTS if ascii_mode else _ACCENTS
    return table.get(stage, table.get("baby", ("", "")))


def apply_accent(text: str, stage: str, ascii_mode: bool = False) -> str:
    """Wrap the first line of a face with the stage accent."""
    left, right = accent_for(stage, ascii_mode=ascii_mode)
    lines = text.split("\n")
    lines[0] = f"{left}{lines[0]}{right}"
    return "\n".join(lines)


def face_frames(mood: str, stage: str = "baby", ascii_mode: bool = False) -> list[str]:
    """All accented frames for this mood and stage."""
    mood = normalize_mood(mood)
    table = _ASCII_FACES if ascii_mode else _FACES
    raw = table.get(mood) or table["content"]
    return [apply_accent(frame, stage, ascii_mode=ascii_mode) for frame in raw]


def face_text(
    mood: str,
    stage: str = "baby",
    frame: int = 0,
    ascii_mode: bool = False,
) -> str:
    frames = face_frames(mood, stage, ascii_mode=ascii_mode)
    if not frames:
        return ""
    index = int(frame) % len(frames)
    return frames[index]


def avatar_payload(
    creature: Any,
    frame: int = 0,
    mood: str | None = None,
    ascii_mode: bool = False,
    interval_ms: int = DEFAULT_INTERVAL_MS,
) -> dict[str, Any]:
    """pet.avatar.ok body: style 'face' plus every animation frame."""
    word = face_mood(creature, override=mood)
    stage = creature.species.stage
    frames = face_frames(word, stage, ascii_mode=ascii_mode)
    count = max(1, len(frames))
    index = int(frame) % count
    return {
        "style": "face",
        "mood": word,
        "frame": index,
        "frames": frames,
        "interval_ms": int(interval_ms),
    }


def pet_avatar(
    creature: Any,
    frame: int = 0,
    ascii_mode: bool = False,
) -> dict[str, Any]:
    """Compact avatar object attached to the pet payload."""
    word = face_mood(creature)
    stage = creature.species.stage
    frames = face_frames(word, stage, ascii_mode=ascii_mode)
    count = max(1, len(frames))
    index = int(frame) % count
    return {
        "style": "face",
        "mood": word,
        "frame": index,
        "text": frames[index],
    }


def format_face_sheet(ascii_mode: bool = False) -> str:
    """A readable catalog of every mood and frame, for --show-faces."""
    from pet.evolution import SPECIES, forms_in_evolution_order

    lines: list[str] = ["Cyberdeck Pet — text faces", ""]
    # Show each mood on the starter, then one accent sample per stage.
    lines.append("Moods (baby accent):")
    for mood in FACE_MOODS:
        frames = face_frames(mood, "baby", ascii_mode=ascii_mode)
        preview = "   |   ".join(f.replace("\n", " / ") for f in frames)
        lines.append(f"  {mood:10} {preview}")
    lines.append("")
    lines.append("Stage accents (content face, frame 0):")
    seen_stages: set[str] = set()
    for species_id in forms_in_evolution_order():
        species = SPECIES[species_id]
        if species.stage in seen_stages:
            continue
        seen_stages.add(species.stage)
        text = face_text("content", species.stage, frame=0, ascii_mode=ascii_mode)
        lines.append(f"  {species.stage:13} {text.replace(chr(10), ' / ')}")
    lines.append("")
    return "\n".join(lines)


def write_face_sheet(directory, ascii_mode: bool = False) -> "Path":
    """Write docs/faces/sheet.txt and return the path."""
    from pathlib import Path

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    dest = path / "sheet.txt"
    dest.write_text(format_face_sheet(ascii_mode=ascii_mode), encoding="utf-8")
    return dest


def boxed_banner(title: str, *body: str, width: int = 46) -> str:
    """A simple boxed popup for evolve_soon / evolving / evolve_choice."""
    inner = width - 2
    lines = [title] + [line for line in body if line is not None]
    out = ["+" + "-" * inner + "+"]
    for line in lines:
        text = line[:inner]
        out.append("|" + text.ljust(inner) + "|")
    out.append("+" + "-" * inner + "+")
    return "\n".join(out)


def evolution_banner(kind: str, detail: dict[str, Any], name: str = "PULSE") -> str:
    """Boxed terminal popup for an evolution event."""
    if kind == "evolve_soon":
        from_name = detail.get("from") or name
        cands = detail.get("to_candidates") or []
        dest = ", ".join(str(c) for c in cands) or str(detail.get("next_stage") or "?")
        eta = detail.get("eta_hours")
        eta_line = ""
        if eta is not None:
            try:
                hours = float(eta)
                eta_line = "ready now" if hours <= 0 else f"about {hours:g} hours to go"
            except (TypeError, ValueError):
                eta_line = ""
        return boxed_banner(
            f" {name} is close to evolving!",
            f" {from_name} -> {dest}",
            f" {eta_line}" if eta_line else "",
        )
    if kind == "evolve_choice":
        from_name = detail.get("from") or name
        stage = detail.get("next_stage") or "next stage"
        rows = [
            f" {name} is ready to evolve!",
            f" {from_name} -> {stage}",
            " send: pet.evolve {species}",
        ]
        for choice in detail.get("choices") or []:
            flag = "available" if choice.get("available") else "locked"
            hint = choice.get("hint") or ""
            species = choice.get("species") or choice.get("species_id") or "?"
            rows.append(f"  * {species} [{flag}]  {hint}")
        return boxed_banner(*rows)
    if kind == "evolving":
        return boxed_banner(
            f" {name} is evolving!",
            f" {detail.get('from')} -> {detail.get('to')}",
        )
    if kind == "evolved":
        return boxed_banner(
            f" {name} evolved!",
            f" {detail.get('from')} -> {detail.get('to')}",
        )
    return boxed_banner(f" {name}: {kind}")
