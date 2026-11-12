"""Dungeon combat helpers and protocol messages."""

from __future__ import annotations

import random
import unittest

from link.handler import CompanionHandler
from link.protocol import Message, new_id
from pet.combat import (
    apply_combat_end,
    apply_hit,
    incoming_taken,
    outgoing_accuracy,
    outgoing_damage,
    perform_action,
    pick_move,
)
from pet.creature import Creature
from pet.moves import GUARD_MOVE, get_move
from pet.stats import Stats


def _req(typ: str, payload: dict | None = None) -> Message:
    return Message(type=typ, id=new_id(), payload=payload or {})


class FormulaTests(unittest.TestCase):
    def test_outgoing_accuracy_is_clamped(self) -> None:
        chirp = get_move("chirp")
        self.assertEqual(outgoing_accuracy(chirp, pet_speed=10, enemy_speed=10), chirp.accuracy)
        self.assertGreaterEqual(outgoing_accuracy(chirp, pet_speed=1, enemy_speed=10_000), 5)
        self.assertLessEqual(outgoing_accuracy(chirp, pet_speed=10_000, enemy_speed=1), 100)

    def test_outgoing_damage_and_guard(self) -> None:
        chirp = get_move("chirp")
        self.assertEqual(outgoing_damage(chirp, pet_attack=6, enemy_defense=2), max(1, chirp.power + 6 - 2))
        self.assertEqual(outgoing_damage(GUARD_MOVE, pet_attack=99, enemy_defense=0), 0)

    def test_incoming_taken_halves_when_guarding(self) -> None:
        # defense 8 -> 8//4 = 2 subtracted, then halved if guarding.
        self.assertEqual(incoming_taken(10, defense=8, guarding=False), 8)
        self.assertEqual(incoming_taken(10, defense=8, guarding=True), 4)


class ActionTests(unittest.TestCase):
    def test_asleep_and_fainted_are_refused(self) -> None:
        asleep = Creature(stats=Stats(hunger=80, energy=80, mood=70), asleep=True)
        move, _fallback, reason = pick_move(asleep, "Chirp")
        self.assertEqual(reason, "asleep")
        result = perform_action(asleep, "Chirp", {"name": "slime"}, random.Random(0))
        self.assertFalse(result["acted"])
        self.assertEqual(result["reason"], "asleep")

        fainted = Creature(stats=Stats(hp=0, max_hp=32, hunger=80, energy=80, mood=70))
        result = perform_action(fainted, "Chirp", {"name": "slime"}, random.Random(0))
        self.assertFalse(result["acted"])
        self.assertEqual(result["reason"], "fainted")

    def test_tired_falls_back_to_guard(self) -> None:
        pet = Creature(stats=Stats(hunger=80, energy=0, mood=70), known_move_ids=["chirp"])
        move, fallback, reason = pick_move(pet, "Chirp")
        self.assertIsNone(reason)
        self.assertTrue(fallback)
        self.assertEqual(move.id, "guard")
        result = perform_action(pet, "Chirp", {"name": "slime", "defense": 0, "speed": 1}, random.Random(0))
        self.assertTrue(result["acted"])
        self.assertEqual(result["effect"], "guard")
        self.assertEqual(result["damage"], 0)
        self.assertEqual(result["energy_spent"], 0)
        self.assertTrue(pet.guarding)

    def test_chirp_fallback_when_a_costly_move_is_requested(self) -> None:
        pet = Creature(
            stats=Stats(hunger=80, energy=5, mood=70),
            known_move_ids=["chirp", "noise_veil"],
        )
        move, fallback, reason = pick_move(pet, "Noise Veil")
        self.assertIsNone(reason)
        self.assertTrue(fallback)
        self.assertEqual(move.id, "chirp")

    def test_hit_and_heal(self) -> None:
        pet = Creature(stats=Stats(hp=20, max_hp=32, defense=4, hunger=80, energy=80, mood=70))
        taken = apply_hit(pet, {"damage": 12})
        self.assertEqual(taken["damage_taken"], incoming_taken(12, 4, False))
        self.assertEqual(pet.stats.hp, 20 - taken["damage_taken"])
        healed = apply_hit(pet, {"heal": 5})
        self.assertEqual(healed["healed"], 5)
        down = Creature(stats=Stats(hp=0, max_hp=32, hunger=80, energy=80))
        apply_hit(down, {"heal": 8})
        self.assertGreater(down.stats.hp, 0)
        self.assertFalse(down.fainted)

    def test_combat_end_win_grants_xp(self) -> None:
        pet = Creature(stats=Stats(xp=0, level=1, hunger=80, energy=80, mood=50))
        result = apply_combat_end(pet, {"result": "win", "xp": 10})
        self.assertTrue(result["ok"])
        self.assertEqual(pet.stats.xp, 10)
        self.assertGreater(pet.stats.mood, 50)

    def test_combat_end_lose_while_fainted_is_a_care_mistake(self) -> None:
        pet = Creature(stats=Stats(hp=0, max_hp=32, hunger=80, energy=80, mood=50, care_mistakes=0))
        result = apply_combat_end(pet, {"result": "lose", "xp": 0})
        self.assertEqual(pet.stats.care_mistakes, 1)
        self.assertTrue(any("Care mistake" in line for line in result["events"]))


class CombatProtocolTests(unittest.TestCase):
    def test_stats_action_hit_end_round_trip(self) -> None:
        pet = Creature(stats=Stats(hunger=80, energy=80, mood=70, attack=6, defense=4, speed=5, hp=32, max_hp=32))
        handler = CompanionHandler(pet)
        handler.rng = random.Random(1)
        stats = handler.handle(_req("pet.combat.stats")).payload
        self.assertEqual(stats["hp"], 32)
        self.assertTrue(any(m["name"] == "Chirp" for m in stats["moves"]))

        action = handler.handle(
            _req("pet.combat.action", {"move": "Chirp", "enemy": {"name": "slime", "hp": 8, "attack": 2, "defense": 0, "speed": 1}})
        )
        self.assertEqual(action.type, "pet.combat.action.ok")
        self.assertTrue(action.payload["acted"])
        self.assertEqual(action.payload["move"], "Chirp")

        hit = handler.handle(_req("pet.combat.hit", {"damage": 6}))
        self.assertEqual(hit.type, "pet.combat.hit.ok")
        self.assertIn("damage_taken", hit.payload)

        end = handler.handle(_req("pet.combat.end", {"result": "win", "xp": 5}))
        self.assertEqual(end.type, "pet.combat.end.ok")
        self.assertEqual(end.payload["xp_granted"], 5)

    def test_action_refused_when_asleep(self) -> None:
        handler = CompanionHandler(Creature(stats=Stats(hunger=80, energy=80), asleep=True))
        reply = handler.handle(_req("pet.combat.action", {"move": "Chirp", "enemy": {"name": "slime"}}))
        self.assertFalse(reply.payload["acted"])
        self.assertEqual(reply.payload["reason"], "asleep")

    def test_end_rejects_bad_result(self) -> None:
        handler = CompanionHandler(Creature())
        reply = handler.handle(_req("pet.combat.end", {"result": "draw"}))
        self.assertEqual(reply.payload["code"], "bad_payload")


if __name__ == "__main__":
    unittest.main()
