"""Tests for the two-way rookie branch and the earlier baby evolution."""

from __future__ import annotations

import unittest

from pet.creature import Creature
from pet.evolution import choose_evolution, choose_next_form, get_species, FINAL_STAGE, STAGES
from pet.stats import Stats


def _wavling_ready(*, care_mistakes: int, mood: float, attack: int = 5) -> Creature:
    """A Wavling that is old and experienced enough to go rookie."""
    stats = Stats(
        age=24.0,
        level=3,
        xp=30,
        care_mistakes=care_mistakes,
        mood=mood,
        attack=attack,
        hunger=80,
        energy=80,
    )
    return Creature(
        name="Branch",
        species_id="wavling",
        stats=stats,
        known_move_ids=["chirp"],
    )


class EvolutionTableTests(unittest.TestCase):
    def test_chirplet_evolves_to_wavling_when_thresholds_are_met(self) -> None:
        stats = Stats(age=8.0, level=2, xp=10, hunger=80, energy=80, mood=70)
        species = choose_evolution("chirplet", stats)
        self.assertIsNotNone(species)
        self.assertEqual(species.id, "wavling")

    def test_chirplet_does_not_evolve_too_early(self) -> None:
        stats = Stats(age=3.0, level=1, xp=0)
        self.assertIsNone(choose_evolution("chirplet", stats))

    def test_well_cared_for_wavling_becomes_aethling(self) -> None:
        pet = _wavling_ready(care_mistakes=1, mood=70)
        # choose_evolution should already point at Aethling...
        self.assertEqual(choose_evolution(pet.species_id, pet.stats).id, "aethling")
        # ...and a tick (even a tiny one that does not change much) applies it.
        events = pet._check_evolution()
        self.assertEqual(pet.species_id, "aethling")
        self.assertEqual(pet.species.stage, "rookie")
        self.assertTrue(any("Aethling" in line for line in events))
        # The new form's bonuses should have landed on the sheet.
        aethling = get_species("aethling")
        self.assertGreaterEqual(pet.stats.defense, 5 + aethling.bonus_defense)

    def test_neglected_wavling_becomes_staticore(self) -> None:
        pet = _wavling_ready(care_mistakes=5, mood=20)
        self.assertEqual(choose_evolution(pet.species_id, pet.stats).id, "staticore")
        events = pet._check_evolution()
        self.assertEqual(pet.species_id, "staticore")
        self.assertEqual(pet.species.stage, "rookie")
        self.assertTrue(any("Staticore" in line for line in events))
        staticore = get_species("staticore")
        self.assertGreaterEqual(pet.stats.attack, 5 + staticore.bonus_attack)

    def test_low_mood_alone_takes_the_staticore_branch(self) -> None:
        # Even with few care mistakes, a gloomy Wavling misses the Aethling gate.
        pet = _wavling_ready(care_mistakes=0, mood=20)
        self.assertEqual(choose_evolution(pet.species_id, pet.stats).id, "staticore")

    def test_young_rookies_do_not_evolve_naturally(self) -> None:
        stats = Stats(age=30, level=4, xp=50, mood=80)
        self.assertIsNone(choose_evolution("aethling", stats))
        self.assertIsNone(choose_evolution("staticore", stats))
        # An evolve gift still advances, skipping the time gates.
        self.assertEqual(choose_next_form("aethling", stats).id, "luminode")
        self.assertEqual(choose_next_form("staticore", stats).id, "voltshard")

    def test_cared_for_aethling_becomes_luminode(self) -> None:
        stats = Stats(age=48, level=5, xp=80, mood=80, hunger=80, energy=80)
        self.assertEqual(choose_evolution("aethling", stats).id, "luminode")
        pet = Creature(name="Care", species_id="aethling", stats=stats, known_move_ids=["chirp"])
        events = pet._check_evolution()
        self.assertEqual(pet.species_id, "luminode")
        self.assertEqual(pet.species.stage, "champion")
        self.assertTrue(any("Luminode" in line for line in events))

    def test_discovery_forks_aethling_to_hexwave(self) -> None:
        stats = Stats(age=48, level=5, xp=80, mood=80, hunger=80, energy=80)
        # Same care, but three unique signals take the discovery branch.
        self.assertEqual(choose_evolution("aethling", stats, discovered=3).id, "hexwave")
        self.assertEqual(choose_next_form("aethling", stats, discovered=3).id, "hexwave")
        self.assertEqual(choose_next_form("aethling", stats, discovered=2).id, "luminode")

    def test_discovery_forks_staticore_to_jammeron(self) -> None:
        stats = Stats(age=48, level=5, xp=80, mood=20, care_mistakes=5, hunger=80, energy=80)
        self.assertEqual(choose_evolution("staticore", stats).id, "voltshard")
        self.assertEqual(choose_evolution("staticore", stats, discovered=3).id, "jammeron")

    def test_later_stages_follow_each_fork_to_mega(self) -> None:
        ready = Stats(age=200, level=20, xp=999, mood=80, hunger=80, energy=80)
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
            chosen = choose_evolution(current, ready)
            self.assertIsNotNone(chosen, msg=current)
            self.assertEqual(chosen.id, nxt)
            self.assertEqual(chosen.stage, stage)

    def test_megas_have_no_further_evolution(self) -> None:
        stats = Stats(age=999, level=99, xp=9999, mood=100)
        for species_id in ("aethernova", "signumega", "nullstorm", "voidstatic"):
            self.assertEqual(get_species(species_id).stage, FINAL_STAGE)
            self.assertIsNone(choose_evolution(species_id, stats))
            self.assertIsNone(choose_next_form(species_id, stats))

    def test_every_species_uses_a_known_stage(self) -> None:
        from pet.evolution import SPECIES

        for species in SPECIES.values():
            self.assertIn(species.stage, STAGES)


if __name__ == "__main__":
    unittest.main()
