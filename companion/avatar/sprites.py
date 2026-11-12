"""Original 16x16 sprites for each creature form, stored as text grids.

A sprite is just a list of 16 strings of length 16. Each character is a key
in PALETTE, which maps it to an RGB triple (or to None for a transparent
pixel). That keeps the art editable in a text editor and keeps the rendered
pixels a plain 2D array — the same array a future SPI, e-ink, or OLED
driver can blit without caring about terminals.

Each form has two idle frames (a tiny antenna/tail/sparkle wiggle; the face
stays put) plus mood overlays (happy, hungry/tired, sad) that paint over
the face. Neutral is the default face overlay.

Form ids match pet.evolution.SPECIES (chirplet, wavling, aethling, staticore).
"""

from __future__ import annotations

# One side of the square. Renderers and tests should use this constant.
SPRITE_SIZE = 16

# A compact shared palette. Values are (red, green, blue) in 0–255, or None
# for "no pixel" (the terminal background, or a clear pixel on a display).
PALETTE: dict[str, tuple[int, int, int] | None] = {
    ".": None,  # transparent
    "k": (18, 16, 24),  # ink / outline
    "d": (42, 36, 54),  # deep shadow
    "w": (250, 248, 240),  # shine
    "e": (255, 255, 255),  # eye white
    "p": (20, 16, 28),  # pupil
    "c": (255, 132, 150),  # blush
    "t": (120, 170, 220),  # tear / tired lid
    # Chirplet — warm spark
    "y": (255, 212, 64),
    "o": (236, 148, 36),
    "n": (255, 240, 170),
    "a": (255, 255, 210),
    # Wavling — teal wave
    "s": (16, 110, 120),
    "m": (36, 186, 184),
    "l": (140, 236, 226),
    "b": (80, 255, 210),
    # Aethling — gold and ice
    "g": (214, 186, 86),
    "f": (255, 232, 160),
    "i": (186, 220, 255),
    "h": (255, 248, 230),
    # Staticore — violet static
    "u": (150, 70, 210),
    "v": (70, 28, 120),
    "z": (255, 80, 200),
    "r": (255, 70, 80),
}

# Canonical mood names. "tired" is an alias of "hungry".
MOODS: tuple[str, ...] = ("neutral", "happy", "hungry", "sad")
MOOD_ALIASES: dict[str, str] = {
    "neutral": "neutral",
    "idle": "neutral",
    "happy": "happy",
    "sad": "sad",
    "hungry": "hungry",
    "tired": "hungry",
    "hungry/tired": "hungry",
    "hungrytired": "hungry",
}

# ---------------------------------------------------------------------------
# Chirplet (baby): a thumbnail spark with a stubby antenna.
# Face sits on rows 8 (eyes) and 10 (mouth).
# ---------------------------------------------------------------------------
_CHIRPLET_F0 = (
    "................",
    "........n.......",
    ".......kak......",
    "........a.......",
    "......koook.....",
    "....koyyyyok....",
    "...koyyyyyyok...",
    "...kyyyyyyyyk...",
    "..koyyyyyyyyok..",
    "..koyyyyyyyyok..",
    "...koyyyyyyok...",
    "....koyyyyok....",
    ".....kykyyk.....",
    "......k...k.....",
    "................",
    "................",
)
_CHIRPLET_F1 = (
    "................",
    ".......n........",
    "......kak.......",
    ".......an.......",
    "......koook.....",
    "....koyyyyok....",
    "...koyyyyyyok...",
    "...kyyyyyyyyk...",
    "..koyyyyyyyyok..",
    "..koyyyyyyyyok..",
    "...koyyyyyyok...",
    "....koyyyyok....",
    ".....kykyyk.....",
    ".......k.k......",
    "................",
    "................",
)
_CHIRPLET_FACES = {
    "neutral": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        ".....ep.ep......",
        "................",
        ".......kk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "happy": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "....ce...ec.....",
        ".....k...k......",
        "......k.k.......",
        ".......k........",
        "................",
        "................",
        "................",
        "................",
    ),
    "hungry": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "......t...t.....",
        ".....k...k......",
        "................",
        "......kwk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "sad": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "......k...k.....",
        ".....ep.ept.....",
        ".......t........",
        ".......k........",
        "......k.k.......",
        "................",
        "................",
        "................",
        "................",
    ),
}

# ---------------------------------------------------------------------------
# Wavling (in-training): the spark grew a looping waveform tail.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_WAVLING_F0 = (
    "................",
    ".....kkkkk......",
    "...kkmlllmkk....",
    "..kmslllllsmk...",
    "..kmslwlllsmk...",
    "..kmsllllllsmk..",
    "..kmsllllllsmk..",
    "..kmsllllllsmk..",
    "...kmsllllsmk.b.",
    "....kkmsmkk.b...",
    "...k..kmk..b.b..",
    "..........b.....",
    ".........b.b....",
    "..........b.....",
    "................",
    "................",
)
_WAVLING_F1 = (
    "................",
    ".....kkkkk......",
    "...kkmlllmkk....",
    "..kmslllllsmk...",
    "..kmslllwllmk...",
    "..kmsllllllsmk..",
    "..kmsllllllsmk..",
    "..kmsllllllsmk..",
    "...kmsllllsmkb..",
    "....kkmsmkkb.b..",
    "...k..kmk.b.....",
    ".........b.b....",
    "..........b.....",
    ".........b.b....",
    "................",
    "................",
)
_WAVLING_FACES = {
    "neutral": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        ".....ep..ep.....",
        "................",
        ".......kk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "happy": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "....ce....ec....",
        ".....k....k.....",
        "......k..k......",
        ".......kk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "hungry": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "......t..t......",
        ".....k....k.....",
        "................",
        ".......kwk......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "sad": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "......k..k......",
        ".....ep..ept....",
        "..........t.....",
        ".......k........",
        "......k..k......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
}

# ---------------------------------------------------------------------------
# Aethling (rookie, cared-for): a clear-signal form with a pale ice halo.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_AETHLING_F0 = (
    ".......ii.......",
    "......ikiki.....",
    ".....kfgfgfk....",
    "....kffhhhffk...",
    "....kfhwhhhfk...",
    "...kkfhhhhhffk..",
    "...kffhhhhhffk..",
    "...kffhhhhhffk..",
    "....kffhhhffk.i.",
    "....kkffffkk.i..",
    ".....kfggfk.i...",
    "......kg.gk.....",
    "......k...k.....",
    ".......k.k......",
    "........k.......",
    "................",
)
_AETHLING_F1 = (
    "......i..i......",
    ".......ikik.....",
    ".....kfgfgfk....",
    "....kffhhhffk...",
    "....kfhhwhhfk...",
    "...kkfhhhhhffk..",
    "...kffhhhhhffk..",
    "...kffhhhhhffk..",
    "....kffhhhffki..",
    "....kkffffkki...",
    ".....kfggfk.....",
    "......kg.gk.i...",
    "......k...k.....",
    ".......k.k......",
    ".......k........",
    "................",
)
_AETHLING_FACES = {
    "neutral": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        ".....ep..ep.....",
        "................",
        ".......kk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "happy": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "....ce....ec....",
        ".....k....k.....",
        "......k..k......",
        ".......kk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "hungry": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "......t..t......",
        ".....k....k.....",
        "................",
        ".......kwk......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "sad": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "......k..k......",
        ".....ep..ept....",
        "..........t.....",
        ".......k........",
        "......k..k......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
}

# ---------------------------------------------------------------------------
# Staticore (rookie, hardened): a jagged, crackling form.
# Face sits on rows 5 (eyes) and 7 (mouth).
# ---------------------------------------------------------------------------
_STATICORE_F0 = (
    ".z....k.......z.",
    "...kkvvvvvkk....",
    "..kvuuvvuuvvk...",
    ".kkvuuuuuuuvkk..",
    ".kvuuwuuuuuuvk.z",
    ".kvuuuuuuuuvkk..",
    ".kvuuuuuuuuvvk..",
    ".kvuuuuuuuuvk...",
    "..kvuuuuuuuvk.z.",
    "..kkvuvvuvkk....",
    ".r..kkkkk...z...",
    "..k...z...k..r..",
    ".k.........k....",
    "..k.......k.....",
    "................",
    "................",
)
_STATICORE_F1 = (
    "z......k.....z..",
    "...kkvvvvvkk..r.",
    "..kvvuuvuuvvk...",
    ".kkvuuuuuuuvkk..",
    "zkvuuuuwuuuuvk..",
    ".kvuuuuuuuuvkk..",
    ".kvuuuuuuuuvvk..",
    ".kvuuuuuuuuvk.z.",
    "..kvuuuuuuuvk...",
    "..kkvuvvuvkk.z..",
    "....kkkkk....r..",
    "r.k.....z.k.....",
    ".k.........k....",
    "..k.......k.....",
    "................",
    "................",
)
_STATICORE_FACES = {
    "neutral": (
        "................",
        "................",
        "................",
        "................",
        "................",
        ".....ep..ep.....",
        "................",
        "......kkk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "happy": (
        "................",
        "................",
        "................",
        "................",
        "................",
        "....ze....ez....",
        ".....k....k.....",
        "......k..k......",
        ".......kk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "hungry": (
        "................",
        "................",
        "................",
        "................",
        "......t..t......",
        ".....k....k.....",
        "................",
        "......kwk.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
    "sad": (
        "................",
        "................",
        "................",
        "................",
        "......k..k......",
        ".....ep..epr....",
        "..........r.....",
        ".......k........",
        "......k..k......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ),
}


def _validate_grid(label: str, rows: tuple[str, ...] | list[str]) -> None:
    """Raise ValueError if a grid is not 16x16 or uses an unknown character."""
    if len(rows) != SPRITE_SIZE:
        raise ValueError(f"{label}: expected {SPRITE_SIZE} rows, got {len(rows)}")
    for index, row in enumerate(rows):
        if len(row) != SPRITE_SIZE:
            raise ValueError(
                f"{label} row {index}: expected {SPRITE_SIZE} cols, got {len(row)}"
            )
        unknown = set(row) - set(PALETTE)
        if unknown:
            raise ValueError(f"{label} row {index}: unknown palette chars {sorted(unknown)}")


def _validate_palette() -> None:
    for key, value in PALETTE.items():
        if len(key) != 1:
            raise ValueError(f"Palette keys must be a single character, got {key!r}")
        if value is None:
            continue
        if not (isinstance(value, tuple) and len(value) == 3):
            raise ValueError(f"Palette {key!r} must be an (r, g, b) tuple or None")
        if not all(isinstance(channel, int) and 0 <= channel <= 255 for channel in value):
            raise ValueError(f"Palette {key!r} has a channel outside 0–255: {value}")


# Form table keyed by the same ids used in pet.evolution.SPECIES.
FORMS: dict[str, dict] = {
    "chirplet": {
        "id": "chirplet",
        "name": "Chirplet",
        "frames": (_CHIRPLET_F0, _CHIRPLET_F1),
        "faces": _CHIRPLET_FACES,
    },
    "wavling": {
        "id": "wavling",
        "name": "Wavling",
        "frames": (_WAVLING_F0, _WAVLING_F1),
        "faces": _WAVLING_FACES,
    },
    "aethling": {
        "id": "aethling",
        "name": "Aethling",
        "frames": (_AETHLING_F0, _AETHLING_F1),
        "faces": _AETHLING_FACES,
    },
    "staticore": {
        "id": "staticore",
        "name": "Staticore",
        "frames": (_STATICORE_F0, _STATICORE_F1),
        "faces": _STATICORE_FACES,
    },
}


def _validate_all() -> None:
    _validate_palette()
    for form_id, form in FORMS.items():
        for index, frame in enumerate(form["frames"]):
            _validate_grid(f"{form_id} frame {index}", frame)
        for mood, face in form["faces"].items():
            _validate_grid(f"{form_id} face {mood}", face)
        missing = set(MOODS) - set(form["faces"])
        if missing:
            raise ValueError(f"{form_id} is missing moods {sorted(missing)}")


_validate_all()


def resolve_mood(mood: str) -> str:
    """Map a casual mood name ('tired', 'Hungry/tired') to a canonical key."""
    key = mood.strip().lower().replace(" ", "")
    if key not in MOOD_ALIASES:
        raise KeyError(f"Unknown mood {mood!r}. Try one of {MOODS}.")
    return MOOD_ALIASES[key]


def _aliases_from_evolution() -> dict[str, str]:
    """id and display-name aliases, hooked to pet.evolution.SPECIES when importable."""
    aliases = {}
    for form_id, form in FORMS.items():
        aliases[form_id.lower()] = form_id
        aliases[form["name"].lower()] = form_id
    try:
        from pet.evolution import SPECIES
    except ImportError:
        return aliases
    for species_id, species in SPECIES.items():
        aliases[species_id.lower()] = species_id
        aliases[species.name.lower()] = species_id
    return aliases


def lookup_form(name: str) -> dict:
    """Find a form by species id or display name ('chirplet' or 'Chirplet')."""
    aliases = _aliases_from_evolution()
    key = name.strip().lower()
    if key not in aliases:
        known = ", ".join(sorted(FORMS))
        raise KeyError(f"Unknown form {name!r}. Known forms: {known}.")
    form_id = aliases[key]
    if form_id not in FORMS:
        raise KeyError(f"Species {name!r} resolved to {form_id!r}, which has no sprite.")
    return FORMS[form_id]


def list_forms() -> list[str]:
    """Species ids in a stable, evolution-friendly order."""
    return ["chirplet", "wavling", "aethling", "staticore"]


def composite_grid(base: tuple[str, ...] | list[str], overlay: tuple[str, ...] | list[str]) -> list[str]:
    """Paint overlay onto base; '.' in the overlay leaves the base pixel alone."""
    rows = []
    for base_row, over_row in zip(base, overlay, strict=True):
        merged = [
            over_ch if over_ch != "." else base_ch
            for base_ch, over_ch in zip(base_row, over_row, strict=True)
        ]
        rows.append("".join(merged))
    return rows


def sprite_grid(form: str, frame: int = 0, mood: str = "neutral") -> list[str]:
    """Return the 16 character-rows for this form, idle frame, and mood."""
    record = lookup_form(form)
    frames = record["frames"]
    index = frame % len(frames)
    mood_key = resolve_mood(mood)
    return composite_grid(frames[index], record["faces"][mood_key])


def grid_to_pixels(rows: list[str] | tuple[str, ...]) -> list[list[tuple[int, int, int] | None]]:
    """Turn a character grid into a plain 2D array of RGB tuples (or None).

    This is the display-agnostic format: any renderer, including a future
    hardware driver, should consume this and not the text grid.
    """
    return [[PALETTE[ch] for ch in row] for row in rows]


def sprite_pixels(
    form: str, frame: int = 0, mood: str = "neutral"
) -> list[list[tuple[int, int, int] | None]]:
    """Convenience: lookup + composite + convert to a 2D pixel array."""
    return grid_to_pixels(sprite_grid(form, frame=frame, mood=mood))


def mood_from_meters(hunger: float, energy: float, mood: float) -> str:
    """Pick a face from the three caretaker meters (all 0–100)."""
    if mood < 35:
        return "sad"
    if hunger < 40 or energy < 35:
        return "hungry"
    if mood >= 70:
        return "happy"
    return "neutral"


def mood_for_creature(creature: object) -> str:
    """Read hunger/energy/mood off a Creature (or any object with .stats)."""
    stats = getattr(creature, "stats", creature)
    return mood_from_meters(stats.hunger, stats.energy, stats.mood)
