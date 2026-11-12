"""pages.py — what the LCD shows: idle pages, blurbs, and line layout.

In plain English: when nothing urgent is happening the screen rotates
through "idle pages" (config/lcd.ini [pages] order):

  pulse   PULSE's name, level and mood, plus food / energy / happiness
  blurb   one short line from PULSE: a recent reaction (from discoveries
          and pet events) or a flavor line for its current mood from
          text/lcd_blurbs.txt — edit that file to give PULSE your words
  clock   the time, the companion link state, and the battery

A Screen is just two line specs. layout_message() turns any message into
one, and lines_at() works out exactly what is on the glass `elapsed`
seconds after it appeared (long lines scroll like a ticker).
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .messages import ALERT, MANUAL, NOTICE, LcdMessage
from .text import G, collapse, fit, marquee, marquee_steps, to_lcd, wrap2

# Moods the companion (or the deck's sim pet) can report, folded into the
# section names used in text/lcd_blurbs.txt. Unknown moods use [any].
MOOD_GROUPS = {
    "joyful": "happy", "playful": "happy", "happy": "happy", "excited": "excited",
    "content": "content", "hungry": "hungry", "tired": "tired", "hurt": "tired", "fainted": "tired",
    "grumpy": "sad", "sad": "sad", "angry": "sad", "bored": "sad",
    "sleeping": "sleeping", "asleep": "sleeping",
}

# Short forms for the PULSE page when the mood word does not fit.
MOOD_SHORT = {"fainted": "KO", "sleeping": "zZ", "excited": "!!", "content": "ok", "playful": "play",
              "joyful": "joy", "hungry": "hngry", "grumpy": "grr", "happy": "yay", "tired": "tired"}

# Used when text/lcd_blurbs.txt is missing.
FALLBACK_BLURBS = {
    "any": ["{name} is watching the airwaves", "{heart} hi, operator!", "{wifi} listening..."],
    "sleeping": ["zZz... {name} dreams of packets"],
    "hungry": ["{food} snack please?"],
}


# ---------------------------------------------------------------------------
# Flavor blurbs
# ---------------------------------------------------------------------------
def load_blurbs(path: str | Path | None) -> dict[str, list[str]]:
    """text/lcd_blurbs.txt -> {mood group: [lines]}.

    File format: a [mood] line starts a group, every other non-empty
    line is one blurb, and lines starting with # are comments.
    """
    groups: dict[str, list[str]] = {}
    try:
        text = Path(path).read_text(encoding="utf-8") if path else ""
    except OSError:
        text = ""
    current = "any"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower() or "any"
            continue
        groups.setdefault(current, []).append(line)
    return groups or {k: list(v) for k, v in FALLBACK_BLURBS.items()}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:      # unknown {thing} stays as written
        return "{" + key + "}"


def fill(template: str, pet: dict) -> str:
    """Fill {name} {level} {mood} {species} {hunger} {energy} {happiness}."""
    values = _SafeDict({k: pet.get(k, "?") for k in ("level", "mood", "species", "hunger", "energy", "happiness",
                                                      "stage")})
    values["name"] = pet.get("name") or "PULSE"
    try:
        return template.format_map(values)
    except (ValueError, IndexError):
        return template


class BlurbPool:
    """Recent PULSE reactions first, then mood flavor lines."""

    def __init__(self, blurbs: dict[str, list[str]], reaction_minutes: float = 30.0,
                 rng: random.Random | None = None) -> None:
        self.blurbs = blurbs
        self.reaction_seconds = reaction_minutes * 60
        self.reactions: deque[tuple[float, str]] = deque(maxlen=12)
        self._unseen: deque[str] = deque(maxlen=6)
        self.rng = rng or random.Random()
        self._last = ""

    def add_reaction(self, text: str, now: float) -> None:
        text = collapse(str(text or ""))
        if not text or any(t == text for _, t in self.reactions):
            return
        self.reactions.append((now, text))
        self._unseen.append(text)

    def next(self, pet: dict, now: float) -> str:
        """The next blurb: a fresh reaction if there is one, else flavor."""
        if self._unseen:
            self._last = self._unseen.popleft()
            return self._last
        fresh = [t for ts, t in self.reactions if now - ts <= self.reaction_seconds]
        mood = str(pet.get("mood") or "").lower()
        if pet.get("asleep"):
            mood = "sleeping"
        group = MOOD_GROUPS.get(mood, mood)
        flavor = list(self.blurbs.get(group, [])) + list(self.blurbs.get("any", []))
        choices = [fill(t, pet) for t in flavor] + fresh
        if len(choices) > 1 and self._last in choices:
            choices.remove(self._last)          # never the same line twice in a row
        self._last = self.rng.choice(choices) if choices else f"{pet.get('name') or 'PULSE'} is here"
        return self._last


# ---------------------------------------------------------------------------
# Screens: two line specs
# ---------------------------------------------------------------------------
@dataclass
class Screen:
    """What one page / message looks like. Lines are already LCD-mapped."""

    lines: list[str]
    name: str = ""               # page or message kind, for `lcd status`

    def steps(self, cols: int) -> int:
        """Longest ticker on this screen, in steps (0 = nothing scrolls)."""
        return max((marquee_steps(line, cols) for line in self.lines), default=0)


def lines_at(screen: Screen, elapsed: float, cols: int, rows: int, fps: float, pause: float) -> list[str]:
    """The exact characters on the glass `elapsed` seconds into a screen."""
    step = max(0, int((elapsed - pause) * fps))
    out = [marquee(line, cols, step) for line in screen.lines[:rows]]
    return out + [" " * cols] * (rows - len(out))


def screen_seconds(screen: Screen, cols: int, fps: float, pause: float, base: float) -> float:
    """How long a screen should stay: base, or long enough to scroll once."""
    steps = screen.steps(cols)
    return base if steps == 0 else max(base, pause + steps / fps + 0.6)


HEADERS = {ALERT: G["bell"] + " ALERT", NOTICE: G["diamond"] + " PULSE", MANUAL: G["face"] + " DECK"}


def layout_text(text: str, header: str, cols: int, rows: int) -> list[str]:
    """Body text: word-wrapped if it fits, else a header + a ticker."""
    body = collapse(to_lcd(text))
    if rows >= 2:
        wrapped = wrap2(body, cols, rows)
        if wrapped is not None:
            return [fit(line, cols) for line in wrapped]
        return [fit(collapse(to_lcd(header)), cols)] + [body] + [""] * (rows - 2)
    return [body]


def layout_message(msg: LcdMessage, cols: int = 16, rows: int = 2) -> Screen:
    """A notification -> a Screen (title on line 1, text on line 2)."""
    if msg.title and rows >= 2:
        title = collapse(to_lcd(msg.title))
        body = collapse(to_lcd(msg.text))
        return Screen([title if len(title) > cols else fit(title, cols), body] + [""] * (rows - 2), msg.kind)
    header = HEADERS.get(msg.priority, G["diamond"] + " PULSE")
    return Screen(layout_text(msg.text, header, cols, rows), msg.kind)


def _stat(glyph: str, value) -> str:
    try:
        return f"{glyph}{int(round(float(value)))}"
    except (TypeError, ValueError):
        return f"{glyph}?"


def pulse_page(pet: dict, cols: int = 16, rows: int = 2) -> Screen:
    """Name, level, mood / food, energy, happiness."""
    if not pet:
        return Screen(layout_text("PULSE is not linked yet", "PULSE", cols, rows), "pulse")
    name = to_lcd(str(pet.get("name") or "PULSE"))[:8]
    level = pet.get("level")
    left = f"{G['face']}{name}" + (f" L{level}" if level is not None else "")
    mood = "zZ" if pet.get("asleep") else to_lcd(str(pet.get("mood") or ""))
    room = cols - len(left) - 1
    if len(mood) > room:
        mood = MOOD_SHORT.get(mood.lower(), mood)
    line1 = left + (" " + mood[:room].rjust(room) if room > 0 and mood else "")
    line2 = " ".join([_stat(G["food"], pet.get("hunger")), _stat(G["bolt"], pet.get("energy")),
                      _stat(G["heart"], pet.get("happiness", pet.get("mood_value")))])
    return Screen([fit(line1, cols), fit(line2, cols)][:rows], "pulse")


def clock_page(now: float, link: str, battery: float | None, fmt: str = "%a %d %b %H:%M",
               cols: int = 16, rows: int = 2) -> Screen:
    """The time / link state and battery."""
    line1 = to_lcd(time.strftime(fmt, time.localtime(now)))
    bat = f"{G['bolt']}{battery:.0f}%" if battery is not None else ""
    left = f"{G['wifi']}{to_lcd(link)}"
    line2 = left + bat.rjust(max(0, cols - len(left))) if bat else left
    return Screen([line1 if len(line1) > cols else fit(line1, cols), fit(line2, cols)][:rows], "clock")


def blurb_page(text: str, pet: dict, cols: int = 16, rows: int = 2) -> Screen:
    header = f"{G['face']}{to_lcd(str(pet.get('name') or 'PULSE'))[:10]} says"
    return Screen(layout_text(text, header, cols, rows), "blurb")
