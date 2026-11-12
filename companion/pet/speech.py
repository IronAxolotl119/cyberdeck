"""Dialogue engine: line pools, a no-repeat picker, and stage/mood voices.

Pools live in pet/lines/*.lines (plain text, one line per entry under a
``[category.key]`` header). ``Voice.speak`` picks a body line, sometimes adds a
mood opener and a stage closer, applies the time-of-day habit (afternoon
exclamations, late-night rambles), the pet's own verbal tic, and finally
rewrites the whole thing in the stage's voice: babies babble, in-training
pets speak in short simple words, rookies and up use full sentences, and
ultimate / mega forms sound a little wise.

``LinePicker`` keeps a per-category ring buffer of recently used indexes
(persisted in the save) so a line is not reused until most of its pool has
been spoken.
"""

from __future__ import annotations

import random
import re
import string
from pathlib import Path
from typing import Any, Iterable

LINES_DIR = Path(__file__).resolve().parent / "lines"

# A line will not repeat until this share of its pool has been used.
NO_REPEAT_SHARE = 0.8

# Stage -> voice tier.
STAGE_TIERS = {
    "baby": "baby",
    "in-training": "simple",
    "rookie": "kid",
    "champion": "teen",
    "ultimate": "sage",
    "mega": "sage",
}


def parse_lines(text: str) -> dict[str, list[str]]:
    """Parse one .lines file into {key: [lines]}."""
    pools: dict[str, list[str]] = {}
    key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            key = stripped[1:-1].strip()
            pools.setdefault(key, [])
            continue
        if key is None:
            continue
        # Keep a leading space for closers/tics such as " bam!".
        entry = line if line.startswith(" ") and not line.startswith("  ") else stripped
        pools[key].append(entry)
    return pools


_POOLS: dict[str, list[str]] | None = None


def load_pools(directory: Path | None = None) -> dict[str, list[str]]:
    """All pools from every .lines file (cached for the default directory)."""
    global _POOLS
    if directory is None and _POOLS is not None:
        return _POOLS
    base = directory or LINES_DIR
    merged: dict[str, list[str]] = {}
    for path in sorted(base.glob("*.lines")):
        for key, lines in parse_lines(path.read_text(encoding="utf-8")).items():
            merged.setdefault(key, []).extend(lines)
    if directory is None:
        _POOLS = merged
    return merged


def pool(key: str) -> list[str]:
    return list(load_pools().get(key, []))


def resolve_key(key: str, pools: dict[str, list[str]] | None = None) -> str | None:
    """Most-specific existing key: a.b.c -> a.b -> a."""
    pools = pools if pools is not None else load_pools()
    parts = key.split(".")
    while parts:
        candidate = ".".join(parts)
        if pools.get(candidate):
            return candidate
        parts.pop()
    return None


class _Slots(dict):
    """format_map mapping that leaves unknown slots readable."""

    DEFAULTS = {
        "name": "PULSE",
        "nickname": "PULSE",
        "food": "snack",
        "game": "tag",
        "network": "that network",
        "place": "somewhere",
        "move": "Chirp",
        "trinket": "trinket",
        "rival": "the rival",
        "foe": "foe",
        "species": "pet",
        "app": "that app",
        "item": "thing",
        "flavor": "tasty",
        "count": "a few",
        "hours": "a few",
        "minutes": "a few",
        "days": "a few",
        "weather": "nice",
        "holiday": "a holiday",
        "time": "the usual time",
    }

    def __missing__(self, key: str) -> str:
        return str(self.DEFAULTS.get(key, key))


_FORMATTER = string.Formatter()


_ONE_UNIT = re.compile(r"\b1 (hour|minute|day|month|year|time|snack|win|loss|visit)s\b")
_A_AN = re.compile(r"\b([Aa]) (?=(?!one\b|use|uni|eu)[AEIOaeio]\w)")
_LONE_I = re.compile(r"(?<![\w'])i(?=(?:'[a-z]+)?\b)(?![-\w])")


def tidy(text: str) -> str:
    """Small grammar fixes after slots are filled: '1 hours' and 'a Aethling'."""
    text = _ONE_UNIT.sub(lambda m: f"1 {m.group(1)}", text)
    return _A_AN.sub(lambda m: f"{m.group(1)}n ", text)


def sentence_case(text: str) -> str:
    """Capitalize the first letter and a lone 'i' (pools are written casually)."""
    text = _LONE_I.sub("I", text)
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if ch == "*":  # leave *actions* alone
            return text
    return text


def fill(template: str, slots: dict[str, Any] | None = None) -> str:
    """Fill {slots}; unknown slots get a gentle default, never a KeyError."""
    mapping = _Slots({k: v for k, v in (slots or {}).items() if v is not None})
    try:
        return tidy(_FORMATTER.vformat(template, (), mapping))
    except (ValueError, IndexError, AttributeError):
        return template


class LinePicker:
    """Pick lines without repeating until most of the pool has been used."""

    def __init__(self, history: dict[str, list[int]] | None = None, share: float = NO_REPEAT_SHARE) -> None:
        self.history: dict[str, list[int]] = {
            str(k): [int(i) for i in v] for k, v in (history or {}).items()
        }
        self.share = share

    def pick_index(self, key: str, size: int, rng: random.Random) -> int:
        if size <= 0:
            raise ValueError("empty pool")
        if size == 1:
            return 0
        recent = self.history.setdefault(key, [])
        cap = max(1, min(size - 1, int(size * self.share)))
        blocked = set(recent[-cap:])
        choices = [i for i in range(size) if i not in blocked]
        if not choices:
            choices = list(range(size))
        index = rng.choice(choices)
        recent.append(index)
        if len(recent) > cap:
            del recent[: len(recent) - cap]
        return index

    def pick(self, key: str, rng: random.Random, pools: dict[str, list[str]] | None = None) -> str:
        pools = pools if pools is not None else load_pools()
        resolved = resolve_key(key, pools)
        if resolved is None:
            return ""
        lines = pools[resolved]
        return lines[self.pick_index(resolved, len(lines), rng)]

    def to_dict(self) -> dict[str, list[int]]:
        return {k: list(v) for k, v in sorted(self.history.items()) if v}


# ---------------------------------------------------------------------------
# Stage voices
# ---------------------------------------------------------------------------

_BABY_WORDS = {
    "hungry": "hungy",
    "food": "nomnom",
    "snack": "nomnom",
    "eat": "nom",
    "sleep": "seepy",
    "sleepy": "seepy",
    "tired": "seepy",
    "play": "pway",
    "hello": "hewwo",
    "hi": "hiii",
    "love": "wuv",
    "you": "yu",
    "yes": "yesh",
    "no": "nu",
    "thank": "tank",
    "thanks": "tankyu",
    "happy": "happi",
    "sad": "sab",
    "little": "widdle",
    "please": "pwease",
    "pls": "pwease",
    "friend": "fwen",
    "signal": "sigga",
    "network": "netty",
    "bored": "bowed",
    "back": "bak",
    "scared": "scawed",
    "the": "",
    "a": "",
    "is": "",
    "am": "",
    "i": "me",
    "my": "me",
}
_BABY_SOUNDS = ("pii!", "bu!", "mya~", "pip!", "ba!", "eee!", "boop!", "nyu~", "bwa!", "chuu~")

_SIMPLE_DROP = {
    "the", "a", "an", "really", "very", "just", "so", "actually", "honestly",
    "basically", "totally", "absolutely", "completely", "extremely", "definitely",
    "that", "of", "to", "is", "are", "am", "be", "been", "it", "will",
}
_SIMPLE_SWAP = {
    "i'm": "me", "i": "me", "my": "me", "myself": "me", "you're": "you",
    "cannot": "no can", "can't": "no can", "don't": "no", "do not": "no",
    "wonderful": "good", "amazing": "good", "delicious": "yum", "excellent": "good",
    "favorite": "fav", "immediately": "now", "beautiful": "pretty",
}
_ACTION = re.compile(r"\*[^*]+\*")


def _words(text: str) -> list[str]:
    return text.split()


def babble(text: str, rng: random.Random, pure_pool: list[str] | None = None) -> str:
    """Turn a line into baby talk: a few mangled words plus baby sounds."""
    if pure_pool and rng.random() < 0.3:
        return rng.choice(pure_pool)
    actions = _ACTION.findall(text)
    plain = _ACTION.sub(" ", text)
    out: list[str] = []
    for raw in _words(plain):
        word = raw.strip(".,!?;:\"()").lower()
        if not word:
            continue
        if word in _BABY_WORDS:
            mapped = _BABY_WORDS[word]
        elif "{" in word:
            mapped = word
        elif len(word) > 6 and rng.random() < 0.5:
            # first syllable, doubled: network -> ne-ne
            mapped = f"{word[:2]}-{word[:2]}"
        else:
            mapped = word.replace("r", "w").replace("l", "w")
        if mapped:
            out.append(mapped)
        if len(out) >= 4:
            break
    sound = rng.choice(_BABY_SOUNDS)
    core = " ".join(out) if out else ""
    pieces = []
    if actions and rng.random() < 0.5:
        pieces.append(actions[0])
    if core:
        pieces.append(core + rng.choice(("!", "?", "~", "...", "!!")))
    pieces.append(sound)
    return " ".join(pieces).strip()


def simplify(text: str, rng: random.Random, max_words: int = 9) -> str:
    """In-training voice: lowercase, short, simple words."""
    actions = _ACTION.findall(text)
    plain = _ACTION.sub(" ", text).lower()
    for long_form, short in _SIMPLE_SWAP.items():
        plain = re.sub(rf"\b{re.escape(long_form)}\b", short, plain)
    out: list[str] = []
    for raw in _words(plain):
        word = raw.strip(",;:\"()")
        bare = word.strip(".!?")
        if bare in _SIMPLE_DROP and len(out) > 0:
            continue
        if word:
            out.append(word)
        if len(out) >= max_words:
            break
    sentence = " ".join(out).strip(" ,") or "hm."
    if sentence[-1] not in ".!?~":
        sentence += rng.choice((".", "!", "?", "!!"))
    if actions and rng.random() < 0.6:
        sentence = f"{actions[0]} {sentence}"
    return sentence


def _strip_end(text: str) -> str:
    return text.rstrip(" .!?~")


class Voice:
    """Compose lines for one pet."""

    def __init__(self, picker: LinePicker, rng: random.Random) -> None:
        self.picker = picker
        self.rng = rng

    def line(self, key: str, slots: dict[str, Any] | None = None) -> str:
        """One filled line from a pool, with no extra styling."""
        return fill(self.picker.pick(key, self.rng), slots)

    def speak(
        self,
        key: str,
        slots: dict[str, Any] | None = None,
        *,
        mood: str = "content",
        stage: str = "rookie",
        time_of_day: str | None = None,
        tic: str | None = None,
        catchphrase: str | None = None,
        decorate: bool = True,
    ) -> str:
        """A styled speech bubble for this pet."""
        rng = self.rng
        pools = load_pools()
        body = self.picker.pick(f"{key}.{mood}", rng, pools) or self.picker.pick(key, rng, pools)
        if not body:
            return ""
        text = fill(body, slots)
        tier = STAGE_TIERS.get(stage, "kid")
        if decorate:
            if rng.random() < 0.35 and resolve_key(f"open.{mood}", pools):
                opener = fill(self.picker.pick(f"open.{mood}", rng, pools), slots)
                text = f"{opener} {text}"
            close_tier = {"baby": None, "simple": None}.get(tier, tier)
            if close_tier and rng.random() < 0.25:
                closer = self.picker.pick(f"close.{close_tier}", rng, pools)
                if closer and not text.rstrip().endswith("*"):
                    text = _strip_end(text) + closer
            if time_of_day == "afternoon" and rng.random() < 0.4:
                text = _strip_end(text) + rng.choice(("!!", "!!!", "! zoom!", "! hehe!"))
            elif time_of_day == "night" and rng.random() < 0.45 and tier not in {"baby"}:
                ramble = self.picker.pick("ramble", rng, pools)
                text = f"{_strip_end(text)}... {fill(ramble, slots)}"
            if catchphrase and rng.random() < 0.06:
                text = f"{text} {catchphrase}"
            elif tic and rng.random() < 0.1:
                text = _strip_end(text) + tic
        if tier == "baby":
            text = babble(text, rng, pools.get("babble"))
        elif tier == "simple":
            text = simplify(text, rng)
        else:
            text = sentence_case(text)
        return text.strip()


def pool_sizes(keys: Iterable[str] | None = None) -> dict[str, int]:
    pools = load_pools()
    return {k: len(pools[k]) for k in (keys or sorted(pools))}
