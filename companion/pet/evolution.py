"""Data-driven species and evolution rules.

The evolution line is original to this project (no official Digimon names):

    Chirplet  (baby)
        -> Wavling  (in-training)
            -> Aethling   (rookie, well-cared-for / balanced path)
            -> Staticore  (rookie, neglected or combat-hardened path)

Rules live in EVOLUTION_TABLE rather than in a long chain of if/else, so you
can add a species or tweak a threshold without rewriting Creature. When more
than one rule matches, the one with the higher priority wins.
"""

from __future__ import annotations

from dataclasses import dataclass

from pet.stats import Stats


@dataclass(frozen=True)
class Species:
    """One form the creature can take."""

    id: str
    name: str
    stage: str  # "baby", "in-training", or "rookie"
    description: str
    # Flat bonuses applied once, at the moment the pet evolves into this form.
    bonus_hp: int = 0
    bonus_attack: int = 0
    bonus_defense: int = 0
    bonus_speed: int = 0


@dataclass(frozen=True)
class EvolutionRule:
    """A single possible change of species.

    Numeric thresholds are minimums (or maximums, for the care-mistake cap)
    that the pet must already satisfy. None means "this field is not part of
    the condition."
    """

    from_species: str
    to_species: str
    min_age: float = 0.0
    min_level: int = 1
    min_xp: int = 0
    max_care_mistakes: int | None = None
    min_care_mistakes: int | None = None
    min_mood: float | None = None
    min_attack: int | None = None
    priority: int = 0


# All known forms, keyed by species id.
SPECIES: dict[str, Species] = {
    "chirplet": Species(
        id="chirplet",
        name="Chirplet",
        stage="baby",
        description="A thumbnail-sized spark that peeps on an unused frequency.",
    ),
    "wavling": Species(
        id="wavling",
        name="Wavling",
        stage="in-training",
        description="The spark has grown a looping waveform for a tail.",
        bonus_hp=10,
        bonus_attack=2,
        bonus_defense=2,
        bonus_speed=2,
    ),
    "aethling": Species(
        id="aethling",
        name="Aethling",
        stage="rookie",
        description="A calm, clear-signal form that appears when the pet is well cared for.",
        bonus_hp=16,
        bonus_attack=2,
        bonus_defense=4,
        bonus_speed=4,
    ),
    "staticore": Species(
        id="staticore",
        name="Staticore",
        stage="rookie",
        description="A harsh, crackling form shaped by neglect or hard training.",
        bonus_hp=16,
        bonus_attack=6,
        bonus_defense=2,
        bonus_speed=2,
    ),
}

STARTER_SPECIES_ID = "chirplet"

# Baby -> in-training is a single path. In-training -> rookie has two branches:
# the well-cared-for rule is checked first (higher priority); otherwise the
# neglected / combat-hardened rule catches everyone else who is old enough.
EVOLUTION_TABLE: list[EvolutionRule] = [
    EvolutionRule(
        from_species="chirplet",
        to_species="wavling",
        min_age=8.0,
        min_level=2,
        min_xp=10,
        priority=1,
    ),
    EvolutionRule(
        from_species="wavling",
        to_species="aethling",
        min_age=24.0,
        min_level=3,
        max_care_mistakes=2,
        min_mood=50.0,
        priority=2,
    ),
    EvolutionRule(
        from_species="wavling",
        to_species="staticore",
        min_age=24.0,
        min_level=3,
        # No care/mood gate: this is the fallback branch once age and level
        # are met. It also matches early if the pet was clearly neglected or
        # trained hard (those extra fields are left unused so one rule covers
        # every non-Aethling case).
        priority=1,
    ),
]


def get_species(species_id: str) -> Species:
    """Look up a species by id, or raise KeyError."""
    if species_id not in SPECIES:
        raise KeyError(f"Unknown species id: {species_id!r}")
    return SPECIES[species_id]


def _rule_matches(rule: EvolutionRule, stats: Stats) -> bool:
    """True if this creature's current numbers satisfy the rule."""
    if stats.age < rule.min_age:
        return False
    if stats.level < rule.min_level:
        return False
    if stats.xp < rule.min_xp:
        return False
    if rule.max_care_mistakes is not None and stats.care_mistakes > rule.max_care_mistakes:
        return False
    if rule.min_care_mistakes is not None and stats.care_mistakes < rule.min_care_mistakes:
        return False
    if rule.min_mood is not None and stats.mood < rule.min_mood:
        return False
    if rule.min_attack is not None and stats.attack < rule.min_attack:
        return False
    return True


def available_evolutions(species_id: str, stats: Stats) -> list[EvolutionRule]:
    """Every matching rule for this species, highest priority first."""
    matches = [
        rule
        for rule in EVOLUTION_TABLE
        if rule.from_species == species_id and _rule_matches(rule, stats)
    ]
    return sorted(matches, key=lambda rule: rule.priority, reverse=True)


def choose_evolution(species_id: str, stats: Stats) -> Species | None:
    """The species this pet should become right now, or None if none apply."""
    matches = available_evolutions(species_id, stats)
    if not matches:
        return None
    return get_species(matches[0].to_species)
