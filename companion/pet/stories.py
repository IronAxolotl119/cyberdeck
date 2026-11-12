"""Fragment-assembled text: diary entries, adventure stories, names.

Every generator draws from pools in pet/lines/stories.lines through the
pet's LinePicker, so pieces do not repeat back to back, and the multiplied
fragments give thousands of distinct results.
"""

from __future__ import annotations

import random
from typing import Any

from pet.speech import LinePicker, fill, load_pools, pool

FOOD_NAMES = (
    "byte biscuit", "chip crumble", "packet pie", "voltberry", "cache cookie",
    "static candy", "pixel pudding", "spark noodle", "data donut", "RAM ramen",
    "honey bits", "pepper ping", "glitch gummy", "ion apple", "server sushi",
)
TOY_NAMES = (
    "bouncy bit", "squeaky mouse", "laser dot", "yarn cable", "spinner fan",
    "rubber ducky", "tiny frisbee", "puzzle cube", "jingle ball", "paper plane",
)
BATTERY_NAMES = ("AA cell", "coin battery", "spare charger", "power bank", "solar chip")


def trinket_name(picker: LinePicker, rng: random.Random) -> str:
    adj = picker.pick("trinket.adj", rng)
    noun = picker.pick("trinket.noun", rng)
    return f"{adj} {noun}".strip()


def rival_name(rng: random.Random) -> str:
    return f"{rng.choice(pool('rival.prefix'))}{rng.choice(pool('rival.suffix'))}"


def catchphrase(rng: random.Random) -> str:
    return f"{rng.choice(pool('catch.a'))}{rng.choice(pool('catch.b'))}".strip()


def adventure_place(picker: LinePicker, rng: random.Random) -> str:
    return picker.pick("adv.place", rng)


def adventure_story(
    picker: LinePicker,
    rng: random.Random,
    outcome: str,
    slots: dict[str, Any],
    loot: str | None,
) -> str:
    """setting + travel + encounter + outcome + loot + ending."""
    parts = [
        fill(picker.pick("adv.setting", rng), slots),
        fill(picker.pick("adv.travel", rng), slots),
    ]
    first = " ".join(parts)
    rest = [
        fill(picker.pick("adv.encounter", rng), slots),
        fill(picker.pick(f"adv.{outcome}", rng), slots),
        fill(picker.pick("adv.loot" if loot else "adv.noloot", rng), dict(slots, trinket=loot)),
        fill(picker.pick("adv.ending", rng), slots),
    ]
    return " ".join([first] + rest)


# Diary sections in the order they are considered: (counter, pool, slot builder).
_DIARY_COUNTERS = (
    ("fed", "diary.fed"),
    ("played", "diary.played"),
    ("discoveries", "diary.discoveries"),
    ("treasures", "diary.treasure"),
    ("wins", "diary.wins"),
    ("losses", "diary.losses"),
    ("praised", "diary.praised"),
    ("scolded", "diary.scolded"),
    ("adventures", "diary.adventure"),
    ("sick", "diary.sick"),
    ("healed", "diary.healed"),
    ("tantrums", "diary.tantrum"),
    ("rival_seen", "diary.rival"),
    ("rival_wins", "diary.rival_win"),
    ("gifts_given", "diary.gift_given"),
    ("gifts_got", "diary.gift_got"),
    ("visits", "diary.visit"),
)


def diary_entry(
    picker: LinePicker,
    rng: random.Random,
    day: dict[str, Any],
    slots: dict[str, Any],
    mood_score: float,
    signoff: str,
) -> str:
    """Assemble one diary entry for a finished (or in-progress) day."""
    counts: dict[str, int] = {k: int(v) for k, v in (day.get("c") or {}).items()}
    hi: dict[str, Any] = dict(day.get("h") or {})
    tone = "good" if mood_score >= 65 else "bad" if mood_score < 35 else "okay"
    s = dict(slots)
    s.update({k: v for k, v in hi.items() if v})
    s.setdefault("color", picker.pick("diary.color", rng))

    lines = [fill(picker.pick(f"diary.open.{tone}", rng), s)]
    events: list[str] = []
    for counter, key in _DIARY_COUNTERS:
        n = counts.get(counter, 0)
        if n <= 0:
            continue
        events.append(fill(picker.pick(key, rng), dict(s, count=n)))
    if counts.get("fed", 0) == 0:
        events.append(fill(picker.pick("diary.fed_none", rng), s))
    if counts.get("visits", 0) == 0:
        events.append(fill(picker.pick("diary.no_visit", rng), s))
    if hi.get("holiday"):
        events.append(fill(picker.pick("diary.holiday", rng), s))
    if hi.get("birthday"):
        events.append(fill(picker.pick("diary.birthday", rng), s))
    if hi.get("weather"):
        events.append(fill(picker.pick("diary.weather", rng), s))
    # Keep entries readable: the most notable 2-5 event lines in order.
    if len(events) > 5:
        keep = sorted(rng.sample(range(len(events)), 5))
        events = [events[i] for i in keep]
    lines.extend(events)
    for _ in range(rng.choice((1, 1, 2))):
        lines.append(fill(picker.pick("diary.filler", rng), s))
    lines.append(fill(picker.pick(f"diary.feel.{tone}", rng), s))
    lines.append(fill(picker.pick("diary.close", rng), s))
    lines.append(f"- {signoff}")
    return " ".join(lines)


def combination_estimates() -> dict[str, int]:
    """Rough count of distinct outputs per generator (for docs / reports)."""
    p = load_pools()

    def n(key: str) -> int:
        return len(p.get(key, []))

    adv_outcomes = sum(n(f"adv.{o}") for o in ("great", "good", "meh", "bad"))
    adventure = (
        n("adv.setting") * n("adv.place") * n("adv.travel") * n("adv.encounter")
        * adv_outcomes * (n("adv.loot") + n("adv.noloot")) * n("adv.ending")
    )
    diary_openers = n("diary.open.good") + n("diary.open.okay") + n("diary.open.bad")
    diary = diary_openers * n("diary.filler") * (n("diary.feel.good") + n("diary.feel.okay") + n("diary.feel.bad")) * n("diary.close")
    for _counter, key in _DIARY_COUNTERS[:6]:
        diary *= max(1, n(key))
    return {
        "adventure_stories": adventure,
        "diary_entries": diary,
        "trinkets": n("trinket.adj") * n("trinket.noun"),
        "rival_names": n("rival.prefix") * n("rival.suffix"),
        "catchphrases": n("catch.a") * n("catch.b"),
    }
