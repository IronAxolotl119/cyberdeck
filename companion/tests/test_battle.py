"""Turn-based battle: types, roster, bag, battle protocol, and the 44-char ASCII rule."""

from __future__ import annotations

import random
import unittest
from datetime import datetime

from link.handler import CompanionHandler
from link.protocol import TYPE_ERROR, Message
from pet import battle as B
from pet.combat import apply_hit, flee_chance, perform_action, use_item
from pet.creature import Creature
from pet.life import Life
from pet.moves import MOVE_LIBRARY
from pet.speech import LinePicker, fill, load_pools
from pet.stats import Stats

T0 = datetime(2026, 11, 12, 14, 0).timestamp()

# Main's spec: (id, glyph, type, weak to, first depth, face)
MAIN_ROSTER = [
    ("bug", "b", "DATA", "VOLT", 1, "(o_o)"),
    ("glitch", "g", "VOID", "DATA", 1, "(#_#)"),
    ("spam", "s", "NET", "FIRE", 1, "(^o^)"),
    ("virus", "v", "DATA", "FIRE", 2, "(>_<)"),
    ("lag", "l", "ICE", "VOLT", 2, "(-_-)"),
    ("worm", "w", "NET", "ICE", 3, "(~o~)"),
    ("firewall", "F", "FIRE", "ICE", 3, "[=_=]"),
    ("daemon", "D", "VOID", "DATA", 4, "(`_')"),
    ("kernel_panic", "K", "VOID", "DATA", 5, "(X_X)"),
]

# Longest realistic slot values, so the length test is a worst case.
LONG_SLOTS = {
    "name": "PULSE", "nickname": "PULSE", "foe": "kernel panic", "move": "Stray Handshake",
    "item": "overclock chip", "amount": "50", "type": "VOID",
}
BATTLE_PREFIXES = ("battle.", "desc.", "item.")


def battle_pools() -> dict[str, list[str]]:
    return {k: v for k, v in load_pools().items() if k.startswith(BATTLE_PREFIXES)}


def make(seed: int = 1, species: str = "chirplet", moves: list[str] | None = None, **stats) -> CompanionHandler:
    pet = Creature(name="Pulse", species_id=species, stats=Stats(**stats) if stats else Stats(),
                   life=Life.new(random.Random(seed), T0))
    pet.life.last_now = T0
    if moves:
        pet.known_move_ids = list(moves)
    handler = CompanionHandler(pet, clock=lambda: T0)
    handler.rng = random.Random(seed)
    return handler


def req(handler: CompanionHandler, typ: str, payload: dict | None = None) -> Message:
    return handler.handle(Message.request(typ, payload or {}))


def assert_short_ascii(test: unittest.TestCase, text: str, where: str = "") -> None:
    test.assertTrue(text.isascii(), f"{where}: not ASCII: {text!r}")
    test.assertLessEqual(len(text), B.SAY_MAX, f"{where}: {len(text)} chars: {text!r}")


class TypeChartTests(unittest.TestCase):
    def test_types(self) -> None:
        self.assertEqual(B.TYPES, ("NORM", "VOLT", "DATA", "NET", "FIRE", "ICE", "VOID"))

    def test_multipliers(self) -> None:
        self.assertEqual(B.effectiveness("VOLT", "DATA"), 2.0)
        self.assertEqual(B.effectiveness("DATA", "VOID"), 2.0)
        self.assertEqual(B.effectiveness("NET", "VOID"), 0.0)
        self.assertEqual(B.effectiveness("FIRE", "FIRE"), 0.0)
        self.assertEqual(B.effectiveness("NORM", "VOID"), 0.5)
        self.assertEqual(B.effectiveness("NORM", "DATA"), 1.0)
        self.assertEqual(B.effectiveness("bogus", "DATA"), 1.0)  # unknown -> NORM

    def test_labels(self) -> None:
        self.assertEqual(B.effectiveness_label(2.0), "super")
        self.assertEqual(B.effectiveness_label(0.5), "weak")
        self.assertEqual(B.effectiveness_label(1.0), "normal")
        self.assertEqual(B.effectiveness_label(0.0), "immune")

    def test_chart_only_uses_known_types_and_values(self) -> None:
        for attacker, row in B.CHART.items():
            self.assertIn(attacker, B.TYPES)
            for defender, mult in row.items():
                self.assertIn(defender, B.TYPES)
                self.assertIn(mult, (0.0, 0.5, 2.0))

    def test_chart_matches_mains_roster_weaknesses(self) -> None:
        for ident, _glyph, typ, weak, _depth, _face in MAIN_ROSTER:
            self.assertEqual(B.effectiveness(weak, typ), 2.0, ident)

    def test_every_type_has_a_counter(self) -> None:
        for typ in B.TYPES:
            if typ != "NORM":
                self.assertTrue(B.weaknesses(typ), typ)

    def test_every_move_has_a_type(self) -> None:
        for move in MOVE_LIBRARY.values():
            self.assertIn(move.type, B.TYPES, move.id)
        self.assertEqual(MOVE_LIBRARY["chirp"].type, "NORM")
        self.assertEqual(MOVE_LIBRARY["static_snap"].type, "VOLT")
        self.assertEqual(MOVE_LIBRARY["packet_burst"].type, "DATA")
        covered = {m.type for m in MOVE_LIBRARY.values()}
        self.assertEqual(covered, set(B.TYPES))

    def test_every_species_has_a_type(self) -> None:
        from pet.evolution import SPECIES

        for species in SPECIES:
            self.assertIn(B.SPECIES_TYPES[species], B.TYPES)


class RosterTests(unittest.TestCase):
    def test_mains_roster_is_present(self) -> None:
        for ident, glyph, typ, weak, depth, face in MAIN_ROSTER:
            entry = B.roster_entry(ident)
            self.assertIsNotNone(entry, ident)
            self.assertEqual((entry["glyph"], entry["type"], entry["weak"], entry["depth"], entry["face"]),
                             (glyph, typ, weak, depth, face), ident)

    def test_roster_shape(self) -> None:
        roster = B.roster_payload()
        self.assertEqual(len(roster), 12)
        glyphs = [e["glyph"] for e in roster]
        self.assertEqual(len(glyphs), len(set(glyphs)))
        for entry in roster:
            for key in ("id", "name", "glyph", "type", "depth", "face", "hp", "attack", "defense", "speed", "desc"):
                self.assertIn(key, entry, entry["id"])
            assert_short_ascii(self, entry["face"], entry["id"])
            assert_short_ascii(self, entry["desc"], entry["id"])
            if entry["weak"]:
                self.assertIn(entry["weak"], entry["weak_to"])
        boss = B.roster_entry("kernel panic")
        self.assertTrue(boss["boss"])
        self.assertEqual(boss["every"], 5)

    def test_normalize_enemy(self) -> None:
        foe = B.normalize_enemy({"name": "worm", "level": 3})
        self.assertEqual(foe["type"], "NET")
        self.assertEqual(foe["hp"], 34 + 6 * 2)
        self.assertEqual(foe["face"], "(~o~)")
        mine = B.normalize_enemy({"name": "worm", "type": "ICE", "attack": 99, "defense": 0})
        self.assertEqual((mine["type"], mine["attack"], mine["defense"]), ("ICE", 99, 0))
        stranger = B.normalize_enemy({"name": "mystery"})
        self.assertEqual(stranger["type"], "NORM")
        self.assertEqual(B.normalize_enemy({"id": "kernel_panic"})["boss"], True)

    def test_roster_message(self) -> None:
        reply = req(make(), "pet.combat.roster")
        self.assertEqual(len(reply.payload["enemies"]), 12)
        self.assertEqual(reply.payload["chart"]["VOLT"]["DATA"], 2.0)
        self.assertEqual(reply.payload["types"], list(B.TYPES))


class LineRuleTests(unittest.TestCase):
    def test_every_battle_line_is_short_ascii(self) -> None:
        pools = battle_pools()
        self.assertGreater(len(pools), 50)
        for key, lines in pools.items():
            for line in lines:
                assert_short_ascii(self, fill(line, LONG_SLOTS), key)

    def test_big_pools(self) -> None:
        pools = load_pools()
        for key in ("battle.start", "battle.move", "battle.super", "battle.weak", "battle.miss", "battle.crit",
                    "battle.hurt", "battle.victory", "battle.disobey"):
            self.assertGreaterEqual(len(pools[key]), 20, key)
        for key in ("battle.lowhp", "battle.faint", "battle.item.heal", "battle.flee.ok", "battle.defeat"):
            self.assertGreaterEqual(len(pools[key]), 15, key)
        for typ in B.TYPES:
            self.assertGreaterEqual(len(pools[f"battle.start.{typ.lower()}"]), 10, typ)
            self.assertGreaterEqual(len(pools[f"battle.move.{typ.lower()}"]), 6, typ)
        for move_id in MOVE_LIBRARY:
            self.assertGreaterEqual(len(pools[f"desc.{move_id}"]), 2, move_id)

    def test_picker_cycles_battle_pools(self) -> None:
        picker, rng = LinePicker(), random.Random(4)
        pool = load_pools()["battle.super"]
        seen = [picker.pick("battle.super", rng) for _ in range(int(len(pool) * 0.8))]
        self.assertEqual(len(seen), len(set(seen)))

    def test_clamp_say(self) -> None:
        self.assertEqual(B.clamp_say("caf\u00e9 \u2014 ok"), "cafe - ok")
        long = "word " * 30
        out = B.clamp_say(long)
        self.assertLessEqual(len(out), 44)
        self.assertTrue(out.endswith("..."))
        self.assertEqual(B.clamp_say("short"), "short")

    def test_live_battles_never_break_the_rule(self) -> None:
        """Fuzz: every stage, a 16-char nickname, angry and loyal pets, all the messages."""
        stages = ["chirplet", "wavling", "aethling", "luminode", "aurorion", "aethernova", "voidstatic"]
        all_moves = list(MOVE_LIBRARY)
        for seed, species in enumerate(stages):
            h = make(seed, species, moves=all_moves, level=10, attack=12, mood=20 if seed % 2 else 80)
            h.creature.life.nickname = "Sir Bitsy Boopsy"
            h.creature.life.bond = 90 if seed % 3 else 10
            h.creature.life.obedience = 10 if seed % 2 else 90
            texts: list[str] = []
            for entry in B.load_roster():
                enemy = {"id": entry["id"], "name": entry["name"], "level": 3}
                texts += [req(h, "pet.combat.stats", {"enemy": enemy}).payload["say"]]
                for turn in range(6):
                    move = MOVE_LIBRARY[all_moves[(turn + seed) % len(all_moves)]].name
                    a = req(h, "pet.combat.action", {"move": move, "enemy": enemy, "turn": turn}).payload
                    texts += [a["say"]] + a["log"] + ([a["cheer"]] if "cheer" in a else [])
                    hit = req(h, "pet.combat.hit", {"damage": 6, "enemy": enemy}).payload
                    texts += [hit["say"]] + hit["log"]
                    h.creature.stats.hp = max(h.creature.stats.hp, 5)
                    h.creature.stats.energy = 90
                for item in ("cookie", "battery", "medicine", "nothing"):
                    r = req(h, "pet.combat.item", {"item": item})
                    texts += [r.payload.get("say", "")] + r.payload.get("log", [])
                run = req(h, "pet.combat.run", {"enemy": enemy}).payload
                texts += [run["say_ok"], run["say_fail"]]
                for result in ("win", "lose", "flee"):
                    end = req(h, "pet.combat.end", {"result": result, "xp": 5, "enemies": [enemy]}).payload
                    texts += [end["say"]] + end["log"]
                    h.creature.stats.hp = h.creature.stats.max_hp
            for move in req(h, "pet.combat.moves").payload["known"]:
                texts.append(move["desc"])
            for row in req(h, "pet.inventory").payload["bag"]:
                texts.append(row["desc"])
            for text in texts:
                assert_short_ascii(self, text, species)


class ActionTests(unittest.TestCase):
    def action(self, enemy_type: str, move: str = "Static Snap", seed: int = 2, **kw) -> dict:
        h = make(seed, moves=["chirp", "static_snap", "echo_pulse", "signal_sniff", "noise_veil"], **kw)
        enemy = {"name": "dummy", "type": enemy_type, "defense": 0, "speed": 0, "hp": 99}
        return req(h, "pet.combat.action", {"move": move, "enemy": enemy, "turn": 3}).payload

    def test_super_weak_normal_immune(self) -> None:
        normal = self.action("NORM")
        superb = self.action("DATA")
        weak = self.action("VOLT")
        immune = self.action("VOID", move="Echo Pulse")
        self.assertTrue(normal["hit"] and superb["hit"] and weak["hit"])
        self.assertEqual(superb["effectiveness"], 2.0)
        self.assertEqual(superb["effectiveness_label"], "super")
        self.assertEqual(superb["damage"], normal["damage"] * 2)
        self.assertEqual(weak["effectiveness_label"], "weak")
        self.assertEqual(weak["damage"], normal["damage"] // 2)
        self.assertEqual(immune["effectiveness_label"], "immune")
        self.assertEqual(immune["damage"], 0)
        self.assertIn("It's super effective!", superb["log"])
        self.assertIn("It's not very effective...", weak["log"])
        self.assertTrue(any("doesn't affect" in line for line in immune["log"]))

    def test_reply_fields(self) -> None:
        a = self.action("DATA")
        for key in ("move_used", "type", "damage", "effectiveness", "effectiveness_label", "crit", "missed",
                    "obeyed", "energy_cost", "hp", "energy", "status", "say", "log", "turn",
                    "move", "hit", "energy_spent", "pet_hp", "reaction", "pet"):
            self.assertIn(key, a)
        self.assertEqual(a["move_used"], "Static Snap")
        self.assertEqual(a["type"], "VOLT")
        self.assertEqual(a["energy_cost"], 8)
        self.assertEqual(a["turn"], 3)
        self.assertTrue(a["obeyed"])
        self.assertEqual(a["log"][0], "PULSE used STATIC SNAP!")
        self.assertEqual(a["status"], "ok")

    def test_crit(self) -> None:
        pet = make(moves=["chirp"]).creature
        rng = random.Random(0)
        rng.random = lambda: 0.0  # type: ignore[method-assign]
        rng.randint = lambda a, b: 1  # type: ignore[method-assign]
        out = perform_action(pet, "Chirp", {"name": "x", "defense": 0, "speed": 0}, rng)
        self.assertTrue(out["crit"])
        self.assertIn("A critical hit!", out["log"])
        self.assertEqual(out["damage"], int((8 + pet.stats.attack) * B.CRIT_MULT))

    def test_miss(self) -> None:
        pet = make(moves=["chirp"]).creature
        rng = random.Random(0)
        rng.randint = lambda a, b: 100  # type: ignore[method-assign]
        out = perform_action(pet, "Chirp", {"name": "x", "speed": 99}, rng)
        self.assertTrue(out["missed"])
        self.assertEqual(out["damage"], 0)
        self.assertIn("PULSE's attack missed!", out["log"])

    def test_tantrum_means_not_obeyed(self) -> None:
        h = make()
        h.creature.life.start_tantrum(h.creature, h.rng, T0, "test")
        a = req(h, "pet.combat.action", {"move": "Chirp", "enemy": {"name": "bug"}}).payload
        self.assertFalse(a["obeyed"])
        self.assertEqual(a["reason"], "tantrum")
        self.assertTrue(a["say"])

    def test_disobedience(self) -> None:
        h = make(moves=["chirp", "static_snap"])
        h.creature.life.obedience = 0
        results = [req(h, "pet.combat.action", {"move": "Chirp", "enemy": {"name": "bug"}}).payload for _ in range(30)]
        rebels = [r for r in results if not r["obeyed"]]
        self.assertTrue(rebels)
        self.assertTrue(all(r["move_used"] == "Static Snap" for r in rebels))
        self.assertTrue(any("ignored your order" in line for r in rebels for line in r["log"]))
        h.creature.stats.energy = 100

    def test_auto_picks_a_super_effective_move(self) -> None:
        h = make(moves=["chirp", "static_snap", "signal_sniff"], energy=90)
        a = req(h, "pet.combat.action", {"enemy": {"name": "glitch"}}).payload
        self.assertEqual(a["move_used"], "Signal Sniff")  # DATA vs VOID
        self.assertTrue(a.get("auto"))

    def test_too_tired_falls_back(self) -> None:
        h = make(moves=["chirp", "bright_arc"], energy=5)
        a = req(h, "pet.combat.action", {"move": "Bright Arc", "enemy": {"name": "bug"}}).payload
        self.assertEqual(a["move_used"], "Chirp")
        self.assertTrue(a["fallback"])


class HitTests(unittest.TestCase):
    def test_pulse_resists_and_is_weak(self) -> None:
        normal = apply_hit(make(species="chirplet").creature, {"damage": 20, "attack_type": "DATA"})
        weak_to = apply_hit(make(species="chirplet").creature, {"damage": 20, "attack_type": "VOID"})
        self.assertEqual(weak_to["damage"], normal["damage"] * 2)
        self.assertEqual(weak_to["effectiveness_label"], "super")
        fire = make(species="aethling").creature
        immune = apply_hit(fire, {"damage": 20, "attack_type": "FIRE"})
        self.assertEqual(immune["damage"], 0)
        self.assertEqual(immune["effectiveness_label"], "immune")

    def test_attack_type_defaults_to_enemy_type(self) -> None:
        out = apply_hit(make().creature, {"damage": 10, "enemy": {"name": "daemon"}})
        self.assertEqual(out["attack_type"], "VOID")

    def test_guard_halves(self) -> None:
        pet = make().creature
        plain = apply_hit(make().creature, {"damage": 20})["damage"]
        pet.guarding = True
        self.assertEqual(apply_hit(pet, {"damage": 20})["damage"], plain // 2)

    def test_faint_and_low_hp_lines(self) -> None:
        h = make()
        low = req(h, "pet.combat.hit", {"damage": h.creature.stats.max_hp - 3}).payload
        self.assertTrue(low["low_hp"])
        self.assertTrue(low["say"])
        out = req(h, "pet.combat.hit", {"damage": 99}).payload
        self.assertTrue(out["fainted"])
        self.assertEqual(out["status"], "fainted")
        self.assertIn("PULSE fainted!", out["log"])

    def test_heal_still_works(self) -> None:
        h = make()
        h.creature.stats.hp = 5
        out = req(h, "pet.combat.hit", {"heal": 4}).payload
        self.assertEqual(out["healed"], 4)
        self.assertEqual(out["hp"], 9)


class BagTests(unittest.TestCase):
    def test_new_pet_has_a_starter_bag(self) -> None:
        bag = B.Bag(Creature())
        self.assertEqual(bag.qty("cache_cookie"), 3)
        self.assertEqual(bag.qty("battery"), 1)

    def test_cookie_heals_and_is_used_up(self) -> None:
        h = make()
        h.creature.stats.hp = 2
        reply = req(h, "pet.combat.item", {"item": "cookie"})
        self.assertTrue(reply.payload["ok"])
        self.assertTrue(reply.payload["consumed"])
        self.assertEqual(reply.payload["hp"], min(h.creature.stats.max_hp, 22))
        self.assertEqual(reply.payload["item"]["qty"], 2)
        self.assertTrue(reply.payload["say"])

    def test_cookie_revives_a_fainted_pet(self) -> None:
        pet = make().creature
        pet.stats.hp = 0
        out = use_item(pet, "cache cookie", random.Random(1))
        self.assertTrue(out["ok"])
        self.assertFalse(pet.fainted)

    def test_battery(self) -> None:
        h = make(energy=10)
        reply = req(h, "pet.combat.item", {"item": "battery"}).payload
        self.assertEqual(reply["energy"], 40)
        self.assertNotIn("battery", h.creature.bag)

    def test_medicine_cures_glitch(self) -> None:
        h = make()
        h.creature.life.make_sick(h.creature, "glitch", h.rng, T0)
        self.assertEqual(req(h, "pet.combat.stats").payload["pet"]["status"], "glitch")
        reply = req(h, "pet.combat.item", {"item": "medicine"}).payload
        self.assertTrue(reply["status_cleared"])
        self.assertEqual(reply["status"], "ok")

    def test_no_effect_is_not_consumed(self) -> None:
        h = make()
        reply = req(h, "pet.combat.item", {"item": "cookie"}).payload
        self.assertFalse(reply["ok"])
        self.assertFalse(reply["consumed"])
        self.assertEqual(reply["reason"], "no_effect")
        self.assertEqual(B.Bag(h.creature).qty("cache_cookie"), 3)

    def test_error_codes(self) -> None:
        h = make()
        none = req(h, "pet.combat.item", {"item": "byte burger"})
        self.assertEqual(none.type, TYPE_ERROR)
        self.assertEqual(none.payload["code"], "none_left")
        self.assertTrue(none.payload["say"])
        B.Bag(h.creature).add("evo chip")
        evo = req(h, "pet.combat.item", {"item": "evo chip"})
        self.assertEqual(evo.payload["code"], "not_usable_in_battle")
        self.assertEqual(B.Bag(h.creature).qty("evo_chip"), 1)
        self.assertEqual(req(h, "pet.combat.item", {}).payload["code"], "bad_payload")

    def test_last_one_then_none_left(self) -> None:
        h = make()
        for _ in range(3):
            h.creature.stats.hp = 1
            self.assertTrue(req(h, "pet.combat.item", {"item": "cookie"}).payload["ok"])
        h.creature.stats.hp = 1
        self.assertEqual(req(h, "pet.combat.item", {"item": "cookie"}).payload["code"], "none_left")

    def test_gifts_can_go_to_the_bag(self) -> None:
        h = make(hunger=50)
        reply = req(h, "pet.gift", {"item": {"name": "byte burger", "kind": "food", "power": 45}, "store": True}).payload
        self.assertTrue(reply["stored"])
        self.assertEqual(B.Bag(h.creature).qty("byte_burger"), 1)
        req(h, "pet.gift", {"item": {"name": "patch kit", "kind": "medicine"}})  # not sick: saved
        self.assertEqual(B.Bag(h.creature).qty("patch_kit"), 1)
        # Normal gifts are still used right away.
        req(h, "pet.gift", {"item": {"name": "glitch berry", "kind": "food", "power": 20}})
        self.assertEqual(B.Bag(h.creature).qty("glitch_berry"), 0)

    def test_inventory_message(self) -> None:
        h = make()
        bag = req(h, "pet.inventory").payload["bag"]
        self.assertTrue(all({"id", "name", "qty", "usable_in_battle", "desc"} <= set(r) for r in bag))
        add = req(h, "pet.inventory", {"action": "add", "item": {"name": "data cookie", "kind": "food", "power": 30}, "qty": 2}).payload
        self.assertEqual(add["changed"]["qty"], 2)
        drop = req(h, "pet.inventory", {"action": "drop", "item": "data cookie"}).payload
        self.assertEqual(drop["changed"]["qty"], 1)
        self.assertEqual(req(h, "pet.inventory", {"action": "drop", "item": "nope"}).payload["code"], "none_left")
        self.assertEqual(req(h, "pet.inventory", {"action": "eat"}).type, TYPE_ERROR)

    def test_unknown_food_uses_its_power(self) -> None:
        pet = make().creature
        info = B.Bag(pet).add({"name": "mega taco", "kind": "food", "power": 33})
        self.assertEqual(info["amount"], 33)
        self.assertTrue(info["usable_in_battle"])
        trinket = B.Bag(pet).add("lucky gear")
        self.assertFalse(trinket["usable_in_battle"])


class StatsMovesEndTests(unittest.TestCase):
    def test_stats_as_battle_start(self) -> None:
        h = make(moves=list(MOVE_LIBRARY))
        body = req(h, "pet.combat.stats", {"enemy": {"id": "spam", "name": "spam", "type": "NET", "level": 1}}).payload
        self.assertLessEqual(len(body["moves"]), 4)
        for move in body["moves"]:
            for key in ("name", "type", "power", "energy_cost", "accuracy", "rank", "desc"):
                self.assertIn(key, move)
        self.assertEqual(len(body["known_moves"]), len(MOVE_LIBRARY))
        for key in ("species", "level", "hp", "max_hp", "energy", "max_energy", "mood", "status", "face"):
            self.assertIn(key, body["pet"])
        self.assertTrue(body["say"])
        self.assertTrue(body["bag"])
        for key in ("hp", "max_hp", "attack", "defense", "speed", "level", "fainted", "asleep", "guarding"):
            self.assertIn(key, body)  # legacy sheet

    def test_stats_without_enemy_has_no_taunt(self) -> None:
        self.assertEqual(req(make(), "pet.combat.stats").payload["say"], "")

    def test_loadout_prefers_variety(self) -> None:
        pet = make(moves=list(MOVE_LIBRARY)).creature
        moves = B.loadout(pet)
        self.assertEqual(len(moves), 4)
        self.assertIn("chirp", [m.id for m in moves])

    def test_set_moves(self) -> None:
        h = make(moves=["chirp", "static_snap", "echo_pulse", "signal_sniff", "blue_pulse"])
        reply = req(h, "pet.combat.moves", {"set": ["Blue Pulse", "Chirp"]}).payload
        self.assertEqual([m["name"] for m in reply["moves"]], ["Blue Pulse", "Chirp"])
        self.assertFalse(reply["auto"])
        self.assertEqual(req(h, "pet.combat.moves", {"set": ["Bright Arc"]}).type, TYPE_ERROR)
        self.assertTrue(req(h, "pet.combat.moves", {"auto": True}).payload["auto"])

    def test_win(self) -> None:
        h = make()
        end = req(h, "pet.combat.end", {"result": "win", "xp": 60, "enemies": ["bug"],
                                         "loot": [{"name": "power cell", "kind": "battery", "power": 25}]}).payload
        self.assertEqual(end["xp"], 60)
        self.assertTrue(end["level_up"])
        self.assertGreater(end["bond_delta"], 0)
        self.assertEqual(end["loot_added"][0]["id"], "power_cell")
        self.assertIn("PULSE gained 60 XP!", end["log"])
        self.assertTrue(end["say"])
        self.assertIn("learned_move", end)

    def test_learned_move_on_level_up(self) -> None:
        h = make(level=2, xp=0)
        end = req(h, "pet.combat.end", {"result": "win", "xp": 500, "enemies": ["bug"]}).payload
        self.assertTrue(end["level_up"])
        self.assertIsNotNone(end["learned_move"])

    def test_boss_win_bonds_more(self) -> None:
        a = req(make(), "pet.combat.end", {"result": "win", "xp": 1, "enemies": ["bug"]}).payload["bond_delta"]
        b = req(make(), "pet.combat.end", {"result": "win", "xp": 1, "enemies": [{"id": "kernel_panic"}]}).payload["bond_delta"]
        self.assertGreater(b, a)

    def test_default_xp(self) -> None:
        end = req(make(), "pet.combat.end", {"result": "win", "enemies": [{"name": "bug", "level": 2}]}).payload
        self.assertEqual(end["xp"], 11)

    def test_lose_faints_then_limps_home(self) -> None:
        h = make()
        h.creature.stats.hp = 0
        mistakes = h.creature.stats.care_mistakes
        end = req(h, "pet.combat.end", {"result": "lose", "xp": 0, "enemies": ["daemon"]}).payload
        self.assertTrue(end["fainted"])
        self.assertEqual(end["hp"], 1)
        self.assertEqual(h.creature.stats.care_mistakes, mistakes + 1)
        kinds = {m["kind"] for m in h.creature.life.active_modifiers(T0)}
        self.assertIn("fainted", kinds)
        self.assertLess(h.creature.life.mood_offset(T0), 0)
        self.assertTrue(end["say"])

    def test_flee(self) -> None:
        end = req(make(), "pet.combat.end", {"result": "flee", "xp": 0}).payload
        self.assertEqual(end["result"], "flee")
        self.assertIn("Got away safely!", end["log"])

    def test_flee_chance(self) -> None:
        pet = make(speed=10, level=5).creature
        easy = flee_chance(pet, {"name": "lag", "level": 1})
        hard = flee_chance(pet, {"name": "kernel panic", "level": 9})
        self.assertGreater(easy, hard)
        self.assertTrue(0.1 <= hard <= easy <= 0.95)
        run = req(make(), "pet.combat.run", {"enemy": {"name": "bug"}}).payload
        self.assertTrue(run["say_ok"] and run["say_fail"])

    def test_bag_and_loadout_are_saved(self) -> None:
        pet = make(moves=["chirp", "static_snap"]).creature
        pet.battle_moves = ["static_snap"]
        B.Bag(pet).add("evo chip")
        clone = Creature.from_dict(pet.to_dict())
        self.assertEqual(clone.bag, pet.bag)
        self.assertEqual(clone.battle_moves, ["static_snap"])
        old = pet.to_dict()
        old.pop("bag")
        self.assertEqual(B.Bag(Creature.from_dict(old)).qty("cache_cookie"), 3)


if __name__ == "__main__":
    unittest.main()
