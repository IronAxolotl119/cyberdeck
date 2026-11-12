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
    hint: str = ""


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

# ---------------------------------------------------------------------------
# Time gates (age / level / XP), keyed by the stage the pet is evolving FROM.
# These are slower than the early demo values and grow at each stage.
# You can overlay a project-root evolution.json with the same shape, and
# scale every number with --evolution-speed (1.0 = these defaults;
# 2.0 = twice as fast, gates halved; 0.5 = half speed, gates doubled).
# ---------------------------------------------------------------------------
DEFAULT_EVOLUTION_GATES: dict[str, dict[str, float]] = {
    "baby": {"min_age": 36.0, "min_level": 4, "min_xp": 40},
    "in-training": {"min_age": 100.0, "min_level": 8, "min_xp": 140},
    "rookie": {"min_age": 220.0, "min_level": 13, "min_xp": 320},
    "champion": {"min_age": 400.0, "min_level": 19, "min_xp": 600},
    "ultimate": {"min_age": 620.0, "min_level": 26, "min_xp": 960},
}

# How close (0–1) counts as "about to evolve" for the evolve_soon event.
EVOLVE_SOON_PROGRESS = 0.90

# Branch rules only: which form, under what care/stat/discovery conditions.
# Time gates are applied from DEFAULT_EVOLUTION_GATES when the table is built.
_BRANCHES: list[dict] = [
    {
        "from_species": "chirplet",
        "to_species": "wavling",
        "priority": 1,
        "hint": "the next form along the line",
    },
    {
        "from_species": "wavling",
        "to_species": "aethling",
        "max_care_mistakes": 2,
        "min_mood": 50.0,
        "priority": 2,
        "hint": "needs good care (mood 50+, at most 2 care mistakes)",
    },
    {
        "from_species": "wavling",
        "to_species": "staticore",
        "priority": 1,
        "hint": "the harder path (low mood or many care mistakes)",
    },
    {
        "from_species": "aethling",
        "to_species": "luminode",
        "priority": 1,
        "hint": "the default care path",
    },
    {
        "from_species": "aethling",
        "to_species": "hexwave",
        "min_discoveries": DISCOVERY_BRANCH,
        "priority": 2,
        "hint": "needs 3+ unique discoveries",
    },
    {
        "from_species": "staticore",
        "to_species": "voltshard",
        "priority": 1,
        "hint": "the default combat path",
    },
    {
        "from_species": "staticore",
        "to_species": "jammeron",
        "min_discoveries": DISCOVERY_BRANCH,
        "priority": 2,
        "hint": "needs 3+ unique discoveries",
    },
    {"from_species": "luminode", "to_species": "aurorion", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "hexwave", "to_species": "resonarch", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "voltshard", "to_species": "stormbit", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "jammeron", "to_species": "blackout", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "aurorion", "to_species": "aethernova", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "resonarch", "to_species": "signumega", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "stormbit", "to_species": "nullstorm", "priority": 1, "hint": "the next form along this line"},
    {"from_species": "blackout", "to_species": "voidstatic", "priority": 1, "hint": "the next form along this line"},
]

_evolution_speed: float = 1.0
_loaded_gates: dict[str, dict[str, float]] = {k: dict(v) for k, v in DEFAULT_EVOLUTION_GATES.items()}
EVOLUTION_TABLE: list[EvolutionRule] = []


def evolution_speed() -> float:
    return _evolution_speed


def current_gates(stage: str) -> dict[str, float]:
    """Age / level / XP thresholds for this stage, after speed scaling."""
    base = _loaded_gates.get(stage) or DEFAULT_EVOLUTION_GATES.get(stage) or {
        "min_age": 36.0,
        "min_level": 4,
        "min_xp": 40,
    }
    speed = _evolution_speed if _evolution_speed > 0 else 1.0
    return {
        "min_age": max(1.0, float(base["min_age"]) / speed),
        "min_level": max(2, int(round(float(base["min_level"]) / speed))),
        "min_xp": max(0, int(round(float(base["min_xp"]) / speed))),
    }


def current_gates_for_species(species_id: str) -> dict[str, float]:
    stage = get_species(species_id).stage if species_id in SPECIES else "baby"
    return current_gates(stage)


def _rebuild_table() -> None:
    """Rebuild EVOLUTION_TABLE from branch rules + scaled time gates."""
    table: list[EvolutionRule] = []
    for spec in _BRANCHES:
        from_id = spec["from_species"]
        stage = SPECIES[from_id].stage
        gates = current_gates(stage)
        table.append(
            EvolutionRule(
                from_species=from_id,
                to_species=spec["to_species"],
                min_age=float(gates["min_age"]),
                min_level=int(gates["min_level"]),
                min_xp=int(gates["min_xp"]),
                max_care_mistakes=spec.get("max_care_mistakes"),
                min_care_mistakes=spec.get("min_care_mistakes"),
                min_mood=spec.get("min_mood"),
                min_attack=spec.get("min_attack"),
                min_discoveries=spec.get("min_discoveries"),
                priority=int(spec.get("priority", 1)),
                hint=str(spec.get("hint") or ""),
            )
        )
    EVOLUTION_TABLE.clear()
    EVOLUTION_TABLE.extend(table)


def apply_evolution_config(speed: float = 1.0, path: str | None = None) -> None:
    """Install a speed multiplier and optional evolution.json, then rebuild the table.

    Speed must be > 0. A file, if given (or the default project-root
    evolution.json when it exists), overlays DEFAULT_EVOLUTION_GATES.
    """
    global _evolution_speed, _loaded_gates
    if speed <= 0:
        raise ValueError("evolution speed must be > 0")
    _evolution_speed = float(speed)
    gates = {k: dict(v) for k, v in DEFAULT_EVOLUTION_GATES.items()}
    import json
    from pathlib import Path

    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "evolution.json")
    for candidate in candidates:
        if candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for stage, values in data.items():
                    if stage in gates and isinstance(values, dict):
                        for key in ("min_age", "min_level", "min_xp"):
                            if key in values:
                                gates[stage][key] = float(values[key])
            break
    _loaded_gates = gates
    _rebuild_table()


def get_species(species_id: str) -> Species:
    """Look up a species by id, or raise KeyError."""
    if species_id not in SPECIES:
        raise KeyError(f"Unknown species id: {species_id!r}")
    return SPECIES[species_id]


def resolve_species(name_or_id: str) -> Species | None:
    """Find a species by id or display name (case-insensitive)."""
    key = name_or_id.strip().lower()
    if not key:
        return None
    if key in SPECIES:
        return SPECIES[key]
    for species in SPECIES.values():
        if species.name.lower() == key:
            return species
    return None


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


def time_gates_met(species_id: str, stats: Stats) -> bool:
    """True when this form's age/level/XP gates (after speed scaling) are met."""
    rules = rules_from(species_id)
    if not rules:
        return False
    return _time_gates_match(rules[0], stats)


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
    """True if time gates and branch gates both pass."""
    return _time_gates_match(rule, stats) and _branch_gates_match(rule, stats, discovered)


def _best(rules: list[EvolutionRule]) -> EvolutionRule | None:
    if not rules:
        return None
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)[0]


def available_evolutions(species_id: str, stats: Stats, discovered: int = 0) -> list[EvolutionRule]:
    """Every matching rule for this species (time + branch), highest priority first."""
    matches = [
        rule
        for rule in rules_from(species_id)
        if _rule_matches(rule, stats, discovered)
    ]
    return sorted(matches, key=lambda rule: rule.priority, reverse=True)


def choose_evolution(species_id: str, stats: Stats, discovered: int = 0) -> Species | None:
    """The species this pet *could* naturally become right now, or None.

    The pet never auto-evolves; this is used for readiness / hints only.
    """
    matches = available_evolutions(species_id, stats, discovered)
    if not matches:
        return None
    return get_species(matches[0].to_species)


def choose_next_form(species_id: str, stats: Stats, discovered: int = 0) -> Species | None:
    """The form an auto-pick (or 'preferred next') should take, ignoring time gates."""
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


def _rule_progress(rule: EvolutionRule, stats: Stats) -> tuple[float, float | None]:
    """(progress 0-1, remaining age-hours if the age gate still applies)."""
    ratios: list[float] = []
    eta_hours: float | None = None
    if rule.min_age > 0:
        ratios.append(stats.age / rule.min_age)
        remaining = rule.min_age - stats.age
        eta_hours = remaining if remaining > 0 else 0.0
    if rule.min_level > 1:
        ratios.append(stats.level / rule.min_level)
    if rule.min_xp > 0:
        ratios.append(stats.xp / rule.min_xp)
    if not ratios:
        return 1.0, 0.0
    progress = min(1.0, min(ratios))
    return progress, eta_hours


def evolution_choices(
    species_id: str,
    stats: Stats,
    discovered: int = 0,
    *,
    ready_override: bool = False,
) -> list[dict]:
    """Every next-stage branch, with availability and a short hint."""
    choices: list[dict] = []
    for rule in rules_from(species_id):
        dest = get_species(rule.to_species)
        choices.append(
            {
                "species": dest.name,
                "species_id": dest.id,
                "stage": dest.stage,
                "hint": rule.hint or "the next form along the line",
                "available": _branch_gates_match(rule, stats, discovered),
            }
        )
    return choices


def evolution_status(
    species_id: str,
    stats: Stats,
    discovered: int = 0,
    *,
    ready_override: bool = False,
) -> dict:
    """Hints for pet.status.evolution.

    progress is measured against the time gates of this stage. ready is True
    when those gates pass, or when an evolve-chip has skipped them.
    choices lists every branch for the next stage (available follows care /
    stat / discovery rules, not the time gates).
    """
    current = get_species(species_id) if species_id in SPECIES else None
    choices = evolution_choices(species_id, stats, discovered, ready_override=ready_override)
    hint: dict = {
        "next_stage": None,
        "progress": 1.0 if current is not None and current.stage == FINAL_STAGE else 0.0,
        "ready": False,
        "choices": choices,
        "to_candidates": [c["species"] for c in choices if c["available"]],
        "eta_hours": None,
        "from": current.name if current is not None else species_id,
    }
    rules = rules_from(species_id)
    if not rules or (current is not None and current.stage == FINAL_STAGE):
        return hint
    progress, eta_hours = _rule_progress(rules[0], stats)
    ready = bool(ready_override) or time_gates_met(species_id, stats)
    next_stage = get_species(rules[0].to_species).stage
    hint.update(
        {
            "next_stage": next_stage,
            "progress": 1.0 if ready_override else round(progress, 3),
            "ready": ready,
            "eta_hours": 0.0 if ready_override else (None if eta_hours is None else round(max(0.0, eta_hours), 2)),
        }
    )
    return hint


# Build the default table at import time (speed 1.0, optional evolution.json).
apply_evolution_config(speed=1.0)
