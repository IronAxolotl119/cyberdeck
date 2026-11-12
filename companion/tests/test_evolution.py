"""Evolution gates, branch rules, and player-chosen form changes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pet.creature import Creature
from pet.evolution import (
    DEFAULT_EVOLUTION_GATES,
    FINAL_STAGE,
    STAGES,
    apply_evolution_config,
    choose_evolution,
    choose_next_form,
    current_gates,
    current_gates_for_species,
    evolution_choices,
    evolution_status,
    get_species,
)
from pet.stats import Stats


def ready_stats(species_id: str, **overrides) -> Stats:
    """A sheet that meets this form's current time gates."""
    gates = current_gates_for_species(species_id)
    data = dict(
        age=float(gates["min_age"]),
        level=int(gates["min_level"]),
        xp=int(gates["min_xp"]),
        hunger=80,
        energy=80,
        mood=70,
    )
    data.update(overrides)
    return Stats(**data)


def _wavling_ready(*, care_mistakes: int, mood: float, attack: int = 5) -> Creature:
    return Creature(
        name="Branch",
        species_id="wavling",
        stats=ready_stats("wavling", care_mistakes=care_mistakes, mood=mood, attack=attack),
        known_move_ids=["chirp"],
    )


class EvolutionTableTests(unittest.TestCase):
    def setUp(self) -> None:
        apply_evolution_config(speed=1.0)
        self.addCleanup(lambda: apply_evolution_config(speed=1.0))

    def test_default_gates_are_documented_and_grow(self) -> None:
        expected = {
            "baby": {"min_age": 36.0, "min_level": 4, "min_xp": 40},
            "in-training": {"min_age": 100.0, "min_level": 8, "min_xp": 140},
            "rookie": {"min_age": 220.0, "min_level": 13, "min_xp": 320},
            "champion": {"min_age": 400.0, "min_level": 19, "min_xp": 600},
            "ultimate": {"min_age": 620.0, "min_level": 26, "min_xp": 960},
        }
        self.assertEqual(DEFAULT_EVOLUTION_GATES, expected)
        ages = [DEFAULT_EVOLUTION_GATES[s]["min_age"] for s in ("baby", "in-training", "rookie", "champion", "ultimate")]
        self.assertEqual(ages, sorted(ages))

    def test_chirplet_evolves_to_wavling_when_thresholds_are_met(self) -> None:
        stats = ready_stats("chirplet")
        species = choose_evolution("chirplet", stats)
        self.assertIsNotNone(species)
        self.assertEqual(species.id, "wavling")

    def test_chirplet_does_not_evolve_too_early(self) -> None:
        stats = Stats(age=3.0, level=1, xp=0)
        self.assertIsNone(choose_evolution("chirplet", stats))

    def test_tick_never_auto_evolves(self) -> None:
        pet = Creature(name="Wait", stats=ready_stats("chirplet"), known_move_ids=["chirp"])
        events = pet.tick(dt=0.01)
        self.assertEqual(pet.species_id, "chirplet")
        self.assertTrue(pet.evolution_status()["ready"])
        self.assertTrue(any("ready to evolve" in line for line in events))

    def test_well_cared_for_wavling_becomes_aethling_when_chosen(self) -> None:
        pet = _wavling_ready(care_mistakes=1, mood=70)
        self.assertEqual(choose_evolution(pet.species_id, pet.stats).id, "aethling")
        pet.tick(dt=0.01)
        self.assertEqual(pet.species_id, "wavling")
        result = pet.evolve_to("aethling")
        self.assertTrue(result["accepted"])
        self.assertEqual(pet.species_id, "aethling")
        self.assertEqual(pet.species.stage, "rookie")
        self.assertTrue(any("Aethling" in line for line in result["events"]))
        aethling = get_species("aethling")
        self.assertGreaterEqual(pet.stats.defense, 5 + aethling.bonus_defense)

    def test_neglected_wavling_becomes_staticore_when_chosen(self) -> None:
        pet = _wavling_ready(care_mistakes=5, mood=20)
        self.assertEqual(choose_evolution(pet.species_id, pet.stats).id, "staticore")
        result = pet.evolve_to("staticore")
        self.assertTrue(result["accepted"])
        self.assertEqual(pet.species_id, "staticore")
        staticore = get_species("staticore")
        self.assertGreaterEqual(pet.stats.attack, 5 + staticore.bonus_attack)

    def test_locked_branch_is_refused(self) -> None:
        pet = _wavling_ready(care_mistakes=5, mood=20)
        result = pet.evolve_to("aethling")
        self.assertFalse(result["accepted"])
        self.assertIn("not available", result["reason"])
        self.assertEqual(pet.species_id, "wavling")

    def test_unknown_species_is_flagged(self) -> None:
        pet = _wavling_ready(care_mistakes=0, mood=80)
        result = pet.evolve_to("agumon")
        self.assertFalse(result["accepted"])
        self.assertTrue(result["unknown"])

    def test_not_ready_is_refused(self) -> None:
        pet = Creature(name="Young", species_id="wavling", stats=Stats(age=1, level=1, xp=0, mood=80))
        result = pet.evolve_to("aethling")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "not ready to evolve yet")

    def test_low_mood_alone_takes_the_staticore_branch(self) -> None:
        pet = _wavling_ready(care_mistakes=0, mood=20)
        self.assertEqual(choose_evolution(pet.species_id, pet.stats).id, "staticore")

    def test_young_rookies_do_not_evolve_naturally(self) -> None:
        stats = Stats(age=30, level=4, xp=50, mood=80)
        self.assertIsNone(choose_evolution("aethling", stats))
        self.assertIsNone(choose_evolution("staticore", stats))
        self.assertEqual(choose_next_form("aethling", stats).id, "luminode")
        self.assertEqual(choose_next_form("staticore", stats).id, "voltshard")

    def test_cared_for_aethling_becomes_luminode(self) -> None:
        stats = ready_stats("aethling", mood=80)
        self.assertEqual(choose_evolution("aethling", stats).id, "luminode")
        pet = Creature(name="Care", species_id="aethling", stats=stats, known_move_ids=["chirp"])
        result = pet.evolve_to("luminode")
        self.assertTrue(result["accepted"])
        self.assertEqual(pet.species_id, "luminode")
        self.assertEqual(pet.species.stage, "champion")

    def test_discovery_forks_aethling_to_hexwave(self) -> None:
        stats = ready_stats("aethling", mood=80)
        self.assertEqual(choose_evolution("aethling", stats, discovered=3).id, "hexwave")
        self.assertEqual(choose_next_form("aethling", stats, discovered=3).id, "hexwave")
        self.assertEqual(choose_next_form("aethling", stats, discovered=2).id, "luminode")
        choices = evolution_choices("aethling", stats, discovered=2)
        hexwave = next(c for c in choices if c["species_id"] == "hexwave")
        self.assertFalse(hexwave["available"])
        self.assertIn("3+", hexwave["hint"])

    def test_discovery_forks_staticore_to_jammeron(self) -> None:
        stats = ready_stats("staticore", mood=20, care_mistakes=5)
        self.assertEqual(choose_evolution("staticore", stats).id, "voltshard")
        self.assertEqual(choose_evolution("staticore", stats, discovered=3).id, "jammeron")

    def test_later_stages_follow_each_fork_to_mega(self) -> None:
        path = [
            ("luminode", "aurorion", "ultimate"),
            ("aurorion", "aethernova", "mega"),
            ("hexwave", "resonarch", "ultimate"),
            ("resonarch", "signumega", "mega"),
            ("voltshard", "stormbit", "ultimate"),
            ("stormbit", "nullstorm", "mega"),
            ("jammeron", "blackout", "ultimate"),
            ("blackout", "voidstatic", "mega"),
        ]
        for current, nxt, stage in path:
            chosen = choose_evolution(current, ready_stats(current, mood=80))
            self.assertIsNotNone(chosen, msg=current)
            self.assertEqual(chosen.id, nxt)
            self.assertEqual(chosen.stage, stage)

    def test_megas_have_no_further_evolution(self) -> None:
        stats = Stats(age=999, level=99, xp=9999, mood=100)
        for species_id in ("aethernova", "signumega", "nullstorm", "voidstatic"):
            self.assertEqual(get_species(species_id).stage, FINAL_STAGE)
            self.assertIsNone(choose_evolution(species_id, stats))
            self.assertIsNone(choose_next_form(species_id, stats))
            self.assertEqual(evolution_status(species_id, stats)["choices"], [])

    def test_every_species_uses_a_known_stage(self) -> None:
        from pet.evolution import SPECIES

        for species in SPECIES.values():
            self.assertIn(species.stage, STAGES)

    def test_evolution_speed_scales_gates(self) -> None:
        apply_evolution_config(speed=2.0)
        baby = current_gates("baby")
        self.assertAlmostEqual(baby["min_age"], 18.0)
        self.assertEqual(baby["min_level"], 2)
        self.assertEqual(baby["min_xp"], 20)
        apply_evolution_config(speed=0.5)
        baby = current_gates("baby")
        self.assertAlmostEqual(baby["min_age"], 72.0)
        self.assertEqual(baby["min_level"], 8)
        self.assertEqual(baby["min_xp"], 80)

    def test_evolution_json_overlays_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evolution.json"
            path.write_text(json.dumps({"baby": {"min_age": 10, "min_level": 3, "min_xp": 15}}), encoding="utf-8")
            apply_evolution_config(speed=1.0, path=str(path))
            baby = current_gates("baby")
            self.assertAlmostEqual(baby["min_age"], 10.0)
            self.assertEqual(baby["min_level"], 3)
            self.assertEqual(baby["min_xp"], 15)
            # Unmentioned stages stay on the defaults.
            self.assertAlmostEqual(current_gates("rookie")["min_age"], 220.0)

    def test_evo_chip_makes_ready_without_changing_form(self) -> None:
        pet = Creature(name="Chip", stats=Stats(age=1, level=1, xp=0, mood=80), known_move_ids=["chirp"])
        lines = pet.catalyze_evolution()
        self.assertEqual(pet.species_id, "chirplet")
        status = pet.evolution_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["progress"], 1.0)
        self.assertTrue(any("ready to choose" in line or "ready to evolve" in line for line in lines))
        notices = [kind for kind, _ in pet.take_evolution_notices()]
        self.assertEqual(notices, ["evolve_soon", "evolve_choice"])

    def test_evolve_soon_then_choice_on_natural_ready(self) -> None:
        pet = Creature(name="Near", stats=Stats(age=1, level=1, xp=0, hunger=80, energy=80, mood=80))
        gates = current_gates("baby")
        # Just under the 90% window: no soon event yet.
        pet.stats.age = gates["min_age"] * 0.5
        pet.stats.level = int(gates["min_level"])
        pet.stats.xp = int(gates["min_xp"])
        pet.tick(0.01)
        self.assertEqual(pet.take_evolution_notices(), [])
        pet.stats.age = gates["min_age"] * 0.91
        pet.tick(0.01)
        kinds = [k for k, _ in pet.take_evolution_notices()]
        self.assertEqual(kinds, ["evolve_soon"])
        pet.stats.age = gates["min_age"]
        pet.tick(0.01)
        kinds = [k for k, _ in pet.take_evolution_notices()]
        self.assertEqual(kinds, ["evolve_choice"])

    def test_first_available_choice_helper(self) -> None:
        pet = _wavling_ready(care_mistakes=1, mood=70)
        self.assertEqual(pet.first_available_choice(), "aethling")
        young = Creature(species_id="wavling", stats=Stats(age=1, level=1, xp=0, mood=80))
        self.assertIsNone(young.first_available_choice())


if __name__ == "__main__":
    unittest.main()
