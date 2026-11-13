"""Network dungeons: discovered wifi / bt / lan signals become dungeons."""

from __future__ import annotations

import json
import random
import unittest
from datetime import datetime

from link.handler import CompanionHandler
from link.protocol import Message, TYPE_ERROR
from pet import dungeons as dgn
from pet.creature import Creature
from pet.life import Life
from pet.speech import fill, load_pools
from pet.stats import Stats

T0 = datetime(2026, 11, 12, 14, 0).timestamp()
MAC1 = "AA:BB:CC:DD:EE:01"
MAC2 = "AA:BB:CC:DD:EE:02"
BT1 = "11:22:33:44:55:66"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


def make(species: str = "luminode", **stats) -> tuple[CompanionHandler, Clock]:
    clock = Clock()
    pet = Creature(name="Pulse", species_id=species, stats=Stats(**({"level": 12} | stats)),
                   life=Life.new(random.Random(3), T0 - 9 * 86400))
    pet.life.last_now = T0
    handler = CompanionHandler(pet, clock=clock)
    handler.rng = random.Random(3)
    handler.events_enabled = True
    return handler, clock


def req(handler: CompanionHandler, typ: str, payload: dict | None = None) -> Message:
    return handler.handle(Message.request(typ, payload or {}))


def discover(handler: CompanionHandler, kind: str, ident: str, label: str | None = None, **extra) -> dict:
    payload = {"kind": kind, "id": ident, **extra}
    if label is not None:
        payload["label"] = label
    return req(handler, "pet.discovery", payload).payload


def events(handler: CompanionHandler, name: str) -> list[dict]:
    return [m.payload["detail"] for m in handler._pending if m.payload.get("event") == name]


def check_text(case: unittest.TestCase, text: str) -> None:
    case.assertIsInstance(text, str)
    case.assertTrue(text.isascii(), repr(text))
    case.assertLessEqual(len(text), 44, repr(text))


class IdentityTests(unittest.TestCase):
    def test_same_id_same_dungeon(self) -> None:
        bodies = []
        for _ in range(2):
            h, _clock = make()
            did = discover(h, "wifi", MAC1, "CafeNet-5G", rssi=-60)["dungeon"]["id"]
            enter = req(h, "pet.dungeon.enter", {"id": did}).payload
            later = [h.dungeon_floor_info(did, h.creature.life.dungeons[did], f, "") for f in (2, 5, 13)]
            bodies.append((did, {k: enter[k] for k in ("seed", "layout", "theme", "enemies", "boss", "flavor")}, later))
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(dgn.dungeon_id("wifi", MAC1, "x"), dgn.dungeon_id("wifi", MAC1.lower(), "renamed"))
        self.assertNotEqual(dgn.dungeon_id("wifi", MAC1, "x"), dgn.dungeon_id("wifi", MAC2, "x"))
        self.assertNotEqual(dgn.seed_for("aaaa0000"), dgn.seed_for("aaaa0001"))
        self.assertEqual(dgn.dungeon_id("lan", "", "printer"), dgn.dungeon_id("lan", None, "PRINTER"))

    def test_ids_and_names_are_display_safe(self) -> None:
        h, _ = make()
        discover(h, "wifi", MAC1, "Caf\u00e9 \u2014 Super Long Network Name 5GHz Extra")
        discover(h, "wifi", MAC2)  # hidden: no label, the "name" is the raw BSSID
        discover(h, "bt", BT1, BT1)
        rows = req(h, "pet.dungeons").payload["dungeons"]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertRegex(row["id"], r"^[0-9a-f]{8}$")
            self.assertTrue(row["name"].isascii())
            self.assertLessEqual(len(row["name"]), dgn.NAME_MAX)
        hidden = [r for r in rows if r["name"].startswith("hidden-")]
        self.assertEqual(len(hidden), 2)
        for row in hidden:
            self.assertEqual(row["name"], f"hidden-{row['id'][:4]}")
            self.assertTrue(row["rare"])
        # Raw identifiers never reach the deck, the save, or territory memory.
        did = rows[0]["id"]
        wire = json.dumps([req(h, "pet.dungeons").payload, req(h, "pet.dungeon.enter", {"id": did}).payload,
                           req(h, "pet.dungeon.floor", {"id": did, "floor": 1, "result": "cleared"}).payload,
                           h.creature.life.dungeons, req(h, "pet.stats").payload]).lower()
        for raw in (MAC1, MAC2, BT1):
            self.assertNotIn(raw.lower(), wire)
        self.assertNotIn(MAC2, h.creature.life.networks)


class ThemeAndDifficultyTests(unittest.TestCase):
    def test_theme_by_source(self) -> None:
        h, _ = make()
        discover(h, "wifi", MAC1, "HomeNet", rssi=-60)
        discover(h, "bt", BT1, "JBL Flip", rssi=-60)
        discover(h, "lan", "10.0.0.7", "printer.local")
        discover(h, "wifi", MAC2, rssi=-60)  # hidden
        discover(h, "rfid", "04:A2:B3", "badge")  # no dungeon for rfid
        types = {r["name"]: r["type"] for r in req(h, "pet.dungeons").payload["dungeons"]}
        self.assertEqual(types.pop("HomeNet"), "NET")
        self.assertEqual(types.pop("JBL Flip"), "VOLT")
        self.assertEqual(types.pop("printer.local"), "DATA")
        self.assertEqual(list(types.values()), ["VOID"])
        for rec in h.creature.life.dungeons.values():
            self.assertNotIn(rec["type2"], (rec["type"], "NORM"))
        self.assertEqual(dgn.theme_for("abcd1234", "wifi", rare=True)[0], "VOID")

    def test_difficulty_from_signal_and_security(self) -> None:
        self.assertLess(dgn.difficulty_for(-35, False, False), dgn.difficulty_for(-88, False, False))
        self.assertEqual(dgn.difficulty_for(-60, True, False), dgn.difficulty_for(-60, False, False) + 1)
        for rssi in (-10, -40, -60, -90, -120, None):
            for secured in (False, True):
                for rare in (False, True):
                    self.assertTrue(1 <= dgn.difficulty_for(rssi, secured, rare) <= 10)
        h, _ = make()
        easy = discover(h, "wifi", MAC1, "Loud", rssi=-42)["dungeon"]["id"]
        hard = discover(h, "wifi", MAC2, "Faint", rssi=-86, security="wpa2")["dungeon"]["id"]
        dun = h.creature.life.dungeons
        self.assertTrue(dun[hard]["secured"])
        self.assertLess(dun[easy]["difficulty"], dun[hard]["difficulty"])
        self.assertLess(dgn.enemy_level(dun[easy], 1), dgn.enemy_level(dun[hard], 1))

    def test_depth_scaling_and_bosses(self) -> None:
        _, rec = dgn.new_record("wifi", MAC1, "Cafe", -60, T0)
        did = dgn.dungeon_id("wifi", MAC1, "Cafe")
        levels = [dgn.enemy_level(rec, f) for f in range(1, 31)]
        self.assertEqual(levels, sorted(levels))
        self.assertGreater(levels[-1], levels[0])
        for floor in range(1, 31):
            boss = dgn.boss_for(did, rec, floor)
            if floor % 5 == 0:
                self.assertEqual(boss["name"], "kernel panic")
                self.assertGreater(boss["level"], max(e["level"] for e in dgn.enemies_for(did, rec, floor)))
                self.assertLessEqual(len(boss["title"]), 20)
            else:
                self.assertIsNone(boss)
        secured = dict(rec, secured=True)
        self.assertEqual(dgn.boss_for(did, secured, 5)["level"], dgn.boss_for(did, rec, 5)["level"] + 1)

    def test_enemy_mix_leans_on_theme(self) -> None:
        for kind, theme in (("wifi", "NET"), ("bt", "VOLT"), ("lan", "DATA")):
            _, rec = dgn.new_record(kind, "id-" + kind, "Some Place", -60, T0)
            weights = dgn.enemy_weights(rec, 10)
            from pet.battle import load_roster

            types = {e["id"]: e["type"] for e in load_roster()}
            themed = [w for eid, w in weights.items() if types[eid] == theme]
            other = [w for eid, w in weights.items() if types[eid] not in (theme, rec["type2"])]
            if themed:
                self.assertGreater(min(themed), max(other) * 0.99)
            self.assertNotIn("kernel_panic", weights)
        # Shallow floors only use shallow roster entries.
        self.assertTrue(all(eid in {"bug", "glitch", "spam", "popup"} for eid in dgn.enemy_weights(rec, 1)))

    def test_layout_bounds_and_growth(self) -> None:
        shallow, deep = [], []
        for n in range(40):
            for kind in ("wifi", "bt", "lan"):
                did, rec = dgn.new_record(kind, f"dev-{n}", f"net{n}", -40 - n, T0, secured=n % 2 == 0)
                for floor in (1, 2, 5, 9, 15, 25, 40, 99):
                    lay = dgn.layout(did, rec, floor)
                    self.assertTrue(40 <= lay["width"] <= 72)
                    self.assertTrue(13 <= lay["height"] <= 21)
                    self.assertTrue(1 <= lay["rooms_min"] <= lay["rooms_max"] <= 10)
                    self.assertEqual(len(lay["room_w"]), 2)
                    self.assertLessEqual(lay["room_w"][0], lay["room_w"][1])
                    self.assertLessEqual(lay["room_h"][0], lay["room_h"][1])
                    self.assertIn(lay["corridor"], ("straight", "winding"))
                    self.assertTrue(0 <= lay["loops"] <= 3)
                    self.assertTrue(0 <= lay["traps"] <= 5)
                    self.assertTrue(0 <= lay["items"] <= 5)
                    self.assertIsInstance(lay["dark"], bool)
                shallow.append(dgn.layout(did, rec, 1))
                deep.append(dgn.layout(did, rec, 25))
        avg = lambda rows, k: sum(r[k] for r in rows) / len(rows)  # noqa: E731
        for key in ("width", "height", "traps", "rooms_max"):
            self.assertGreater(avg(deep, key), avg(shallow, key))
        self.assertGreater(sum(r["dark"] for r in deep), sum(r["dark"] for r in shallow))

    def test_loot_scales_with_depth(self) -> None:
        did, rec = dgn.new_record("wifi", MAC1, "Cafe", -60, T0)
        from pet.battle import ITEMS

        def value(floor: int) -> float:
            items = [i for s in range(30) for i in dgn.roll_loot(did, rec, floor, False, s)]
            return sum(ITEMS[i][2] for i in items) / 30

        self.assertLess(value(1), value(12))
        self.assertGreater(dgn.loot_mult(rec, 20), dgn.loot_mult(rec, 1))


class ProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h, self.clock = make()
        self.did = discover(self.h, "wifi", MAC1, "CafeNet", rssi=-60)["dungeon"]["id"]

    def floor(self, floor: int, result: str = "cleared", **extra) -> dict:
        reply = req(self.h, "pet.dungeon.floor", {"id": self.did, "floor": floor, "result": result, **extra})
        self.assertNotEqual(reply.type, TYPE_ERROR, reply.payload)
        return reply.payload

    def test_checkpoints_and_persistence(self) -> None:
        enter = req(self.h, "pet.dungeon.enter", {"id": self.did}).payload
        self.assertEqual(enter["floor"], 1)
        self.assertTrue(enter["checkpoint"])
        for floor in range(1, 6):
            out = self.floor(floor)
            self.assertEqual(out["depth_record"], floor)
            self.assertTrue(out["new_record"])
            self.assertEqual(out["checkpoint_reached"], floor == 5)
            self.assertEqual(out["next"]["floor"], floor + 1)
        self.assertIsNotNone(self.h.dungeon_floor_info(self.did, self.h.creature.life.dungeons[self.did], 5, "")["boss"])
        self.assertTrue(out["boss_beaten"])
        self.assertTrue(out["next"]["checkpoint"])
        # Save round trip, then the default start is the checkpoint (floor 6).
        pet = Creature.from_dict(json.loads(json.dumps(self.h.creature.to_dict())))
        h2 = CompanionHandler(pet, clock=self.clock)
        row = req(h2, "pet.dungeons").payload["dungeons"][0]
        self.assertEqual((row["depth_record"], row["checkpoint"], row["starts"], row["boss_kills"], row["new"]),
                         (5, 6, [1, 6], 1, False))
        self.assertEqual(req(h2, "pet.dungeon.enter", {"id": self.did}).payload["floor"], 6)
        self.assertEqual(req(h2, "pet.dungeon.enter", {"id": self.did, "floor": 1}).payload["floor"], 1)
        self.assertEqual(req(h2, "pet.dungeon.enter", {"id": self.did, "floor": 4}).payload["floor"], 1)
        self.assertEqual(req(h2, "pet.dungeon.enter", {"id": self.did, "floor": 50}).payload["floor"], 6)

    def test_fainted_and_fled_end_the_run(self) -> None:
        req(self.h, "pet.dungeon.enter", {"id": self.did})
        for floor in range(1, 8):
            self.floor(floor)
        lost = self.floor(8, "fainted")
        self.assertIsNone(lost["next"])
        self.assertEqual((lost["depth_record"], lost["checkpoint"], lost["xp"]), (7, 6, 0))
        self.assertEqual(req(self.h, "pet.dungeon.enter", {"id": self.did}).payload["floor"], 6)
        bond = self.h.creature.life.bond
        fled = self.floor(6, "fled")
        self.assertIsNone(fled["next"])
        self.assertEqual(fled["depth_record"], 7)
        self.assertEqual(self.h.creature.life.bond, bond)
        stairs = self.floor(6, "stairs")
        self.assertEqual(stairs["loot"], [])
        self.assertGreater(stairs["xp"], 0)
        left = req(self.h, "pet.dungeon.leave", {"id": self.did, "floor": 7}).payload
        self.assertEqual((left["depth_record"], left["checkpoint"]), (7, 6))
        check_text(self, left["say"])

    def test_bad_requests(self) -> None:
        self.assertEqual(req(self.h, "pet.dungeon.enter", {"id": "nope"}).payload["code"], "unknown_dungeon")
        self.assertEqual(req(self.h, "pet.dungeon.enter", {}).payload["code"], "bad_payload")
        self.assertEqual(req(self.h, "pet.dungeon.enter", {"id": self.did, "floor": 0}).payload["code"], "bad_payload")
        bad = req(self.h, "pet.dungeon.floor", {"id": self.did, "floor": 9, "result": "cleared"})
        self.assertEqual(bad.payload["code"], "bad_payload")
        bad = req(self.h, "pet.dungeon.floor", {"id": self.did, "floor": 1, "result": "won"})
        self.assertEqual(bad.payload["code"], "bad_payload")

    def test_deck_xp_and_loot(self) -> None:
        self.clock.t += 3600  # out of range: x1
        plain = self.floor(1, "fled")
        self.assertEqual(plain["xp"], 0)
        out = self.floor(1, "fled", xp=10, loot=["data cookie", {"name": "odd shell", "kind": "trinket"}])
        self.assertEqual(out["xp"], 10)
        self.assertEqual({r["name"] for r in out["loot"]}, {"data cookie", "odd shell"})


class RangeAndHomeTests(unittest.TestCase):
    def test_in_range_bonus(self) -> None:
        h, clock = make()
        did = discover(h, "wifi", MAC1, "CafeNet", rssi=-60)["dungeon"]["id"]
        row = req(h, "pet.dungeons").payload["dungeons"][0]
        self.assertTrue(row["in_range"])
        self.assertEqual(row["xp_mult"], 1.5)
        hot = req(h, "pet.dungeon.floor", {"id": did, "floor": 1, "result": "cleared"}).payload
        clock.t += dgn.IN_RANGE_WINDOW + 1
        row = req(h, "pet.dungeons").payload["dungeons"][0]
        self.assertFalse(row["in_range"])
        self.assertEqual(row["xp_mult"], 1.0)
        cold = req(h, "pet.dungeon.floor", {"id": did, "floor": 1, "result": "cleared"}).payload
        self.assertEqual(hot["xp"], round(cold["xp"] * 1.5))
        # Battles inside an in-range dungeon get the bonus too, and are counted per dungeon.
        discover(h, "wifi", MAC1, "CafeNet")
        plain = req(h, "pet.combat.end", {"result": "win", "xp": 10}).payload
        boosted = req(h, "pet.combat.end", {"result": "win", "xp": 10, "dungeon_id": did, "floor": 2}).payload
        self.assertEqual((plain["xp"], boosted["xp"], boosted["xp_mult"]), (10, 15, 1.5))
        req(h, "pet.combat.end", {"result": "flee", "dungeon_id": did, "floor": 2})
        self.assertEqual(h.creature.life.dungeons[did]["battles"], {"wins": 1, "losses": 0, "flees": 1})
        self.assertEqual(h.creature.life.record["wins"], 2)

    def test_home_turf(self) -> None:
        h, _ = make(hp=10, max_hp=60)
        far = discover(h, "wifi", MAC2, "Faraway", rssi=-80)["dungeon"]["id"]
        home = discover(h, "wifi", MAC1, "HomeNet", rssi=-50)["dungeon"]["id"]
        h.creature.life.networks["HomeNet"].update(label="home", count=9)
        listing = req(h, "pet.dungeons").payload
        self.assertEqual(listing["home"], home)
        self.assertEqual(listing["dungeons"][0]["id"], home)
        enter = req(h, "pet.dungeon.enter", {"id": home}).payload
        self.assertTrue(enter["home"])
        self.assertFalse(enter["layout"]["dark"])
        rec = h.creature.life.dungeons[home]
        self.assertEqual(dgn.enemy_level(rec, 3), dgn.enemy_level(dict(rec, home=False), 3) - 1)
        out = req(h, "pet.dungeon.floor", {"id": home, "floor": 1, "result": "cleared"}).payload
        self.assertEqual(out["healed"], 9)
        self.assertEqual(out["hp"], 19)
        away = req(h, "pet.dungeon.floor", {"id": far, "floor": 1, "result": "cleared"}).payload
        self.assertEqual(away["healed"], 0)

    def test_sort_order(self) -> None:
        h, clock = make()
        a = discover(h, "wifi", MAC1, "Old Deep")["dungeon"]["id"]
        for f in range(1, 4):
            req(h, "pet.dungeon.floor", {"id": a, "floor": f, "result": "cleared"})
        b = discover(h, "bt", BT1, "Old Shallow")["dungeon"]["id"]
        clock.t += 1000
        c = discover(h, "lan", "10.0.0.9", "Nearby")["dungeon"]["id"]
        order = [r["id"] for r in req(h, "pet.dungeons").payload["dungeons"]]
        self.assertEqual(order, [c, a, b])


class EventAndSaveTests(unittest.TestCase):
    def test_dungeon_found_is_rate_limited(self) -> None:
        h, clock = make()
        discover(h, "wifi", MAC1, "First")
        discover(h, "wifi", MAC2, "Second")
        discover(h, "bt", BT1, "Third")
        h.tick(1)
        found = events(h, "dungeon_found")
        self.assertEqual(len(found), 1)
        self.assertEqual(set(found[0]), {"id", "name", "kind", "type", "rare", "say"})
        self.assertEqual(found[0]["name"], "First")
        check_text(self, found[0]["say"])
        self.assertEqual(events(h, "dungeons_found"), [])
        clock.t += dgn.FOUND_COOLDOWN + 1
        h.tick(1)
        summary = events(h, "dungeons_found")
        self.assertEqual(len(summary), 1)
        self.assertEqual((summary[0]["count"], summary[0]["names"]), (2, ["Second", "Third"]))
        check_text(self, summary[0]["say"])
        # A repeat sighting does not unlock (or announce) anything.
        discover(h, "wifi", MAC1, "First")
        self.assertEqual(len(h.creature.life.dungeons), 3)

    def test_old_saves_load(self) -> None:
        h, _ = make()
        data = h.creature.to_dict()
        data["life"].pop("dungeons", None)
        data["life"].pop("dungeon_pending", None)
        pet = Creature.from_dict(data)
        self.assertEqual(pet.life.dungeons, {})
        h2 = CompanionHandler(pet, clock=lambda: T0)
        self.assertEqual(req(h2, "pet.dungeons").payload["dungeons"], [])
        # A partial record from an early build gets its missing fields.
        life = Life({"dungeons": {"abcd1234": {"name": "Old", "kind": "wifi"}}}, T0)
        rec = life.dungeons["abcd1234"]
        self.assertEqual((rec["depth"], rec["difficulty"], rec["battles"]["wins"]), (0, 4, 0))

    def test_activity_dungeon_flow_still_works(self) -> None:
        h, _ = make()
        start = req(h, "pet.activity", {"kind": "dungeon_start", "name": "Crypt"})
        self.assertNotEqual(start.type, TYPE_ERROR)
        win = req(h, "pet.activity", {"kind": "dungeon", "name": "Crypt", "result": "win"})
        self.assertNotEqual(win.type, TYPE_ERROR)

    def test_stats_summary(self) -> None:
        h, _ = make()
        did = discover(h, "wifi", MAC1, "A Very Long Network Name!!")["dungeon"]["id"]
        for f in range(1, 4):
            req(h, "pet.dungeon.floor", {"id": did, "floor": f, "result": "cleared"})
        sheet = req(h, "pet.stats").payload
        self.assertEqual(sheet["dungeons"]["count"], 1)
        self.assertEqual(sheet["dungeons"]["deepest"], 3)
        found = sheet["pages"][7]
        self.assertEqual(found["title"], "Discoveries")
        self.assertTrue(any(line.startswith("Dungeons") for line in found["lines"]))
        self.assertTrue(any(line.startswith("Deepest") and "B3F" in line for line in found["lines"]))
        for line in found["lines"]:
            check_text(self, line)


class TextTests(unittest.TestCase):
    SLOTS = {"name": "PULSE", "dungeon": "CafeNet-5G-X", "floor": 25, "count": 12, "boss": "Pairing Phantom", "hp": 12}

    def test_pools_fit(self) -> None:
        pools = {k: v for k, v in load_pools().items() if k.startswith("dungeon.")}
        self.assertGreaterEqual(sum(len(v) for v in pools.values()), 500)
        for key in ("found", "found.rare", "found.many", "intro.net", "intro.volt", "intro.data", "intro.void",
                    "home", "home.heal", "floor", "floor.boss", "floor.checkpoint", "cleared", "stairs",
                    "fainted", "fled", "leave", "record", "milestone", "inrange", "flavor.net", "flavor.volt",
                    "flavor.data", "flavor.void"):
            lines = pools.get(f"dungeon.{key}")
            self.assertTrue(lines and len(lines) >= 15, key)
            self.assertEqual(len(lines), len(set(lines)), key)
            for line in lines:
                check_text(self, fill(line, self.SLOTS))

    def test_names_keep_their_case(self) -> None:
        self.assertEqual(dgn.keep_name("Printer.local goes down.", "printer.local"), "printer.local goes down.")
        self.assertEqual(dgn.keep_name("Map unlocked: nas!", "nas"), "Map unlocked: nas!")
        from pet.speech import sentence_case

        self.assertEqual(sentence_case("4 more dungeons!"), "4 more dungeons!")

    def test_replies_fit(self) -> None:
        for species in ("chirplet", "luminode", "aethernova"):
            h, _ = make(species)
            for i, (kind, label) in enumerate((("wifi", "Free Public WiFi Lounge Extra"), ("bt", None), ("lan", "nas"))):
                discover(h, kind, f"aa:bb:cc:00:00:0{i}", label, rssi=-90, security="wpa3")
            for row in req(h, "pet.dungeons").payload["dungeons"]:
                enter = req(h, "pet.dungeon.enter", {"id": row["id"]}).payload
                for text in [enter["say"], enter["flavor"], *enter["log"]]:
                    check_text(self, text)
                for floor in range(1, 12):
                    out = req(h, "pet.dungeon.floor", {"id": row["id"], "floor": floor, "result": "cleared",
                                                       "loot": ["an extremely long legendary item name"]}).payload
                    for text in [out["say"], out["next"]["say"], *out["log"]]:
                        check_text(self, text)
                check_text(self, req(h, "pet.dungeon.leave", {"id": row["id"]}).payload["say"])


if __name__ == "__main__":
    unittest.main()
