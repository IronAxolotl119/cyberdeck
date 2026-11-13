"""tests/test_stats.py — the PULSE STATS screen (statsheet.py, features/petstats.py).

  * paging: left/right and h/l flip (and wrap), 1-8 jump, esc/q close,
    up/down scroll a long page, the '3/8' indicator, remembered page,
    config/pet.ini [stats]
  * fallback: pet.stats -> pet.status {full:true} -> plain pet.status
    (basic pages), a flat `lines` list only, junk pages, a dead link
  * sim parity: the sim pet's pet.stats has exactly the real companion's
    fields, and the deck's page builder makes the companion's pages
    line for line; the battle record counts wins/losses/flees/bosses/faints
  * widths: every row of every page is exactly 48 columns of ASCII
  * battles: STATS on the PULSE card never uses a turn
  * where it opens: `pet stats` (headless and --script), `s` on the
    PULSE tile in the unified UI, `s` on the classic PULSE tab, F3
  * end to end against the real companion/companion.py over TCP
"""

from __future__ import annotations

import json
import random
import socket
import subprocess
import sys
import unittest
from pathlib import Path

from cyberdeck.commands import build_registry
from cyberdeck.companion import PetState, SimCompanionLink
from cyberdeck.features.battle import Battle, battle_text, enemy_for
from cyberdeck.features.dungeon import PulseAlly
from cyberdeck.protocol import Message
from cyberdeck.statsheet import (
    PAGE_TITLES,
    SCREEN_W,
    StatsSettings,
    StatsView,
    basic_sheet,
    build_pages,
    fetch_sheet,
    load_stats_settings,
)

from helpers import companion_script, make_ctx, write_config

ROOT = Path(__file__).resolve().parent.parent

# The real companion's sheet, made in-process by its own code (no network).
REAL_SHEET_SCRIPT = r"""
import json, random, sys
from datetime import datetime
sys.path.insert(0, '.')
from link.handler import CompanionHandler
from link.protocol import Message
from pet.creature import Creature
from pet.life import Life
T0 = datetime(2026, 11, 12, 14, 0).timestamp()
pet = Creature(name="Pulse", species_id="chirplet", life=Life.new(random.Random(3), T0 - 5 * 86400))
pet.life.last_now = T0
h = CompanionHandler(pet, clock=lambda: T0)
h.rng = random.Random(3)
stats = h.handle(Message.request("pet.stats", {})).payload
full = h.handle(Message.request("pet.status", {"full": True})).payload
plain = h.handle(Message.request("pet.status", {})).payload
print(json.dumps({"stats": stats, "full": full, "plain": plain}, default=str))
"""


def real_sheets() -> dict:
    script = companion_script()
    if not (script.parent / "pet" / "fullstats.py").exists():
        raise unittest.SkipTest("this companion checkout has no pet.stats (pet/fullstats.py)")
    out = subprocess.run([sys.executable, "-c", REAL_SHEET_SCRIPT], cwd=str(script.parent),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class Recorder:
    """Wraps a link and remembers every message type sent."""

    def __init__(self, link) -> None:
        self.link = link
        self.sent: list[str] = []

    def request(self, typ, payload=None, timeout=1.0):
        self.sent.append(typ)
        return self.link.request(typ, payload or {}, timeout)

    def __getattr__(self, name):
        return getattr(self.link, name)


class FakeCompanion:
    """A companion whose answers we choose: replies = {type: payload | 'unknown' | 'bad'}."""

    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.sent: list[tuple[str, dict]] = []

    def request(self, typ, payload=None, timeout=1.0):
        payload = payload or {}
        self.sent.append((typ, payload))
        msg = Message.request(typ, payload)
        key = f"{typ} full" if typ == "pet.status" and payload.get("full") and f"{typ} full" in self.replies else typ
        answer = self.replies.get(key, "unknown")
        if answer == "unknown":
            return msg.error("unknown_type", f"unsupported message type: {typ}")
        if answer == "bad":
            return msg.error("bad_payload", "no")
        return msg.reply(typ + ".ok", answer)


def sim_link(**pet) -> SimCompanionLink:
    link = SimCompanionLink(pet=PetState(**pet), clock=lambda: 0.0)
    link.connect()
    return link


def sim_view(**pet) -> StatsView:
    sheet, source, _note = fetch_sheet(sim_link(**pet).request)
    return StatsView(sheet, source)


def assert_box(test: unittest.TestCase, lines: list[str]) -> None:
    for line in lines:
        test.assertEqual(len(line), SCREEN_W, repr(line))
        test.assertTrue(line.isascii(), repr(line))
        test.assertTrue(line[0] in "+|" and line[-1] in "+|", repr(line))


# ---------------------------------------------------------------------------
class PagingTests(unittest.TestCase):
    def test_eight_pages_in_order_with_indicator(self):
        v = sim_view()
        self.assertEqual([p["title"] for p in v.pages], list(PAGE_TITLES))
        self.assertIn("PULSE STATS", v.text()[1])
        self.assertTrue(v.text()[1].rstrip(" |").endswith("Overview  1/8"))
        v.key("3")
        self.assertTrue(v.text()[1].rstrip(" |").endswith("Care  3/8"))

    def test_left_right_h_l_flip_and_wrap(self):
        v = sim_view()
        for key, page in (("right", 1), ("l", 2), ("left", 1), ("h", 0), ("left", 7), ("right", 0)):
            self.assertFalse(v.key(key))
            self.assertEqual(v.page, page, key)

    def test_no_wrap_setting_stops_at_the_ends(self):
        sheet, _s, _n = fetch_sheet(sim_link().request)
        v = StatsView(sheet, settings=StatsSettings(wrap=False))
        v.key("left")
        self.assertEqual(v.page, 0)
        v.key("8")
        v.key("right")
        self.assertEqual(v.page, 7)

    def test_digits_jump_and_out_of_range_is_ignored(self):
        v = sim_view()
        v.key("7")
        self.assertEqual(v.pages[v.page]["title"], "Battle Record")
        v.key("9")
        v.key("0")
        self.assertEqual(v.page, 6)

    def test_esc_and_q_close(self):
        for key in ("esc", "q"):
            self.assertTrue(sim_view().key(key))
        self.assertFalse(sim_view().key("x"))

    def test_a_long_page_scrolls(self):
        moves = [f"move {i}" for i in range(20)]
        v = sim_view(moves=moves)
        v.key("5")
        first = v.text()[3]
        self.assertIn("v", v.text()[-2])                 # "more" hint pointing down
        v.key("down")
        self.assertNotEqual(v.text()[3], first)
        for _ in range(50):
            v.key("j")
        self.assertIn("* = loadout", "".join(v.text()))  # the last line is reachable
        v.key("right")
        self.assertEqual(v.scroll, 0)                    # a new page starts at the top

    def test_bars_are_drawn_from_the_raw_fields(self):
        v = sim_view(hp=7, max_hp=28)
        rows = v.render()
        hp_row = next(r for r in rows if "HP [" in "".join(t for t, _ in r))
        self.assertIn(("[##........]", "alert"), hp_row)      # 7/28 = 25%: red (rounded like the companion)
        v.settings.color_bars = False
        self.assertNotIn("alert", [role for r in v.render() for _t, role in r])

    def test_settings_file_and_remembered_page(self):
        cfg = write_config({"pet.ini": "[stats]\nrows = 6\nwrap = no\nstart_page = 7\n"})
        s = load_stats_settings(cfg)
        self.assertEqual((s.rows, s.wrap, s.start_page), (6, False, 7))
        ctx = make_ctx(cfg)
        reg = build_registry(include_plugins=False)
        reg.dispatch("pet stats", ctx)
        act = ctx.activity
        self.assertEqual(act.view.pages[act.view.page]["title"], "Battle Record")
        self.assertEqual(len(act.render(48, 40)), 6 + 6)
        act.on_key("left")
        act.on_key("esc")
        ctx.activity = None
        reg.dispatch("pet stats", ctx)
        self.assertEqual(ctx.activity.view.page, 5)          # reopens where you left


# ---------------------------------------------------------------------------
class FallbackTests(unittest.TestCase):
    PET = {"name": "PULSE", "species": "Chirplet", "stage": "baby", "level": 2, "xp": 30, "hp": 18, "max_hp": 20,
           "energy": 64, "hunger": 40, "happiness": 70, "mood": "playful", "attack": 5, "defense": 4,
           "speed": 6, "moves": ["peck", "chirp"], "care_mistakes": 1, "age_hours": 5.25}

    def test_pet_stats_first(self):
        sheet = PetState().full_stats()
        fake = FakeCompanion({"pet.stats": sheet})
        got, source, _ = fetch_sheet(fake.request)
        self.assertEqual(source, "pet.stats")
        self.assertEqual(got["pages"], sheet["pages"])
        self.assertEqual([t for t, _ in fake.sent], ["pet.stats"])

    def test_status_full_when_pet_stats_is_unknown(self):
        sheet = PetState().full_stats()
        fake = FakeCompanion({"pet.status full": {"pet": self.PET, "full": sheet}})
        got, source, _ = fetch_sheet(fake.request)
        self.assertEqual(source, "pet.status full")
        self.assertEqual(len(got["pages"]), 8)
        self.assertEqual(fake.sent[1], ("pet.status", {"full": True}))

    def test_old_companion_gets_basic_pages(self):
        fake = FakeCompanion({"pet.status": {"pet": self.PET, "evolution": {
            "next_stage": "in-training", "progress": 0.5, "ready": False,
            "choices": [{"species": "Wavling", "available": True}]}}})
        got, source, note = fetch_sheet(fake.request)
        self.assertEqual((source, note), ("pet.status", "basic stats"))
        titles = [p["title"] for p in got["pages"]]
        self.assertEqual(titles, ["Overview", "Combat", "Care", "Traits & Evolution", "Moves & Loadout"])
        flat = "\n".join(got["lines"])
        for bit in ("Lv 2  XP 30", "HP [#########.] 18/20", "Attack       5", "Next: in-training  50%",
                    "Can become: Wavling", " chirp", "(older companion: basic stats only)", "Age 5.2h"):
            self.assertIn(bit, flat)
        v = StatsView(got, source)
        for i in range(len(v.pages)):
            v.go(i)
            assert_box(self, v.text())
        self.assertIn("[1-5] jump", v.text()[-2])

    def test_full_flag_rejected_then_plain_status(self):
        fake = FakeCompanion({"pet.status full": "bad", "pet.status": {"pet": {"name": "BITSY"}}})
        got, source, _ = fetch_sheet(fake.request)
        self.assertEqual(source, "pet.status")
        self.assertEqual([t for t, _ in fake.sent], ["pet.stats", "pet.status", "pet.status"])
        self.assertEqual(got["pages"][0]["lines"][0], "BITSY")
        self.assertIn("BITSY STATS", StatsView(got).text()[1])

    def test_only_flat_lines_and_junk_are_cleaned(self):
        lines = ["== Overview ==", "PULSE", "== Bag ==", "cookie x3 " + "\u2014" * 3 + "z" * 60]
        got, _s, _n = fetch_sheet(FakeCompanion({"pet.stats": {"name": "PULSE", "lines": lines}}).request)
        self.assertEqual([p["title"] for p in got["pages"]], ["Overview", "Bag"])
        bag = got["pages"][1]["lines"][0]
        self.assertTrue(bag.isascii() and len(bag) <= 44 and bag.endswith("..."), bag)
        junk = {"pages": [{"title": "A very long page title here", "lines": ["ok", 5, None, {"x": 1}]}, "nope"]}
        got, _s, _n = fetch_sheet(FakeCompanion({"pet.stats": junk}).request)
        self.assertEqual(got["pages"], [{"title": "A very long page...", "lines": ["ok", "5"]}])

    def test_dead_link(self):
        def boom(typ, payload=None, timeout=1.0):
            raise OSError("link down")
        got, _s, note = fetch_sheet(boom)
        self.assertIsNone(got)
        self.assertIn("link down", note)
        ctx = make_ctx()
        ctx.companion.request = boom
        self.assertIn("no stats from PULSE", build_registry(include_plugins=False).dispatch("pet stats", ctx)[0])


# ---------------------------------------------------------------------------
class SimParityTests(unittest.TestCase):
    def test_sim_has_exactly_the_real_companions_fields(self):
        real = real_sheets()["stats"]
        sim = sim_link().request("pet.stats", {}).payload
        self.assertEqual(set(sim), set(real))
        for key in ("evolution", "record", "rival", "quirks"):
            self.assertEqual(set(sim[key]), set(real[key]), key)
        self.assertEqual(set(sim["moves"][0]), set(real["moves"][0]))
        self.assertEqual(set(sim["bag"][0]), set(real["bag"][0]))
        for key, value in real.items():
            if value is not None and sim[key] is not None:
                self.assertEqual(type(sim[key]) in (int, float), type(value) in (int, float), key)

    def test_the_deck_builds_the_companions_pages_line_for_line(self):
        sheets = real_sheets()
        real = sheets["stats"]
        self.assertEqual(build_pages(real), real["pages"])
        self.assertEqual(sheets["full"]["full"]["pages"], real["pages"])
        self.assertNotIn("full", sheets["plain"])            # plain pet.status is unchanged

    def test_long_values_wrap_like_the_companion(self):
        # Companion 7fa74cc: long values wrap onto lines indented 2 (no '...'),
        # and Care modifier rows show the real amount.
        script = companion_script()
        if not (script.parent / "pet" / "fullstats.py").exists():
            self.skipTest("this companion checkout has no pet.stats")
        long_script = REAL_SHEET_SCRIPT.replace("stats = h.handle", """
from pet.battle import Bag
import pet.fullstats as fs
pet.life.add_to_shelf("a trinket with a very very long name indeed", "test", 3, T0)
pet.life.add_modifier("a_really_long_modifier_name_here", -12, 3600, T0)
Bag(pet).add({"name": "an extremely long legendary loot item name", "kind": "food"}, 3)
stats = h.handle""", 1).replace("print(json.dumps({", """raw = dict(stats)
raw["evolution"] = dict(stats["evolution"], next_stage="champion", choices=[
    {"species": n, "available": i % 2 == 0}
    for i, n in enumerate(["Luminode", "Hexwave", "Aethernova", "Voidstatic", "Glitchling"])])
print(json.dumps({"choices": [raw, fs._pages(raw)], """, 1)
        out = subprocess.run([sys.executable, "-c", long_script], cwd=str(script.parent),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        sheets = json.loads(out.stdout)
        real = sheets["stats"]
        self.assertEqual(build_pages(real), real["pages"])
        raw, pages = sheets["choices"]
        self.assertEqual(build_pages(raw), pages)
        lines = [line for page in build_pages(real) + build_pages(raw) for line in page["lines"]]
        self.assertFalse(any(line.endswith("...") for line in lines))
        self.assertTrue(all(line.isascii() and len(line) <= 44 for line in lines))
        joined = " ".join(line.strip() for line in lines)
        for text in ("a trinket with a very very long name indeed", "an extremely long legendary loot item name",
                     "a_really_long_modifier_name_here -12", "Glitchling", "Voidstatic(locked)"):
            self.assertIn(text, joined)

    def test_status_full_matches_pet_stats_in_the_sim(self):
        link = sim_link()
        stats = link.request("pet.stats", {}).payload
        full = link.request("pet.status", {"full": True}).payload
        self.assertEqual(full["full"]["pages"], stats["pages"])
        self.assertNotIn("full", link.request("pet.status", {}).payload)

    def test_page_rules(self):
        for sheet in (PetState().full_stats(), PetState(moves=["a" * 30], name="X" * 40).full_stats(),
                      basic_sheet(FallbackTests.PET)):
            for page in sheet["pages"]:
                self.assertLessEqual(len(page["title"]), 20)
                for line in page["lines"]:
                    self.assertTrue(line.isascii() and len(line) <= 44, repr(line))
            self.assertEqual(sum(1 for line in sheet["lines"] if line.startswith("== ")), len(sheet["pages"]))

    def test_battle_record_counts_everything(self):
        link = sim_link()
        foe = {"name": "bug", "level": 1}
        boss = {"name": "kernel panic", "level": 5, "boss": True}
        link.request("pet.combat.end", {"result": "win", "enemies": [foe]})
        link.request("pet.combat.end", {"result": "win", "enemies": [boss]})
        link.request("pet.combat.end", {"result": "flee", "enemies": [foe]})
        link.request("pet.combat.hit", {"damage": 999, "enemy": foe})
        link.request("pet.combat.end", {"result": "lose", "enemies": [foe]})
        link.request("pet.combat.end", {"result": "lose", "enemies": [foe]})   # lost, but did not faint
        sheet = link.request("pet.stats", {}).payload
        self.assertEqual(sheet["record"], {"wins": 2, "losses": 2, "flees": 1, "bosses": 1, "faints": 1,
                                           "fights": 5})
        page = dict(zip(PAGE_TITLES, sheet["pages"]))["Battle Record"]["lines"]
        self.assertEqual(page[:6], ["Fights       5", "Wins         2", "Losses       2", "Fled         1",
                                    "Bosses       1", "Fainted      1"])

    def test_favourites_stay_hidden_until_found(self):
        link = sim_link()
        traits = lambda: dict(zip(PAGE_TITLES, link.request("pet.stats", {}).payload["pages"]))[
            "Traits & Evolution"]["lines"]
        self.assertIn("Fav food     ???", traits())
        link.request("pet.gift", {"item": {"name": "data cookie", "kind": "food", "power": 5}})
        self.assertIn("Fav food     data cookie", traits())
        self.assertEqual(link.request("pet.stats", {}).payload["favorites_hidden"], 3)


# ---------------------------------------------------------------------------
class WidthTests(unittest.TestCase):
    def test_every_page_is_48_columns_of_ascii(self):
        sheets = [PetState().full_stats(), PetState(name="SUPERCALIFRAGILISTIC", moves=["m" * 40] * 3).full_stats(),
                  basic_sheet(FallbackTests.PET)]
        try:
            sheets.append(real_sheets()["stats"])
        except unittest.SkipTest:
            pass
        for sheet in sheets:
            v = StatsView(sheet)
            for i in range(len(v.pages)):
                v.go(i)
                for height in (40, 12, 8):
                    assert_box(self, v.text(height))
            assert_box(self, v.all_text())

    def test_centred_in_a_wide_screen(self):
        rows = sim_view().render(100, 30)
        line = "".join(t for t, _ in rows[0])
        self.assertEqual(line, " " * 26 + "+" + "-" * 46 + "+")


# ---------------------------------------------------------------------------
class BattleNoTurnTests(unittest.TestCase):
    def start(self):
        rec = Recorder(sim_link())
        ally = PulseAlly(rec.request)
        self.assertTrue(ally.join())
        b = Battle(ally, enemy_for("bug", 2), depth=2, rng=random.Random(3))
        self.assertTrue(b.start())
        while b.phase == "log":
            b.key("enter")
        return b, rec

    def snapshot(self, b):
        return (b.turn, b.enemy.hp, b.pet.get("hp"), b.pet.get("energy"), list(b.history), b.over)

    def test_stats_from_the_pulse_card_uses_no_turn(self):
        b, rec = self.start()
        before = self.snapshot(b)
        sent = len(rec.sent)
        b.key("p")
        self.assertEqual(b.phase, "pulse")
        self.assertIn("> STATS", "\n".join(battle_text(b)))
        b.key("enter")
        self.assertEqual(b.phase, "stats")
        screen = battle_text(b)
        self.assertIn("PULSE STATS", screen[1])
        assert_box(self, screen)
        for key in ("right", "right", "7", "l", "h", "1", "down", "up"):
            b.key(key)
        b.key("esc")
        self.assertEqual(b.phase, "pulse")                 # back to the PULSE card
        b.key("esc")
        self.assertEqual(b.phase, "menu")
        self.assertEqual(self.snapshot(b), before)
        self.assertEqual(rec.sent[sent:], ["pet.stats"])   # nothing that costs a turn
        b.key("1")                                          # the turn is still ours
        self.assertEqual(b.turn, before[0] + 1)

    def test_q_and_s_keys_and_the_headless_command(self):
        b, rec = self.start()
        before = self.snapshot(b)
        b.key("p")
        b.key("s")
        self.assertEqual(b.phase, "stats")
        b.key("q")
        self.assertEqual(b.phase, "pulse")
        self.assertEqual(self.snapshot(b), before)
        ctx = make_ctx()
        reg = build_registry(include_plugins=False)
        reg.dispatch("battle virus 2", ctx)
        bt = ctx.services["battle"]
        turn, hp = bt.turn, bt.enemy.hp
        out = reg.dispatch("battle stats 7", ctx)
        self.assertIn("Battle Record  7/8", out[1])
        self.assertEqual((bt.turn, bt.enemy.hp), (turn, hp))
        self.assertEqual(len(reg.dispatch("battle stats", ctx)), 8 * 19)
        self.assertNotEqual(bt.phase, "stats")

    def test_stats_in_a_dungeon_battle(self):
        from cyberdeck.features.dungeon import DungeonActivity, new_game

        ctx = make_ctx()
        game = new_game(ctx, seed=5)
        m = next(iter(game.monsters))
        self.assertTrue(game.start_battle(m))
        act = DungeonActivity(ctx, game)
        b = game.battle
        while b.phase == "log":
            act.on_key("enter")
        turn = b.turn
        for key in ("p", "enter", "right"):
            act.on_key(key)
        self.assertIn("Combat  2/8", "\n".join("".join(t for t, _ in r) for r in act.render(100, 34)))
        act.on_key("esc")
        act.on_key("esc")
        self.assertEqual((b.turn, b.phase), (turn, "menu"))


# ---------------------------------------------------------------------------
class CommandTests(unittest.TestCase):
    def test_pet_stats_headless(self):
        ctx = make_ctx()
        reg = build_registry(include_plugins=False)
        reg.dispatch("pet stats", ctx)
        out = ctx.activity.headless()
        self.assertEqual(len(out), 8 * 19)                  # 8 boxes, 13 body rows + 6
        for i, title in enumerate(PAGE_TITLES, 1):
            self.assertIn(f"{title}  {i}/8", "\n".join(out))
        assert_box(self, out)
        ctx.activity = None
        reg.dispatch("pet stats bag", ctx)
        self.assertIn("Bag  6/8", ctx.activity.headless()[1])
        ctx.activity = None
        reg.dispatch("pet stats 3", ctx)
        self.assertEqual(len(ctx.activity.headless()), 19)
        self.assertIn("no stats page 'zzz'", reg.dispatch("pet stats zzz", ctx)[0])
        lines = reg.dispatch("pet stats lines", ctx)
        self.assertEqual(lines[0], "== Overview ==")
        from cyberdeck.features.petstats import page_completer

        self.assertEqual(page_completer(ctx, [])[:3], ["overview", "combat", "care"])

    def test_script_flag(self):
        out = subprocess.run([sys.executable, "-m", "cyberdeck", "--headless", "--mode", "sim",
                              "--script", "pet stats record", "--script", "quit"],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Battle Record  7/8", out.stdout)

    def test_f3_is_pet_stats(self):
        from cyberdeck.keys import DEFAULT_HOTKEYS

        self.assertEqual(DEFAULT_HOTKEYS["f3"], "pet stats")
        self.assertIn("F3 = pet stats", (ROOT / "config" / "keys.ini").read_text())


# ---------------------------------------------------------------------------
class UiTests(unittest.TestCase):
    def test_s_on_the_pulse_tile_in_the_rich_ui(self):
        try:
            sys.path.insert(0, str(ROOT / "tests" / "dash"))
            from test_unified import HAVE_RICH, make_unified, press, render
        finally:
            sys.path.pop(0)
        if not HAVE_RICH:
            self.skipTest("rich not installed")
        dash = make_unified()
        dash.go_to_panel("pet", zoom=False)
        self.assertIn("stats", render(dash, 100, 34).splitlines()[-1])     # footer: s stats
        press(dash, "s")
        self.assertEqual(dash.bridge.activity.title, "STATS")
        screen = render(dash, 100, 34)
        self.assertIn("PULSE STATS", screen)
        self.assertIn("Overview  1/8", screen)
        self.assertIn("page", screen.splitlines()[-1])                     # footer: stats keys
        press(dash, "right", "right")
        self.assertIn("Care  3/8", render(dash, 100, 34))
        press(dash, "8")
        self.assertIn("Discoveries  8/8", render(dash, 100, 34))
        press(dash, "esc")
        self.assertIsNone(dash.bridge.activity)

    def test_classic_pet_pane_and_s_on_the_pulse_tab(self):
        import cyberdeck.tui as tui
        from cyberdeck.config import load_layout, load_theme
        from cyberdeck.panes import pet_pane

        ctx = make_ctx()
        rows = pet_pane(ctx, 40, 30)
        self.assertIn("F3 / pet stats: full stats (s on this tab)", ["".join(t for t, _ in r) for r in rows])
        ui = tui.DeckUI(ctx, build_registry(include_plugins=False), load_theme(), load_layout())
        opened = []
        ui._run_activity = lambda stdscr, act: opened.append(act)

        class Screen:
            def __init__(self, key):
                self.key = key

            def get_wch(self):
                return self.key

        ui._showing = "output"
        ui._handle_key(Screen("s"))
        self.assertEqual((opened, ui.input), ([], "s"))     # elsewhere s just types
        ui.input, ui.cursor = "", 0
        ui._showing = "pet"
        ui._handle_key(Screen("s"))
        self.assertEqual([a.title for a in opened], ["STATS"])
        self.assertEqual(ui.input, "")


# ---------------------------------------------------------------------------
class RealCompanionStats(unittest.TestCase):
    """pet.stats against the real companion/companion.py over TCP."""

    def setUp(self):
        from test_link import fast, free_port, wait_for

        from cyberdeck.link import StreamCompanionLink, TcpTransport

        script = companion_script()
        if not (script.parent / "pet" / "fullstats.py").exists():
            self.skipTest("this companion checkout has no pet.stats")
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

    def test_pages_over_the_wire_and_in_battle(self):
        sheet, source, _ = fetch_sheet(self.link.request)
        self.assertEqual(source, "pet.stats")
        self.assertEqual([p["title"] for p in sheet["pages"]], list(PAGE_TITLES))
        self.assertEqual(build_pages(sheet), sheet["pages"])
        v = StatsView(sheet, source)
        for i in range(8):
            v.go(i)
            assert_box(self, v.text())
        self.link.request("pet.sleep", {"on": False})
        rec = Recorder(self.link)
        ally = PulseAlly(rec.request)
        self.assertTrue(ally.join(), ally.note)
        b = Battle(ally, enemy_for("bug", 1), rng=random.Random(4))
        self.assertTrue(b.start(), b.note)
        while b.phase == "log":
            b.key("enter")
        turn, hp, sent = b.turn, b.enemy.hp, len(rec.sent)
        for key in ("p", "enter", "7", "esc", "esc"):
            b.key(key)
        self.assertEqual((b.turn, b.enemy.hp, b.phase), (turn, hp, "menu"))
        self.assertEqual(rec.sent[sent:], ["pet.stats"])
        b._finish("flee", [])
        record = self.link.request("pet.stats", {}).payload["record"]
        self.assertEqual(record["flees"], 1)


if __name__ == "__main__":
    unittest.main()
