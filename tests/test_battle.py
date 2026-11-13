"""tests/test_battle.py — the turn-based dungeon battles (features/battle.py).

  * battle flow: intro, FIGHT, the enemy's turn, win / faint / flee,
    BAG items, the PULSE card, and what happens back on the map
  * protocol fallback: an older companion (no types, no log, no bag)
    and one that can't fight at all (you fight solo, as before)
  * sim parity: the sim pet answers every battle message with the same
    fields as companion/docs/DUNGEON_INTEGRATION.md, plus the companion's
    4 asks (best move by type, types from the roster, 44-char say lines,
    gifts apply now / store:true goes to the bag)
  * the screen: every row exactly 48 columns and plain ASCII in every
    phase, and the say line on its own row ('PULSE: ' only if it fits)
  * the LCD, config/battle.ini, headless --script, the unified Rich UI
  * end to end against the real companion/companion.py (when present)
"""

from __future__ import annotations

import json
import random
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

from cyberdeck.battledata import (
    CHART,
    ROSTER,
    SAY_MAX,
    SIM_LINES,
    TYPES,
    clamp_say,
    effectiveness,
    flee_chance,
    load_battle_config,
    roster_entry,
)
from cyberdeck.commands import build_registry
from cyberdeck.companion import PetState, SimCompanionLink
from cyberdeck.features import dungeon
from cyberdeck.features.battle import PREFIX_MAX, Battle, Enemy, battle_text, enemy_for, render_battle, say_row
from cyberdeck.features.dungeon import Game, PulseAlly
from cyberdeck.protocol import Message

from helpers import companion_battle_data_missing, companion_script, make_ctx, write_config

ROOT = Path(__file__).resolve().parent.parent


def sim(**pet) -> SimCompanionLink:
    link = SimCompanionLink(pet=PetState(**pet), clock=lambda: 0.0)
    link.connect()
    return link


def joined(link) -> PulseAlly:
    ally = PulseAlly(link.request)
    assert ally.join(), ally.note
    return ally


def battle(foe="bug", level=1, link=None, seed=1, odds=None, **pet) -> tuple[Battle, SimCompanionLink]:
    link = link or sim(**pet)
    b = Battle(joined(link), enemy_for(foe, level), depth=level, rng=random.Random(seed))
    if odds:
        b.odds.update(odds)
    assert b.start(), b.note
    return b, link


def text(rows) -> list[str]:
    return ["".join(t for t, _ in row) for row in rows]


def win(b: Battle, limit: int = 40) -> None:
    for _ in range(limit):
        if b.over:
            break
        b.drain()
        b.fight(None)
    b.drain()


class OldCompanion:
    """A companion from before battles: old sheet, old replies, no new messages."""

    def __init__(self, fight: bool = True) -> None:
        self.fight = fight
        self.sent: list[tuple[str, dict]] = []
        self.hp = 20

    def is_linked(self):
        return True

    def request(self, typ, payload=None, timeout=1.0):
        payload = payload or {}
        self.sent.append((typ, payload))
        msg = Message.request(typ, payload)
        if typ == "pet.status":
            return msg.reply("pet.status.ok", {"pet": {"name": "PULSE", "energy": 50}})
        if not self.fight or typ not in {"pet.combat.stats", "pet.combat.action", "pet.combat.hit", "pet.combat.end"}:
            return msg.error("unknown_type", f"unsupported message type: {typ}")
        if typ == "pet.combat.stats":
            return msg.reply(typ + ".ok", {"hp": self.hp, "max_hp": 20, "attack": 5, "defense": 4, "speed": 5,
                                           "level": 3, "moves": ["peck", "zap"]})
        if typ == "pet.combat.action":
            return msg.reply(typ + ".ok", {"move": payload.get("move") or "peck", "hit": True, "damage": 50,
                                           "acted": True, "energy_spent": 2, "pet_hp": self.hp})
        if typ == "pet.combat.hit":
            self.hp = max(0, self.hp - int(payload.get("damage", 0)))
            return msg.reply(typ + ".ok", {"damage_taken": payload.get("damage"), "pet_hp": self.hp,
                                           "fainted": self.hp <= 0})
        return msg.reply(typ + ".ok", {"result": payload.get("result"), "xp_granted": payload.get("xp", 0),
                                       "reaction": "PULSE cheers!"})

    def drain_events(self):
        return []

    def pet_snapshot(self):
        return None

    def link_label(self):
        return "LINK UP"


# ---------------------------------------------------------------------------
class BattleFlowTests(unittest.TestCase):
    def test_intro_then_menu_then_fight_and_win(self):
        b, link = battle("bug", 2)
        self.assertEqual(b.phase, "log")
        self.assertEqual(b.shown[:2], ["A wild BUG appeared!", "Go, PULSE!"])
        self.assertTrue(b.say)
        b.key("enter")
        self.assertEqual(b.phase, "menu")
        b.key("right")
        self.assertEqual(b.cursor["menu"], 1)          # BAG
        b.key("left")
        b.key("enter")
        self.assertEqual(b.phase, "fight")
        b.key("down")
        self.assertEqual(b.moves[b.cursor["fight"]]["name"], "static zap")
        hp = b.enemy.hp
        b.key("enter")
        self.assertLess(b.enemy.hp, hp)
        self.assertIn("PULSE used STATIC ZAP!", b.history)
        self.assertIn("It's super effective!", b.history)          # VOLT vs DATA
        win(b)
        self.assertEqual(b.result, "win")
        self.assertEqual(b.phase, "end")
        screen = "\n".join(battle_text(b))
        self.assertIn("VICTORY!", screen)
        self.assertIn("XP", screen)
        self.assertIn("BOND friendly (+2)", screen)
        self.assertTrue(b.key("x"))                   # any key closes the end screen

    def test_enemy_ai_picks_by_type_and_the_deck_names_the_attack(self):
        # PULSE (an Aethling) is FIRE: a smart firewall skips PORT BURN (FIRE -> FIRE is immune).
        b, _ = battle("firewall", 3, odds={"smart": 1.0})
        b.drain()
        b.fight(0)                                     # peck: the firewall survives
        self.assertIn("FIREWALL used BLOCK!", b.history)
        b, _ = battle("worm", 3, odds={"smart": 1.0})
        b.drain()
        b.fight(0)
        self.assertIn("WORM used TUNNEL!", b.history)   # NET vs FIRE is 0.5, Burrow Bite NORM 0.5 x 0.8

    def test_faint_returns_to_the_dungeon_sad(self):
        b, link = battle("daemon", 4, hp=2, max_hp=28)
        win(b)
        self.assertEqual(b.result, "lose")
        self.assertEqual(b.end_body["mood"], "sad")
        self.assertEqual(link.pet.hp, 1)                        # limps home with 1 HP
        self.assertIn("FAINTED", "\n".join(battle_text(b)))
        self.assertEqual(b.enemy.calm, int(b.odds["calm_turns"]))

    def test_run_success_and_failure(self):
        b, link = battle("bug", odds={"flee_min": 1.0, "flee_max": 1.0})
        b.drain()
        b.pick("RUN")
        self.assertEqual(b.result, "flee")
        self.assertEqual(b.end_body["result"], "flee")
        self.assertIn("Got away safely!", b.history)
        b, _ = battle("bug", odds={"flee_min": 0.0, "flee_max": 0.0})
        b.drain()
        b.key("r")
        self.assertIsNone(b.result)
        self.assertIn("Couldn't get away!", b.history)
        self.assertTrue(any("used" in line and "BUG" in line for line in b.history))   # the enemy got a turn

    def test_flee_formula_matches_the_spec(self):
        self.assertEqual(flee_chance(6, 4, 4, 2), 0.7)
        self.assertEqual(flee_chance(6, 4, 4, 2, boss=True), 0.35)
        self.assertEqual(flee_chance(0, 1, 50, 30), 0.10)
        self.assertEqual(flee_chance(50, 30, 0, 1), 0.95)

    def test_bag_items(self):
        b, link = battle("bug", hp=10, max_hp=40)
        b.drain()
        link.pet.bag["evo_chip"] = {"qty": 1}
        b.bag = link.pet.bag_payload()
        b.pick("BAG")
        self.assertEqual(b.phase, "bag")
        screen = "\n".join(battle_text(b))
        self.assertIn("CACHE COOKIE", screen)
        self.assertIn("EVO CHIP           x1   (not in battle)", screen)
        b.key("enter")                                   # cache cookie
        self.assertEqual(link.pet.bag["cache_cookie"]["qty"], 2)
        self.assertIn("You used CACHE COOKIE!", b.history)
        self.assertIn("PULSE recovered 20 HP.", b.history)
        b.drain()
        # The evo chip is greyed out: no request, no turn.
        evo = next(i for i, it in enumerate(b.bag) if it["id"] == "evo_chip")
        turn = b.turn
        b.use_item(evo)
        self.assertEqual(b.turn, turn)
        self.assertIn("Can't use the evo chip", b.say)
        # Medicine on a healthy, full pet: no effect, no turn used.
        link.pet.hp = link.pet.max_hp
        med = next(i for i, it in enumerate(b.bag) if it["id"] == "medicine")
        b.use_item(med)
        self.assertEqual(b.turn, turn)
        # none_left comes back as an error with a say line.
        reply = link.request("pet.combat.item", {"item": "byte burger"})
        self.assertEqual((reply.type, reply.payload["code"]), ("error", "none_left"))
        self.assertTrue(reply.payload["say"])
        reply = link.request("pet.combat.item", {"item": "evo chip"})
        self.assertEqual(reply.payload["code"], "not_usable_in_battle")

    def test_pulse_card_and_back(self):
        b, _ = battle("bug")
        b.drain()
        b.key("p")
        screen = "\n".join(battle_text(b))
        self.assertIn("Mood:", screen)
        self.assertIn("Status: ok", screen)
        b.key("esc")
        self.assertEqual(b.phase, "menu")

    def test_lcd_gets_the_say_lines(self):
        said = []
        b = Battle(joined(sim()), enemy_for("bug"), rng=random.Random(2), on_say=lambda t, f: said.append((t, f)))
        b.start()
        self.assertEqual(said[0][1], "bug")
        self.assertLessEqual(len(said[0][0]), SAY_MAX)


class DungeonBattleTests(unittest.TestCase):
    def game(self, link=None, **pet) -> Game:
        ally = joined(link or sim(**pet))
        return Game(rng=random.Random(5), ally=ally, battles=True)

    def next_to_you(self, g: Game, name="worm") -> dungeon.Monster:
        kind = roster_entry(name)
        g.monsters = [dungeon.Monster(kind.name, kind.glyph, g.px + 1, g.py, kind.solo_hp, kind.solo_attack, 1, kind)]
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        return g.monsters[0]

    def test_walking_into_an_enemy_starts_a_battle_and_a_win_keeps_score_and_xp(self):
        g = self.game()
        m = self.next_to_you(g, "bug")
        score, hp = g.score, g.hp
        g.move(1, 0)
        self.assertIsNotNone(g.battle)
        self.assertEqual(g.hp, hp)                      # no bump damage, no monster turn
        win(g.battle)
        g.close_battle()
        self.assertNotIn(m, g.monsters)
        self.assertGreater(g.score, score)
        self.assertEqual(g.ally.defeated, ["bug"])
        self.assertEqual(g.battles_won, 1)
        self.assertIn("beat the bug", g.messages[-1])
        self.assertTrue(g.finish()[0].startswith("PULSE: 1 battle won, +"))

    def test_an_enemy_next_to_you_ambushes(self):
        g = self.game()
        self.next_to_you(g)
        g.wait()
        self.assertIsNotNone(g.battle)
        self.assertIn("jumps you", g.messages[-1])

    def test_fled_enemy_stays_calm_then_fainted_pulse_means_fists(self):
        g = self.game()
        m = self.next_to_you(g)
        g.move(1, 0)
        g.battle.odds.update(flee_min=1.0, flee_max=1.0)
        g.battle.drain()
        g.battle.run()
        g.close_battle()
        self.assertIn(m, g.monsters)
        self.assertEqual(m.calm, 3)
        g.wait()                                         # calm: no ambush
        self.assertIsNone(g.battle)
        g.ally.hp = 0                                    # PULSE out cold: old bump fight
        g.move(1, 0)
        self.assertIsNone(g.battle)
        self.assertLess(m.hp, roster_entry("worm").solo_hp)

    def test_activity_routes_keys_and_renders_the_battle(self):
        reg = build_registry(include_plugins=False)      # registers the pet log hooks
        ctx = make_ctx()
        game = dungeon.new_game(ctx, seed=3)
        self.assertTrue(game.battles)
        act = dungeon.DungeonActivity(ctx, game)
        self.next_to_you(game, "spam")
        act.on_key("right")
        self.assertIsNotNone(game.battle)
        rows = text(act.render(80, 30))
        self.assertTrue(any("SPAM  Lv1  NET" in r for r in rows))
        act.on_key("enter")                              # past the intro
        act.on_key("q")                                  # no quitting mid-battle
        self.assertFalse(act.done)
        self.assertIn("RUN", act.game.battle.say)
        self.assertIn("battle", act.subtitle)
        while game.battle is not None:
            act.on_key("enter" if game.battle.phase != "menu" else "1")
        self.assertIn("beat the spam", "\n".join(game.messages))
        act.on_key("q")
        self.assertTrue(any("battle" in line for line in act.result_lines))
        self.assertIn("battle vs spam: win", "\n".join(reg.dispatch("pet log", ctx)))

    def test_boss_every_fifth_floor(self):
        g = Game(rng=random.Random(1))
        while g.depth < 5:
            g.new_level()
        self.assertTrue(any(m.glyph == "K" for m in g.monsters))
        g.new_level()
        self.assertFalse(any(m.glyph == "K" for m in g.monsters))

    def test_battle_off_keeps_the_old_crawl(self):
        ctx = make_ctx(write_config({"fx.ini": "[dungeon]\nbattle = no\n"}))
        self.assertFalse(dungeon.new_game(ctx, seed=1).battles)


class FallbackTests(unittest.TestCase):
    def test_older_companion_still_battles(self):
        old = OldCompanion()
        ally = PulseAlly(old.request)
        self.assertTrue(ally.join())
        b = Battle(ally, enemy_for("bug"), rng=random.Random(1))
        self.assertTrue(b.start())
        self.assertEqual([m["type"] for m in b.moves], ["NORM", "NORM"])
        self.assertEqual(b.shown, ["A wild BUG appeared!", "Go, PULSE!"])     # the deck wrote the log
        b.drain()
        b.pick("FIGHT")
        self.assertIn("vs DATA: normal", "\n".join(battle_text(b)))            # x1 without a chart
        b.pick("BAG")
        self.assertEqual(b.phase, "fight")                                      # nothing opened
        self.assertIn("No battle bag", b.say)
        b.fight(0)
        self.assertEqual(b.result, "win")
        self.assertIn("PULSE used PECK!", b.history)
        self.assertIn("BUG took 50 damage.", b.history)
        end = [p for t, p in old.sent if t == "pet.combat.end"][-1]
        self.assertEqual((end["result"], end["enemies"][0]["type"]), ("win", "DATA"))

    def test_older_companion_enemy_turn_and_faint(self):
        old = OldCompanion()
        old.hp = 1
        ally = PulseAlly(old.request)
        ally.join()
        b = Battle(ally, enemy_for("daemon", 4), rng=random.Random(1))
        b.start()
        b.drain()
        b.run_lines = {}
        b.odds.update(flee_min=0.0, flee_max=0.0)
        b.run()
        self.assertEqual(b.result, "lose")
        self.assertIn("PULSE fainted!", b.history)

    def test_no_battles_means_solo_bump(self):
        ally = PulseAlly(OldCompanion(fight=False).request)
        ally.join()
        g = Game(rng=random.Random(5), ally=ally, battles=True)
        kind = roster_entry("bug")
        g.monsters = [dungeon.Monster("bug", "b", g.px + 1, g.py, 30, 1, 1, kind)]
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        g.move(1, 0)
        self.assertIsNone(g.battle)
        self.assertLess(g.monsters[0].hp, 30)

    def test_link_drop_mid_battle(self):
        b, link = battle("bug")
        b.drain()

        def down(*_a, **_k):
            raise RuntimeError("link down")
        b.ally.request = down
        b.fight(0)
        self.assertEqual(b.result, "abort")
        b.drain()
        self.assertIn("LINK LOST", "\n".join(battle_text(b)))
        self.assertFalse(b.ally.online)


class SimParityTests(unittest.TestCase):
    """The sim pet speaks the battle protocol like the real companion."""

    def test_stats_with_enemy_is_the_battle_start(self):
        body = sim().request("pet.combat.stats", {"enemy": {"name": "bug", "level": 2}}).payload
        for key in ("say", "log", "moves", "known_moves", "bag", "pet", "enemy", "flee_chance", "type",
                    "energy", "max_energy", "hp", "max_hp", "attack", "defense", "speed", "level", "fainted",
                    "asleep", "guarding"):
            self.assertIn(key, body)
        self.assertLessEqual(len(body["moves"]), 4)
        for key in ("id", "name", "type", "power", "energy_cost", "accuracy", "rank", "bonus", "kind", "effect", "desc"):
            self.assertIn(key, body["moves"][0])
        for key in ("name", "species", "type", "level", "hp", "max_hp", "energy", "max_energy", "mood", "status", "face"):
            self.assertIn(key, body["pet"])
        self.assertEqual(body["enemy"]["type"], "DATA")          # (2) filled in from the roster by name
        self.assertEqual(sim().request("pet.combat.stats", {}).payload["say"], "")

    def test_omitted_move_picks_the_best_for_the_enemy_type(self):
        link = sim(moves=["peck", "static zap", "wave dash"])
        body = link.request("pet.combat.action", {"enemy": {"name": "lag"}}).payload    # (1) ICE: VOLT is 2x
        self.assertEqual((body["move_used"], body["auto"]), ("static zap", True))
        body = link.request("pet.combat.action", {"enemy": {"name": "spam", "type": "VOLT"}}).payload
        self.assertEqual(body["move_used"], "wave dash")                                # NET vs VOLT is 2x
        for key in ("move_used", "type", "damage", "effectiveness", "effectiveness_label", "crit", "missed",
                    "obeyed", "energy_cost", "hp", "energy", "status", "turn", "say", "log", "acted", "enemy",
                    "move", "hit", "effect", "energy_spent", "pet_hp", "reaction", "pet"):
            self.assertIn(key, body)

    def test_hit_applies_the_chart_to_pulses_type(self):
        link = sim()                                    # an Aethling: FIRE
        self.assertEqual(link.pet.pet_type(), "FIRE")
        body = link.request("pet.combat.hit", {"damage": 9, "enemy": {"name": "worm"}, "attack_type": "ICE"}).payload
        self.assertEqual((body["effectiveness"], body["damage_taken"]), (2.0, (9 - 6 // 4) * 2))
        body = link.request("pet.combat.hit", {"damage": 9, "enemy": {"name": "firewall"}}).payload
        self.assertEqual((body["attack_type"], body["effectiveness_label"], body["damage_taken"]), ("FIRE", "immune", 0))
        for key in ("damage", "damage_taken", "attack_type", "effectiveness", "effectiveness_label", "hp", "pet_hp",
                    "energy", "status", "fainted", "low_hp", "say", "log", "healed", "pet"):
            self.assertIn(key, body)

    def test_end_loot_xp_default_and_moves_roster_run_inventory(self):
        link = sim()
        body = link.request("pet.combat.end", {"result": "win", "enemies": [{"name": "bug", "level": 2}],
                                               "loot": [{"name": "data cookie", "kind": "food", "power": 30}]}).payload
        self.assertEqual(body["xp_granted"], 5 + 3 * 2)
        self.assertEqual(body["loot_added"][0]["id"], "data_cookie")
        self.assertIn("Put DATA COOKIE in the bag.", body["log"])
        for key in ("result", "xp", "xp_granted", "level_up", "level", "learned_move", "bond_delta", "bond", "mood",
                    "fainted", "loot_added", "say", "log", "hp", "energy", "status", "reaction", "pet"):
            self.assertIn(key, body)
        moves = link.request("pet.combat.moves", {"set": ["peck"]}).payload
        self.assertEqual(([m["name"] for m in moves["moves"]], moves["auto"]), (["peck"], False))
        self.assertTrue(link.request("pet.combat.moves", {"auto": True}).payload["auto"])
        self.assertEqual(link.request("pet.combat.moves", {"set": ["nope"]}).payload["code"], "bad_payload")
        roster = link.request("pet.combat.roster", {}).payload
        self.assertEqual(roster["types"], list(TYPES))
        self.assertEqual(roster["chart"]["NET"]["VOID"], 0.0)
        self.assertEqual({e["id"] for e in roster["enemies"]}, {k.id for k in ROSTER})
        self.assertIn("VOLT", next(e for e in roster["enemies"] if e["id"] == "bug")["weak_to"])
        run = link.request("pet.combat.run", {"enemy": {"name": "bug"}}).payload
        self.assertTrue(0.1 <= run["chance"] <= 0.95 and run["say_ok"] and run["say_fail"])
        inv = link.request("pet.inventory", {"action": "add", "item": {"name": "patch kit", "kind": "medicine"}}).payload
        self.assertEqual(inv["changed"], {"id": "patch_kit", "name": "patch kit", "qty": 1})
        self.assertIn("patch_kit", [b["id"] for b in link.request("pet.inventory", {}).payload["bag"]])
        drop = link.request("pet.inventory", {"action": "drop", "item": "patch kit"}).payload
        self.assertEqual(drop["changed"]["qty"], 0)

    def test_gifts_apply_now_and_store_true_goes_to_the_bag(self):
        link = sim(hunger=60)
        before = link.pet.bag_payload()
        body = link.request("pet.gift", {"item": {"name": "data cookie", "kind": "food", "power": 30}}).payload
        self.assertLess(link.pet.hunger, 60)                        # (4) applied right away
        self.assertNotIn("stored", body)
        self.assertEqual(link.pet.bag_payload(), before)
        body = link.request("pet.gift", {"item": {"name": "data cookie", "kind": "food", "power": 30},
                                         "store": True}).payload
        self.assertTrue(body["stored"])
        self.assertEqual(link.pet.bag["data_cookie"]["qty"], 1)
        link.request("pet.gift", {"item": {"name": "medicine", "kind": "medicine"}})   # healthy: saved for later
        self.assertEqual(link.pet.bag["medicine"]["qty"], 2)

    def test_every_say_line_fits(self):
        for key, lines in SIM_LINES.items():
            for line in lines:
                filled = line.replace("{foe}", "kernel panic")
                self.assertLessEqual(len(filled), SAY_MAX, (key, filled))
                self.assertTrue(filled.isascii())
        self.assertEqual(len(clamp_say("x" * 80)), SAY_MAX)
        self.assertTrue(clamp_say("word " * 20).endswith("..."))

    def test_chart_matches_the_spec(self):
        self.assertEqual(effectiveness("NET", "VOID"), 0)
        self.assertEqual(effectiveness("FIRE", "FIRE"), 0)
        self.assertEqual(effectiveness("VOLT", "DATA"), 2)
        self.assertEqual(effectiveness("NORM", "NORM"), 1)
        self.assertEqual(effectiveness("bogus", "DATA"), 1)
        for kind in ROSTER:                    # every listed weakness really is 2x
            self.assertEqual(effectiveness(kind.weak, kind.type), 2, kind.id)

    def test_roster_matches_the_companions_data(self):
        data = companion_script().parent / "pet" / "data" / "roster.json"
        if not data.exists():
            self.skipTest(f"no {data} (the companion checkout has no roster data)")
        theirs = {e["id"]: e for e in json.loads(data.read_text())["enemies"] if not e.get("extra")}
        self.assertEqual(set(theirs), {k.id for k in ROSTER})
        for kind in ROSTER:
            e = theirs[kind.id]
            self.assertEqual((kind.glyph, kind.type, kind.weak, kind.face, kind.depth, kind.boss),
                             (e["glyph"], e["type"], e["weak"], e["face"], e["depth"], bool(e.get("boss"))), kind.id)


class ScreenTests(unittest.TestCase):
    def assert_box(self, rows, width=48):
        lines = text(rows)
        self.assertTrue(lines)
        for line in lines:
            body = line.lstrip(" ")
            self.assertEqual(len(body), 48, repr(line))
            self.assertTrue(body.isascii(), repr(line))
            self.assertIn(body[0], "+|")
            self.assertLessEqual(len(line), max(48, width))

    def test_every_phase_is_48_columns(self):
        b, link = battle("bug", 2, hp=6, max_hp=28)
        for phase in ("log", "menu", "fight", "bag", "pulse"):
            b.phase = phase
            for width in (48, 80, 120):
                self.assert_box(render_battle(b, width, 30), width)
        b.phase = "menu"
        win(b)
        self.assertIsNotNone(b.result)
        self.assert_box(render_battle(b, 100, 30), 100)
        for result in ("win", "lose", "flee", "abort"):
            b.result = result
            self.assert_box(render_battle(b, 48, 30))

    def test_every_enemy_at_high_level(self):
        link = sim()
        for kind in ROSTER:
            b = Battle(joined(link), Enemy.spawn(kind, 12), rng=random.Random(1))
            b.start()
            self.assert_box(render_battle(b, 60, 30), 60)
            self.assertTrue(any(kind.name.upper() in line for line in battle_text(b)), kind.id)

    def test_short_screens_shrink_the_log(self):
        b, _ = battle("bug")
        b.phase = "fight"
        rows = render_battle(b, 48, 24)
        self.assertEqual(len(rows), 24)
        self.assertEqual(b.log_rows, 2)
        self.assertIn("up/down", text(rows)[-2])

    def test_say_line_row(self):
        self.assertEqual(say_row("PULSE", "Weakness found!"), "PULSE: Weakness found!")
        long37 = "x" * PREFIX_MAX
        self.assertEqual(PREFIX_MAX, 37)
        self.assertEqual(say_row("PULSE", long37), "PULSE: " + long37)
        long38 = "y" * 38
        self.assertEqual(say_row("PULSE", long38), long38)        # bare: no room for the prefix
        full = "z" * 44
        self.assertEqual(say_row("PULSE", full), full)
        b, _ = battle("bug")
        b.say = full
        lines = battle_text(b)
        row = next(i for i, line in enumerate(lines) if full in line)
        self.assertEqual(lines[row], "| " + full + " |")          # a row of its own
        self.assertTrue(lines[row - 1].startswith("+---") and lines[row + 1].startswith("+---"))


class ConfigAndLcdTests(unittest.TestCase):
    def test_battle_ini_overrides(self):
        cfg = write_config({"battle.ini": "[odds]\nflee_base = 0.9\n[enemy.bug]\nface = (O_O)\nhp = 99\n"
                                          "[enemy.spam]\nenabled = no\n"})
        roster, odds = load_battle_config(cfg)
        self.assertEqual(odds["flee_base"], 0.9)
        bug = roster_entry("bug", roster)
        self.assertEqual((bug.face, bug.hp), ("(O_O)", 99))
        self.assertIsNone(roster_entry("spam", roster))
        self.assertEqual(load_battle_config(ROOT / "config")[0], ROSTER)   # the shipped file changes nothing

    def test_lcd_battle_say(self):
        from dataclasses import replace

        from cyberdeck.lcd import LcdService, LcdSettings

        lcd = LcdService(LcdSettings(), threaded=False, blurbs={"any": ["hi"]})
        self.assertEqual(lcd.battle_say("Weakness found!", "bug", now=100.0), "ok")
        lcd.battle_say("Take that!", "bug", now=100.5)
        keys = [m.key for m in lcd.queue.items if m.key.startswith("battle:")]
        self.assertEqual(keys, ["battle:Take that!"])              # only the newest waits
        self.assertIn("Weakness found!", [t for _, t in lcd.blurbs.reactions])
        off = LcdService(replace(LcdSettings(), battle=False), threaded=False, blurbs={"any": ["hi"]})
        self.assertEqual(off.battle_say("Ouch!", "bug", now=1.0), "skip")


class CommandTests(unittest.TestCase):
    def test_battle_command_in_headless(self):
        from cyberdeck.headless import run_headless
        import io

        ctx = make_ctx()
        reg = build_registry(include_plugins=False)
        out = io.StringIO()
        run_headless(ctx, reg, ["battle firewall 3", "battle bag", "battle pulse", "battle fight 1",
                                "battle fight static zap", "battle bag 1", "battle show", "battle end"], out)
        text_out = out.getvalue()
        self.assertIn("+----------------------------------------------+", text_out)
        self.assertIn("A wild FIREWALL appeared!", text_out)
        self.assertIn("PULSE used PECK!", text_out)
        self.assertIn("PULSE used STATIC ZAP!", text_out)
        self.assertIn("1. cache cookie x3", text_out)
        self.assertIn("You used CACHE COOKIE!", text_out)
        self.assertIn("Mood:", text_out)
        self.assertRegex(text_out, r"(got away from|beat|fainted vs) the firewall")
        self.assertIn("no enemy 'nope'", reg.dispatch("battle nope", ctx)[0])
        self.assertIn("no battle going on", reg.dispatch("battle run", ctx)[0])

    def test_script_flag_end_to_end(self):
        out = subprocess.run([sys.executable, "-m", "cyberdeck", "--headless", "--mode", "sim",
                              "--script", "battle kernel panic 1", "--script", "battle run", "--script", "quit"],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("KERNEL PANIC", out.stdout)
        self.assertIn("BOSS", out.stdout)


class UnifiedUiTests(unittest.TestCase):
    def test_battle_in_the_rich_ui(self):
        try:
            sys.path.insert(0, str(ROOT / "tests" / "dash"))
            from test_unified import HAVE_RICH, make_unified, press, render
        finally:
            sys.path.pop(0)
        if not HAVE_RICH:
            self.skipTest("rich not installed")
        dash = make_unified()
        dash.deck_ui_action(":tab console")
        press(dash, "battle glitch", "enter")
        self.assertEqual(dash.bridge.activity.title, "BATTLE")
        screen = render(dash, 100, 34)
        self.assertIn("GLITCH  Lv1  VOID", screen)
        self.assertIn("pick", screen)                              # the battle key hints
        while dash.bridge.activity is not None:
            b = dash.bridge.activity.battle
            press(dash, "enter" if b.phase != "menu" else "1")
        self.assertRegex(dash.bridge.last_output[-1], r"(beat|fainted vs) the glitch")


class RealCompanionBattle(unittest.TestCase):
    """A whole battle against the real companion/companion.py over TCP."""

    def setUp(self):
        from test_link import fast, free_port, wait_for

        from cyberdeck.link import StreamCompanionLink, TcpTransport

        script = companion_script()
        if not script.exists():
            self.skipTest("no companion/ checkout")
        missing = companion_battle_data_missing()
        if missing:
            self.skipTest(missing)
        port = free_port()
        self.proc = subprocess.Popen([sys.executable, str(script), "--tcp", f"127.0.0.1:{port}",
                                      "--event-interval", "0"],
                                     cwd=str(script.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def listening():
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return True
            except OSError:
                return False

        self.assertTrue(wait_for(listening, 10), "companion did not start")
        self.link = StreamCompanionLink(lambda: TcpTransport("127.0.0.1", port), fast(port=port))
        self.link.connect()

    def tearDown(self):
        self.link.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def test_win_a_battle_and_match_the_sim(self):
        if self.link.request("pet.combat.roster", {}).payload.get("code") == "unknown_type":
            self.skipTest("this companion has no battles yet")
        self.link.request("pet.sleep", {"on": False})
        b = Battle(joined(self.link), enemy_for("bug", 1), rng=random.Random(4))
        self.assertTrue(b.start(), b.note)
        sim_link = sim()
        sim_start = sim_link.request("pet.combat.stats", {"enemy": b.enemy.describe()}).payload
        real_start = self.link.request("pet.combat.stats", {"enemy": b.enemy.describe()}).payload
        self.assertLessEqual(set(real_start) - {"known_moves"}, set(sim_start) | {"stage", "xp", "bond", "species",
                                                                                     "moves_known", "name", "mood"})
        win(b, limit=60)
        self.assertEqual(b.result, "win", b.history)
        self.assertIn("won the battle", " ".join(b.history))
        self.assertTrue(all(len(line) <= SAY_MAX for line in b.history))
        self.assert_rows(b)

    def assert_rows(self, b):
        for line in battle_text(b):
            self.assertEqual(len(line), 48)
            self.assertTrue(line.isascii())


if __name__ == "__main__":
    unittest.main()
