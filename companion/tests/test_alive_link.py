"""Protocol side of the life engine: new messages, events, and pet.status fields."""

from __future__ import annotations

import random
import unittest
from datetime import datetime

from link.handler import CompanionHandler
from link.protocol import EVENT_KINDS, TYPE_ERROR, TYPE_PET_EVENT, Message
from pet import life as L
from pet.creature import Creature
from pet.life import Life
from pet.stats import Stats
from save import load_creature, save_creature

T0 = datetime(2026, 11, 12, 14, 0).timestamp()


class FakeClock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(seed: int = 1, species: str | None = None, **stats) -> tuple[CompanionHandler, FakeClock]:
    clock = FakeClock()
    pet = Creature(name="Pulse", stats=Stats(**stats) if stats else Stats(), life=Life.new(random.Random(seed), T0))
    if species:
        pet.species_id = species
    handler = CompanionHandler(pet, clock=clock)
    handler.rng = random.Random(seed)
    return handler, clock


def req(handler: CompanionHandler, typ: str, payload: dict | None = None) -> Message:
    return handler.handle(Message.request(typ, payload or {}))


class StatusFieldTests(unittest.TestCase):
    def test_status_has_life_fields(self) -> None:
        handler, _ = make()
        pet = req(handler, "pet.status").payload["pet"]
        for key in ("nickname", "display_name", "bond", "sick", "tantrum", "away", "costume", "speech", "ask", "life"):
            self.assertIn(key, pet)
        self.assertEqual(pet["bond"], "friendly")
        self.assertIn("modifiers", pet["life"])

    def test_old_status_fields_unchanged(self) -> None:
        handler, _ = make()
        pet = req(handler, "pet.status").payload["pet"]
        for key in ("name", "species", "stage", "level", "hp", "hunger", "energy", "mood"):
            self.assertIn(key, pet)

    def test_costume_on_a_holiday(self) -> None:
        handler, clock = make()
        clock.now = datetime(2026, 12, 25, 10, 0).timestamp()
        handler.tick(1.0)
        self.assertEqual(req(handler, "pet.status").payload["pet"]["costume"], "christmas")


class MessageTests(unittest.TestCase):
    def test_rename(self) -> None:
        handler, _ = make()
        reply = req(handler, "pet.rename", {"name": "  Bitsy  "})
        self.assertEqual(reply.type, "pet.rename.ok")
        self.assertEqual(reply.payload["nickname"], "Bitsy")
        self.assertTrue(reply.payload["say"])
        self.assertEqual(req(handler, "pet.status").payload["pet"]["display_name"], "Bitsy")
        self.assertEqual(req(handler, "pet.rename", {"name": ""}).type, TYPE_ERROR)
        self.assertEqual(req(handler, "pet.rename", {"name": "x" * 40}).type, TYPE_ERROR)

    def test_praise_and_scold(self) -> None:
        handler, _ = make()
        praise = req(handler, "pet.praise")
        self.assertTrue(praise.payload["accepted"])
        self.assertIn(praise.payload["obedience"], {"wild", "willful", "good", "loyal"})
        scold = req(handler, "pet.scold")
        self.assertIn("deserved", scold.payload)

    def test_tantrum_blocks_care(self) -> None:
        handler, clock = make(hunger=30)
        for _ in range(3):
            req(handler, "pet.scold")
        feed = req(handler, "pet.feed", {"item": "ration"})
        self.assertFalse(feed.payload["fed"])
        self.assertEqual(feed.payload["reason"], "tantrum")
        self.assertTrue(feed.payload["say"])
        play = req(handler, "pet.play", {"game": "fetch"})
        self.assertEqual(play.payload.get("reason"), "tantrum")
        clock.advance(L.TANTRUM_SECONDS + 5)
        self.assertNotEqual(req(handler, "pet.feed", {"item": "ration"}).payload.get("reason"), "tantrum")

    def test_heal_and_medicine_gift(self) -> None:
        handler, _ = make()
        life = handler.creature.life
        self.assertFalse(req(handler, "pet.heal").payload["healed"])
        life.make_sick(handler.creature, "glitch", handler.rng, T0)
        self.assertTrue(req(handler, "pet.status").payload["pet"]["sick"])
        self.assertTrue(req(handler, "pet.heal").payload["healed"])
        life.make_sick(handler.creature, "corrupted", handler.rng, T0)
        gift = req(handler, "pet.gift", {"item": {"kind": "medicine", "name": "patch kit", "power": 1}})
        self.assertNotEqual(gift.type, TYPE_ERROR)
        self.assertIsNone(life.sick)

    def test_feed_reports_reaction(self) -> None:
        handler, _ = make(hunger=30)
        handler.creature.life.prefs["fav_food"] = "sweet"
        reply = req(handler, "pet.feed", {"item": "cookie"})
        self.assertTrue(reply.payload["fed"])
        self.assertEqual(reply.payload["reaction"], "like")
        self.assertEqual(reply.payload["flavor"], "sweet")
        self.assertTrue(reply.payload["say"])

    def test_diary_and_shelf(self) -> None:
        handler, _ = make()
        diary = req(handler, "pet.diary", {"today": True})
        self.assertEqual(diary.payload["count"], 0)
        self.assertTrue(diary.payload["today"]["text"])
        self.assertEqual(req(handler, "pet.diary", {"limit": "x"}).type, TYPE_ERROR)
        handler.creature.life.add_to_shelf("shiny gear", "test", 2, T0)
        shelf = req(handler, "pet.shelf", {"show_off": True})
        self.assertEqual(shelf.payload["favorite"], "shiny gear")
        self.assertTrue(shelf.payload["say"])

    def test_activity_and_dungeon_cheer(self) -> None:
        handler, _ = make()
        handler.creature.life.bond = 60
        start = req(handler, "pet.activity", {"kind": "dungeon_start", "name": "Crypt"})
        self.assertTrue(start.payload["cheer"])
        win = req(handler, "pet.activity", {"kind": "dungeon", "name": "Crypt", "result": "win"})
        self.assertTrue(win.payload["say"])
        self.assertEqual(req(handler, "pet.activity", {}).type, TYPE_ERROR)

    def test_say_topics(self) -> None:
        handler, _ = make()
        for topic in ("chatter", "funfact", "greeting", "dream", "ramble", "cheer", "ask"):
            reply = req(handler, "pet.say", {"topic": topic})
            self.assertTrue(reply.payload["say"], topic)
        self.assertEqual(req(handler, "pet.say", {"topic": "nope"}).type, TYPE_ERROR)

    def test_rival_challenge(self) -> None:
        handler, _ = make(level=5, attack=12, defense=10, speed=10)
        self.assertFalse(req(handler, "pet.rival").payload["present"])
        reply = req(handler, "pet.rival", {"action": "challenge"})
        self.assertTrue(reply.payload["ok"])
        self.assertIn(reply.payload["result"], {"win", "lose"})
        self.assertTrue(reply.payload["log"])
        self.assertGreaterEqual(handler.creature.stats.hp, 1)  # never faints to a rival
        self.assertFalse(req(handler, "pet.rival", {"action": "fight"}).payload["ok"])
        self.assertEqual(req(handler, "pet.rival", {"action": "dance"}).type, TYPE_ERROR)

    def test_adventure_go_status_recall(self) -> None:
        handler, clock = make(energy=90)
        go = req(handler, "pet.adventure", {"action": "go"})
        self.assertTrue(go.payload["ok"])
        self.assertTrue(req(handler, "pet.adventure").payload["away"])
        self.assertTrue(req(handler, "pet.status").payload["pet"]["away"])
        clock.advance(30)
        back = req(handler, "pet.adventure", {"action": "recall"})
        self.assertTrue(back.payload["ok"])
        self.assertTrue(back.payload["story"])
        self.assertFalse(req(handler, "pet.adventure").payload["away"])

    def test_feeding_recalls_an_adventurer(self) -> None:
        handler, _ = make(energy=90, hunger=30)
        req(handler, "pet.adventure", {"action": "go"})
        req(handler, "pet.feed", {"item": "ration"})
        self.assertFalse(handler.creature.life.away())

    def test_egg_needs_final_form_and_confirm(self) -> None:
        handler, _ = make()
        self.assertFalse(req(handler, "pet.egg", {"action": "lay"}).payload["ok"])
        handler2, _ = make(species="aethernova")
        self.assertTrue(req(handler2, "pet.egg").payload["can_lay"])
        self.assertTrue(req(handler2, "pet.egg", {"action": "lay"}).payload["ok"])
        self.assertEqual(req(handler2, "pet.egg", {"action": "hatch"}).type, TYPE_ERROR)
        hatched = req(handler2, "pet.egg", {"action": "hatch", "confirm": True})
        self.assertTrue(hatched.payload["ok"])
        self.assertEqual(hatched.payload["pet"]["stage"], "baby")
        self.assertEqual(handler2.creature.life.generation, 2)

    def test_reset_needs_confirm(self) -> None:
        handler, _ = make()
        handler.creature.stats.level = 7
        self.assertEqual(req(handler, "pet.reset").type, TYPE_ERROR)
        self.assertTrue(req(handler, "pet.reset", {"confirm": True}).payload["reset"])
        self.assertEqual(handler.creature.stats.level, 1)

    def test_life_message(self) -> None:
        handler, _ = make()
        life = req(handler, "pet.life").payload["life"]
        for key in ("bond", "obedience", "modifiers", "likes", "quirks", "rival", "generation"):
            self.assertIn(key, life)


class EventTests(unittest.TestCase):
    def events(self, handler: CompanionHandler) -> list[dict]:
        return [m.payload for m in handler.drain_events() if m.type == TYPE_PET_EVENT]

    def test_new_event_kinds_are_registered(self) -> None:
        for kind in ("speech", "greeting", "idle", "ask", "dream", "tantrum", "adventure_return",
                     "rival_appears", "birthday", "holiday", "diary", "egg_hatched", "treasure"):
            self.assertIn(kind, EVENT_KINDS)

    def test_no_life_events_without_opt_in(self) -> None:
        handler, clock = make()
        req(handler, "hello", {"proto": 1})
        for _ in range(30):
            clock.advance(60)
            handler.tick(60)
        self.assertEqual(handler.drain_events(), [])

    def test_greeting_arrives_on_the_next_tick(self) -> None:
        handler, _ = make()
        req(handler, "hello", {"proto": 1, "events": True})
        self.assertEqual(self.events(handler), [])
        handler.tick(1.0)
        kinds = [e["event"] for e in self.events(handler)]
        self.assertIn("greeting", kinds)

    def test_life_keeps_happening_over_an_afternoon(self) -> None:
        handler, clock = make(seed=4)
        req(handler, "hello", {"proto": 1, "events": True})
        kinds: set[str] = set()
        for _ in range(240):  # four hours at one tick a minute
            clock.advance(60)
            handler.tick(60)
            kinds |= {e["event"] for e in self.events(handler)}
        self.assertTrue({"greeting", "idle"} <= kinds, kinds)
        self.assertGreaterEqual(len(kinds), 5, kinds)
        for kind in kinds:
            self.assertIn(kind, EVENT_KINDS)


class CombatModTests(unittest.TestCase):
    def test_default_pet_is_neutral(self) -> None:
        from pet.combat import combat_mods

        mods = combat_mods(Creature(name="x"))
        self.assertEqual(mods["damage_mult"], 1.0)
        self.assertEqual(mods["refuse_chance"], 0.0)

    def test_angry_hits_harder_and_low_obedience_disobeys(self) -> None:
        from pet.combat import combat_mods

        pet = Creature(name="x", stats=Stats(mood=10))
        pet.life.obedience = 5
        mods = combat_mods(pet)
        self.assertGreater(mods["damage_mult"], 1.0)
        self.assertGreater(mods["disobey_chance"], 0.0)
        pet.stats.energy = 10
        self.assertGreater(combat_mods(pet)["refuse_chance"], 0.0)

    def test_bond_gives_crits(self) -> None:
        from pet.combat import combat_mods

        pet = Creature(name="x")
        pet.life.bond = 100
        self.assertGreater(combat_mods(pet)["crit_chance"], 0.0)


class SaveTests(unittest.TestCase):
    def test_life_survives_save_and_load(self) -> None:
        import tempfile
        from pathlib import Path

        handler, _ = make()
        req(handler, "pet.rename", {"name": "Bitsy"})
        handler.creature.life.add_to_shelf("shiny gear", "test", 2, T0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pet.json"
            save_creature(handler.creature, path)
            loaded = load_creature(path)
        self.assertEqual(loaded.life.nickname, "Bitsy")
        self.assertEqual(loaded.life.shelf[0]["name"], "shiny gear")
        self.assertEqual(loaded.life.picker.history, handler.creature.life.picker.history)


if __name__ == "__main__":
    unittest.main()
