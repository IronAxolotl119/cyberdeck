"""text.py — turn any text into something a 16x2 character LCD can show.

In plain English: an HD44780 character LCD (the chip on every LCD1602)
only knows plain ASCII plus 8 home-made symbols ("custom glyphs") that we
draw ourselves, pixel by pixel. This file:

  * GLYPHS        the 8 custom symbols (heart, PULSE's face, Wi-Fi bars,
                  bell, lightning bolt, diamond, music note, apple), each a
                  5x8 pixel picture. Each sits in one of the chip's 8 CGRAM
                  slots; on screen slot N is the character chr(N).
  * to_lcd()      maps everything else to ASCII: ◆ ♥ ⚡ 🔔 become our glyphs,
                  é becomes e, — becomes -, → becomes >, unknown emoji vanish.
                  {heart} style tokens in your own blurbs work too.
  * wrap2()       word-wraps a message onto the 2 lines when it fits.
  * marquee()     slides long text through one line (a ticker).
  * fit()         pads / cuts a line to exactly 16 characters.

Everything here is pure (text in, text out), so it is unit-tested and
works the same on your computer and on the Pi.
"""

from __future__ import annotations

import textwrap
import unicodedata

# ---------------------------------------------------------------------------
# The 8 custom glyphs. Each row is 5 pixels wide ("#" = pixel on).
# Change a picture here and every screen shows the new one. Keep exactly
# 8 rows of 5 characters. Slot numbers 0-7 are the chip's CGRAM slots.
# ---------------------------------------------------------------------------
GLYPH_ART: dict[str, tuple[int, list[str]]] = {
    "heart": (0, [".....", ".#.#.", "#####", "#####", "#####", ".###.", "..#..", "....."]),
    "face": (1, [".###.", "#...#", "#.#.#", "#...#", "#.#.#", "#..##", ".###.", "....."]),
    "wifi": (2, [".....", "....#", "....#", "..#.#", "..#.#", "#.#.#", "#.#.#", "....."]),
    "bell": (3, ["..#..", ".###.", ".###.", ".###.", "#####", ".....", "..#..", "....."]),
    "bolt": (4, ["...#.", "..#..", ".#...", "#####", "...#.", "..#..", ".#...", "....."]),
    "diamond": (5, [".....", "..#..", ".###.", "#####", ".###.", "..#..", ".....", "....."]),
    "note": (6, ["...#.", "...##", "...#.", "...#.", ".###.", "####.", ".##..", "....."]),
    "food": (7, ["...#.", "..#..", ".###.", "#####", "#####", "#####", ".###.", "....."]),
}


def glyph_rows(art: list[str]) -> list[int]:
    """'.#.#.' style rows -> the 8 numbers the LCD chip wants (bit 4 = left pixel)."""
    return [int(row.replace("#", "1").replace(".", "0"), 2) for row in art]


# name -> (slot, [8 row bytes]); what backends upload to the chip.
GLYPHS: dict[str, tuple[int, list[int]]] = {name: (slot, glyph_rows(art)) for name, (slot, art) in GLYPH_ART.items()}

# The character that means "show custom glyph <name>" inside LCD text.
G = {name: chr(slot) for name, (slot, _) in GLYPHS.items()}

# How the simulated screen draws each glyph (a look-alike Unicode symbol)
# and a one-character ASCII stand-in for plain-text snapshots.
SIM_LOOK = {G["heart"]: "♥", G["face"]: "☺", G["wifi"]: "▟", G["bell"]: "Δ",
            G["bolt"]: "ϟ", G["diamond"]: "◆", G["note"]: "♪", G["food"]: "●"}
ASCII_LOOK = {G["heart"]: "<", G["face"]: "@", G["wifi"]: "|", G["bell"]: "!",
              G["bolt"]: "~", G["diamond"]: "*", G["note"]: "d", G["food"]: "o"}

# ---------------------------------------------------------------------------
# Character mapping
# ---------------------------------------------------------------------------
# Symbols that become one of our custom glyphs.
TO_GLYPH = {
    "♥": "heart", "❤": "heart", "♡": "heart", "💖": "heart", "💕": "heart", "🩷": "heart",
    "☺": "face", "☻": "face", "🙂": "face", "😀": "face", "😊": "face", "🐾": "face",
    "≋": "wifi", "📶": "wifi", "📡": "wifi",
    "🔔": "bell", "🛎": "bell", "⚠": "bell", "❗": "bell", "‼": "bell",
    "⚡": "bolt", "ϟ": "bolt", "🔋": "bolt",
    "◆": "diamond", "♦": "diamond", "◇": "diamond", "✦": "diamond", "★": "diamond", "☆": "diamond",
    "✨": "diamond", "💎": "diamond",
    "♪": "note", "♫": "note", "🎵": "note", "🎶": "note",
    "🍖": "food", "🍔": "food", "🍎": "food", "🍗": "food", "🍕": "food", "🍪": "food",
}

# Symbols with a plain-ASCII look-alike.
TO_ASCII = {
    "—": "-", "–": "-", "−": "-", "‐": "-", "…": "...", "·": "*", "•": "*", "●": "*",
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "«": "<<", "»": ">>",
    "→": ">", "⟶": ">", "▸": ">", "►": ">", "←": "<", "◂": "<", "↑": "^", "↓": "v", "⇅": "^v",
    "×": "x", "÷": "/", "°": "o", "±": "+-", "≥": ">=", "≤": "<=", "≠": "!=",
    "✓": "v", "✔": "v", "✗": "x", "✖": "x", "☐": "[]", "☑": "[x]",
    "\u00a0": " ", "\t": " ", "ß": "ss", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "đ": "d", "ł": "l",
    # The common A00 LCD ROM shows "\" as a Yen sign and "~" as an arrow.
    "\\": "/", "~": "-",
}

# Words you can write in blurbs to get a glyph: "{heart} hi {name}".
TOKENS = {f"{{{name}}}": ch for name, ch in G.items()}


def to_lcd(text: str, glyphs: bool = True) -> str:
    """Map any text to characters the LCD can show.

    glyphs=False uses ASCII stand-ins instead of custom glyphs (handy for
    the i2c fallback when the chip refuses custom characters).
    """
    text = str(text)
    for token, ch in TOKENS.items():
        text = text.replace(token, ch)
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 8:                                   # already a glyph slot
            out.append(ch if glyphs else ASCII_LOOK.get(ch, "?"))
        elif ch in ("\n", "\r"):
            out.append(" ")
        elif 32 <= code < 127 and ch not in TO_ASCII:
            out.append(ch)
        elif ch in TO_GLYPH:
            g = G[TO_GLYPH[ch]]
            out.append(g if glyphs else ASCII_LOOK[g])
        elif ch in TO_ASCII:
            out.append(TO_ASCII[ch])
        elif unicodedata.category(ch) in ("Mn", "Me", "Cf", "Cc"):
            continue                                   # combining marks, zero-width joiners, variation selectors
        else:
            # Accented letters: é -> e, Ü -> U (ß, æ, ø are in TO_ASCII).
            plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
            if plain:
                out.append(plain)
            elif unicodedata.category(ch).startswith("S"):
                continue                               # unknown emoji / symbol: drop it
            else:
                out.append("?")
    return "".join(out)


def collapse(text: str) -> str:
    """Squash runs of spaces (after mapping) and trim the ends."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def fit(text: str, cols: int = 16) -> str:
    """Exactly `cols` characters: cut or pad with spaces."""
    return text[:cols].ljust(cols)


def wrap2(text: str, cols: int = 16, rows: int = 2) -> list[str] | None:
    """Word-wrap onto `rows` lines if it fits, else None (then it scrolls)."""
    text = collapse(text)
    if not text:
        return [""] * rows
    lines = textwrap.wrap(text, cols, break_long_words=False, break_on_hyphens=True)
    if len(lines) > rows or any(len(line) > cols for line in lines):
        return None
    return lines + [""] * (rows - len(lines))


def marquee(text: str, cols: int, offset: int, gap: int = 4) -> str:
    """The `cols`-wide window of a ticker at step `offset`.

    Short text is returned as-is (padded). Long text loops with `gap`
    spaces between the end and the next start.
    """
    if len(text) <= cols:
        return fit(text, cols)
    loop = text + " " * gap
    offset %= len(loop)
    return (loop + loop)[offset:offset + cols]


def marquee_steps(text: str, cols: int, gap: int = 4) -> int:
    """How many steps one full pass of the ticker takes (0 = no scrolling)."""
    return 0 if len(text) <= cols else len(text) + gap


def sim_view(line: str, ascii_only: bool = False) -> str:
    """How a line looks on the simulated screen (glyph slots drawn as symbols)."""
    look = ASCII_LOOK if ascii_only else SIM_LOOK
    return "".join(look.get(ch, ch) for ch in line)
