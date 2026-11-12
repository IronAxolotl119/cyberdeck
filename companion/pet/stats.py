"""Vital statistics for a creature.

Every number the pet "has" lives in the Stats dataclass so the rest of the
program can read and write one consistent bundle of values. Meters such as
hunger, energy, and mood are clamped to 0–100 so a bug elsewhere cannot push
them into nonsense ranges.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


# Hunger, energy, and mood are "meters" that fill and drain over time.
METER_MIN = 0.0
METER_MAX = 100.0


def _clamp(value: float, lo: float, hi: float) -> float:
    """Return value boxed into the inclusive range [lo, hi]."""
    return max(lo, min(hi, value))


@dataclass
class Stats:
    """All of a creature's changeable numbers.

    Combat stats (hp, attack, defense, speed) grow through training and
    evolution. The three meters (hunger, energy, mood) change every tick and
    are what the caretaker actions (feed, train, rest) are for. Age is
    measured in simulated hours. XP raises level; too many care mistakes
    (leaving the pet starving or exhausted) steers evolution toward the
    harsher combat line (Staticore and what follows).
    """

    hp: int = 20
    max_hp: int = 20
    attack: int = 5
    defense: int = 5
    speed: int = 5
    hunger: float = 80.0  # 0 = starving, 100 = stuffed
    energy: float = 80.0  # 0 = exhausted, 100 = fully rested
    mood: float = 70.0  # 0 = miserable, 100 = delighted
    age: float = 0.0  # simulated hours of life
    xp: int = 0
    care_mistakes: int = 0
    level: int = 1

    def clamp(self) -> None:
        """Keep every field inside a legal range. Call after any change."""
        if self.max_hp < 1:
            self.max_hp = 1
        self.hp = int(_clamp(self.hp, 0, self.max_hp))
        self.attack = int(max(1, self.attack))
        self.defense = int(max(1, self.defense))
        self.speed = int(max(1, self.speed))
        self.hunger = _clamp(float(self.hunger), METER_MIN, METER_MAX)
        self.energy = _clamp(float(self.energy), METER_MIN, METER_MAX)
        self.mood = _clamp(float(self.mood), METER_MIN, METER_MAX)
        self.age = max(0.0, float(self.age))
        self.xp = int(max(0, self.xp))
        self.care_mistakes = int(max(0, self.care_mistakes))
        self.level = int(max(1, self.level))

    def to_dict(self) -> dict:
        """Plain dict suitable for JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Stats:
        """Rebuild a Stats object, ignoring unknown keys and filling defaults."""
        allowed = {item.name for item in fields(cls)}
        cleaned = {key: value for key, value in data.items() if key in allowed}
        stats = cls(**cleaned)
        stats.clamp()
        return stats
