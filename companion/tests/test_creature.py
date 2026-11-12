"""Tests for tick() and the caretaker actions."""

from __future__ import annotations

import unittest

from pet.creature import ENERGY_DRAIN_PER_HOUR, HUNGER_DRAIN_PER_HOUR, Creature
from pet.stats import Stats


class TickTests(unittest.TestCase):
    def test_tick_advances_age_and_drains_meters(self) -> None:
        pet = Creature(name="Tick")
        start_hunger = pet.stats.hunger
        start_energy = pet.stats.energy
        start_mood = pet.stats.mood

        events = pet.tick(dt=2.0)

        self.assertEqual(pet.stats.age, 2.0)
        self.assertAlmostEqual(pet.stats.hunger, start_hunger - HUNGER_DRAIN_PER_HOUR * 2.0)
        self.assertAlmostEqual(pet.stats.energy, start_energy - ENERGY_DRAIN_PER_HOUR * 2.0)
        # A well-fed, well-rested pet (hunger and energy both start at 80)
        # cheers up a little, so mood should not simply fall by the base drain.
        self.assertNotEqual(pet.stats.mood, start_mood)
        self.assertIsInstance(events, list)

    def test_tick_zero_or_negative_dt_is_a_no_op(self) -> None:
        pet = Creature(name="Still")
        snapshot = pet.to_dict()
        self.assertEqual(pet.tick(dt=0), [])
        self.assertEqual(pet.tick(dt=-3), [])
        self.assertEqual(pet.to_dict(), snapshot)

    def test_starvation_causes_a_care_mistake_and_hp_loss(self) -> None:
        pet = Creature(
            name="Neglected",
            stats=Stats(hunger=0, energy=80, hp=20, max_hp=20),
        )
        events = pet.tick(dt=1.0)
        # Hunger drains below the threshold, so this tick is a mistake.
        self.assertGreaterEqual(pet.stats.care_mistakes, 1)
        self.assertTrue(any("Care mistake" in line for line in events))
        self.assertTrue(any("starving" in line for line in events))
        self.assertLess(pet.stats.hp, 20)


class ActionTests(unittest.TestCase):
    def test_feed_raises_hunger(self) -> None:
        pet = Creature(name="Hungry", stats=Stats(hunger=20, xp=0))
        events = pet.feed()
        self.assertGreater(pet.stats.hunger, 20)
        self.assertTrue(any("eats" in line for line in events))
        self.assertGreaterEqual(pet.stats.xp, 1)

    def test_rest_raises_energy(self) -> None:
        pet = Creature(name="Tired", stats=Stats(energy=10))
        events = pet.rest()
        self.assertGreater(pet.stats.energy, 10)
        self.assertTrue(any("rests" in line for line in events))

    def test_train_raises_a_stat_and_costs_energy(self) -> None:
        pet = Creature(name="Pupil", stats=Stats(attack=5, energy=80, hunger=80))
        events = pet.train("attack")
        self.assertEqual(pet.stats.attack, 6)
        self.assertLess(pet.stats.energy, 80)
        self.assertTrue(any("trains attack" in line for line in events))

    def test_train_refuses_when_exhausted(self) -> None:
        pet = Creature(name="Wiped", stats=Stats(attack=5, energy=1))
        events = pet.train("attack")
        self.assertEqual(pet.stats.attack, 5)
        self.assertTrue(any("too tired" in line for line in events))

    def test_train_rejects_unknown_stat(self) -> None:
        pet = Creature()
        with self.assertRaises(ValueError):
            pet.train("luck")


if __name__ == "__main__":
    unittest.main()
