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
    "sick",
    "pouting",
    "party",
    "dreaming",
    "love",
    "nervous",
    "suspicious",
    "hot",
    "lonely",
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
    "sick": [
        "(x~x)",
        "(@_@)",
        "(x~x);",
        "(%_%)",
    ],
    "pouting": [
        "(>3<)",
        "(-3-)",
        "(>3<)",
        "( -3-)",
    ],
    "party": [
        "\\(^o^)/",
        "(^_^)/",
        "\\(^v^)/",
        "(^o^)",
    ],
    "dreaming": [
        "(-_-) zZ\n   o O ( ~ )",
        "(-_-) zZz\n  o O ( ~~ )",
        "(u_u) zZ\n   o O (~~~)",
        "(-_-) zZz\n  o O ( ~ )",
    ],
    "love": [
        "(^_^)<3",
        "(^o^)<3",
        "(*^_^*)",
        "(^v^)<3",
    ],
    "nervous": [
        "(o_o;)",
        "(;o_o)",
        "(O_O;)",
        "(o_o;)",
    ],
    "suspicious": [
        "(¬_¬)",
        "(¬_¬ )",
        "( ¬_¬)",
        "(¬.¬)",
    ],
    "hot": [
        "(>_<);;",
        "(@_@);;",
        "(-_-);;",
        "(>o<);;",
    ],
    "lonely": [
        "(._.)",
        "( ._.)",
        "(._. )",
        "(;_;)",
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
    "sick": ["(x~x)", "(@_@)", "(x~x);", "(%_%)"],
    "pouting": ["(>3<)", "(-3-)", "(>3<)", "( -3-)"],
    "party": ["\\(^o^)/", "(^_^)/", "\\(^v^)/", "(^o^)"],
    "dreaming": ["(-_-) zZ\n   o O ( ~ )", "(-_-) zZz\n  o O ( ~~ )", "(u_u) zZ\n   o O (~~~)", "(-_-) zZz\n  o O ( ~ )"],
    "love": ["(^_^)<3", "(^o^)<3", "(*^_^*)", "(^v^)<3"],
    "nervous": ["(o_o;)", "(;o_o)", "(O_O;)", "(o_o;)"],
    "suspicious": ["(-_-)", "(-_- )", "( -_-)", "(-.-)"],
    "hot": ["(>_<);;", "(@_@);;", "(-_-);;", "(>o<);;"],
    "lonely": ["(._.)", "( ._.)", "(._. )", "(;_;)"],
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
    "sick": "sick",
    "glitch": "sick",
    "corrupted": "sick",
    "pouting": "pouting",
    "pout": "pouting",
    "tantrum": "pouting",
    "jealous": "pouting",
    "party": "party",
    "birthday": "party",
    "dreaming": "dreaming",
    "dream": "dreaming",
    "love": "love",
    "bonded": "love",
    "nervous": "nervous",
    "anxious": "nervous",
    "suspicious": "suspicious",
    "hot": "hot",
    "lonely": "lonely",
    "bored": "content",
}

# Short action animations (idle life, quirks, reunions). Plain ASCII so they
# work in --ascii mode too. Stage accents and costumes are applied on top.
ANIMS: dict[str, list[str]] = {
    "look_around": ["(o_o )", "( o_o)", "(o_o )", "(o_o)"],
    "yawn": ["(-o-)", "(-O-)", "(-o-)", "(-_-)"],
    "doze": ["(-_-)", "(-_-) z", "(-.-) zZ", "(o_o)!"],
    "chase_bug": ["(o_o)  *", "(o_o) * ", " (o_o)*", "(^o^)/*"],
    "stretch": ["\\(-_-)/", "(-_-)", "\\(-o-)/", "(^_^)"],
    "hum": ["(-_-)~", "(-o-)~~", "(-_-)~", "(^_^)~"],
    "stare": ["(O_O)", "(O_O)", "(o_o)", "(O_O)"],
    "show_off": ["(^_^)b", "(^o^)b", "(*_*)b", "(^v^)b"],
    "count": ["(o_o) 1", "(o_o) 2", "(o_o) 3", "(^_^) !"],
    "talk_router": ["(o_o) ?", "(o_o) ..", "(-_-) ...", "(o_o) ?"],
    "night": ["(-_-) *", "(-_-)  .", "(o_o) *", "(-_-) ."],
    "waiting": ["(o_o)", "(o_o) ?", "( o_o)", "(o_o) ?"],
    "away": ["   ...", "(o_o)>>", "  >>", "   ..."],
    "return": ["(^o^)/", "(^_^)", "\\(^o^)/", "(^_^)"],
    "reunion": ["\\(^o^)/", "(T_T)<3", "\\(^v^)/", "(^_^)<3"],
    "treasure": ["(*o*)", "(*_*)!", "(*o*)!!", "(^o^)/"],
    "gift": ["(^_^)>[]", "(^o^)>[]", "(^_^)>[]", "(^v^)<3"],
    "pout": ["(>3<)", "(-3-)", "(>3<)", "( -3-)"],
    "hiccup:hiccup": ["(o_o) hic!", "(o_O)", "(O_o) hic!", "(-_-)"],
    "sneeze:tiny": ["(>_<)", "(>.<) pip!", "(-_-)", "(o_o)"],
    "sneeze:explosive": ["(o_O)", "(>O<) ACHOO!", "(@_@)", "(-_-)"],
    "sneeze:static": ["(>_<)", "(>_<) kshh~", "(=_=)", "(o_o)"],
    "sneeze:double": ["(>_<) ah-", "(>o<) CHOO!", "(>o<) CHOO!", "(-_-)"],
    "dance:robot shuffle": ["(o_o)>", "<(o_o)", "(o_o)>", "<(o_o)"],
    "dance:happy wiggle": ["~(^_^)~", "(^_^)", "~(^o^)~", "(^_^)"],
    "dance:antenna twirl": ["(^_^)/", "\\(^_^)", "(^o^)/", "\\(^o^)"],
    "dance:pixel shuffle": ["[^_^]", "]^_^[", "[^o^]", "]^o^["],
    "dance:moonwalk": ["(^_^)  <", " (^_^) <", "  (^_^)<", " (^_^) <"],
    "dance:spin cycle": ["(^_^)", "(  ^_)", "(  _ )", "(_^  )"],
    "dance:bounce": ["(^_^)", " (^o^)", "(^_^)", " (^o^)"],
    "dance:jazz hands": ["*(^_^)*", "(^o^)", "*(^v^)*", "(^o^)"],
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
    life = getattr(creature, "life", None)
    if life is not None and life.in_tantrum():
        return "pouting"
    if getattr(creature, "asleep", False):
        anim = life.current_anim() if life is not None else None
        if anim and anim.get("face") == "dreaming":
            return "dreaming"
        return "sleeping"
    if life is not None and life.sick:
        return "sick"
    anim = life.current_anim() if life is not None else None
    if anim and anim.get("face"):
        return normalize_mood(anim["face"])
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
    if life is not None:
        mood += life.mood_offset()
        if life.separated_at is not None and mood < 60:
            return "lonely"
    if mood <= 28:
        return "angry" if mood <= 18 else "sad"
    if mood >= 70:
        if life is not None and life.bond >= 70:
            return "love"
        if life is not None and life.costume() == "party":
            return "party"
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


def apply_costume(text: str, costume: str | None, ascii_mode: bool = False) -> str:
    """Put a holiday / party accessory in front of the face's first line."""
    from pet.calendar import costume_text

    accessory = costume_text(costume, ascii_mode=ascii_mode)
    if not accessory:
        return text
    lines = text.split("\n")
    lines[0] = f"{accessory} {lines[0]}"
    return "\n".join(lines)


def anim_frames(name: str, stage: str = "baby", ascii_mode: bool = False) -> list[str]:
    raw = ANIMS.get(name) or []
    return [apply_accent(frame, stage, ascii_mode=ascii_mode) for frame in raw]


def live_frames(
    creature: Any,
    mood: str | None = None,
    ascii_mode: bool = False,
) -> tuple[str, list[str], str | None, str | None]:
    """(face mood, frames, anim name, costume) for the live pet."""
    word = face_mood(creature, override=mood)
    stage = creature.species.stage
    life = getattr(creature, "life", None)
    anim_name = None
    frames: list[str] = []
    if mood is None and life is not None and not getattr(creature, "asleep", False):
        anim = life.current_anim()
        if anim and anim.get("name") in ANIMS and word not in {"pouting", "sick", "fainted"}:
            anim_name = anim["name"]
            frames = anim_frames(anim_name, stage, ascii_mode=ascii_mode)
    if not frames:
        frames = face_frames(word, stage, ascii_mode=ascii_mode)
    costume = life.costume() if life is not None else None
    if costume:
        frames = [apply_costume(f, costume, ascii_mode=ascii_mode) for f in frames]
    return word, frames, anim_name, costume


def avatar_payload(
    creature: Any,
    frame: int = 0,
    mood: str | None = None,
    ascii_mode: bool = False,
    interval_ms: int = DEFAULT_INTERVAL_MS,
) -> dict[str, Any]:
    """pet.avatar.ok body: style 'face' plus every animation frame."""
    word, frames, anim_name, costume = live_frames(creature, mood=mood, ascii_mode=ascii_mode)
    count = max(1, len(frames))
    index = int(frame) % count
    life = getattr(creature, "life", None)
    bubble = life.current_bubble() if life is not None else None
    return {
        "style": "face",
        "mood": word,
        "frame": index,
        "frames": frames,
        "interval_ms": int(interval_ms),
        "anim": anim_name,
        "costume": costume,
        "bubble": bubble["text"] if bubble else None,
    }


def pet_avatar(
    creature: Any,
    frame: int = 0,
    ascii_mode: bool = False,
) -> dict[str, Any]:
    """Compact avatar object attached to the pet payload."""
    word, frames, anim_name, costume = live_frames(creature, ascii_mode=ascii_mode)
    count = max(1, len(frames))
    index = int(frame) % count
    return {
        "style": "face",
        "mood": word,
        "frame": index,
        "text": frames[index],
        "anim": anim_name,
        "costume": costume,
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
    lines.append("Animations (idle life, quirks, dances):")
    for name in ANIMS:
        frames = anim_frames(name, "baby", ascii_mode=ascii_mode)
        lines.append(f"  {name:20} " + "   |   ".join(frames))
    lines.append("")
    lines.append("Costumes (holidays and birthdays, shown on the happy face):")
    from pet.calendar import COSTUMES

    for key in COSTUMES:
        sample = apply_costume(face_text("happy", "rookie", 0, ascii_mode=ascii_mode), key, ascii_mode=ascii_mode)
        lines.append(f"  {key:16} {sample}")
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


LIFE_TITLES = {
    "birthday": "it is {name}'s birthday!",
    "hatchday": "{name}'s monthly hatch-day!",
    "holiday": "{name} is celebrating",
    "rival_appears": "a rival appears!",
    "rival_result": "rival battle",
    "adventure_return": "{name} is back from an adventure",
    "egg_laid": "{name} laid an egg!",
    "egg_hatched": "the egg hatched!",
    "tantrum": "{name} is throwing a tantrum",
    "sick": "{name} is sick",
    "healed": "{name} feels better",
    "gift_back": "{name} has a present for you",
    "treasure": "treasure found!",
    "reunion": "reunited!",
    "diary": "{name}'s diary",
}


def wrap_text(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(str(text or ""), width=width) or [""]


def life_banner(kind: str, detail: dict[str, Any], name: str = "PULSE", width: int = 46) -> str:
    """Boxed popup for big life moments (birthday, rival, adventure, egg...)."""
    title = LIFE_TITLES.get(kind, kind).format(name=name)
    body: list[str] = []
    if kind == "rival_appears":
        body.append(f" {detail.get('rival')} (Lv{detail.get('level')})  {detail.get('taunt') or ''}")
        body.append(" send: pet.rival {action: fight | ignore}")
    if kind == "holiday" and detail.get("label"):
        title = f"{title} {detail['label']}!"
    text = detail.get("text") or detail.get("story") or detail.get("say") or ""
    for line in wrap_text(text, width - 4):
        body.append(f" {line}")
    if kind == "rival_result":
        body.insert(0, f" result: {detail.get('result')}  +{detail.get('xp')} XP")
    if kind == "gift_back" and detail.get("item"):
        body.append(f" item: {detail['item'].get('name')} ({detail['item'].get('kind')})")
    return boxed_banner(f" {title}", *body, width=width)


def speech_line(text: str | None, width: int = 60) -> str:
    """A one-line speech bubble for the terminal."""
    if not text:
        return ""
    text = str(text)
    if len(text) > width:
        text = text[: width - 3] + "..."
    return f"< {text} >"
