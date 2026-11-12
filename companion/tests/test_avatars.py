"""Text-face avatars: moods, frames, accents, and banners."""

from __future__ import annotations

import unittest

from avatar.faces import (
    FACE_MOODS,
    avatar_payload,
    evolution_banner,
    face_frames,
    face_mood,
    face_text,
    format_face_sheet,
    normalize_mood,
    pet_avatar,
)
from pet.creature import Creature
from pet.stats import Stats


class FaceCatalogTests(unittest.TestCase):
    def test_every_mood_has_two_to_four_frames(self) -> None:
        for mood in FACE_MOODS:
            frames = face_frames(mood, "baby")
            self.assertGreaterEqual(len(frames), 2, msg=mood)
            self.assertLessEqual(len(frames), 4, msg=mood)
            for frame in frames:
                self.assertTrue(1 <= len(frame.splitlines()) <= 3, msg=(mood, frame))

    def test_stage_accents_wrap_the_face(self) -> None:
        baby = face_text("content", "baby", frame=0)
        self.assertTrue(baby.startswith("'") and baby.endswith("'"))
        rookie = face_text("content", "rookie", frame=0)
        self.assertTrue(rookie.startswith("[") and rookie.endswith("]"))
        mega = face_text("content", "mega", frame=0)
        self.assertTrue(mega.startswith("«") and mega.endswith("»"))
        mega_ascii = face_text("content", "mega", frame=0, ascii_mode=True)
        self.assertTrue(mega_ascii.startswith("*") and mega_ascii.endswith("*"))

    def test_aliases_map_onto_face_moods(self) -> None:
        self.assertEqual(normalize_mood("joyful"), "happy")
        self.assertEqual(normalize_mood("playful"), "happy")
        self.assertEqual(normalize_mood("grumpy"), "angry")
        self.assertEqual(normalize_mood("asleep"), "sleeping")
        with self.assertRaises(ValueError):
            normalize_mood("confused")

    def test_live_mood_from_stats(self) -> None:
        happy = Creature(stats=Stats(hunger=80, energy=80, mood=90))
        self.assertEqual(face_mood(happy), "happy")
        sleepy = Creature(stats=Stats(hunger=80, energy=80, mood=70), asleep=True)
        self.assertEqual(face_mood(sleepy), "sleeping")
        fainted = Creature(stats=Stats(hp=0, max_hp=32, hunger=80, energy=80, mood=70))
        self.assertEqual(face_mood(fainted), "fainted")
        hungry = Creature(stats=Stats(hunger=5, energy=80, mood=70))
        self.assertEqual(face_mood(hungry), "hungry")

    def test_payload_shape(self) -> None:
        pet = Creature(stats=Stats(hunger=80, energy=80, mood=90))
        body = avatar_payload(pet, frame=0)
        self.assertEqual(body["style"], "face")
        self.assertEqual(body["mood"], "happy")
        self.assertEqual(body["frame"], 0)
        self.assertIsInstance(body["frames"], list)
        self.assertGreaterEqual(len(body["frames"]), 2)
        self.assertEqual(body["interval_ms"], 450)
        compact = pet_avatar(pet, frame=1)
        self.assertEqual(compact["style"], "face")
        self.assertEqual(compact["text"], body["frames"][1])

    def test_sheet_lists_moods_and_stages(self) -> None:
        sheet = format_face_sheet()
        for mood in FACE_MOODS:
            self.assertIn(mood, sheet)
        self.assertIn("champion", sheet)
        self.assertIn("mega", sheet)

    def test_evolution_banners_are_boxed(self) -> None:
        soon = evolution_banner(
            "evolve_soon",
            {"from": "Chirplet", "to_candidates": ["Wavling"], "eta_hours": 4},
            name="PULSE",
        )
        self.assertIn("close to evolving", soon)
        self.assertIn("Chirplet -> Wavling", soon)
        self.assertTrue(soon.startswith("+"))
        choice = evolution_banner(
            "evolve_choice",
            {
                "from": "Wavling",
                "next_stage": "rookie",
                "choices": [
                    {"species": "Aethling", "available": True, "hint": "needs good care"},
                    {"species": "Staticore", "available": True, "hint": "the harder path"},
                ],
            },
        )
        self.assertIn("ready to evolve", choice)
        self.assertIn("Aethling", choice)
        self.assertIn("pet.evolve", choice)


if __name__ == "__main__":
    unittest.main()
