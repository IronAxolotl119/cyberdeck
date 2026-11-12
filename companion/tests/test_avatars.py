"""Tests for 16x16 sprite grids, the palette, and form lookup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from avatar.renderer import TerminalRenderer, format_catalog, write_sprite_sheets
from avatar.sprites import (
    FORMS,
    MOODS,
    PALETTE,
    SPRITE_SIZE,
    lookup_form,
    mood_for_creature,
    mood_from_meters,
    resolve_mood,
    sprite_grid,
    sprite_pixels,
)
from pet.creature import Creature
from pet.evolution import SPECIES
from pet.stats import Stats


class PaletteTests(unittest.TestCase):
    def test_every_key_is_a_single_character(self) -> None:
        for key in PALETTE:
            self.assertEqual(len(key), 1)

    def test_every_color_is_none_or_rgb_bytes(self) -> None:
        for key, value in PALETTE.items():
            if value is None:
                continue
            self.assertIsInstance(value, tuple, msg=key)
            self.assertEqual(len(value), 3, msg=key)
            for channel in value:
                self.assertIsInstance(channel, int, msg=key)
                self.assertGreaterEqual(channel, 0, msg=key)
                self.assertLessEqual(channel, 255, msg=key)

    def test_transparent_dot(self) -> None:
        self.assertIsNone(PALETTE["."])


class DimensionTests(unittest.TestCase):
    def test_every_form_frame_and_mood_is_16x16(self) -> None:
        self.assertEqual(SPRITE_SIZE, 16)
        for form_id in FORMS:
            for frame in (0, 1):
                for mood in MOODS:
                    grid = sprite_grid(form_id, frame=frame, mood=mood)
                    self.assertEqual(len(grid), SPRITE_SIZE, msg=f"{form_id}/{mood}/{frame}")
                    for row in grid:
                        self.assertEqual(len(row), SPRITE_SIZE, msg=f"{form_id}/{mood}/{frame}")
                    pixels = sprite_pixels(form_id, frame=frame, mood=mood)
                    self.assertEqual(len(pixels), SPRITE_SIZE)
                    for row in pixels:
                        self.assertEqual(len(row), SPRITE_SIZE)
                        for pixel in row:
                            if pixel is not None:
                                self.assertEqual(len(pixel), 3)

    def test_grid_characters_are_all_in_the_palette(self) -> None:
        for form_id, form in FORMS.items():
            for index, frame in enumerate(form["frames"]):
                for row in frame:
                    self.assertTrue(set(row) <= set(PALETTE), msg=f"{form_id} frame {index}")
            for mood, face in form["faces"].items():
                for row in face:
                    self.assertTrue(set(row) <= set(PALETTE), msg=f"{form_id} {mood}")


class LookupTests(unittest.TestCase):
    def test_every_evolution_species_has_a_sprite(self) -> None:
        for species_id, species in SPECIES.items():
            form = lookup_form(species_id)
            self.assertEqual(form["id"], species_id)
            self.assertEqual(form["name"], species.name)
            # Display names from the evolution table also resolve.
            self.assertEqual(lookup_form(species.name)["id"], species_id)

    def test_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(lookup_form("Chirplet")["id"], "chirplet")
        self.assertEqual(lookup_form("AETHLING")["id"], "aethling")

    def test_unknown_form_raises(self) -> None:
        with self.assertRaises(KeyError):
            lookup_form("agumon")

    def test_tired_is_an_alias_of_hungry(self) -> None:
        self.assertEqual(resolve_mood("tired"), "hungry")
        self.assertEqual(resolve_mood("Hungry/tired"), "hungry")
        self.assertEqual(
            sprite_grid("chirplet", mood="tired"),
            sprite_grid("chirplet", mood="hungry"),
        )

    def test_unknown_mood_raises(self) -> None:
        with self.assertRaises(KeyError):
            resolve_mood("confused")

    def test_mood_from_meters(self) -> None:
        self.assertEqual(mood_from_meters(80, 80, 20), "sad")
        self.assertEqual(mood_from_meters(10, 80, 60), "hungry")
        self.assertEqual(mood_from_meters(80, 10, 60), "hungry")
        self.assertEqual(mood_from_meters(80, 80, 80), "happy")
        self.assertEqual(mood_from_meters(60, 60, 50), "neutral")

    def test_mood_for_creature_reads_stats(self) -> None:
        pet = Creature(name="Face", stats=Stats(hunger=90, energy=90, mood=85))
        self.assertEqual(mood_for_creature(pet), "happy")
        pet.stats.mood = 10
        self.assertEqual(mood_for_creature(pet), "sad")


class RendererTests(unittest.TestCase):
    def test_ascii_renderer_is_plain_text(self) -> None:
        pixels = sprite_pixels("chirplet", frame=0, mood="happy")
        text = TerminalRenderer(ascii_mode=True).render(pixels)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("▀", text)
        lines = text.splitlines()
        self.assertGreaterEqual(len(lines), 8)
        for line in lines:
            self.assertLessEqual(len(line), SPRITE_SIZE)

    def test_halfblock_renderer_uses_ansi_and_packs_rows(self) -> None:
        pixels = sprite_pixels("aethling", frame=0, mood="neutral")
        text = TerminalRenderer(ascii_mode=False).render(pixels)
        self.assertIn("\x1b[38;2;", text)
        self.assertTrue("▀" in text or "▄" in text)
        # 16 pixel rows become at most 8 terminal rows.
        self.assertLessEqual(len(text.splitlines()), 8)

    def test_catalog_and_sheet_writer(self) -> None:
        ascii_sheet = format_catalog(ascii_mode=True)
        self.assertIn("Chirplet", ascii_sheet)
        self.assertIn("Wavling", ascii_sheet)
        self.assertIn("Aethling", ascii_sheet)
        self.assertIn("Staticore", ascii_sheet)
        self.assertIn("Luminode", ascii_sheet)
        self.assertIn("Aethernova", ascii_sheet)
        self.assertIn("Voidstatic", ascii_sheet)
        self.assertIn("— champion —", ascii_sheet)
        self.assertIn("— mega —", ascii_sheet)
        self.assertIn("happy", ascii_sheet)
        self.assertIn("hungry / tired", ascii_sheet)
        self.assertNotIn("\x1b", ascii_sheet)
        with tempfile.TemporaryDirectory() as tmp:
            ansi_path, txt_path = write_sprite_sheets(tmp)
            self.assertTrue(ansi_path.is_file())
            self.assertTrue(txt_path.is_file())
            self.assertEqual(ansi_path.name, "sheet.ansi")
            self.assertEqual(txt_path.name, "sheet.txt")
            self.assertIn("\x1b[38;2;", ansi_path.read_text(encoding="utf-8"))
            self.assertEqual(txt_path.read_text(encoding="utf-8"), ascii_sheet)


if __name__ == "__main__":
    unittest.main()
