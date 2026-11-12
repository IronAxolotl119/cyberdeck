"""tests/test_companion.py — simulated pet and companion link.

The sim companion is what `pet` and `ping` talk to when no second
board is plugged in. These tests check the protocol replies, that
feed/play change the pet, and that needs drift as time passes.
"""

from __future__ import annotations

import unittest

from cyberdeck.companion import CompanionError, PetState, SimCompanionLink
from cyberdeck.protocol import (
    TYPE_ERROR,
    TYPE_HELLO_OK,
    TYPE_PET_FEED,
    TYPE_PET_FEED_OK,
    TYPE_PET_PLAY,
    TYPE_PET_STATUS,
    TYPE_PET_STATUS_OK,
    TYPE_PING,
    TYPE_PONG,
    Message,
)


class PetStateTests(unittest.TestCase):
    """Direct tests of the fake pet, without the link wrapper."""

    def test_feed_lowers_hunger_and_raises_happiness(self):
        pet = PetState(hunger=60, happiness=40, energy=80)
        result = pet.feed("ration")
        self.assertLess(pet.hunger, 60)
        self.assertGreater(pet.happiness, 40)
        self.assertEqual(result["item"], "ration")
        self.assertLess(result["hunger_delta"], 0)
        self.assertEqual(result["pet"]["hunger"], int(round(pet.hunger)))

    def test_play_raises_happiness_and_spends_energy(self):
        pet = PetState(hunger=30, happiness=50, energy=80)
        result = pet.play("spar")
        self.assertTrue(result["played"])
        self.assertGreater(pet.happiness, 50)
        self.assertLess(pet.energy, 80)
        self.assertGreater(pet.hunger, 30)

    def test_play_refused_when_exhausted(self):
        pet = PetState(energy=5, happiness=50)
        result = pet.play("spar")
        self.assertFalse(result["played"])
        self.assertEqual(result["reason"], "too tired")
        self.assertEqual(pet.energy, 5)

    def test_tick_increases_hunger_over_time(self):
        pet = PetState(hunger=20, happiness=80, energy=40)
        pet.tick(60 * 10)  # ten simulated minutes
        self.assertGreater(pet.hunger, 20)
        self.assertLess(pet.happiness, 80)
        self.assertGreater(pet.energy, 40)  # rest recovers energy

    def test_mood_hungry_when_hunger_is_high(self):
        self.assertEqual(PetState(hunger=90, happiness=80, energy=80).mood(), "hungry")


class SimCompanionLinkTests(unittest.TestCase):
    """The in-process link should speak the real protocol."""

    def setUp(self):
        # A controllable clock lets us jump time without waiting.
        self.now = 1000.0
        self.link = SimCompanionLink(clock=lambda: self.now, pet=PetState(hunger=50, happiness=60, energy=70))
        self.link.connect()

    def test_connect_marks_linked_and_hello_succeeds(self):
        self.assertTrue(self.link.is_linked())
        self.assertIn("SIM", self.link.link_label())
        self.assertEqual(self.link._hello["role"], "companion")
        self.assertEqual(self.link._hello["pet"], "PULSE")

    def test_ping_returns_pong_with_same_id(self):
        req = Message.request(TYPE_PING, {})
        reply = self.link.handle(req)
        self.assertEqual(reply.type, TYPE_PONG)
        self.assertEqual(reply.id, req.id)
        self.assertTrue(reply.payload.get("ok"))

    def test_request_ping_via_public_api(self):
        reply = self.link.request(TYPE_PING, {})
        self.assertEqual(reply.type, TYPE_PONG)

    def test_status_returns_pet_payload(self):
        reply = self.link.request(TYPE_PET_STATUS, {})
        self.assertEqual(reply.type, TYPE_PET_STATUS_OK)
        pet = reply.payload["pet"]
        self.assertEqual(pet["name"], "PULSE")
        self.assertIn("hunger", pet)
        self.assertIn("mood", pet)

    def test_feed_changes_state_reported_by_status(self):
        before = self.link.request(TYPE_PET_STATUS, {}).payload["pet"]["hunger"]
        feed = self.link.request(TYPE_PET_FEED, {"item": "ration"})
        self.assertEqual(feed.type, TYPE_PET_FEED_OK)
        after = feed.payload["pet"]["hunger"]
        self.assertLess(after, before)

    def test_unknown_type_returns_error_reply(self):
        req = Message.request("teleport", {})
        reply = self.link.handle(req)
        self.assertEqual(reply.type, TYPE_ERROR)
        self.assertEqual(reply.id, req.id)
        self.assertEqual(reply.payload["code"], "unknown_type")

    def test_state_changes_when_clock_advances(self):
        hunger_before = self.link.pet.hunger
        self.now += 60 * 15  # fifteen minutes
        after = self.link.request(TYPE_PET_STATUS, {}).payload["pet"]["hunger"]
        self.assertGreater(after, hunger_before)

    def test_request_fails_when_link_is_down(self):
        self.link.close()
        self.assertFalse(self.link.is_linked())
        with self.assertRaises(CompanionError):
            self.link.request(TYPE_PING, {})

    def test_hello_ok_type_constant(self):
        # Sanity check so the handshake name stays aligned with PROTOCOL.md.
        self.assertEqual(TYPE_HELLO_OK, "hello.ok")
        self.assertEqual(TYPE_PET_PLAY, "pet.play")


if __name__ == "__main__":
    unittest.main()
