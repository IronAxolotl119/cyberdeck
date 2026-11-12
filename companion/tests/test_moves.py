"""Tests for learning moves from levels and from discovery events."""

from __future__ import annotations

import unittest

from discovery.base import Signal
from discovery.effects import apply_signals
from pet.creature import Creature, xp_needed_for_level
from pet.moves import get_move, moves_for_level
from pet.stats import Stats


class LevelMoveTests(unittest.TestCase):
    def test_level_one_starts_with_chirp(self) -> None:
        pet = Creature(name="Hatchling")
        self.assertEqual(pet.known_move_ids, ["chirp"])
        self.assertEqual(pet.known_moves()[0].name, "Chirp")

    def test_reaching_level_three_unlocks_static_snap(self) -> None:
        # Give just enough XP to land on level 3 (30 XP total).
        pet = Creature(name="Student", stats=Stats(xp=0, level=1))
        events = pet.add_xp(xp_needed_for_level(3))
        self.assertEqual(pet.stats.level, 3)
        self.assertIn("static_snap", pet.known_move_ids)
        self.assertTrue(any("Static Snap" in line for line in events))
        self.assertTrue(any("level 3" in line for line in events))

    def test_discovery_only_moves_are_not_in_the_level_table(self) -> None:
        ids = {move.id for move in moves_for_level(99)}
        self.assertIn("chirp", ids)
        self.assertIn("bright_arc", ids)
        self.assertNotIn("stray_handshake", ids)
        self.assertNotIn("packet_burst", ids)
        self.assertIsNone(get_move("stray_handshake").learn_level)

    def test_learn_move_is_idempotent(self) -> None:
        pet = Creature(name="Repeat")
        first = pet.learn_move("stray_handshake")
        second = pet.learn_move("stray_handshake")
        self.assertTrue(first)
        self.assertEqual(second, [])
        self.assertEqual(pet.known_move_ids.count("stray_handshake"), 1)


class DiscoveryMoveTests(unittest.TestCase):
    def test_mystery_beacon_teaches_stray_handshake(self) -> None:
        pet = Creature(name="Listener")
        signal = Signal(kind="bt", id="bt:mystery-beacon", name="??-BEACON", rssi=-40)
        events = apply_signals(pet, [signal])
        self.assertIn("stray_handshake", pet.known_move_ids)
        self.assertTrue(any("Stray Handshake" in line for line in events))
        self.assertIn("bt:mystery-beacon", pet.discovered_ids)

    def test_strong_new_wifi_grants_xp(self) -> None:
        pet = Creature(name="Scout", stats=Stats(xp=0, level=1))
        signal = Signal(kind="wifi", id="wifi:roof-mesh", name="RoofMesh", rssi=-32)
        events = apply_signals(pet, [signal])
        self.assertGreater(pet.stats.xp, 0)
        self.assertTrue(any("strong network" in line for line in events))

    def test_the_same_signal_is_not_applied_twice(self) -> None:
        pet = Creature(name="Once", stats=Stats(xp=0, level=1))
        signal = Signal(kind="wifi", id="wifi:lab-net", name="LAB-NET", rssi=-40)
        apply_signals(pet, [signal])
        xp_after_first = pet.stats.xp
        events = apply_signals(pet, [signal])
        self.assertEqual(pet.stats.xp, xp_after_first)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
