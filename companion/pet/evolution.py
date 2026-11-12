"""Data-driven species and evolution rules.

The evolution line is original to this project (no official Digimon names).
Care, combat stats, and how many unique signals the pet has discovered all
steer the branches:

    Chirplet  (baby)
        -> Wavling  (in-training)
            -> Aethling   (rookie, well-cared-for / balanced path)
                -> Luminode   (champion, default care path)
                    -> Aurorion   (ultimate)
                        -> Aethernova (mega)
                -> Hexwave    (champion, well-travelled / many discoveries)
                    -> Resonarch  (ultimate)
                        -> Signumega  (mega)
            -> Staticore  (rookie, neglected or combat-hardened path)
                -> Voltshard  (champion, default combat path)
                    -> Stormbit   (ultimate)
                        -> Nullstorm  (mega)
                -> Jammeron   (champion, many discoveries on the hard path)
                    -> Blackout   (ultimate)
                        -> Voidstatic (mega)

Rules live in EVOLUTION_TABLE rather than in a long chain of if/else, so you
can add a species or tweak a threshold without rewriting Creature. When more
than one rule matches, the one with the higher priority wins. An evolve gift
follows the same branches but skips the age/level/XP gates; it is refused
only at mega (or any form with no outgoing rule).
"""

from __future__ import annotations

from dataclasses import dataclass

from pet.stats import Stats


# Lowest to highest. We start at baby (the main deck also lists an unused "egg").
STAGES: tuple[str, ...] = (
    "baby",
    "in-training",
    "rookie",
    "champion",
    "ultimate",
    "mega",
)
FINAL_STAGE = "mega"

# How many unique discovered signals it takes to take a discovery branch.
DISCOVERY_BRANCH = 3


@dataclass(frozen=True)
class Species:
    """One form the creature can take."""

    id: str
    name: str
    stage: str  # one of STAGES
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
    the condition." Age, level, and XP are *time gates* used by natural
    evolution. Care, mood, attack, and discoveries are *branch gates* that
    also steer an evolve gift (which skips the time gates).
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
    min_discoveries: int | None = None
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
    "luminode": Species(
        id="luminode",
        name="Luminode",
        stage="champion",
        description="A bright relay-node form, still gentle, now lighting a whole channel.",
        bonus_hp=20,
        bonus_attack=3,
        bonus_defense=5,
        bonus_speed=5,
    ),
    "hexwave": Species(
        id="hexwave",
        name="Hexwave",
        stage="champion",
        description="A six-phase scanner form, grown from sniffing out many signals.",
        bonus_hp=18,
        bonus_attack=4,
        bonus_defense=3,
        bonus_speed=7,
    ),
    "voltshard": Species(
        id="voltshard",
        name="Voltshard",
        stage="champion",
        description="A jagged voltage shard, all edges and sparks.",
        bonus_hp=20,
        bonus_attack=8,
        bonus_defense=3,
        bonus_speed=3,
    ),
    "jammeron": Species(
        id="jammeron",
        name="Jammeron",
        stage="champion",
        description="A dish-backed jammer, shaped by hard living and a lot of sniffing.",
        bonus_hp=22,
        bonus_attack=5,
        bonus_defense=6,
        bonus_speed=2,
    ),
    "aurorion": Species(
        id="aurorion",
        name="Aurorion",
        stage="ultimate",
        description="An aurora-winged form that drapes the sky in a clear signal.",
        bonus_hp=24,
        bonus_attack=4,
        bonus_defense=6,
        bonus_speed=6,
    ),
    "resonarch": Species(
        id="resonarch",
        name="Resonarch",
        stage="ultimate",
        description="A crowned resonance form that answers every channel at once.",
        bonus_hp=22,
        bonus_attack=5,
        bonus_defense=5,
        bonus_speed=8,
    ),
    "stormbit": Species(
        id="stormbit",
        name="Stormbit",
        stage="ultimate",
        description="A living storm of bits, louder and sharper than Voltshard.",
        bonus_hp=24,
        bonus_attack=10,
        bonus_defense=4,
        bonus_speed=4,
    ),
    "blackout": Species(
        id="blackout",
        name="Blackout",
        stage="ultimate",
        description="An eclipse disc that swallows nearby signals whole.",
        bonus_hp=26,
        bonus_attack=6,
        bonus_defense=8,
        bonus_speed=3,
    ),
    "aethernova": Species(
        id="aethernova",
        name="Aethernova",
        stage="mega",
        description="A nova of clear aether: the care line's last, brightest form.",
        bonus_hp=30,
        bonus_attack=6,
        bonus_defense=8,
        bonus_speed=8,
    ),
    "signumega": Species(
        id="signumega",
        name="Signumega",
        stage="mega",
        description="The master sigil of the airwaves, born from a life of discovery.",
        bonus_hp=28,
        bonus_attack=7,
        bonus_defense=6,
        bonus_speed=10,
    ),
    "nullstorm": Species(
        id="nullstorm",
        name="Nullstorm",
        stage="mega",
        description="A total static storm; the combat line's last, harshest form.",
        bonus_hp=30,
        bonus_attack=14,
        bonus_defense=5,
        bonus_speed=5,
    ),
    "voidstatic": Species(
        id="voidstatic",
        name="Voidstatic",
        stage="mega",
        description="A hole punched through the noise, the jammer line's last form.",
        bonus_hp=32,
        bonus_attack=8,
        bonus_defense=10,
        bonus_speed=4,
    ),
}

STARTER_SPECIES_ID = "chirplet"

# Time gates grow with the later stages. Branch gates (care / mood / attack /
# discoveries) decide *which* form; a rule with no branch gate is the fallback
# used by evolve gifts when no specialist branch matches.
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
        # trained hard.
        priority=1,
    ),
    # Rookie -> champion. Discovery wins when the pet has sniffed out enough.
    EvolutionRule(
        from_species="aethling",
        to_species="luminode",
        min_age=48.0,
        min_level=5,
        priority=1,
    ),
    EvolutionRule(
        from_species="aethling",
        to_species="hexwave",
        min_age=48.0,
        min_level=5,
        min_discoveries=DISCOVERY_BRANCH,
        priority=2,
    ),
    EvolutionRule(
        from_species="staticore",
        to_species="voltshard",
        min_age=48.0,
        min_level=5,
        priority=1,
    ),
    EvolutionRule(
        from_species="staticore",
        to_species="jammeron",
        min_age=48.0,
        min_level=5,
        min_discoveries=DISCOVERY_BRANCH,
        priority=2,
    ),
    # Champion -> ultimate (linear on each fork).
    EvolutionRule(from_species="luminode", to_species="aurorion", min_age=80.0, min_level=8, priority=1),
    EvolutionRule(from_species="hexwave", to_species="resonarch", min_age=80.0, min_level=8, priority=1),
    EvolutionRule(from_species="voltshard", to_species="stormbit", min_age=80.0, min_level=8, priority=1),
    EvolutionRule(from_species="jammeron", to_species="blackout", min_age=80.0, min_level=8, priority=1),
    # Ultimate -> mega.
    EvolutionRule(from_species="aurorion", to_species="aethernova", min_age=120.0, min_level=12, priority=1),
    EvolutionRule(from_species="resonarch", to_species="signumega", min_age=120.0, min_level=12, priority=1),
    EvolutionRule(from_species="stormbit", to_species="nullstorm", min_age=120.0, min_level=12, priority=1),
    EvolutionRule(from_species="blackout", to_species="voidstatic", min_age=120.0, min_level=12, priority=1),
]


def get_species(species_id: str) -> Species:
    """Look up a species by id, or raise KeyError."""
    if species_id not in SPECIES:
        raise KeyError(f"Unknown species id: {species_id!r}")
    return SPECIES[species_id]


def rules_from(species_id: str) -> list[EvolutionRule]:
    """Every outgoing rule for this species, table order."""
    return [rule for rule in EVOLUTION_TABLE if rule.from_species == species_id]


def _time_gates_match(rule: EvolutionRule, stats: Stats) -> bool:
    if stats.age < rule.min_age:
        return False
    if stats.level < rule.min_level:
        return False
    if stats.xp < rule.min_xp:
        return False
    return True


def _branch_gates_match(rule: EvolutionRule, stats: Stats, discovered: int) -> bool:
    if rule.max_care_mistakes is not None and stats.care_mistakes > rule.max_care_mistakes:
        return False
    if rule.min_care_mistakes is not None and stats.care_mistakes < rule.min_care_mistakes:
        return False
    if rule.min_mood is not None and stats.mood < rule.min_mood:
        return False
    if rule.min_attack is not None and stats.attack < rule.min_attack:
        return False
    if rule.min_discoveries is not None and discovered < rule.min_discoveries:
        return False
    return True


def _rule_matches(rule: EvolutionRule, stats: Stats, discovered: int = 0) -> bool:
    """True if time gates and branch gates both pass (natural evolution)."""
    return _time_gates_match(rule, stats) and _branch_gates_match(rule, stats, discovered)


def _best(rules: list[EvolutionRule]) -> EvolutionRule | None:
    if not rules:
        return None
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)[0]


def available_evolutions(species_id: str, stats: Stats, discovered: int = 0) -> list[EvolutionRule]:
    """Every matching natural-evolution rule for this species, highest priority first."""
    matches = [
        rule
        for rule in rules_from(species_id)
        if _rule_matches(rule, stats, discovered)
    ]
    return sorted(matches, key=lambda rule: rule.priority, reverse=True)


def choose_evolution(species_id: str, stats: Stats, discovered: int = 0) -> Species | None:
    """The species this pet should become right now (time gates on), or None."""
    matches = available_evolutions(species_id, stats, discovered)
    if not matches:
        return None
    return get_species(matches[0].to_species)


def choose_next_form(species_id: str, stats: Stats, discovered: int = 0) -> Species | None:
    """The form an evolve gift (or 'what is next?') should pick, ignoring time gates.

    Prefers a fully due natural evolution, then the highest-priority rule
    whose branch gates already match, then a fallback rule with no branch
    gates. Returns None only when this species has no outgoing rule (mega).
    """
    if species_id not in SPECIES:
        return None
    if get_species(species_id).stage == FINAL_STAGE:
        return None
    rules = rules_from(species_id)
    if not rules:
        return None
    due = choose_evolution(species_id, stats, discovered)
    if due is not None:
        return due
    branched = [rule for rule in rules if _branch_gates_match(rule, stats, discovered)]
    chosen = _best(branched)
    if chosen is None:
        fallback = [rule for rule in rules if rule.min_discoveries is None]
        chosen = _best(fallback) or _best(rules)
    if chosen is None:
        return None
    return get_species(chosen.to_species)


def forms_in_evolution_order() -> list[str]:
    """Starter first, then each new form in table order, then any leftovers."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(species_id: str) -> None:
        if species_id not in seen and species_id in SPECIES:
            seen.add(species_id)
            ordered.append(species_id)

    add(STARTER_SPECIES_ID)
    changed = True
    while changed:
        changed = False
        for rule in EVOLUTION_TABLE:
            if rule.from_species in seen and rule.to_species not in seen:
                add(rule.to_species)
                changed = True
    for species_id in SPECIES:
        add(species_id)
    return ordered
