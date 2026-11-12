"""fx.py — small visual-effect helpers shared by the fun features.

Everything here is a plain function that returns text, so the boot
animation, the "hack the mainframe" show, the typing game, and the
visualizer can all reuse it, and tests can check it without a screen.

  banner(text)          big block letters (3x5 font) for titles
  glitch(text, rng, n)  randomly corrupt some characters
  progress(frac, w)     a [████░░░░] bar
  fake_code_line(rng)   one believable-looking line of "hacker" code
  hex_dump_line(rng)    one line of a hex dump
  BARS                  the eight block heights used by the visualizer

Pass unicode=False to get plain ASCII for terminals without box/block
characters (layout.ini use_unicode = no).
"""

from __future__ import annotations

import random

# Eight heights for the visualizer, lowest to highest.
BARS = "▁▂▃▄▅▆▇█"
BARS_ASCII = " .:-=+*#"

# Characters glitch() swaps in.
GLITCH_CHARS = "▓▒░█#@%&$!?/\\|<>{}[]~^*01"
GLITCH_ASCII = "#@%&$!?/\\|<>{}[]~^*01"

# A tiny 3x5 block font, enough for titles like ACCESS GRANTED.
FONT = {
    "A": [" █ ", "█ █", "███", "█ █", "█ █"], "B": ["██ ", "█ █", "██ ", "█ █", "██ "],
    "C": [" ██", "█  ", "█  ", "█  ", " ██"], "D": ["██ ", "█ █", "█ █", "█ █", "██ "],
    "E": ["███", "█  ", "██ ", "█  ", "███"], "F": ["███", "█  ", "██ ", "█  ", "█  "],
    "G": [" ██", "█  ", "█ █", "█ █", " ██"], "H": ["█ █", "█ █", "███", "█ █", "█ █"],
    "I": ["███", " █ ", " █ ", " █ ", "███"], "J": ["  █", "  █", "  █", "█ █", " █ "],
    "K": ["█ █", "█ █", "██ ", "█ █", "█ █"], "L": ["█  ", "█  ", "█  ", "█  ", "███"],
    "M": ["█ █", "███", "█ █", "█ █", "█ █"], "N": ["██ ", "█ █", "█ █", "█ █", "█ █"],
    "O": [" █ ", "█ █", "█ █", "█ █", " █ "], "P": ["██ ", "█ █", "██ ", "█  ", "█  "],
    "Q": [" █ ", "█ █", "█ █", "██ ", " ██"], "R": ["██ ", "█ █", "██ ", "█ █", "█ █"],
    "S": [" ██", "█  ", " █ ", "  █", "██ "], "T": ["███", " █ ", " █ ", " █ ", " █ "],
    "U": ["█ █", "█ █", "█ █", "█ █", "███"], "V": ["█ █", "█ █", "█ █", "█ █", " █ "],
    "W": ["█ █", "█ █", "█ █", "███", "█ █"], "X": ["█ █", "█ █", " █ ", "█ █", "█ █"],
    "Y": ["█ █", "█ █", " █ ", " █ ", " █ "], "Z": ["███", "  █", " █ ", "█  ", "███"],
    "0": ["███", "█ █", "█ █", "█ █", "███"], "1": [" █ ", "██ ", " █ ", " █ ", "███"],
    "!": [" █ ", " █ ", " █ ", "   ", " █ "], "/": ["  █", "  █", " █ ", "█  ", "█  "],
    "-": ["   ", "   ", "███", "   ", "   "], " ": ["  ", "  ", "  ", "  ", "  "],
}


def banner(text: str, unicode: bool = True) -> list[str]:
    """Five lines of big letters. Unknown characters become blanks."""
    rows = ["", "", "", "", ""]
    for ch in text.upper():
        glyph = FONT.get(ch, FONT[" "])
        for i in range(5):
            rows[i] += glyph[i] + " "
    rows = [r.rstrip() for r in rows]
    return rows if unicode else [r.replace("█", "#") for r in rows]


def glitch(text: str, rng: random.Random, amount: float, unicode: bool = True) -> str:
    """Corrupt roughly `amount` (0..1) of the non-space characters."""
    if amount <= 0:
        return text
    pool = GLITCH_CHARS if unicode else GLITCH_ASCII
    return "".join(rng.choice(pool) if ch != " " and rng.random() < amount else ch for ch in text)


def progress(fraction: float, width: int = 20, unicode: bool = True) -> str:
    """[████░░░░] for 0..1."""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    full, empty = ("█", "░") if unicode else ("#", ".")
    return "[" + full * filled + empty * (width - filled) + "]"


def level_char(level: float, unicode: bool = True) -> str:
    """One visualizer bar for a 0..1 level."""
    chars = BARS if unicode else BARS_ASCII
    return chars[max(0, min(len(chars) - 1, int(level * (len(chars) - 1) + 0.5)))]


# Pieces for fake code. Everything is made up and harmless.
_FUNCS = ["decrypt", "inject", "bypass", "spoof", "tunnel", "overflow", "handshake", "mutate", "exfil", "ghost"]
_DOING = ["decrypting", "injecting", "bypassing", "spoofing", "tunneling", "mutating", "probing", "ghosting"]
_THINGS = ["firewall", "kernel", "packet", "token", "cipher", "socket", "daemon", "payload", "proxy", "node"]
_TEMPLATES = [
    "{f}_{t}(0x{h4}, {n});",
    "if (({t}->flags & 0x{h2}) != 0) {{ {f}_{t}(); }}",
    "mov eax, 0x{h8}   ; {f} {t}",
    "for (int i = 0; i < {n}; i++) {t}[i] ^= 0x{h2};",
    "#define {T}_KEY 0x{h8}",
    ">> {g} {t} @ 10.{n}.{m}.{k} ... ok",
    "push 0x{h4}; call {f}_{t}",
    "{t}_buf[{n}] = ({f}_t*) malloc(0x{h4});",
    "while (!{t}->ready) {f}_{t}(--retries);",
    "sha256({t}) = {h8}{h8}...",
]


def fake_code_line(rng: random.Random) -> str:
    """One line of movie-style code."""
    def hx(n: int) -> str:
        return "".join(rng.choice("0123456789abcdef") for _ in range(n))

    t = rng.choice(_THINGS)
    return rng.choice(_TEMPLATES).format(
        f=rng.choice(_FUNCS), g=rng.choice(_DOING), t=t, T=t.upper(), h2=hx(2), h4=hx(4), h8=hx(8),
        n=rng.randint(2, 254), m=rng.randint(0, 254), k=rng.randint(1, 254))


def hex_dump_line(rng: random.Random, address: int) -> str:
    """'0000a0f0  4f 2a …  |O*..|' style line."""
    data = [rng.randint(0, 255) for _ in range(12)]
    hexes = " ".join(f"{b:02x}" for b in data)
    text = "".join(chr(b) if 33 <= b < 127 else "." for b in data)
    return f"{address:08x}  {hexes}  |{text}|"
