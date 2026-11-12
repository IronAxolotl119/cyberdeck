"""pet.discovery on the deck's built-in sim pet (cyberdeck/companion.py).

The combined interface sends the dashboard's Wi-Fi / Bluetooth / LAN /
RFID finds to PULSE over the deck's one link. With --link sim (or auto
and no companion running) the in-process sim pet answers, following the
same rules as the real companion (companion/docs/DISCOVERY.md).
"""

import unittest

from cyberdeck.companion import DISCOVERY_MOVES, MAX_MOVE_RANK, PetState, SimCompanionLink


class SimDiscovery(unittest.TestCase):
    def setUp(self):
        self.link = SimCompanionLink()
        self.link.connect()

    def ask(self, **payload):
        return self.link.request("pet.discovery", payload)

    def test_xp_table_and_dedupe(self):
        first = self.ask(kind="wifi", id="aa:bb", interest=2, label="cafe-guest")
        self.assertEqual(first.type, "pet.discovery.ok")
        self.assertEqual(first.payload["xp"], 7)
        self.assertIn("environment", first.payload["pet"])
        again = self.ask(kind="wifi", id="aa:bb", interest=2)
        self.assertEqual(again.payload["xp"], 0)
        self.assertIn("already logged", again.payload["reaction"])
        self.assertEqual(self.ask(kind="rfid", id="04:a2", interest=9).payload["xp"], 14)   # interest clamped to 3

    def test_bad_payload(self):
        self.assertEqual(self.ask(kind="laser", id="x").payload["code"], "bad_payload")
        self.assertEqual(self.ask(kind="bt", id=" ").payload["code"], "bad_payload")

    def test_asleep_halves_xp_and_learns_nothing(self):
        self.link.pet.asleep = True
        reply = self.ask(kind="rfid", id="t1", interest=3)
        self.assertEqual(reply.payload["xp"], 7)
        self.assertIsNone(reply.payload["learned_move"])

    def test_learn_then_strengthen_with_ranks(self):
        pet = PetState()
        pet.rng.random = lambda: 0.0          # always interact with the move pool
        pool = DISCOVERY_MOVES["lan"]
        learned = [pet.discover("lan", f"h{i}", 1)["learned_move"] for i in range(len(pool))]
        self.assertEqual(sorted(learned), sorted(pool))
        res = pet.discover("lan", "h-next", 1)
        self.assertIn(res["strengthened_move"], pool)
        stats = {m["name"]: m for m in pet.combat_stats()["moves"]}
        self.assertEqual(stats[res["strengthened_move"]]["rank"], 1)
        self.assertEqual(stats[res["strengthened_move"]]["bonus"], 2)
        for i in range(20):
            pet.discover("lan", f"more{i}", 1)
        self.assertTrue(all(r <= MAX_MOVE_RANK for r in pet.move_ranks.values()))


if __name__ == "__main__":
    unittest.main()
