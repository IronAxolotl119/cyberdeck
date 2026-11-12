"""pet.discovery: XP, dedupe, themed moves, ranks, environment, sleep."""

from __future__ import annotations

import random
import time
import unittest

from discovery.sighting import (
    ACTIVITY_WINDOW,
    BUSY_AT,
    KIND_MOVES,
    apply_sighting,
    clamp_interest,
    environment_state,
    xp_for,
)
from link.handler import CompanionHandler, pet_payload
from link.protocol import TYPE_ERROR, TYPE_HELLO_OK, TYPE_PET_DISCOVERY_OK, Message
from pet.combat import outgoing_damage, perform_action, stats_payload
from pet.creature import Creature, level_for_xp
from pet.moves import get_move
from pet.stats import Stats
from save import load_creature, save_creature


def _req(typ: str, payload: dict | None = None) -> Message:
    return Message.request(typ, payload or {})


def _handler(creature: Creature | None = None) -> CompanionHandler:
    return CompanionHandler(creature or Creature(name="Pulse", stats=Stats()))


class InterestAndXpTests(unittest.TestCase):
    def test_interest_is_clamped_to_1_3(self) -> None:
        self.assertEqual(clamp_interest(None), 1)
        self.assertEqual(clamp_interest("nope"), 1)
        self.assertEqual(clamp_interest(0), 1)
        self.assertEqual(clamp_interest(-4), 1)
        self.assertEqual(clamp_interest(1), 1)
        self.assertEqual(clamp_interest(2.4), 2)
        self.assertEqual(clamp_interest(3), 3)
        self.assertEqual(clamp_interest(99), 3)

    def test_xp_scales_with_kind_and_interest(self) -> None:
        self.assertEqual(xp_for("wifi", 1), 4)
        self.assertEqual(xp_for("wifi", 2), 7)
        self.assertEqual(xp_for("wifi", 3), 10)
        self.assertEqual(xp_for("bt", 1), 3)
        self.assertEqual(xp_for("bt", 2), 6)
        self.assertEqual(xp_for("bt", 3), 9)
        self.assertEqual(xp_for("lan", 1), 3)
        self.assertEqual(xp_for("lan", 3), 9)
        self.assertEqual(xp_for("rfid", 1), 8)
        self.assertEqual(xp_for("rfid", 3), 14)

    def test_asleep_xp_is_halved(self) -> None:
        self.assertEqual(xp_for("rfid", 3, asleep=True), 7)
        self.assertEqual(xp_for("wifi", 1, asleep=True), 2)
        self.assertEqual(xp_for("bt", 1, asleep=True), 2)


class PayloadErrorTests(unittest.TestCase):
    def test_missing_kind_is_bad_payload(self) -> None:
        reply = _handler().handle(_req("pet.discovery", {"id": "aa:bb"}))
        self.assertEqual(reply.type, TYPE_ERROR)
        self.assertEqual(reply.payload["code"], "bad_payload")

    def test_blank_kind_is_bad_payload(self) -> None:
        reply = _handler().handle(_req("pet.discovery", {"kind": "  ", "id": "x"}))
        self.assertEqual(reply.payload["code"], "bad_payload")

    def test_unknown_kind_is_bad_payload(self) -> None:
        reply = _handler().handle(_req("pet.discovery", {"kind": "zigbee", "id": "x"}))
        self.assertEqual(reply.type, TYPE_ERROR)
        self.assertEqual(reply.payload["code"], "bad_payload")

    def test_missing_id_is_bad_payload(self) -> None:
        reply = _handler().handle(_req("pet.discovery", {"kind": "wifi"}))
        self.assertEqual(reply.payload["code"], "bad_payload")

    def test_blank_id_is_bad_payload(self) -> None:
        reply = _handler().handle(_req("pet.discovery", {"kind": "lan", "id": "  "}))
        self.assertEqual(reply.payload["code"], "bad_payload")


class DiscoveryHandlerTests(unittest.TestCase):
    def test_first_sighting_awards_xp_and_ok_shape(self) -> None:
        handler = _handler()
        handler.rng = random.Random(1)
        # Force no learn so the reply is a clean XP-only case.
        handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
        reply = handler.handle(
            _req(
                "pet.discovery",
                {"kind": "wifi", "id": "04:12:ab", "interest": 1, "label": "badge"},
            )
        )
        self.assertEqual(reply.type, TYPE_PET_DISCOVERY_OK)
        body = reply.payload
        self.assertEqual(body["xp"], 4)
        self.assertIsNone(body["learned_move"])
        self.assertIsNone(body["strengthened_move"])
        self.assertIn("badge", body["reaction"])
        self.assertEqual(body["level"], 1)
        self.assertFalse(body["level_up"])
        self.assertIn("pet", body)
        self.assertEqual(body["pet"]["xp"], 4)
        self.assertIn("environment", body["pet"])
        self.assertIn("busy", body["pet"]["environment"])
        self.assertIn("buff", body["pet"]["environment"])

    def test_interest_outside_range_is_clamped_not_an_error(self) -> None:
        handler = _handler()
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
        high = handler.handle(_req("pet.discovery", {"kind": "wifi", "id": "ap-1", "interest": 99}))
        self.assertEqual(high.type, TYPE_PET_DISCOVERY_OK)
        self.assertEqual(high.payload["xp"], 10)
        low = handler.handle(_req("pet.discovery", {"kind": "bt", "id": "ph-1", "interest": 0}))
        self.assertEqual(low.payload["xp"], 3)

    def test_repeat_kind_and_id_gives_zero_xp_gentle_reaction(self) -> None:
        handler = _handler()
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
        first = handler.handle(_req("pet.discovery", {"kind": "wifi", "id": "aa:bb", "interest": 2}))
        self.assertEqual(first.payload["xp"], 7)
        xp_after = first.payload["pet"]["xp"]
        second = handler.handle(_req("pet.discovery", {"kind": "wifi", "id": "aa:bb", "interest": 3}))
        self.assertEqual(second.type, TYPE_PET_DISCOVERY_OK)
        self.assertEqual(second.payload["xp"], 0)
        self.assertIsNone(second.payload["learned_move"])
        self.assertIsNone(second.payload["strengthened_move"])
        self.assertIn("already", second.payload["reaction"].lower())
        self.assertEqual(second.payload["pet"]["xp"], xp_after)

    def test_same_id_different_kind_is_a_new_find(self) -> None:
        handler = _handler()
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
        a = handler.handle(_req("pet.discovery", {"kind": "wifi", "id": "shared"}))
        b = handler.handle(_req("pet.discovery", {"kind": "bt", "id": "shared"}))
        self.assertEqual(a.payload["xp"], 4)
        self.assertEqual(b.payload["xp"], 3)

    def test_hello_accepts_and_ignores_client_field(self) -> None:
        handler = _handler()
        reply = handler.handle(
            _req("hello", {"role": "main", "proto": 1, "events": True, "client": "dash"})
        )
        self.assertEqual(reply.type, TYPE_HELLO_OK)
        self.assertTrue(reply.payload.get("events") is True)
        self.assertNotIn("client", reply.payload)
        self.assertEqual(reply.payload["pet"], "PULSE")

    def test_status_includes_environment(self) -> None:
        handler = _handler()
        status = handler.handle(_req("pet.status", {}))
        env = status.payload["pet"]["environment"]
        self.assertEqual(env["busy"], 0.0)
        self.assertEqual(env["buff"], "calm")


class LearnAndStrengthenTests(unittest.TestCase):
    def test_learn_chance_picks_a_kind_themed_move(self) -> None:
        handler = _handler()
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.0  # type: ignore[method-assign]
        reply = handler.handle(
            _req("pet.discovery", {"kind": "lan", "id": "host-1", "interest": 3, "label": "nas"})
        )
        self.assertEqual(reply.type, TYPE_PET_DISCOVERY_OK)
        learned = reply.payload["learned_move"]
        self.assertIsNotNone(learned)
        self.assertIn(learned, {"Ping Echo", "ARP Whisper"})
        self.assertIsNone(reply.payload["strengthened_move"])
        self.assertIn(learned, reply.payload["pet"]["moves"])

    def test_learned_move_event_is_emitted(self) -> None:
        handler = _handler()
        handler.events_enabled = True
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.0  # type: ignore[method-assign]
        handler.handle(_req("pet.discovery", {"kind": "rfid", "id": "tag-1", "interest": 3}))
        events = handler.drain_events()
        kinds = [e.payload["event"] for e in events]
        self.assertIn("learned_move", kinds)
        learned = next(e for e in events if e.payload["event"] == "learned_move")
        self.assertIn(learned.payload["detail"]["move"], {"Tag Bite", "Near Field"})

    def test_strengthen_when_pool_is_already_known(self) -> None:
        pet = Creature(name="Pulse", stats=Stats())
        for move_id in KIND_MOVES["wifi"]:
            pet.learn_move(move_id)
        handler = _handler(pet)
        handler.events_enabled = True
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.0  # type: ignore[method-assign]
        reply = handler.handle(
            _req("pet.discovery", {"kind": "wifi", "id": "ap-strong", "interest": 3})
        )
        self.assertIsNone(reply.payload["learned_move"])
        self.assertIsNotNone(reply.payload["strengthened_move"])
        self.assertIn(reply.payload["strengthened_move"], {"Signal Sniff", "Beacon Burst", "Channel Hop"})
        events = handler.drain_events()
        kinds = [e.payload["event"] for e in events]
        self.assertIn("move_strengthened", kinds)
        ev = next(e for e in events if e.payload["event"] == "move_strengthened")
        self.assertEqual(ev.payload["detail"]["move"], reply.payload["strengthened_move"])
        self.assertEqual(ev.payload["detail"]["rank"], 1)
        self.assertEqual(ev.payload["detail"]["bonus"], 2)

    def test_rank_is_saved_and_reflected_in_combat_stats(self) -> None:
        pet = Creature(name="Pulse", stats=Stats())
        pet.learn_move("tag_bite")
        pet.strengthen_move("tag_bite")
        pet.strengthen_move("tag_bite")
        self.assertEqual(pet.move_rank("tag_bite"), 2)
        move = get_move("tag_bite")
        self.assertEqual(pet.effective_power(move), move.power + 4)
        payload = stats_payload(pet)
        row = next(m for m in payload["moves"] if m["name"] == "Tag Bite")
        self.assertEqual(row["rank"], 2)
        self.assertEqual(row["bonus"], 4)
        self.assertEqual(row["power"], move.power + 4)
        # Damage uses effective power, not the base.
        base = outgoing_damage(move, pet.stats.attack, 0)
        boosted = outgoing_damage(move, pet.stats.attack, 0, power=pet.effective_power(move))
        self.assertEqual(boosted - base, 4)
        action = perform_action(
            pet,
            "Tag Bite",
            {"name": "dummy", "defense": 0, "speed": 1},
            random.Random(0),
        )
        # Seed 0 may miss; compare against the formula when it hits.
        if action["hit"] and action["move"] == "Tag Bite":
            self.assertEqual(action["damage"], boosted)

    def test_rank_cap_is_five(self) -> None:
        pet = Creature(name="Pulse")
        pet.learn_move("ping_echo")
        for _ in range(10):
            pet.strengthen_move("ping_echo")
        self.assertEqual(pet.move_rank("ping_echo"), 5)
        self.assertEqual(pet.effective_power(get_move("ping_echo")), get_move("ping_echo").power + 10)

    def test_sleeping_records_find_with_reduced_xp_and_no_learn(self) -> None:
        pet = Creature(name="Pulse", stats=Stats(), asleep=True)
        handler = _handler(pet)
        handler.events_enabled = True
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.0  # type: ignore[method-assign]
        reply = handler.handle(
            _req("pet.discovery", {"kind": "rfid", "id": "tag-sleep", "interest": 3, "label": "keycard"})
        )
        self.assertEqual(reply.type, TYPE_PET_DISCOVERY_OK)
        self.assertEqual(reply.payload["xp"], 7)  # half of 14
        self.assertIsNone(reply.payload["learned_move"])
        self.assertIsNone(reply.payload["strengthened_move"])
        self.assertIn("sleep", reply.payload["reaction"].lower())
        self.assertTrue(pet.asleep)
        # Recorded: a later awake repeat is still a gentle zero.
        pet.asleep = False
        again = handler.handle(_req("pet.discovery", {"kind": "rfid", "id": "tag-sleep", "interest": 3}))
        self.assertEqual(again.payload["xp"], 0)
        kinds = [e.payload["event"] for e in handler.drain_events()]
        self.assertNotIn("learned_move", kinds)
        self.assertNotIn("move_strengthened", kinds)

    def test_level_up_event_from_discovery_xp(self) -> None:
        # Level 1 needs 20 XP to reach 2? Check quickly via add_xp path.
        pet = Creature(name="Pulse", stats=Stats(level=1, xp=19))
        # rfid interest 1 is 8 XP, enough to cross a nearby threshold if any.
        handler = _handler(pet)
        handler.events_enabled = True
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
        before = pet.stats.level
        reply = handler.handle(_req("pet.discovery", {"kind": "rfid", "id": "lvl", "interest": 3}))
        if pet.stats.level > before:
            self.assertTrue(reply.payload["level_up"])
            kinds = [e.payload["event"] for e in handler.drain_events()]
            self.assertIn("level_up", kinds)
        else:
            # If the curve needs more than 14 XP, pump the bar and retry.
            pet.stats.xp = 10_000
            pet2 = Creature(name="Pulse", stats=Stats(level=1, xp=0))
            # Discover the curve by granting a known amount.
            need = 1
            while level_for_xp(need) <= 1:
                need += 1
            pet2.stats.xp = need - 1
            handler = _handler(pet2)
            handler.events_enabled = True
            handler.rng = random.Random(0)
            handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
            reply = handler.handle(_req("pet.discovery", {"kind": "rfid", "id": "lvl2", "interest": 3}))
            self.assertTrue(reply.payload["level_up"])
            self.assertGreater(reply.payload["level"], 1)
            kinds = [e.payload["event"] for e in handler.drain_events()]
            self.assertIn("level_up", kinds)


class EnvironmentTests(unittest.TestCase):
    def test_brand_new_pet_is_calm_not_bored(self) -> None:
        env = Creature(name="Pulse").environment(now=1000.0)
        self.assertEqual(env["busy"], 0.0)
        self.assertEqual(env["buff"], "calm")

    def test_busy_window_gives_mood_and_speed_buff(self) -> None:
        pet = Creature(name="Pulse", stats=Stats(mood=70, speed=5))
        now = time.monotonic()
        for i in range(BUSY_AT):
            key = f"lan:host-{i}"
            pet.sighting_keys.add(key)
            pet.note_sighting(now - 1.0, 3)
        env = pet.environment()
        self.assertEqual(env["busy"], 1.0)
        self.assertIn("busy", env["buff"])
        mood_bonus, speed_bonus = pet.environment_bonuses()
        self.assertGreater(mood_bonus, 0)
        self.assertGreater(speed_bonus, 0)
        payload = pet_payload(pet)
        # Temporary: stored mood/speed unchanged, status is boosted.
        self.assertEqual(pet.stats.mood, 70)
        self.assertEqual(pet.stats.speed, 5)
        self.assertEqual(payload["happiness"], 70 + mood_bonus)
        self.assertEqual(payload["speed"], 5 + speed_bonus)
        self.assertEqual(stats_payload(pet)["speed"], 5 + speed_bonus)

    def test_quiet_stretch_after_finds_is_bored(self) -> None:
        pet = Creature(name="Pulse")
        pet.sighting_keys.add("wifi:old")
        pet.note_sighting(time.monotonic() - ACTIVITY_WINDOW - 1, 2)
        env = pet.environment()
        self.assertEqual(env["busy"], 0.0)
        self.assertTrue(str(env["buff"]).startswith("bored"))
        mood_bonus, speed_bonus = pet.environment_bonuses()
        self.assertLess(mood_bonus, 0)
        self.assertEqual(speed_bonus, 0)

    def test_repeats_do_not_fill_the_busy_window(self) -> None:
        handler = _handler()
        handler.rng = random.Random(0)
        handler.rng.random = lambda: 0.99  # type: ignore[method-assign]
        first = handler.handle(_req("pet.discovery", {"kind": "bt", "id": "phone"}))
        busy_after_first = first.payload["pet"]["environment"]["busy"]
        for _ in range(10):
            reply = handler.handle(_req("pet.discovery", {"kind": "bt", "id": "phone"}))
        self.assertEqual(reply.payload["pet"]["environment"]["busy"], busy_after_first)

    def test_environment_state_scales_busy(self) -> None:
        now = 50.0
        recent = [(now - 1, 1) for _ in range(4)]
        state = environment_state(recent, now=now, last_find_at=now - 1)
        self.assertEqual(state["busy"], round(4 / BUSY_AT, 3))


class SaveLoadTests(unittest.TestCase):
    def test_sighting_keys_and_ranks_round_trip(self) -> None:
        import tempfile
        from pathlib import Path

        pet = Creature(name="Pulse")
        apply_sighting(
            pet,
            kind="lan",
            ident="printer",
            interest=2,
            rng=random.Random(0),
        )
        # Force a rank even if the roll did not strengthen.
        pet.learn_move("arp_whisper")
        pet.strengthen_move("arp_whisper")
        snapshot = pet.to_dict()
        self.assertIn("lan:printer", snapshot["sighting_keys"])
        self.assertEqual(snapshot["move_ranks"].get("arp_whisper"), 1)
        clone = Creature.from_dict(snapshot)
        self.assertIn("lan:printer", clone.sighting_keys)
        self.assertEqual(clone.move_rank("arp_whisper"), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pet.json"
            save_creature(pet, path)
            loaded = load_creature(path)
            self.assertEqual(loaded.to_dict()["sighting_keys"], snapshot["sighting_keys"])
            self.assertEqual(loaded.to_dict()["move_ranks"], snapshot["move_ranks"])


if __name__ == "__main__":
    unittest.main()
