"""Handler tests: every request type, error code, and event gating."""

from __future__ import annotations

import unittest

from link.handler import CompanionHandler, encode_avatar, need_hunger, pet_payload
from link.protocol import (
    PROTO_VERSION,
    TYPE_ERROR,
    TYPE_HELLO_OK,
    TYPE_PET_AVATAR_OK,
    TYPE_PET_EVENT,
    TYPE_PET_FEED_OK,
    TYPE_PET_GIFT_OK,
    TYPE_PET_PLAY_OK,
    TYPE_PET_STATUS_OK,
    TYPE_PONG,
    Message,
)
from avatar.sprites import sprite_pixels
from pet.creature import TRAIN_ENERGY_COST, Creature
from pet.stats import Stats


def _req(typ: str, payload: dict | None = None) -> Message:
    return Message.request(typ, payload or {})


def _handler(**stats_kwargs) -> CompanionHandler:
    stats = Stats(**stats_kwargs) if stats_kwargs else Stats()
    return CompanionHandler(Creature(name="Pulse", stats=stats))


class HelloAndPingTests(unittest.TestCase):
    def test_hello_ok_shape(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("hello", {"role": "main", "proto": 1}))
        self.assertEqual(reply.type, TYPE_HELLO_OK)
        self.assertEqual(
            reply.payload,
            {"role": "companion", "proto": PROTO_VERSION, "source": "sim", "pet": "PULSE"},
        )
        self.assertFalse(handler.events_enabled)

    def test_hello_echoes_events_true(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("hello", {"role": "main", "proto": 1, "events": True}))
        self.assertEqual(reply.type, TYPE_HELLO_OK)
        self.assertTrue(reply.payload.get("events") is True)
        self.assertTrue(handler.events_enabled)

    def test_proto_mismatch(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("hello", {"role": "main", "proto": 99}))
        self.assertEqual(reply.type, TYPE_ERROR)
        self.assertEqual(reply.payload["code"], "proto_mismatch")

    def test_missing_proto_is_mismatch(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("hello", {"role": "main"}))
        self.assertEqual(reply.payload["code"], "proto_mismatch")

    def test_ping_pong(self) -> None:
        handler = _handler()
        req = _req("ping", {})
        reply = handler.handle(req)
        self.assertEqual(reply.type, TYPE_PONG)
        self.assertEqual(reply.id, req.id)
        self.assertEqual(reply.payload, {"ok": True, "source": "sim"})


class StatusAndFeedTests(unittest.TestCase):
    def test_status_pet_object(self) -> None:
        handler = _handler(hunger=80, mood=72, energy=80, age=11.5)
        reply = handler.handle(_req("pet.status", {}))
        self.assertEqual(reply.type, TYPE_PET_STATUS_OK)
        pet = reply.payload["pet"]
        self.assertEqual(pet["name"], "PULSE")
        self.assertEqual(pet["species"], "Chirplet")
        self.assertEqual(pet["stage"], "baby")
        # Our fullness 80 → protocol need hunger 20.
        self.assertEqual(pet["hunger"], 20)
        self.assertEqual(pet["happiness"], 72)
        self.assertEqual(pet["energy"], 80)
        self.assertIn(pet["mood"], {"content", "playful", "joyful", "hungry", "tired", "grumpy"})
        self.assertEqual(pet["age_hours"], 11.5)
        self.assertEqual(pet["gifts"], 0)
        for key in (
            "level", "xp", "hp", "max_hp", "attack", "defense", "speed",
            "moves", "care_mistakes", "avatar",
        ):
            self.assertIn(key, pet)
        self.assertEqual(pet["avatar"]["form"], "chirplet")
        self.assertIn(pet["avatar"]["mood"], {"content", "playful", "joyful", "hungry", "tired", "grumpy"})
        self.assertIn(pet["avatar"]["frame"], (0, 1))

    def test_feed_lowers_protocol_hunger(self) -> None:
        handler = _handler(hunger=50, mood=40, energy=80, xp=0)
        before = need_hunger(handler.creature)
        reply = handler.handle(_req("pet.feed", {"item": "ration"}))
        self.assertEqual(reply.type, TYPE_PET_FEED_OK)
        self.assertEqual(reply.payload["item"], "ration")
        self.assertLess(reply.payload["hunger_delta"], 0)
        self.assertLess(reply.payload["pet"]["hunger"], before)
        self.assertGreater(handler.creature.stats.hunger, 50)
        self.assertGreater(handler.creature.stats.mood, 40)

    def test_feed_missing_item_is_bad_payload(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("pet.feed", {}))
        self.assertEqual(reply.type, TYPE_ERROR)
        self.assertEqual(reply.payload["code"], "bad_payload")

    def test_feed_non_string_item_is_bad_payload(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("pet.feed", {"item": 12}))
        self.assertEqual(reply.payload["code"], "bad_payload")


class PlayTests(unittest.TestCase):
    def test_play_when_rested(self) -> None:
        handler = _handler(energy=80, hunger=80, mood=50)
        reply = handler.handle(_req("pet.play", {"game": "spar"}))
        self.assertEqual(reply.type, TYPE_PET_PLAY_OK)
        self.assertTrue(reply.payload["played"])
        self.assertEqual(reply.payload["game"], "spar")
        self.assertLess(handler.creature.stats.energy, 80)

    def test_play_too_tired(self) -> None:
        handler = _handler(energy=TRAIN_ENERGY_COST - 1, hunger=80, mood=50)
        before = handler.creature.stats.energy
        reply = handler.handle(_req("pet.play", {"game": "spar"}))
        self.assertEqual(reply.type, TYPE_PET_PLAY_OK)
        self.assertFalse(reply.payload["played"])
        self.assertEqual(reply.payload["reason"], "too tired")
        self.assertEqual(handler.creature.stats.energy, before)

    def test_play_missing_game_is_bad_payload(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("pet.play", {}))
        self.assertEqual(reply.payload["code"], "bad_payload")


class GiftTests(unittest.TestCase):
    def test_food_gift(self) -> None:
        handler = _handler(hunger=50, mood=40, energy=80)
        before_need = need_hunger(handler.creature)
        reply = handler.handle(
            _req("pet.gift", {"item": {"name": "glitch berry", "kind": "food", "power": 20}})
        )
        self.assertEqual(reply.type, TYPE_PET_GIFT_OK)
        self.assertTrue(reply.payload["accepted"])
        self.assertEqual(reply.payload["item"], {"name": "glitch berry", "kind": "food", "power": 20})
        self.assertIn("munches", reply.payload["reaction"])
        self.assertEqual(reply.payload["effects"]["hunger"], int(round(need_hunger(handler.creature) - before_need)))
        self.assertLess(reply.payload["effects"]["hunger"], 0)
        self.assertEqual(reply.payload["effects"]["happiness"], 5)
        self.assertIsNone(reply.payload["effects"]["stage"])
        self.assertEqual(reply.payload["pet"]["gifts"], 1)

    def test_toy_battery_trinket(self) -> None:
        handler = _handler(hunger=80, mood=40, energy=50)
        toy = handler.handle(_req("pet.gift", {"item": {"name": "squeaky bit", "kind": "toy", "power": 15}}))
        self.assertTrue(toy.payload["accepted"])
        self.assertEqual(toy.payload["effects"]["happiness"], 15)
        self.assertEqual(toy.payload["effects"]["energy"], -5)

        battery = handler.handle(_req("pet.gift", {"item": {"name": "cell", "kind": "battery", "power": 25}}))
        self.assertEqual(battery.payload["effects"]["energy"], 25)

        trinket = handler.handle(_req("pet.gift", {"item": {"name": "shiny bolt", "kind": "trinket"}}))
        self.assertEqual(trinket.payload["effects"]["happiness"], 10)
        self.assertEqual(trinket.payload["item"]["power"], 10)  # default

    def test_unknown_kind_becomes_trinket(self) -> None:
        handler = _handler(mood=40)
        reply = handler.handle(_req("pet.gift", {"item": {"name": "mystery", "kind": "wizard-hat"}}))
        self.assertEqual(reply.payload["item"]["kind"], "trinket")
        self.assertEqual(reply.payload["effects"]["happiness"], 10)

    def test_power_is_clamped(self) -> None:
        handler = _handler(hunger=0, mood=0, energy=0)
        reply = handler.handle(_req("pet.gift", {"item": {"name": "cell", "kind": "battery", "power": 250}}))
        self.assertEqual(reply.payload["item"]["power"], 100)
        self.assertEqual(handler.creature.stats.energy, 100)

    def test_evolve_advances_via_our_rules(self) -> None:
        handler = _handler(mood=70, care_mistakes=0)
        self.assertEqual(handler.creature.species_id, "chirplet")
        first = handler.handle(_req("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}}))
        self.assertTrue(first.payload["accepted"])
        self.assertEqual(handler.creature.species_id, "wavling")
        self.assertEqual(first.payload["effects"]["stage"], "in-training")
        self.assertIn("Wavling", first.payload["reaction"])

        second = handler.handle(_req("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}}))
        self.assertTrue(second.payload["accepted"])
        self.assertEqual(handler.creature.species_id, "aethling")
        self.assertEqual(second.payload["pet"]["stage"], "rookie")

    def test_evolve_at_final_form_is_rejected(self) -> None:
        handler = CompanionHandler(Creature(species_id="aethling", stats=Stats(mood=80)))
        reply = handler.handle(_req("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}}))
        self.assertEqual(reply.type, TYPE_PET_GIFT_OK)
        self.assertFalse(reply.payload["accepted"])
        self.assertIsNone(reply.payload["effects"]["stage"])
        self.assertEqual(reply.payload["pet"]["gifts"], 0)
        self.assertIn("trophy", reply.payload["reaction"])

    def test_neglected_wavling_evolves_to_staticore(self) -> None:
        handler = CompanionHandler(
            Creature(species_id="wavling", stats=Stats(mood=20, care_mistakes=5))
        )
        reply = handler.handle(_req("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}}))
        self.assertTrue(reply.payload["accepted"])
        self.assertEqual(handler.creature.species_id, "staticore")

    def test_missing_item_name_is_bad_item(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("pet.gift", {"item": {"kind": "food"}}))
        self.assertEqual(reply.payload["code"], "bad_item")

    def test_missing_item_object_is_bad_payload(self) -> None:
        handler = _handler()
        reply = handler.handle(_req("pet.gift", {}))
        self.assertEqual(reply.payload["code"], "bad_payload")

    def test_unknown_type(self) -> None:
        handler = _handler()
        req = _req("explode", {})
        reply = handler.handle(req)
        self.assertEqual(reply.type, TYPE_ERROR)
        self.assertEqual(reply.id, req.id)
        self.assertEqual(reply.payload["code"], "unknown_type")


class EventGatingTests(unittest.TestCase):
    def test_no_events_without_opt_in(self) -> None:
        handler = _handler(hunger=80, energy=80, mood=80)
        handler.handle(_req("hello", {"role": "main", "proto": 1}))
        # Drain fullness so protocol hunger climbs past the hungry threshold.
        handler.creature.stats.hunger = 0
        handler.creature.stats.clamp()
        handler.tick(dt=0.01)
        self.assertEqual(handler.drain_events(), [])

    def test_hungry_event_only_after_opt_in(self) -> None:
        handler = _handler(hunger=80, energy=80, mood=80)
        handler.handle(_req("hello", {"role": "main", "proto": 1, "events": True}))
        self.assertEqual(handler.drain_events(), [])  # snapshot, no flood
        handler.creature.stats.hunger = 0
        handler.creature.stats.clamp()
        handler._collect_events()
        events = handler.drain_events()
        hungry = next(ev for ev in events if ev.payload["event"] == "hungry")
        self.assertEqual(hungry.type, TYPE_PET_EVENT)
        self.assertIsInstance(hungry.payload["detail"], dict)
        self.assertIn("pet", hungry.payload)
        self.assertNotEqual(hungry.id, "")

    def test_evolve_and_level_events(self) -> None:
        handler = _handler(mood=70, xp=0, level=1)
        handler.handle(_req("hello", {"role": "main", "proto": 1, "events": True}))
        handler.drain_events()
        handler.handle(_req("pet.gift", {"item": {"name": "evo chip", "kind": "evolve"}}))
        events = handler.drain_events()
        evolved = next(ev for ev in events if ev.payload["event"] == "evolved")
        detail = evolved.payload["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["from"], "Chirplet")
        self.assertEqual(detail["to"], "Wavling")
        self.assertEqual(detail["form"], "Wavling")
        self.assertEqual(detail["stage"], "in-training")
        self.assertEqual(detail["from_stage"], "baby")

        # Enough XP to reach level 3 unlocks Static Snap.
        handler.handle(_req("hello", {"role": "main", "proto": 1, "events": True}))
        handler.drain_events()
        handler.creature.add_xp(30)
        handler._note_growth("wavling", 1, ["chirp"])
        events = handler.drain_events()
        by_kind = {ev.payload["event"]: ev.payload["detail"] for ev in events}
        self.assertEqual(by_kind["level_up"]["level"], handler.creature.stats.level)
        self.assertIn("move", by_kind["learned_move"])

    def test_pet_payload_always_named_pulse(self) -> None:
        pet = pet_payload(Creature(name="Buddy"))
        self.assertEqual(pet["name"], "PULSE")


class AvatarRequestTests(unittest.TestCase):
    def test_avatar_payload_shape_and_indexes(self) -> None:
        handler = _handler(hunger=80, mood=70, energy=80)
        req = _req("pet.avatar", {})
        reply = handler.handle(req)
        self.assertEqual(reply.type, TYPE_PET_AVATAR_OK)
        self.assertEqual(reply.id, req.id)
        body = reply.payload
        self.assertEqual(body["form"], "chirplet")
        self.assertEqual(body["mood"], "playful")
        self.assertEqual(body["width"], 16)
        self.assertEqual(body["height"], 16)
        self.assertIn(body["frame"], (0, 1))
        self.assertIsInstance(body["palette"], list)
        self.assertIsNone(body["palette"][0])
        for entry in body["palette"][1:]:
            self.assertIsInstance(entry, str)
            self.assertRegex(entry, r"^#[0-9a-f]{6}$")
        self.assertEqual(len(body["rows"]), 16)
        for row in body["rows"]:
            self.assertEqual(len(row), 16)
            for index in row:
                self.assertIsInstance(index, int)
                self.assertGreaterEqual(index, 0)
                self.assertLess(index, len(body["palette"]))

    def test_avatar_pixels_match_the_sprite(self) -> None:
        creature = Creature()
        body = encode_avatar(creature, frame=0, mood="playful")
        pixels = sprite_pixels("chirplet", frame=0, mood="happy")  # playful -> happy face
        for y, row in enumerate(body["rows"]):
            for x, index in enumerate(row):
                color = body["palette"][index]
                pixel = pixels[y][x]
                if color is None:
                    self.assertIsNone(pixel)
                else:
                    self.assertIsNotNone(pixel)
                    self.assertEqual(color, f"#{pixel[0]:02x}{pixel[1]:02x}{pixel[2]:02x}")

    def test_frame_override_selects_idle_frame(self) -> None:
        handler = _handler()
        a = handler.handle(_req("pet.avatar", {"frame": 0})).payload
        b = handler.handle(_req("pet.avatar", {"frame": 1})).payload
        self.assertEqual(a["frame"], 0)
        self.assertEqual(b["frame"], 1)
        self.assertNotEqual(a["rows"], b["rows"])

    def test_mood_override(self) -> None:
        handler = _handler(hunger=80, mood=70, energy=80)  # default playful/happy
        happy = handler.handle(_req("pet.avatar", {"frame": 0, "mood": "joyful"})).payload
        sad = handler.handle(_req("pet.avatar", {"frame": 0, "mood": "grumpy"})).payload
        self.assertEqual(happy["mood"], "joyful")
        self.assertEqual(sad["mood"], "grumpy")
        self.assertNotEqual(happy["rows"], sad["rows"])

    def test_bad_frame_or_mood_is_bad_payload(self) -> None:
        handler = _handler()
        self.assertEqual(handler.handle(_req("pet.avatar", {"frame": 3})).payload["code"], "bad_payload")
        self.assertEqual(handler.handle(_req("pet.avatar", {"mood": ""})).payload["code"], "bad_payload")
        self.assertEqual(handler.handle(_req("pet.avatar", {"mood": "confused"})).payload["code"], "bad_payload")
        self.assertEqual(handler.handle(_req("pet.avatar", {"mood": 12})).payload["code"], "bad_payload")

    def test_main_deck_parser_accepts_our_payload(self) -> None:
        try:
            import sys
            from pathlib import Path

            main = Path("/workspace/cyberdeck-main")
            if main.is_dir() and str(main) not in sys.path:
                sys.path.insert(0, str(main))
            from cyberdeck.petdata import parse_avatar
        except ImportError:
            self.skipTest("cyberdeck-main is not importable")
        body = encode_avatar(Creature(), frame=0, mood="playful")
        parsed = parse_avatar(body)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.width, 16)
        self.assertEqual(len(parsed.rows), 16)
        self.assertEqual(parsed.form, "chirplet")
        # Index 0 is transparent in both palettes.
        self.assertIsNone(parsed.palette[0])


if __name__ == "__main__":
    unittest.main()
