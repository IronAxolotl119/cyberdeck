"""Original 16x16 sprites for each creature form, stored as text grids.

A sprite is just a list of 16 strings of length 16. Each character is a key
in PALETTE, which maps it to an RGB triple (or to None for a transparent
pixel). That keeps the art editable in a text editor and keeps the rendered
pixels a plain 2D array — the same array a future SPI, e-ink, or OLED
driver can blit without caring about terminals.

Each form has two idle frames (a tiny antenna/tail/sparkle wiggle; the face
stays put) plus mood overlays (happy, hungry/tired, sad) that paint over
the face. Neutral is the default face overlay.

Form ids match pet.evolution.SPECIES (every form from Chirplet through the mega stage).
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
    # Luminode / Aurorion / Aethernova — pale gold and sky
    "q": (248, 220, 120),
    "x": (120, 200, 255),
    # Hexwave / Resonarch / Signumega — indigo
    "j": (60, 90, 200),
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



def _blank_rows() -> list[str]:
    return ["." * SPRITE_SIZE for _ in range(SPRITE_SIZE)]


def _face_overlays(
    eye_row: int,
    mouth_row: int,
    left: int,
    right: int,
    *,
    blush: str = "c",
    tear: str = "t",
) -> dict[str, tuple[str, ...]]:
    """Build the four mood overlays for a face whose eyes sit at (eye_row, left/right)."""

    def stamp(pairs: list[tuple[int, int, str]]) -> tuple[str, ...]:
        rows = [list(row) for row in _blank_rows()]
        for r, c, ch in pairs:
            if 0 <= r < SPRITE_SIZE and 0 <= c < SPRITE_SIZE:
                rows[r][c] = ch
        return tuple("".join(row) for row in rows)

    mid = (left + right) // 2
    return {
        "neutral": stamp(
            [
                (eye_row, left, "e"),
                (eye_row, left + 1, "p"),
                (eye_row, right, "e"),
                (eye_row, right + 1, "p"),
                (mouth_row, mid, "k"),
                (mouth_row, mid + 1, "k"),
            ]
        ),
        "happy": stamp(
            [
                (eye_row, left - 1, blush),
                (eye_row, left, "e"),
                (eye_row, right + 1, "e"),
                (eye_row, right + 2, blush),
                (eye_row + 1, left, "k"),
                (eye_row + 1, right + 1, "k"),
                (mouth_row, mid - 1, "k"),
                (mouth_row, mid + 2, "k"),
                (mouth_row + 1, mid, "k"),
                (mouth_row + 1, mid + 1, "k"),
            ]
        ),
        "hungry": stamp(
            [
                (eye_row - 1, left, "t"),
                (eye_row - 1, right + 1, "t"),
                (eye_row, left, "k"),
                (eye_row, right + 1, "k"),
                (mouth_row, mid - 1, "k"),
                (mouth_row, mid, "w"),
                (mouth_row, mid + 1, "k"),
            ]
        ),
        "sad": stamp(
            [
                (eye_row - 1, left, "k"),
                (eye_row - 1, right + 1, "k"),
                (eye_row, left, "e"),
                (eye_row, left + 1, "p"),
                (eye_row, right, "e"),
                (eye_row, right + 1, "p"),
                (eye_row, right + 2, tear),
                (eye_row + 1, right + 1, tear),
                (mouth_row, mid, "k"),
                (mouth_row + 1, mid - 1, "k"),
                (mouth_row + 1, mid + 1, "k"),
            ]
        ),
    }


# ---------------------------------------------------------------------------
# Luminode (champion, care): a bright relay node with twin sky orbs.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_LUMINODE_F0 = (
    "......x..x......",
    ".....kq..qk.....",
    "....kqhffhqk....",
    "...kqfhhhhfqk...",
    "...kqhwhhhhqk...",
    "..kkqhhhhhhfqk..",
    "..kqfhhhhhhfqk..",
    "..kqfhhhhhhfqk..",
    "...kqfhhhhfqk.x.",
    "...kkqfffqkk.x..",
    "....kqgqgk.x....",
    ".....kg..gk.....",
    ".....k....k.....",
    "......k..k......",
    ".......kk.......",
    "................",
)
_LUMINODE_F1 = (
    ".....x....x.....",
    "......kqqk......",
    "....kqhffhqk....",
    "...kqfhhhhfqk...",
    "...kqhhwhhhqk...",
    "..kkqhhhhhhfqk..",
    "..kqfhhhhhhfqk..",
    "..kqfhhhhhhfqk..",
    "..xkqfhhhhfqk...",
    "...kkqfffqkk....",
    "....xkqgqgk.....",
    ".....kg..gk.....",
    ".....k....k.....",
    "......k..k......",
    "......k..k......",
    "................",
)
_LUMINODE_FACES = _face_overlays(6, 8, 5, 9, blush="c")

# ---------------------------------------------------------------------------
# Hexwave (champion, discovery): a six-phase scanner with an indigo ring.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_HEXWAVE_F0 = (
    ".......j........",
    ".....kkjkk......",
    "...kkmllllmk....",
    "..kjmllllllmk...",
    "..kmljxllllmk...",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "...kmllllllmk.b.",
    "....kkmlmkk.j...",
    "...j..kmk..b....",
    ".........b.j....",
    "........j.......",
    ".......b.b......",
    "................",
    "................",
)
_HEXWAVE_F1 = (
    "........j.......",
    ".....kkjkk......",
    "...kkmllllmk....",
    "..kjmlllxllmk...",
    "..kmljlllllmk...",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "...kmllllllmkb..",
    "....kkmlmkkj....",
    "......kmk..b.j..",
    ".........j.b....",
    "........b.......",
    ".......j.j......",
    "................",
    "................",
)
_HEXWAVE_FACES = _face_overlays(6, 8, 5, 9, blush="b")

# ---------------------------------------------------------------------------
# Voltshard (champion, combat): a jagged voltage shard with leaping sparks.
# Face sits on rows 5 (eyes) and 7 (mouth).
# ---------------------------------------------------------------------------
_VOLTSHARD_F0 = (
    "y.r...k......r.y",
    "...kkvvvvvkk....",
    "..kvuyyvuuyvk...",
    ".kkvuuuuuuuvkk..",
    ".kvuuwuuuuuuvk.r",
    ".kvuuuuuuuuvkk..",
    ".kvuuuuuuuuvvk..",
    ".kvuuuuuuuuvk...",
    "..kvuuuuuuuvk.y.",
    "..kkvuvvuvkk....",
    ".r..kkykk...z...",
    "..k...y...k..r..",
    "yk.........k..y.",
    "..k..r....k.....",
    "....y........r..",
    "................",
)
_VOLTSHARD_F1 = (
    "r.y....k.....y.r",
    "...kkvvvvvkk..y.",
    "..kvvuuyuuvvk...",
    ".kkvuuuuuuuvkk..",
    "rkvuuuuwuuuuvk..",
    ".kvuuuuuuuuvkk..",
    ".kvuuuuuuuuvvk..",
    ".kvuuuuuuuuvk.y.",
    "..kvuuuuuuuvk...",
    "..kkvuvvuvkk.r..",
    "....kkykk....z..",
    "y.k.....y.k....r",
    ".k.........k....",
    "r.k.......k..y..",
    "......r....y....",
    "................",
)
_VOLTSHARD_FACES = _face_overlays(5, 7, 5, 9, blush="z", tear="r")

# ---------------------------------------------------------------------------
# Jammeron (champion, discovery on the hard path): a dish-backed jammer.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_JAMMERON_F0 = (
    "......dddd......",
    "....ddvvvvdd....",
    "...dvuuuuuuvd...",
    "..dkvuuuuuuvkd..",
    "..kvuuuwuuuuvk..",
    ".dkvuuuuuuuuvkd.",
    ".dkvuuuuuuuuvkd.",
    "..kvuuuuuuuuvk..",
    "..dkvuuuuuuvkd..",
    "...dkvvvvvkd....",
    "....dkkkkkd.....",
    "....k.....k.....",
    "...k.......k....",
    "..z.........z...",
    "................",
    "................",
)
_JAMMERON_F1 = (
    ".....d.dd.d.....",
    "....ddvvvvdd....",
    "...dvuuuuuuvd...",
    "..dkvuuuuuuvkd..",
    "..kvuuuuwuuuvk..",
    ".dkvuuuuuuuuvkd.",
    ".dkvuuuuuuuuvkd.",
    "..kvuuuuuuuuvk..",
    "..dkvuuuuuuvkd..",
    "...dkvvvvvkd....",
    "....dkkkkkd.....",
    "....k.....k.....",
    "...k.......k....",
    "...z.......z....",
    "................",
    "................",
)
_JAMMERON_FACES = _face_overlays(6, 8, 5, 9, blush="z", tear="r")

# ---------------------------------------------------------------------------
# Aurorion (ultimate, care): aurora wings around a gold core.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_AURORION_F0 = (
    "x.i........i.x..",
    ".x.iqq....qqi.x.",
    "..kqhffffhqk.x..",
    ".xkqfhhhhhfqk...",
    "..kqhwhhhhhqk.i.",
    ".kkqfhhhhhhfqk..",
    ".kqfhhhhhhhhqk..",
    ".kqfhhhhhhhhqk..",
    "..kqfhhhhhfqk.x.",
    "i.kkqffffqkk.i..",
    "..x.kqgqgk.x....",
    "....kg...gk.....",
    "x...k.....k...x.",
    ".....k...k......",
    "......k.k.......",
    "................",
)
_AURORION_F1 = (
    ".x.i......i.x...",
    "x.i.qq....qq.ix.",
    "..kqhffffhqk....",
    "..kqfhhhhhfqkx..",
    "i.kqhhwhhhhqk...",
    ".kkqfhhhhhhfqk..",
    ".kqfhhhhhhhhqk..",
    ".kqfhhhhhhhhqk..",
    "x.kqfhhhhhfqk...",
    "..kkqffffqkk.xi.",
    "....kqgqgk.x.i..",
    "....kg...gk.....",
    ".x..k.....k..x..",
    ".....k...k......",
    ".......k........",
    "................",
)
_AURORION_FACES = _face_overlays(6, 8, 5, 9, blush="c")

# ---------------------------------------------------------------------------
# Resonarch (ultimate, discovery): a crowned resonance form.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_RESONARCH_F0 = (
    "....j.b.j.b.....",
    ".....kjbjk......",
    "...kkmllllmkj...",
    "..kjmlllhllmk...",
    "..kmljwlhllmk...",
    ".jkmllllllllmkj.",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "...kmllllllmk.b.",
    "j...kkmlmkk..j..",
    "...b..kmk..b....",
    ".........j......",
    "....j...j...b...",
    "................",
    "................",
    "................",
)
_RESONARCH_F1 = (
    ".....b.j.b.j....",
    ".....kjbjk......",
    "...kkmllllmk....",
    "j.kjmllhlhlmk...",
    "..kmljlwlhlmk.j.",
    ".jkmllllllllmk..",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "...kmllllllmkb..",
    "....kkmlmkk.j.b.",
    "......kmk..b....",
    "....b.....j.....",
    "......j...j.....",
    "................",
    "................",
    "................",
)
_RESONARCH_FACES = _face_overlays(6, 8, 5, 9, blush="b")

# ---------------------------------------------------------------------------
# Stormbit (ultimate, combat): a storm-cloud body with leaping bolts.
# Face sits on rows 5 (eyes) and 7 (mouth).
# ---------------------------------------------------------------------------
_STORMBIT_F0 = (
    "y.....kk.....r.y",
    "...kkvvvvvvkk...",
    "..kvuuuuvuuuvk..",
    ".kkvuuuuuuuuvkk.",
    "rkvuuuwuuuuuuvk.",
    ".kvuuuuuuuuuvkk.",
    ".kvuuuuuuuuuuvk.",
    ".kkvuuuuuuuuvk..",
    "..kkvuvvuvvkk.y.",
    "y.r.kkkkkk...z..",
    "..y...r...y..r..",
    "r..k.....k...y..",
    "....y.r.y.......",
    "..r.......r.....",
    "y.............y.",
    "................",
)
_STORMBIT_F1 = (
    "r.y...kk....y..r",
    "...kkvvvvvvkk...",
    "..kvuuuvuuuuvk..",
    ".kkvuuuuuuuuvkk.",
    ".kvuuuuwuuuuuvkr",
    ".kvuuuuuuuuuvkk.",
    ".kvuuuuuuuuuuvk.",
    ".kkvuuuuuuuuvk..",
    "y.kkvuvvuvvkk...",
    "..z.kkkkkk.r..y.",
    "....y...r...y...",
    "y..k.....k..r...",
    "......y.r.y.....",
    "....r.......r...",
    ".r.............y",
    "................",
)
_STORMBIT_FACES = _face_overlays(5, 7, 5, 9, blush="z", tear="r")

# ---------------------------------------------------------------------------
# Blackout (ultimate, jammer line): an eclipse disc with a dark core.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_BLACKOUT_F0 = (
    "......dddd......",
    "....ddvvvvdd....",
    "...dvvuuuuuvd...",
    "..dkuuuuuuuukd..",
    "..kvuuuwuuuuvk..",
    ".dkvuuuuuuuuvkd.",
    ".dkuuuuuuuuuukd.",
    "..kvuuuuuuuuvk..",
    "..dkuuuuuuuukd..",
    "...dvvuuuuvvd...",
    "....ddvvvvdd....",
    "......dddd......",
    "....z......z....",
    "................",
    "................",
    "................",
)
_BLACKOUT_F1 = (
    "......dddd......",
    "....ddvvvvdd....",
    "...dvvuuuuuvd...",
    "..dkuuuuuuuukd..",
    "..kvuuuuwuuuvk..",
    ".dkvuuuuuuuuvkd.",
    ".dkuuuuuuuuuukd.",
    "..kvuuuuuuuuvk..",
    "..dkuuuuuuuukd..",
    "...dvvuuuuvvd...",
    "....ddvvvvdd....",
    "......dddd......",
    ".....z....z.....",
    "................",
    "................",
    "................",
)
_BLACKOUT_FACES = _face_overlays(6, 8, 5, 9, blush="z", tear="r")

# ---------------------------------------------------------------------------
# Aethernova (mega, care): a nova burst of clear aether.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_AETHERNOVA_F0 = (
    "x...i..q..i...x.",
    ".x.iqffffffqi.x.",
    "i.kqhhhhhhhhqk.i",
    ".kqfhhhhhhhhfqk.",
    "xkqhwhhhhhhhhqkx",
    ".kqfhhhhhhhhhfqk",
    "kqfhhhhhhhhhhfqk",
    "kqfhhhhhhhhhhfqk",
    "xkqfhhhhhhhhfqkx",
    ".kkqffffffqkk.i.",
    "i.x.kqgqqgk.x.i.",
    "x....kg..gk....x",
    "....xk....kx....",
    ".....k....k.....",
    "......k..k......",
    ".......kk.......",
)
_AETHERNOVA_F1 = (
    ".x..i.q.q.i..x..",
    "x.i.qffffffq.ix.",
    ".ikqhhhhhhhhqki.",
    ".kqfhhhhhhhhfqk.",
    ".kqhhwhhhhhhhqk.",
    "xkqfhhhhhhhhhfqx",
    "kqfhhhhhhhhhhfqk",
    "kqfhhhhhhhhhhfqk",
    ".kqfhhhhhhhhfqk.",
    "ixkkqffffffqkkxi",
    "...x.kqgqqgk.x..",
    ".x...kg..gk...x.",
    "....xk....kx....",
    ".....k....k.....",
    "......k..k......",
    "......k..k......",
)
_AETHERNOVA_FACES = _face_overlays(6, 8, 5, 9, blush="c")

# ---------------------------------------------------------------------------
# Signumega (mega, discovery): a towering sigil of the airwaves.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_SIGNUMEGA_F0 = (
    "......jbj.......",
    "....jkkjkkj.....",
    "...kjmllllmjk...",
    "..kjmlllhlllmk..",
    "..kmljwlhhllmk..",
    "j.kmllllllllmk.j",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "b.kmllllllllmk.b",
    "...kkmllllmkk...",
    "j...kkmlmkk...j.",
    ".....k.k.k......",
    "....j.....j.....",
    "...b.......b....",
    "................",
    "................",
)
_SIGNUMEGA_F1 = (
    ".....j.b.j......",
    "....jkkjkkj.....",
    "...kjmllllmjk...",
    "..kjmllhlhllmk..",
    "..kmljlwlhllmk..",
    ".jkmllllllllmkj.",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "..kmllllllllmk..",
    "b..kkmllllmkk.b.",
    "....kkmlmkk....j",
    "j....k.k.k......",
    ".....j...j......",
    "....b.....b.....",
    "................",
    "................",
)
_SIGNUMEGA_FACES = _face_overlays(6, 8, 5, 9, blush="b")

# ---------------------------------------------------------------------------
# Nullstorm (mega, combat): a total static storm.
# Face sits on rows 5 (eyes) and 7 (mouth).
# ---------------------------------------------------------------------------
_NULLSTORM_F0 = (
    "y.r.z.kk.z.r.y.r",
    "r..kkvvvvvvkk..y",
    "..kvuyyvuuyuuvk.",
    ".kkvuuuuuuuuuvkk",
    "ykvuuuwwuuuuuuvk",
    ".kvuuuuuuuuuuvkk",
    "rkvuuuuuuuuuuuvk",
    ".kkvuuuuuuuuuvk.",
    "y.kkvuvvuvvkk.r.",
    "..r.kkkykkk.z.y.",
    "y.r.y.r.y.r.y.r.",
    "r..k.......k..y.",
    "..y..r.y.r..y...",
    "r....y...y....r.",
    "y.r.........r.y.",
    "................",
)
_NULLSTORM_F1 = (
    "r.y.z..kk..z.y.r",
    "y..kkvvvvvvkk..r",
    "..kvuuyvuuyuvvk.",
    ".kkvuuuuuuuuuvkk",
    ".kvuuuuwuuuuuuvk",
    ".kvuuuuuuuuuuvkk",
    ".kvuuuuuuuuuuuvk",
    ".kkvuuuuuuuuuvk.",
    "r.kkvuvvuvvkk.y.",
    "..z.kkkykkk.r...",
    ".r.y.r.y.r.y.r.y",
    "y..k.......k..r.",
    "...y.r.y.r..y...",
    ".r....y...y....r",
    "..y.r.........y.",
    "................",
)
_NULLSTORM_FACES = _face_overlays(5, 7, 5, 9, blush="z", tear="r")

# ---------------------------------------------------------------------------
# Voidstatic (mega, jammer line): a hole punched through the noise.
# Face sits on rows 6 (eyes) and 8 (mouth).
# ---------------------------------------------------------------------------
_VOIDSTATIC_F0 = (
    "z.....dddd....z.",
    "....ddvvvvdd..r.",
    "...dv......vd...",
    "..dk.uuuuuu.kd..",
    "..k.uuuwuuuu.k..",
    ".dk.uuuuuuuu.kd.",
    ".dk.uuuuuuuu.kd.",
    "..k.uuuuuuuu.k..",
    "..dk.uuuuuu.kd..",
    "...dv......vd...",
    "r...ddvvvvdd....",
    "z.....dddd....z.",
    "....z......z....",
    "..r..........r..",
    "................",
    "................",
)
_VOIDSTATIC_F1 = (
    ".z....dddd...z..",
    "r...ddvvvvdd....",
    "...dv......vd...",
    "..dk.uuuuuu.kd..",
    "..k.uuuuwuuu.k..",
    ".dk.uuuuuuuu.kd.",
    ".dk.uuuuuuuu.kd.",
    "..k.uuuuuuuu.k..",
    "..dk.uuuuuu.kd..",
    "...dv......vd...",
    "....ddvvvvdd...r",
    "..z...dddd....z.",
    ".....z....z.....",
    "....r......r....",
    "................",
    "................",
)
_VOIDSTATIC_FACES = _face_overlays(6, 8, 5, 9, blush="z", tear="r")



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
    "luminode": {
        "id": "luminode",
        "name": "Luminode",
        "frames": (_LUMINODE_F0, _LUMINODE_F1),
        "faces": _LUMINODE_FACES,
    },
    "hexwave": {
        "id": "hexwave",
        "name": "Hexwave",
        "frames": (_HEXWAVE_F0, _HEXWAVE_F1),
        "faces": _HEXWAVE_FACES,
    },
    "voltshard": {
        "id": "voltshard",
        "name": "Voltshard",
        "frames": (_VOLTSHARD_F0, _VOLTSHARD_F1),
        "faces": _VOLTSHARD_FACES,
    },
    "jammeron": {
        "id": "jammeron",
        "name": "Jammeron",
        "frames": (_JAMMERON_F0, _JAMMERON_F1),
        "faces": _JAMMERON_FACES,
    },
    "aurorion": {
        "id": "aurorion",
        "name": "Aurorion",
        "frames": (_AURORION_F0, _AURORION_F1),
        "faces": _AURORION_FACES,
    },
    "resonarch": {
        "id": "resonarch",
        "name": "Resonarch",
        "frames": (_RESONARCH_F0, _RESONARCH_F1),
        "faces": _RESONARCH_FACES,
    },
    "stormbit": {
        "id": "stormbit",
        "name": "Stormbit",
        "frames": (_STORMBIT_F0, _STORMBIT_F1),
        "faces": _STORMBIT_FACES,
    },
    "blackout": {
        "id": "blackout",
        "name": "Blackout",
        "frames": (_BLACKOUT_F0, _BLACKOUT_F1),
        "faces": _BLACKOUT_FACES,
    },
    "aethernova": {
        "id": "aethernova",
        "name": "Aethernova",
        "frames": (_AETHERNOVA_F0, _AETHERNOVA_F1),
        "faces": _AETHERNOVA_FACES,
    },
    "signumega": {
        "id": "signumega",
        "name": "Signumega",
        "frames": (_SIGNUMEGA_F0, _SIGNUMEGA_F1),
        "faces": _SIGNUMEGA_FACES,
    },
    "nullstorm": {
        "id": "nullstorm",
        "name": "Nullstorm",
        "frames": (_NULLSTORM_F0, _NULLSTORM_F1),
        "faces": _NULLSTORM_FACES,
    },
    "voidstatic": {
        "id": "voidstatic",
        "name": "Voidstatic",
        "frames": (_VOIDSTATIC_F0, _VOIDSTATIC_F1),
        "faces": _VOIDSTATIC_FACES,
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
    try:
        from pet.evolution import forms_in_evolution_order

        ordered = [sid for sid in forms_in_evolution_order() if sid in FORMS]
        for form_id in FORMS:
            if form_id not in ordered:
                ordered.append(form_id)
        return ordered
    except ImportError:
        return list(FORMS)


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
