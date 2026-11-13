"""pet.stats / pet.status {full:true}: every stat, pages in Main's order, 44-char ASCII rows."""

from __future__ import annotations

import random
import unittest
from datetime import datetime

from link.handler import CompanionHandler
from link.protocol import Message
from pet.battle import Bag
from pet.creature import Creature, xp_needed_for_level
from pet.fullstats import PAGE_TITLES, TITLE_MAX
from pet.life import Life
from pet.moves import MOVE_LIBRARY
from pet.stats import Stats

T0 = datetime(2026, 11, 12, 14, 0).timestamp()
MAIN_ORDER = ["Overview", "Combat", "Care", "Traits & Evolution", "Moves & Loadout", "Bag",
              "Battle Record", "Discoveries"]


def make(species: str = "chirplet", **stats) -> CompanionHandler:
    pet = Creature(name="Pulse", species_id=species, stats=Stats(**stats) if stats else Stats(),
                   life=Life.new(random.Random(3), T0 - 5 * 86400))
    pet.life.last_now = T0
    handler = CompanionHandler(pet, clock=lambda: T0)
    handler.rng = random.Random(3)
    return handler


def req(handler: CompanionHandler, typ: str, payload: dict | None = None) -> Message:
    return handler.handle(Message.request(typ, payload or {}))


class PageTests(unittest.TestCase):
    def check_sheet(self, sheet: dict) -> None:
        titles = [page["title"] for page in sheet["pages"]]
        self.assertEqual(titles, MAIN_ORDER)
        for page in sheet["pages"]:
            self.assertLessEqual(len(page["title"]), TITLE_MAX)
            self.assertTrue(page["title"].isascii())
            self.assertTrue(page["lines"], page["title"])
            for line in page["lines"]:
                self.assertTrue(line.isascii(), repr(line))
                self.assertLessEqual(len(line), 44, repr(line))
        # Flat list: "== Title ==" breaks, same rows, same order.
        expected: list[str] = []
        for page in sheet["pages"]:
            expected.append(f"== {page['title']} ==")
            expected.extend(page["lines"])
        self.assertEqual(sheet["lines"], expected)

    def test_titles_and_order(self) -> None:
        self.assertEqual(list(PAGE_TITLES), MAIN_ORDER)
        self.check_sheet(req(make(), "pet.stats").payload)

    def test_worst_case_pets_stay_in_bounds(self) -> None:
        for species in ("chirplet", "luminode", "aethernova", "voidstatic"):
            h = make(species, level=30, xp=99999, attack=250, defense=250, speed=250, max_hp=999, hp=999)
            pet = h.creature
            pet.known_move_ids = list(MOVE_LIBRARY)
            pet.life.nickname = "Sir Bitsy Boopsy"
            pet.life.make_sick(pet, "corrupted", h.rng, T0)
            pet.life.add_modifier("a_really_long_modifier_name_here", -12, 3600, T0)
            for key in pet.life.prefs:
                pet.life.reveal(key)
            Bag(pet).add({"name": "an extremely long legendary loot item name", "kind": "food", "power": 99}, 99)
            pet.life.add_to_shelf("a trinket with a very very long name indeed", "test", 3, T0)
            pet.life.record.update(wins=12345, losses=678, flees=90, bosses=12)
            self.check_sheet(req(h, "pet.stats").payload)


class WrapTests(unittest.TestCase):
    def test_three_or_more_evolution_choices_all_show(self) -> None:
        import pet.fullstats as fs

        h = make()
        names = ["Luminode", "Hexwave", "Aethernova", "Voidstatic", "Glitchling"]
        sheet = req(h, "pet.stats").payload
        # Build a sheet with five choices (two locked) straight from the page builder.
        raw = dict(sheet)
        raw["evolution"] = dict(sheet["evolution"], next_stage="champion",
                                choices=[{"species": n, "available": i % 2 == 0} for i, n in enumerate(names)])
        traits = fs._pages(raw)[3]["lines"]
        start = next(i for i, line in enumerate(traits) if line.startswith("Can become: "))
        block = traits[start:]
        self.assertGreater(len(block), 1)
        text = " ".join(line.strip() for line in block)
        for i, name in enumerate(names):
            self.assertIn(name + ("" if i % 2 == 0 else "(locked)"), text)
        self.assertFalse(any(line.endswith("...") for line in traits))
        for line in block[1:]:
            self.assertTrue(line.startswith("  "))
        for line in traits:
            self.assertTrue(line.isascii())
            self.assertLessEqual(len(line), 44)

    def test_long_values_wrap_instead_of_truncating(self) -> None:
        h = make()
        pet = h.creature
        trinket = "a trinket with a very very long name indeed"
        item = "an extremely long legendary loot item name"
        pet.life.add_to_shelf(trinket, "test", 3, T0)
        pet.life.add_modifier("a_really_long_modifier_name_here", -12, 3600, T0)
        Bag(pet).add({"name": item, "kind": "food"}, 3)
        sheet = req(h, "pet.stats").payload
        lines = sheet["lines"]
        self.assertFalse(any(line.endswith("...") for line in lines), [l for l in lines if l.endswith("...")])
        joined = " ".join(line.strip() for line in lines)
        self.assertIn(trinket, joined)
        self.assertIn(item, joined)
        self.assertIn("a_really_long_modifier_name_here -12", joined)
        for line in lines:
            self.assertTrue(line.isascii())
            self.assertLessEqual(len(line), 44)


class FieldTests(unittest.TestCase):
    def test_raw_fields(self) -> None:
        sheet = req(make(), "pet.stats").payload
        for key in ("name", "nickname", "species", "stage", "type", "level", "xp", "xp_next_level", "xp_to_next",
                    "xp_pct", "age_hours", "age_days", "generation", "birthday", "hp", "max_hp", "energy",
                    "max_energy", "attack", "defense", "speed", "crit_chance", "hunger", "happiness",
                    "obedience", "discipline", "care_mistakes", "mood", "modifiers", "bond", "sick", "tantrum",
                    "quirks", "favorites", "evolution", "moves", "loadout", "bag", "bag_total", "record",
                    "discoveries", "trinkets", "rival"):
            self.assertIn(key, sheet)
        self.assertEqual(sheet["max_energy"], 100)
        self.assertEqual(sheet["birthday"], "2026-11-07")
        self.assertAlmostEqual(sheet["age_days"], 5.0, places=1)

    def test_xp_math(self) -> None:
        sheet = req(make(level=3, xp=40), "pet.stats").payload
        self.assertEqual(sheet["xp_next_level"], xp_needed_for_level(4))
        self.assertEqual(sheet["xp_to_next"], xp_needed_for_level(4) - 40)
        self.assertEqual(sheet["xp_pct"], 33)

    def test_favorites_stay_hidden_until_discovered(self) -> None:
        h = make()
        sheet = req(h, "pet.stats").payload
        self.assertEqual(sheet["favorites"], {})
        self.assertEqual(sheet["favorites_hidden"], 4)
        traits = sheet["pages"][3]["lines"]
        self.assertTrue(all(h.creature.life.prefs["fav_food"] not in line for line in traits if line.startswith("Fav food")))
        h.creature.life.reveal("fav_food")
        sheet = req(h, "pet.stats").payload
        self.assertEqual(sheet["favorites"], {"fav_food": h.creature.life.prefs["fav_food"]})
        self.assertIn("???", "\n".join(sheet["pages"][3]["lines"]))

    def test_evolution_gates(self) -> None:
        evo = req(make(level=2), "pet.stats").payload["evolution"]
        self.assertEqual(evo["next_stage"], "in-training")
        self.assertEqual(evo["gates"]["level"]["pct"], 50)
        self.assertTrue(evo["choices"])
        final = req(make("aethernova"), "pet.stats").payload
        self.assertIsNone(final["evolution"]["next_stage"])
        self.assertIn("Final form reached.", final["pages"][3]["lines"])

    def test_moves_and_loadout(self) -> None:
        h = make()
        h.creature.known_move_ids = ["chirp", "static_snap", "echo_pulse", "signal_sniff", "blue_pulse"]
        sheet = req(h, "pet.stats").payload
        self.assertEqual(len(sheet["moves"]), 5)
        self.assertLessEqual(len(sheet["loadout"]), 4)
        for move in sheet["moves"]:
            self.assertTrue({"name", "type", "power", "energy_cost", "rank"} <= set(move))
        marked = [line for line in sheet["pages"][4]["lines"] if line.startswith("*") and not line.startswith("* =")]
        self.assertEqual(len(marked), len(sheet["loadout"]))

    def test_battle_record_counts(self) -> None:
        h = make()
        req(h, "pet.combat.end", {"result": "win", "xp": 1, "enemies": ["bug"]})
        req(h, "pet.combat.end", {"result": "win", "xp": 1, "enemies": [{"id": "kernel_panic"}]})
        h.creature.stats.hp = 0
        req(h, "pet.combat.end", {"result": "lose", "xp": 0})
        req(h, "pet.combat.end", {"result": "flee", "xp": 0})
        rec = req(h, "pet.stats").payload["record"]
        self.assertEqual((rec["wins"], rec["losses"], rec["flees"], rec["bosses"], rec["faints"], rec["fights"]),
                         (2, 1, 1, 1, 1, 4))
        clone = Creature.from_dict(h.creature.to_dict())
        self.assertEqual(clone.life.record["wins"], 2)

    def test_status_full_flag(self) -> None:
        h = make()
        self.assertNotIn("full", req(h, "pet.status").payload)
        full = req(h, "pet.status", {"full": True}).payload
        self.assertIn("pet", full)
        self.assertEqual([p["title"] for p in full["full"]["pages"]], MAIN_ORDER)


if __name__ == "__main__":
    unittest.main()
