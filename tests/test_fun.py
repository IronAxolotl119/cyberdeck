"""tests/test_fun.py — boot animation, visualizer, moods, hack show,
typing challenge, dungeon, and the pet.gift message.

Every full-screen mode is an Activity, so these tests feed key names and
fake timestamps and read render() back as plain text.
"""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from cyberdeck.activity import rows_to_text
from cyberdeck.commands import build_registry
from cyberdeck.companion import PetState, SimCompanionLink
from cyberdeck.config import Settings
from cyberdeck.features import dungeon, moods
from cyberdeck.features.boot import BootActivity, BootSettings, load_boot_settings
from cyberdeck.features.mainframe import MainframeActivity
from cyberdeck.features.typing_game import (HighScores, ScoreEntry, TypingActivity, TypingRound, accuracy,
                                            summarize, wpm)
from cyberdeck.features.visualizer import Visualizer
from cyberdeck.fx import banner, fake_code_line, glitch, level_char, progress
from cyberdeck.protocol import TYPE_ERROR, TYPE_PET_GIFT, TYPE_PET_GIFT_OK, Message
from cyberdeck.sensors import SensorReading

from helpers import make_ctx


def reading(**over) -> SensorReading:
    base = dict(hostname="deck", uptime_s=1.0, cpu_temp_c=45.0, cpu_percent=20.0, mem_percent=30.0,
                battery_percent=80.0, charging=False, wifi_ssid="DECK-NET", wifi_dbm=-55)
    base.update(over)
    return SensorReading(**base)


class FxTests(unittest.TestCase):
    def test_banner_shape(self):
        rows = banner("HI")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], "█ █ ███")
        self.assertNotIn("█", "".join(banner("HI", unicode=False)))

    def test_glitch_amounts(self):
        rng = random.Random(1)
        self.assertEqual(glitch("hello", rng, 0.0), "hello")
        self.assertNotEqual(glitch("hello world", rng, 1.0), "hello world")
        self.assertEqual(glitch("a b", rng, 1.0)[1], " ")  # spaces are kept

    def test_progress_and_levels(self):
        self.assertEqual(progress(0.5, 4), "[██░░]")
        self.assertEqual(progress(2, 2, unicode=False), "[##]")
        self.assertEqual(level_char(0.0), "▁")
        self.assertEqual(level_char(1.0), "█")

    def test_fake_code_is_one_line(self):
        rng = random.Random(3)
        for _ in range(50):
            line = fake_code_line(rng)
            self.assertNotIn("\n", line)
            self.assertNotIn("{f}", line)  # every placeholder was filled
            self.assertNotIn("{t}", line)


class BootTests(unittest.TestCase):
    def test_timeline(self):
        boot = BootActivity(BootSettings(duration=4.0), seed=1)
        boot.start(100.0)
        boot.update(100.1)
        early = "\n".join(rows_to_text(boot.render(80, 30)))
        self.assertNotIn("DECRYPTING", early)
        boot.update(103.8)
        late = "\n".join(rows_to_text(boot.render(80, 30)))
        self.assertIn("DECRYPTING KERNEL", late)
        self.assertIn("SYSTEM READY", late)
        self.assertFalse(boot.done)
        boot.update(104.1)
        self.assertTrue(boot.done)

    def test_any_key_skips(self):
        boot = BootActivity(BootSettings(), seed=1)
        boot.on_key("x")
        self.assertTrue(boot.done)

    def test_settings(self):
        s = load_boot_settings(Settings.from_text("[boot]\nenabled = no\nduration = 0.1\nglitch = 5\n"))
        self.assertEqual((s.enabled, s.duration, s.glitch), (False, 0.5, 1.0))


class _FakeMusic:
    playing = True

    def level(self, now):
        return 1.0

    def pitch(self):
        return 1.0  # highest note -> peak on the right


class VisualizerTests(unittest.TestCase):
    def test_idle_stays_low(self):
        viz = Visualizer(width=8, idle=0.3, seed=1)
        for t in range(20):
            levels = viz.levels(10 + t * 0.1)
        self.assertTrue(all(0 <= v <= 0.31 for v in levels))
        self.assertEqual(len(viz.render(12.5)), 8)

    def test_music_peaks_at_pitch(self):
        viz = Visualizer(width=10, seed=1)
        viz.source = _FakeMusic()
        levels = viz.levels(1.0)
        self.assertGreater(levels[-1], levels[0])
        self.assertGreater(max(levels), 0.8)


class MoodTests(unittest.TestCase):
    INI = """
[critical]
when = battery < 8
role = alert
messages =
    {bat}% left!
[hot]
when = temp >= 70
messages =
    {temp}C toaster
[charging]
when = charging
role = ok
messages =
    yum electrons
[idle]
when = always
messages =
    one
    two
"""

    def setUp(self):
        self.rules = moods.parse_rules(Settings.from_text(self.INI))

    def test_rules_parsed_in_order(self):
        self.assertEqual([r.name for r in self.rules], ["critical", "hot", "charging", "idle"])
        self.assertEqual(self.rules[3].messages, ("one", "two"))

    def test_first_matching_rule_wins(self):
        self.assertEqual(moods.pick(self.rules, reading(battery_percent=5, cpu_temp_c=80), 0), ("5% left!", "alert"))
        self.assertEqual(moods.pick(self.rules, reading(cpu_temp_c=72.4), 0)[0], "72C toaster")
        self.assertEqual(moods.pick(self.rules, reading(charging=True), 0), ("yum electrons", "ok"))

    def test_rotation(self):
        self.assertEqual(moods.pick(self.rules, reading(), 0, rotate=10)[0], "one")
        self.assertEqual(moods.pick(self.rules, reading(), 10, rotate=10)[0], "two")

    def test_conditions(self):
        r = reading(wifi_dbm=-80, battery_percent=None)
        self.assertTrue(moods.matches("wifi < -78", r))
        self.assertFalse(moods.matches("battery < 20", r))  # unknown battery never matches
        self.assertFalse(moods.matches("nonsense", r))
        self.assertTrue(moods.matches("pet_alert", r, pet_alert=True))

    def test_bad_placeholder_is_shown_raw(self):
        self.assertEqual(moods.fill("{nope}", reading()), "{nope}")

    def test_bundled_moods_file(self):
        rules = moods.parse_rules(Settings.load("moods.ini"))
        self.assertGreaterEqual(len(rules), 5)
        self.assertEqual(rules[-1].when, "always")


class MainframeTests(unittest.TestCase):
    def test_runs_to_access_granted(self):
        act = MainframeActivity("pentagon", duration=4, seed=2)
        act.update(0.0)
        act.update(2.0)
        mid = "\n".join(rows_to_text(act.render(100, 30)))
        self.assertIn("TARGET: PENTAGON", mid)
        self.assertIn("CRACKING RSA-4096", mid)
        act.update(4.5)
        end = "\n".join(rows_to_text(act.render(100, 30)))
        self.assertIn("welcome to PENTAGON", end)
        self.assertFalse(act.done)
        act.on_key("a")
        self.assertTrue(act.done)

    def test_small_screen_splits_banner(self):
        act = MainframeActivity(duration=2, seed=1)
        act.update(0)
        act.update(3)
        self.assertTrue(rows_to_text(act.render(40, 20)))

    def test_headless(self):
        lines = MainframeActivity(seed=1).headless()
        self.assertIn("TARGET", lines[0])
        self.assertTrue(all("DONE" in line for line in lines[1:7]))


class TypingTests(unittest.TestCase):
    def test_round_scoring(self):
        r = TypingRound("abc def")
        for i, ch in enumerate("abx"):
            r.key(ch, 10.0 + i)
        r.key("backspace", 13.0)
        for ch in "c def":
            r.key("space" if ch == " " else ch, 14.0)
        self.assertTrue(r.done)
        self.assertFalse(r.failed)
        self.assertEqual((r.keystrokes, r.mistakes), (8, 1))
        self.assertEqual(r.elapsed(99), 4.0)
        self.assertEqual(r.correct_chars(), 7)

    def test_time_limit_and_skip(self):
        r = TypingRound("abc", time_limit=5)
        r.key("a", 0)
        r.check_time(6)
        self.assertTrue(r.failed)
        skip = TypingRound("abc")
        skip.key("enter", 1)
        self.assertTrue(skip.failed and skip.done)

    def test_maths(self):
        self.assertEqual(wpm(50, 60), 10.0)
        self.assertEqual(wpm(10, 0), 0.0)
        self.assertEqual(accuracy(10, 1), 0.9)
        self.assertEqual(accuracy(0, 0), 1.0)

    def test_summary_and_verdict(self):
        good = TypingRound("hello")
        for i, ch in enumerate("hello"):
            good.key(ch, i * 0.5)
        result = summarize([good])
        self.assertTrue(result.granted)
        self.assertEqual(result.score, round(result.wpm))

    def test_high_scores_keep_top_n(self):
        path = Path(tempfile.mkdtemp()) / "s.json"
        hs = HighScores(path, keep=2)
        for score in (10, 30, 20):
            hs.add(ScoreEntry("X", score, 1, 1, "d"))
        again = HighScores(path, keep=2).load()
        self.assertEqual([e.score for e in again.entries], [30, 20])
        self.assertIsNone(again.add(ScoreEntry("Y", 5, 1, 1, "d")))

    def test_activity_full_run(self):
        clock = iter(float(i) for i in range(1000))
        scores = HighScores(Path(tempfile.mkdtemp()) / "s.json")
        act = TypingActivity(["ab", "cd"], scores, rounds=2, seed=1, clock=lambda: next(clock))
        for rnd in list(act.rounds):
            for ch in rnd.target:
                act.on_key(ch)
        self.assertIsNotNone(act.result)
        self.assertIn("ACCESS GRANTED", act.result_lines[0])
        self.assertEqual(len(scores.entries), 1)
        screen = "\n".join(rows_to_text(act.render(70, 30)))
        self.assertIn("ACCURACY", screen)
        self.assertIn("NEW HIGH SCORE  #1", screen)
        act.on_key("x")
        self.assertTrue(act.done)

    def test_command_headless_shows_scores(self):
        ctx = make_ctx()
        self.assertIn("needs the full-screen", build_registry(include_plugins=False).dispatch("typehack", ctx)[0])
        ctx.interactive = True
        build_registry(include_plugins=False).dispatch("typehack", ctx)
        self.assertIsInstance(ctx.activity, TypingActivity)


class DungeonTests(unittest.TestCase):
    def game(self, seed=7):
        return dungeon.Game(rng=random.Random(seed))

    def test_same_seed_same_map(self):
        self.assertEqual(self.game(3).map_text(), self.game(3).map_text())
        self.assertNotEqual(self.game(3).map_text(), self.game(4).map_text())

    def test_level_has_player_stairs_monsters_loot(self):
        g = self.game()
        text = "\n".join(g.map_text())
        self.assertEqual(text.count("@"), 1)
        self.assertEqual(text.count(">"), 1)
        self.assertGreaterEqual(len(g.monsters), 3)
        self.assertTrue(any(i["kind"] != "heal" for i in g.items.values()))

    def test_walls_block_without_using_a_turn(self):
        g = self.game()
        g.grid[g.py][g.px + 1] = dungeon.WALL
        g.monsters.clear()
        g.move(1, 0)
        self.assertEqual(g.messages[-1], "bonk. that's a wall.")

    def test_pick_up_loot_calls_on_loot(self):
        g = self.game()
        got = []
        g.on_loot = got.append
        g.monsters.clear()
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        g.items[(g.px + 1, g.py)] = {"name": "bit ball", "kind": "toy", "power": 20, "rarity": "common"}
        g.move(1, 0)
        self.assertEqual(got[0]["name"], "bit ball")
        self.assertEqual(g.found, got)

    def test_fight_and_die(self):
        g = self.game()
        g.monsters = [dungeon.Monster("daemon", "D", g.px + 1, g.py, 999, 50)]
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        for _ in range(30):
            g.move(1, 0)
        self.assertTrue(g.over)
        self.assertIn("derezzed", g.messages[-1])

    def test_kill_scores(self):
        g = self.game()
        g.monsters = [dungeon.Monster("bug", "b", g.px + 1, g.py, 1, 0)]
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        g.move(1, 0)
        self.assertEqual(g.monsters, [])
        self.assertEqual(g.score, 10)

    def test_descend(self):
        g = self.game()
        self.assertFalse(g.descend())
        g.px, g.py = g.stairs
        self.assertTrue(g.descend())
        self.assertEqual(g.depth, 2)

    def test_loot_bag(self):
        bag = dungeon.LootBag(Path(tempfile.mkdtemp()) / "loot.json")
        bag.add({"name": "data cookie", "kind": "food", "power": 30})
        bag.add({"name": "bit ball", "kind": "toy", "power": 20})
        self.assertEqual(bag.take("Bit Ball")["kind"], "toy")
        self.assertEqual(bag.take("1")["name"], "data cookie")
        with self.assertRaises(KeyError):
            bag.take("1")

    def test_activity_keys(self):
        ctx = make_ctx()
        act = dungeon.DungeonActivity(ctx, self.game())
        act.on_key("i")
        self.assertIn("LOOT BAG", "\n".join(rows_to_text(act.render(80, 24))))
        act.on_key("g")
        self.assertIn("empty", act.game.messages[-1])
        act.on_key("q")
        self.assertTrue(act.done)
        self.assertIn("depth 1", act.result_lines[0])


class GiftTests(unittest.TestCase):
    def test_pet_gift_kinds(self):
        pet = PetState(hunger=50, happiness=50, energy=50, stage="rookie")
        self.assertEqual(pet.gift({"name": "cookie", "kind": "food", "power": 30})["effects"]["hunger"], -30)
        self.assertEqual(pet.gift({"name": "ball", "kind": "toy", "power": 20})["effects"]["happiness"], 20)
        self.assertEqual(pet.gift({"name": "cell", "kind": "battery", "power": 25})["effects"]["energy"], 25)
        evo = pet.gift({"name": "evo chip", "kind": "evolve"})
        # A chip makes PULSE ready (every branch unlocked) but never evolves it by itself.
        self.assertEqual((pet.stage, evo["effects"]["stage"]), ("rookie", None))
        self.assertTrue(pet.evolution()["ready"])
        self.assertTrue(all(c["available"] for c in pet.evolution()["choices"]))
        self.assertEqual(pet.gift({"name": "rock", "kind": "weird"})["item"]["kind"], "trinket")
        self.assertEqual(pet.as_payload()["gifts"], 5)

    def test_protocol_round_trip_and_errors(self):
        link = SimCompanionLink()
        link.connect()
        reply = link.handle(Message.request(TYPE_PET_GIFT, {"item": {"name": "bit ball", "kind": "toy", "power": 10}}))
        self.assertEqual(reply.type, TYPE_PET_GIFT_OK)
        self.assertTrue(reply.payload["accepted"])
        bad = link.handle(Message.request(TYPE_PET_GIFT, {"item": {"kind": "toy"}}))
        self.assertEqual((bad.type, bad.payload["code"]), (TYPE_ERROR, "bad_item"))

    def test_loot_gift_command_logs_and_removes(self):
        ctx = make_ctx()
        reg = build_registry(include_plugins=False)
        dungeon.loot_bag(ctx).add({"name": "evo chip", "kind": "evolve", "power": 0, "rarity": "legendary"})
        lines = reg.dispatch("pet gift 1", ctx)
        self.assertIn("ready to evolve", lines[0])
        self.assertEqual(dungeon.loot_bag(ctx).items(), [])
        self.assertIn("evo chip", "\n".join(reg.dispatch("pet log", ctx)))


if __name__ == "__main__":
    unittest.main()
