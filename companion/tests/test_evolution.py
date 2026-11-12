"""Tests for the two-way rookie branch and the earlier baby evolution."""

from __future__ import annotations

import unittest

from pet.creature import Creature
from pet.evolution import choose_evolution, get_species
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

    def test_rookies_have_no_further_evolution(self) -> None:
        stats = Stats(age=100, level=20, xp=999, mood=100)
        self.assertIsNone(choose_evolution("aethling", stats))
        self.assertIsNone(choose_evolution("staticore", stats))


if __name__ == "__main__":
    unittest.main()
