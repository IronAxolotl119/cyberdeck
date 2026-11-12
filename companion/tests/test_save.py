"""Save/load round trip, including discovery memory and learned moves."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from discovery.simulated import SimulatedScanner
from pet.creature import Creature
from pet.stats import Stats
from save import load_creature, save_creature


class SaveLoadTests(unittest.TestCase):
    def test_round_trip_preserves_the_full_snapshot(self) -> None:
        pet = Creature(
            name="Archive",
            species_id="aethling",
            stats=Stats(
                hp=40,
                max_hp=46,
                attack=12,
                defense=14,
                speed=15,
                hunger=65.5,
                energy=50.25,
                mood=88.0,
                age=50.0,
                xp=105,
                care_mistakes=1,
                level=5,
            ),
            known_move_ids=["chirp", "static_snap", "stray_handshake"],
            discovered_ids={"wifi:cafe-lumen", "bt:mystery-beacon"},
            asleep=True,
            evolution_ready_override=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pet.save.json"
            written = save_creature(pet, path)
            self.assertTrue(written.is_file())
            loaded = load_creature(written)

        self.assertEqual(loaded.to_dict(), pet.to_dict())
        self.assertEqual(loaded.name, "Archive")
        self.assertEqual(loaded.species_id, "aethling")
        self.assertEqual(loaded.stats.level, 5)
        self.assertEqual(loaded.known_move_ids, pet.known_move_ids)
        self.assertEqual(loaded.discovered_ids, pet.discovered_ids)
        self.assertTrue(loaded.asleep)
        self.assertTrue(loaded.evolution_ready_override)

    def test_round_trip_via_creature_to_dict_alone(self) -> None:
        pet = Creature(name="Bare")
        pet.tick(dt=3.0)
        clone = Creature.from_dict(pet.to_dict())
        self.assertEqual(clone.to_dict(), pet.to_dict())

    def test_load_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_creature("/tmp/cyberdeck-pet-does-not-exist-xyz.json")

    def test_seeded_scanner_is_deterministic(self) -> None:
        first = SimulatedScanner(seed=42).scan()
        second = SimulatedScanner(seed=42).scan()
        self.assertEqual(first, second)
        other = SimulatedScanner(seed=7).scan()
        self.assertNotEqual(first, other)


if __name__ == "__main__":
    unittest.main()
