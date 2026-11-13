"""Network dungeons: the shared rules, the deck's maps, the picker, the floors.

What these tests pin down (companion/docs/DUNGEON_INTEGRATION.md,
"Network dungeons"):
  * the deck's port of the generation rules matches the real companion
    (subprocess), and the sim pet answers with the same fields;
  * maps are deterministic per (seed, floor) and bad layout numbers are
    clamped (never a crash);
  * the 48-column picker and banners match the mockups;
  * start floors snap down to a checkpoint; faint / flee end the run;
  * battle XP only ever comes from pet.combat.end (no double XP);
  * with no companion / an older one, `dungeon` is the classic dungeon;
  * NEW DUNGEON alerts (console, LCD), and no raw MAC ever shown.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cyberdeck import netdungeons as ndg
from cyberdeck.activity import rows_to_text
from cyberdeck.battledata import ALL_ROSTER, EXTRA_ROSTER, ROSTER, SCREEN_W
from cyberdeck.commands import build_registry
from cyberdeck.companion import PetState, SimCompanionLink
from cyberdeck.features.dungeon import DungeonActivity
from cyberdeck.features.netdungeon import NetDungeonActivity, clean_rows, picker_lines
from cyberdeck.protocol import Message

from helpers import companion_battle_data_missing, companion_script, make_ctx, write_config

REG = build_registry(include_plugins=False)
SAMPLES = [("wifi", "aa:bb:cc:00:11:22", "CafeTest", -60, True, False),
           ("bt", "11:22:33:44:55:66", "11:22:33:44:55:66", -80, False, False),
           ("lan", "10.0.0.5", "printer.local", None, False, True),
           ("wifi", "de:ad:be:ef:00:01", "", -45, True, False)]
FLOORS = (1, 2, 3, 5, 6, 8, 10, 15, 20, 26)

GEN_SCRIPT = r"""
import json, sys
sys.path.insert(0, '.')
from pet import dungeons as d
samples = json.loads(sys.argv[1]); floors = json.loads(sys.argv[2])
out = []
for kind, ident, name, rssi, sec, rare in samples:
    did, rec = d.new_record(kind, ident, name, rssi, 0, secured=sec, rare=rare)
    for home in (False, True):
        rec['home'] = home; rec['depth'] = 12
        out.append([did, rec['name'], rec['type'], rec['type2'], rec['difficulty'], d.seed_for(did),
                    d.theme(did, rec), d.level_range(rec), d.start_floors(12), d.snap_start(12, 9),
                    [[f, d.layout(did, rec, f), d.enemies_for(did, rec, f), d.boss_for(did, rec, f),
                      d.roll_loot(did, rec, f, d.is_boss_floor(f), 3), d.loot_mult(rec, f),
                      d.floor_xp(f, 'cleared', d.is_boss_floor(f)), d.floor_xp(f, 'stairs', False)]
                     for f in floors]])
print(json.dumps(out))
"""

REAL_FLOW = r"""
import json, random, sys
sys.path.insert(0, '.')
from link.handler import CompanionHandler
from link.protocol import Message
from pet.creature import Creature
from pet.life import Life
T0 = 1794492000.0
pet = Creature(name="Pulse", species_id="chirplet", life=Life.new(random.Random(3), T0 - 86400))
pet.life.last_now = T0
h = CompanionHandler(pet, clock=lambda: T0)
h.rng = random.Random(3)
ask = lambda t, p: h.handle(Message.request(t, p)).payload
disc = ask("pet.discovery", {"kind": "wifi", "id": "aa:bb:cc:00:11:22", "label": "CafeTest", "rssi": -60,
                             "security": "wpa2"})
lst = ask("pet.dungeons", {})
did = disc["dungeon"]["id"]
enter = ask("pet.dungeon.enter", {"id": did})
floor = ask("pet.dungeon.floor", {"id": did, "floor": 1, "result": "cleared", "xp": 4})
leave = ask("pet.dungeon.leave", {"id": did})
end = ask("pet.combat.end", {"result": "win", "xp": 10, "enemies": [{"name": "bug", "level": 3}],
                             "dungeon_id": did, "floor": 2})
bad = ask("pet.dungeon.enter", {"id": did, "floor": 0})
unknown = ask("pet.dungeon.enter", {"id": "00000000"})
print(json.dumps({"disc": disc, "list": lst, "enter": enter, "floor": floor, "leave": leave, "end": end,
                  "bad": bad, "unknown": unknown}, default=str))
"""


def run_companion(script: str, *args: str) -> object:
    comp = companion_script().parent
    if not (comp / "pet" / "dungeons.py").exists():
        raise unittest.SkipTest("this companion checkout has no network dungeons (pet/dungeons.py)")
    why = companion_battle_data_missing()
    if why:
        raise unittest.SkipTest(why)
    out = subprocess.run([sys.executable, "-c", script, *args], cwd=str(comp), capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def sim_link(now: float = 1794492000.0) -> SimCompanionLink:
    link = SimCompanionLink(pet=PetState(wall_clock=lambda: now), clock=lambda: 0.0)
    link.connect()
    return link


def deck_ctx(config: dict | None = None):
    ctx = make_ctx(write_config(config) if config else None)
    ctx.interactive = True
    return ctx


def open_picker(ctx) -> NetDungeonActivity:
    REG.dispatch("dungeon", ctx)
    act = ctx.activity
    assert isinstance(act, NetDungeonActivity), type(act)
    return act


def path_to(g, goal):
    start = (g.px, g.py)
    prev = {start: None}
    q = deque([start])
    while q:
        c = q.popleft()
        if c == goal:
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dy)
            if n not in prev and 0 <= n[0] < g.width and 0 <= n[1] < g.height and g.grid[n[1]][n[0]] != "#":
                prev[n] = c
                q.append(n)
    if goal not in prev:
        return None
    out, c = [], goal
    while c != start:
        out.append(c)
        c = prev[c]
    return out[::-1]


KEY = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}


def walk(act, goal, fight=True) -> None:
    g = act.view.game
    for c in path_to(g, goal) or []:
        if act.phase != "play" or g.battle is not None:
            break
        act.on_key(KEY[(c[0] - g.px, c[1] - g.py)])
        while fight and g.battle is not None:
            act.on_key("enter")


class Spy:
    """Wraps a link's request() and remembers (type, payload)."""

    def __init__(self, link) -> None:
        self.sent: list[tuple[str, dict]] = []
        self._orig = link.request
        link.request = self

    def __call__(self, typ, payload=None, timeout=1.0):
        self.sent.append((typ, payload or {}))
        return self._orig(typ, payload, timeout)

    def of(self, typ: str) -> list[dict]:
        return [p for t, p in self.sent if t == typ]


# ---------------------------------------------------------------------------
class SharedRulesTests(unittest.TestCase):
    def test_port_matches_the_real_companion(self):
        theirs = run_companion(GEN_SCRIPT, json.dumps(SAMPLES), json.dumps(FLOORS))
        mine = []
        for kind, ident, name, rssi, sec, rare in SAMPLES:
            did, rec = ndg.new_record(kind, ident, name, rssi, 0, secured=sec, rare=rare)
            for home in (False, True):
                rec["home"], rec["depth"] = home, 12
                mine.append([did, rec["name"], rec["type"], rec["type2"], rec["difficulty"], ndg.seed_for(did),
                             ndg.theme(did, rec), ndg.level_range(rec), ndg.start_floors(12), ndg.snap_start(12, 9),
                             [[f, ndg.layout(did, rec, f), ndg.enemies_for(did, rec, f), ndg.boss_for(did, rec, f),
                               ndg.roll_loot(did, rec, f, ndg.is_boss_floor(f), 3), ndg.loot_mult(rec, f),
                               ndg.floor_xp(f, "cleared", ndg.is_boss_floor(f)), ndg.floor_xp(f, "stairs", False)]
                              for f in FLOORS]])
        self.assertEqual(json.loads(json.dumps(mine)), theirs)

    def test_roster_includes_the_companions_extras(self):
        data = companion_script().parent / "pet" / "data" / "roster.json"
        if not data.exists():
            self.skipTest("no companion roster.json")
        theirs = {e["id"]: e for e in json.loads(data.read_text())["enemies"]}
        self.assertEqual(set(theirs), {k.id for k in ALL_ROSTER})
        self.assertEqual({k.id for k in EXTRA_ROSTER}, {i for i, e in theirs.items() if e.get("extra")})
        for kind in ALL_ROSTER:
            e = theirs[kind.id]
            self.assertEqual((kind.glyph, kind.type, kind.weak, kind.face, kind.depth, kind.boss),
                             (e["glyph"], e["type"], e["weak"], e["face"], e["depth"], bool(e.get("boss"))), kind.id)
        self.assertFalse({k.id for k in EXTRA_ROSTER} & {k.id for k in ROSTER})   # the classic dungeon is unchanged

    def test_privacy_names_and_ids(self):
        did, rec = ndg.new_record("bt", "11:22:33:44:55:66", "11:22:33:44:55:66", -70, 0)
        self.assertEqual(rec["name"], f"hidden-{did[:4]}")
        self.assertTrue(rec["rare"] and rec["type"] == "VOID")
        self.assertEqual(len(did), 8)
        self.assertNotIn("11:22", json.dumps(rec))
        self.assertEqual(ndg.scrub_ids("PULSE logged aa:bb:cc:dd:ee:ff!"), "PULSE logged <hidden>!")
        rows = clean_rows({"dungeons": [{"id": "abcd1234", "name": "aa:bb:cc:dd:ee:ff"}, {"name": "no id"}]})
        self.assertEqual([r["name"] for r in rows], ["hidden-abcd"])

    def test_checkpoint_snapping(self):
        self.assertEqual(ndg.start_floors(0), [1])
        self.assertEqual(ndg.start_floors(4), [1])
        self.assertEqual(ndg.start_floors(5), [1, 6])
        self.assertEqual(ndg.start_floors(12), [1, 6, 11])
        self.assertEqual([ndg.snap_start(12, f) for f in (None, 1, 5, 6, 9, 11, 40, "x")], [11, 1, 1, 6, 6, 11, 11, 11])
        self.assertEqual(ndg.snap_start(3, 9), 1)
        self.assertEqual([f for f in range(1, 17) if ndg.is_boss_floor(f)], [5, 10, 15])
        self.assertEqual([f for f in range(1, 17) if ndg.is_checkpoint_floor(f)], [1, 6, 11, 16])


class MapTests(unittest.TestCase):
    def info(self, floor=7, **rec_extra):
        did, rec = ndg.new_record("wifi", "aa:bb:cc:00:11:22", "CafeTest", -60, 0, secured=True)
        rec.update(rec_extra)
        return ndg.seed_for(did), ndg.layout(did, rec, floor), ndg.enemies_for(did, rec, floor), \
            ndg.clean_boss(ndg.boss_for(did, rec, floor))

    def test_same_seed_and_floor_same_map(self):
        seed, lay, foes, boss = self.info()
        a = ndg.build_floor(seed, 7, lay, foes, boss)
        b = ndg.build_floor(seed, 7, lay, foes, boss)
        self.assertEqual(a.text(), b.text())
        self.assertNotEqual(a.text(), ndg.build_floor(seed, 8, lay, foes, boss).text())
        self.assertNotEqual(a.text(), ndg.build_floor(seed + 1, 7, lay, foes, boss).text())

    def test_layout_bounds_and_reachable_stairs(self):
        for floor in range(1, 31):
            seed, lay, foes, boss = self.info(floor)
            m = ndg.build_floor(seed, floor, lay, foes, boss)
            self.assertEqual((m.width, m.height), (lay["width"], lay["height"]))
            self.assertTrue(lay["rooms_min"] <= len(m.rooms) <= lay["rooms_max"] or len(m.rooms) >= 1)
            self.assertEqual(m.grid[m.stairs[1]][m.stairs[0]], ">")
            self.assertTrue(all(row[0] == "#" and row[-1] == "#" for row in m.grid))
            self.assertEqual(len(m.traps), lay["traps"])
            self.assertEqual(len(m.items), lay["items"])
            self.assertEqual(len(m.enemies), sum(e["count"] for e in foes))
            self.assertEqual(m.boss is not None, ndg.is_boss_floor(floor))

            class G:   # path_to() needs width/height/grid/px/py
                pass
            g = G()
            g.width, g.height, g.grid, (g.px, g.py) = m.width, m.height, m.grid, m.start
            self.assertIsNotNone(path_to(g, m.stairs), floor)
            for _e, spot in m.enemies:
                self.assertIsNotNone(path_to(g, spot))

    def test_bad_numbers_are_clamped_or_defaulted(self):
        for bad in (None, "x", {}, {"width": -5, "height": 9999, "rooms_min": 50, "rooms_max": 0, "room_w": "big",
                                    "room_h": [9, 1], "corridor": "spiral", "loops": True, "dark": "yes",
                                    "traps": 1e9, "items": -1},
                    {"width": 24, "height": 10, "room_w": [99, 99], "room_h": [99, 99], "rooms_max": 99}):
            lay = ndg.clean_layout(bad)
            self.assertTrue(24 <= lay["width"] <= 80 and 10 <= lay["height"] <= 24, lay)
            self.assertTrue(1 <= lay["rooms_min"] <= lay["rooms_max"] <= 12, lay)
            self.assertIn(lay["corridor"], ("winding", "straight"))
            self.assertTrue(0 <= lay["traps"] <= 8 and 0 <= lay["items"] <= 8)
            self.assertIsInstance(lay["dark"], bool)
            m = ndg.build_floor("junk", "also junk", bad, [{"id": "nope", "count": "x"}, 7, None], "boss?")
            self.assertGreaterEqual(len(m.rooms), 1)
        self.assertEqual(ndg.clean_layout(None), ndg.clean_layout(ndg.DEFAULT_LAYOUT))
        th = ndg.clean_theme({"wall_glyph": "@", "floor_glyph": "..", "type": "LAVA"})
        self.assertEqual((th["wall_glyph"], th["floor_glyph"], th["type"]), ("#", ".", "NET"))
        self.assertEqual(ndg.clean_theme({"wall_glyph": "%", "floor_glyph": ":"})["wall_glyph"], "%")


class PickerTests(unittest.TestCase):
    def test_mockup_rows_and_width(self):
        row = {"id": "582967f5", "name": "HomeNet", "type": "NET", "difficulty": 3, "depth_record": 2,
               "home": True, "in_range": True, "rare": False, "starts": [1], "checkpoint": 1}
        self.assertEqual(ndg.box_line(ndg.picker_row(row, True)), "| > HomeNet         NET  D3  B2   HOME         |")
        rare = dict(row, name="hidden-6294", type="VOID", difficulty=8, depth_record=0, home=False, rare=True)
        self.assertEqual(ndg.box_line(ndg.picker_row(rare, False)), "|  *hidden-6294     VOID D8  B0   NEAR         |")
        lines = picker_lines([row, rare])
        self.assertEqual(lines[1], "| DUNGEONS                     2 found  2 near |")
        self.assertIn("| near = in range (x1.5 XP)  * = rare          |", lines)
        self.assertIn("| [ent] enter  [<-/->] start floor  [esc]      |", lines)
        self.assertTrue(all(len(x) == SCREEN_W for x in lines))

    def test_order_home_then_in_range_then_depth(self):
        link = sim_link()
        dg = link.pet.dungeons
        dg.seed_fakes(1794492000.0)
        cafe = next(d for d, r in dg.dungeons.items() if r["name"] == "CafeNet-5G")
        dg.dungeons[cafe]["depth"] = 7
        rows = link.request("pet.dungeons", {}).payload["dungeons"]
        self.assertEqual(rows[0]["name"], "HomeNet")
        self.assertTrue(rows[0]["home"])
        self.assertEqual(rows[1]["name"], "CafeNet-5G")          # in range, deepest record
        self.assertEqual(rows[-1]["name"], "OldOffice")          # out of range last
        self.assertFalse(rows[-1]["in_range"])
        self.assertTrue(any(r["rare"] and r["type"] == "VOID" for r in rows))

    def test_picker_keys_and_start_floor(self):
        ctx = deck_ctx()
        dg = ctx.companion.pet.dungeons
        dg.seed_fakes(ctx.companion.pet.wall_clock())
        home = next(d for d, r in dg.dungeons.items() if r["home"])
        dg.dungeons[home]["depth"] = 12                           # starts 1, 6, 11
        act = open_picker(ctx)
        text = rows_to_text(act.render(SCREEN_W, 30))
        self.assertTrue(all(len(t) <= SCREEN_W for t in text))
        self.assertTrue(text[3].startswith("| > HomeNet"))
        self.assertTrue(text[3].rstrip(" |").endswith("HOME  F11"), text[3])                       # default: the last checkpoint
        act.on_key("left")
        self.assertIn(" F6", rows_to_text(act.render(SCREEN_W, 30))[3])
        act.on_key("left")
        act.on_key("left")
        self.assertTrue(rows_to_text(act.render(SCREEN_W, 30))[3].rstrip(" |").endswith("HOME"))   # floor 1
        act.on_key("right")
        act.on_key("enter")
        self.assertEqual(act.run.floor, 6)
        self.assertEqual(act.phase, "banner")
        act.on_key("down")
        self.assertEqual(act.phase, "play")

    def test_scrolls_when_the_list_is_long(self):
        link = deck_ctx()
        for i in range(30):
            link.companion.request("pet.discovery", {"kind": "wifi", "id": f"x{i}", "label": f"Net{i:02d}"})
        act = open_picker(link)
        for _ in range(25):
            act.on_key("down")
        text = rows_to_text(act.render(SCREEN_W, 20))
        self.assertLessEqual(len(text), 20)
        self.assertTrue(any(t.startswith("| >") for t in text))


class FloorTests(unittest.TestCase):
    def enter(self, ctx, name="CafeNet-5G", floor=None) -> NetDungeonActivity:
        act = open_picker(ctx)
        index = next(i for i, r in enumerate(act.rows) if r["name"] == name)
        self.assertTrue(act.enter(index, floor), act.note)
        act.on_key("x")
        return act

    def test_banners_match_the_mockups(self):
        info = {"name": "CafeNet-5G", "floor": 7, "layout": {"width": 57, "height": 14, "rooms_min": 4,
                "rooms_max": 6, "corridor": "winding", "traps": 3, "items": 3, "dark": False},
                "theme": {"type": "NET", "type2": "FIRE"}, "xp_mult": 1.5, "loot_mult": 1.48,
                "enemies": [{"id": "popup", "name": "popup", "level": 9, "count": 1},
                            {"id": "lag", "name": "lag", "level": 9, "count": 1}],
                "say": "Floor 7. Look out for loot!"}
        self.assertEqual(ndg.floor_banner(info, "A tunnel net spun by worms."), [
            "+----------------------------------------------+",
            "| CafeNet-5G                               B7F |",
            "| NET/FIRE  Lv 9-9  x1.5 XP  loot x1.48        |",
            "+----------------------------------------------+",
            "| A tunnel net spun by worms.                  |",
            "|                                              |",
            "| Foes: popup x1, lag x1                       |",
            "| Map 57x14  rooms 4-6  winding                |",
            "| Traps 3  items 3  lit                        |",
            "|                                              |",
            '| "Floor 7. Look out for loot!"                |',
            "+----------------------------------------------+"])
        boss = dict(info, floor=10, say="Big room. Bigger boss.",
                    boss={"name": "kernel panic", "id": "kernel_panic", "level": 12, "title": "Beacon Lord"})
        self.assertEqual(ndg.floor_banner(boss), [
            "+----------------------------------------------+",
            "| CafeNet-5G                         B10F BOSS |",
            "| BEACON LORD  (kernel panic Lv 12)            |",
            "+----------------------------------------------+",
            '| "Big room. Bigger boss."                     |',
            "+----------------------------------------------+"])

    def test_floor_uses_the_seed_theme_and_enemies(self):
        ctx = deck_ctx()
        act = self.enter(ctx)
        g, run = act.view.game, act.run
        m = ndg.build_floor(run.info["seed"], 1, run.info["layout"], run.info["enemies"])
        self.assertEqual(g.grid, m.grid)
        self.assertEqual((g.px, g.py), m.start)
        self.assertEqual(sorted(x.name for x in g.monsters),
                         sorted(e["name"] for e in run.info["enemies"] for _ in range(e["count"])))
        self.assertTrue(all(x.level == run.info["enemies"][0]["level"] for x in g.monsters))
        wall = run.info["theme"]["wall_glyph"]
        text = "\n".join(rows_to_text(act.render(100, 40)))
        self.assertIn("B1F CafeNet-5G", text)
        self.assertIn("x1.5 XP", text)
        if wall not in ndg.RESERVED_GLYPHS:
            self.assertIn(wall, text)
        self.assertTrue(any(role.startswith("rgb:") for row in act.render(100, 40) for _t, role in row))

    def test_stairs_win_a_fight_and_no_double_xp(self):
        ctx = deck_ctx()
        spy = Spy(ctx.companion)
        act = self.enter(ctx, "HomeNet")
        g = act.view.game
        xp_before = ctx.companion.pet.xp
        while g.monsters and act.phase == "play":
            target = g.monsters[0]
            walk(act, (target.x, target.y))
        self.assertGreaterEqual(g.battles_won, 1)
        ends = spy.of("pet.combat.end")
        self.assertTrue(ends)
        self.assertTrue(all(e["dungeon_id"] == act.run.id and e["floor"] == 1 for e in ends))
        battle_xp = g.battle_xp
        walk(act, g.stairs)
        act.on_key(">")
        self.assertEqual(act.phase, "result")
        report = spy.of("pet.dungeon.floor")[-1]
        self.assertEqual(report["result"], "cleared")
        self.assertEqual(report["xp"], act.run.chest_xp)          # only chests; battles went via combat.end
        reply_xp = int(round((ndg.floor_xp(1, "cleared", False) + report["xp"]) * 1.0))   # home: x1.5 below
        floor_xp = act.run.floor_xp
        self.assertEqual(floor_xp, int(round((ndg.floor_xp(1, "cleared", False) + report["xp"]) * 1.5)))
        self.assertEqual(ctx.companion.pet.xp - xp_before, battle_xp + floor_xp)
        del reply_xp
        act.on_key("x")                                           # next floor's banner
        self.assertEqual((act.phase, act.run.floor, g.depth), ("banner", 2, 2))
        act.on_key("x")
        self.assertEqual(act.phase, "play")

    def test_flee_and_faint_end_the_run_and_restart_at_the_checkpoint(self):
        ctx = deck_ctx()
        spy = Spy(ctx.companion)
        act = self.enter(ctx)
        act.on_key("q")                                           # leave = fled
        self.assertEqual(act.phase, "end")
        self.assertEqual(spy.of("pet.dungeon.floor")[-1]["result"], "fled")
        self.assertTrue(spy.of("pet.dungeon.leave"))
        act.on_key("x")
        self.assertEqual(act.phase, "picker")
        # Fainting: PULSE loses a battle.
        dg = ctx.companion.pet.dungeons
        cafe = next(d for d, r in dg.dungeons.items() if r["name"] == "CafeNet-5G")
        dg.dungeons[cafe]["depth"] = 7                            # checkpoint 6 unlocked
        act.refresh()
        index = next(i for i, r in enumerate(act.rows) if r["id"] == cafe)
        act.enter(index, 6)
        act.on_key("x")
        g = act.view.game
        ctx.companion.pet.hp = 1
        target = g.monsters[0]
        walk(act, (target.x, target.y), fight=False)
        self.assertIsNotNone(g.battle)
        for _ in range(200):
            if act.phase != "play":
                break
            act.on_key("enter")
        self.assertEqual(act.phase, "end")
        last = spy.of("pet.dungeon.floor")[-1]
        self.assertEqual((last["result"], last["floor"]), ("fainted", 6))
        rows = ctx.companion.request("pet.dungeons", {}).payload["dungeons"]
        self.assertEqual(next(r for r in rows if r["id"] == cafe)["checkpoint"], 6)
        self.assertEqual(ctx.companion.request("pet.dungeon.enter", {"id": cafe}).payload["floor"], 6)

    def test_boss_floor_has_the_themed_boss(self):
        ctx = deck_ctx()
        dg = ctx.companion.pet.dungeons
        dg.seed_fakes(ctx.companion.pet.wall_clock())
        cafe = next(d for d, r in dg.dungeons.items() if r["name"] == "CafeNet-5G")
        dg.dungeons[cafe]["depth"] = 9
        act = open_picker(ctx)
        act.enter(next(i for i, r in enumerate(act.rows) if r["id"] == cafe), 6)
        g = act.view.game
        for floor in (6, 7, 8, 9):
            act.on_key("x")
            g.monsters.clear()
            g.px, g.py = g.stairs
            act.on_key(">")
            self.assertEqual(act.phase, "result", act.lines)
            act.on_key("x")
        self.assertEqual(act.run.floor, 10)
        text = "\n".join(act.lines)
        self.assertIn("B10F BOSS", text)
        boss = [m for m in g.monsters if m.kind.boss]
        self.assertEqual(len(boss), 1)
        self.assertEqual(boss[0].kind.id, "kernel_panic")
        self.assertEqual(boss[0].title, act.run.info["boss"]["title"])
        self.assertIn(boss[0].title.upper(), text)
        self.assertEqual(boss[0].battle_enemy().name, boss[0].title)
        self.assertEqual(boss[0].battle_enemy().describe()["name"], "kernel panic")   # the companion's id/name

    def test_every_screen_fits_48_columns(self):
        ctx = deck_ctx()
        act = open_picker(ctx)
        screens = [act.render(SCREEN_W, 40)]
        act.enter()
        screens.append(act.render(SCREEN_W, 40))
        act.on_key("x")
        g = act.view.game
        g.monsters.clear()
        g.px, g.py = g.stairs
        act.on_key(">")
        screens.append(act.render(SCREEN_W, 40))
        act.on_key("x")
        screens.append(act.render(SCREEN_W, 40))
        act.on_key("x")
        act.on_key("q")
        screens.append(act.render(SCREEN_W, 40))
        for rows in screens:
            for line in rows_to_text(rows):
                self.assertLessEqual(len(line), SCREEN_W, line)
                self.assertTrue(line.isascii(), line)

    def test_traps_and_pickups(self):
        ctx = deck_ctx()
        act = self.enter(ctx)
        g, run = act.view.game, act.run
        g.monsters.clear()
        spot = next(iter(g.traps), None)
        if spot is not None:
            hp = g.hp
            g.px, g.py = spot[0] - 1, spot[1]
            g.grid[spot[1]][spot[0] - 1] = "."
            g.move(1, 0)
            self.assertLess(g.hp, hp)
            self.assertIn(spot, g.sprung)
        for spot, item in list(g.items.items()):
            if item["kind"] in {"chest", "bagitem"}:
                g.px, g.py = spot
                g._pick_up()
        self.assertEqual(len(run.loot) + (1 if run.chest_xp else 0) > 0, True)
        g.px, g.py = g.stairs
        spy = Spy(ctx.companion)
        act.on_key(">")
        report = spy.of("pet.dungeon.floor")[-1]
        self.assertEqual(report["loot"], run.loot[:10] if act.phase != "result" else report["loot"])
        self.assertTrue(all(i in ctx.companion.pet.bag for i in report["loot"]))


class FallbackTests(unittest.TestCase):
    def test_no_companion_or_older_companion_is_the_classic_dungeon(self):
        ctx = deck_ctx()
        ctx.companion.close()
        REG.dispatch("dungeon", ctx)
        self.assertIsInstance(ctx.activity, DungeonActivity)
        ctx = deck_ctx()
        orig = ctx.companion.request

        def older(typ, payload=None, timeout=1.0):
            if typ.startswith("pet.dungeon"):
                return Message.request(typ, payload or {}).error("unknown_type", "unsupported")
            return orig(typ, payload, timeout)
        ctx.companion.request = older
        REG.dispatch("dungeon", ctx)
        self.assertIsInstance(ctx.activity, DungeonActivity)
        self.assertIsNone(ctx.activity.game.net)
        self.assertIn("no network dungeons", REG.dispatch("dungeon list", ctx)[0])

    def test_turned_off_or_asked_for_classic(self):
        ctx = deck_ctx({"dungeons.ini": "[dungeons]\nenabled = no\n"})
        REG.dispatch("dungeon", ctx)
        self.assertIsInstance(ctx.activity, DungeonActivity)
        ctx = deck_ctx()
        REG.dispatch("dungeon classic", ctx)
        self.assertIsInstance(ctx.activity, DungeonActivity)
        act = open_picker(deck_ctx())
        act.on_key("c")
        self.assertEqual(act.phase, "classic")
        self.assertIsNone(act.view.game.net)
        act.on_key("q")
        self.assertTrue(act.done)

    def test_headless_list_and_enter(self):
        ctx = make_ctx()
        lines = REG.dispatch("dungeon list", ctx)
        self.assertEqual(lines[1], "| DUNGEONS                     6 found  5 near |")
        out = REG.dispatch("dungeon enter cafe", ctx)
        self.assertIn("| CafeNet-5G                               B1F |", out)
        self.assertTrue(any("@" in ln for ln in out))           # the map preview
        self.assertIn("no dungeon", REG.dispatch("dungeon enter nowhere", ctx)[0])
        self.assertIn("DUNGEONS", REG.dispatch("dungeons", ctx)[1])


class SimParityTests(unittest.TestCase):
    def test_sim_answers_like_the_real_companion(self):
        real = run_companion(REAL_FLOW)
        link = sim_link()
        ask = lambda t, p: link.request(t, p).payload   # noqa: E731
        disc = ask("pet.discovery", {"kind": "wifi", "id": "aa:bb:cc:00:11:22", "label": "CafeTest", "rssi": -60,
                                     "security": "wpa2"})
        did = disc["dungeon"]["id"]
        mine = {"disc": disc, "list": ask("pet.dungeons", {}), "enter": ask("pet.dungeon.enter", {"id": did}),
                "floor": ask("pet.dungeon.floor", {"id": did, "floor": 1, "result": "cleared", "xp": 4}),
                "leave": ask("pet.dungeon.leave", {"id": did}),
                "end": ask("pet.combat.end", {"result": "win", "xp": 10, "enemies": [{"name": "bug", "level": 3}],
                                              "dungeon_id": did, "floor": 2}),
                "bad": ask("pet.dungeon.enter", {"id": did, "floor": 0}),
                "unknown": ask("pet.dungeon.enter", {"id": "00000000"})}
        self.assertEqual(real["disc"]["dungeon"], mine["disc"]["dungeon"])
        for key in ("list", "enter", "floor", "leave"):
            self.assertEqual(set(real[key]), set(mine[key]), key)
        self.assertEqual(set(real["list"]["dungeons"][0]), set(mine["list"]["dungeons"][0]))
        theirs = next(r for r in real["list"]["dungeons"] if r["id"] == did)
        ours = next(r for r in mine["list"]["dungeons"] if r["id"] == did)
        for key in ("name", "kind", "type", "type2", "rare", "difficulty", "levels", "checkpoint", "starts"):
            self.assertEqual(theirs[key], ours[key], key)
        for key in ("seed", "floor", "layout", "theme", "enemies", "boss", "checkpoint", "loot_mult", "starts",
                    "difficulty", "kind", "depth_record", "checkpoint_floor"):
            self.assertEqual(real["enter"][key], mine["enter"][key], key)
        self.assertEqual(set(real["floor"]["next"]), set(mine["floor"]["next"]))
        for key in ("floor", "layout", "theme", "enemies", "boss"):
            self.assertEqual(real["floor"]["next"][key], mine["floor"]["next"][key], key)
        for key in ("xp", "result", "depth_record", "new_record", "checkpoint", "boss_beaten"):
            self.assertEqual(real["floor"][key], mine["floor"][key], key)
        self.assertEqual(sorted(x["id"] for x in real["floor"]["loot"]), sorted(x["id"] for x in mine["floor"]["loot"]))
        self.assertEqual(real["end"]["xp_mult"], mine["end"]["xp_mult"])
        self.assertEqual((real["bad"]["code"], real["unknown"]["code"]), (mine["bad"]["code"], mine["unknown"]["code"]))

    def test_found_events_are_rate_limited_and_batched(self):
        clock = [1794492000.0]
        link = SimCompanionLink(pet=PetState(wall_clock=lambda: clock[0]), clock=lambda: 0.0)
        link.connect()
        for i in range(4):
            link.request("pet.discovery", {"kind": "bt", "id": f"dev{i}", "label": f"Speaker {i}"})
        first = [m.payload for m in link.drain_events() if m.payload["event"].startswith("dungeon")]
        self.assertEqual([e["event"] for e in first], ["dungeon_found"])
        self.assertEqual(first[0]["detail"]["name"], "Speaker 0")
        clock[0] += 31
        later = [m.payload for m in link.drain_events() if m.payload["event"].startswith("dungeon")]
        self.assertEqual([e["event"] for e in later], ["dungeons_found"])
        self.assertEqual(later[0]["detail"]["count"], 3)
        rfid = link.request("pet.discovery", {"kind": "rfid", "id": "04:a2", "label": "tag"}).payload
        self.assertIsNone(rfid["dungeon"])

    def test_stats_sheet_has_the_dungeon_rows(self):
        link = sim_link()
        sheet = link.request("pet.stats", {}).payload
        self.assertEqual(set(sheet["dungeons"]), {"count", "in_range", "deepest", "deepest_name", "floors",
                                                  "boss_kills", "home"})
        found = sheet["pages"][-1]["lines"]
        self.assertTrue(found[-5].startswith("Dungeons"))
        self.assertTrue(found[-1].startswith("Home turf") and "HomeNet" in found[-1])


class AlertTests(unittest.TestCase):
    def test_console_text_and_pet_log(self):
        from cyberdeck.petdata import event_notice

        kind, level, text = event_notice({"event": "dungeon_found", "detail": {"id": "abcd1234", "name": "CafeNet"}})
        self.assertEqual((kind, level), ("dungeon_found", "ok"))
        self.assertTrue(text.startswith("NEW DUNGEON: CafeNet"))
        text = event_notice({"event": "dungeon_found", "detail": {"id": "abcd1234", "name": "aa:bb:cc:dd:ee:ff"}})[2]
        self.assertIn("hidden-abcd", text)
        self.assertNotIn("aa:bb", text)
        many = event_notice({"event": "dungeons_found", "detail": {"count": 4, "names": ["A", "B"]}})[2]
        self.assertTrue(many.startswith("NEW DUNGEONS: 4 found (A, B)"))

    def test_pushed_event_reaches_console_log_and_lcd(self):
        from cyberdeck.features.petwatch import get_watcher
        from cyberdeck.lcd import get_lcd

        ctx = make_ctx(write_config({"lcd.ini": "[lcd]\nbackend = sim\n"}))
        ctx.interactive = True
        lcd = get_lcd(ctx, threaded=False)
        notes = []
        ctx.notify = lambda text, level="info": notes.append(text)
        ctx.companion.request("pet.discovery", {"kind": "wifi", "id": "zz1", "label": "GardenNet"})
        watcher = get_watcher(ctx)
        events = watcher.pushed(ctx, 0.0)
        watcher.handle(ctx, events, 0.0)
        self.assertTrue(any(n.startswith("NEW DUNGEON: GardenNet") for n in notes), notes)
        self.assertTrue(any("dungeon_found" in ln and "NEW DUNGEON: GardenNet" in ln for ln in watcher.log.read()))
        items = [(m.title, m.text) for m in lcd.queue.items] + ([(lcd.current.title, lcd.current.text)]
                                                                 if lcd.current else [])
        self.assertIn(("{diamond}NEW DUNGEON", "GardenNet"), items)

    def test_lcd_limits_dungeon_popups(self):
        from cyberdeck.lcd.service import LcdService

        lcd = LcdService.__new__(LcdService)
        sent = []
        lcd.s = type("S", (), {"pet_events": ["dungeon_found", "dungeons_found"]})()
        lcd.add_blurb = lambda text, now=None: None
        lcd.pet = {}
        lcd.notify = lambda body, title, priority, kind, key, now=None: sent.append((title, body, key)) or "ok"
        self.assertEqual(lcd.pet_event("dungeon_found", "x", {"detail": {"id": "a1", "name": "One"}}, now=100.0), "ok")
        self.assertEqual(lcd.pet_event("dungeon_found", "x", {"detail": {"id": "a2", "name": "Two"}}, now=110.0), "rate")
        self.assertEqual(lcd.pet_event("dungeon_found", "x", {"detail": {"id": "a2", "name": "Two"}}, now=131.0), "ok")
        self.assertEqual(sent[0], ("{diamond}NEW DUNGEON", "One", "pet:dungeon_found:a1"))

    def test_discovery_payload_extras_and_privacy(self):
        from cyberdeck_dash.events import Finding
        from cyberdeck_dash.pet import discovery_payload

        p = discovery_payload(Finding("wifi", "new", "aa:bb:cc:dd:ee:ff", "<hidden>", 1,
                                      {"rssi": -71, "security": "WPA2", "channel": 6}, first=True))
        self.assertEqual((p["rssi"], p["security"], p["label"]), (-71, "wpa2", ""))
        p = discovery_payload(Finding("bt", "new", "11:22:33:44:55:66", "Speaker", 1, {"rssi": -50, "rare": True}))
        self.assertTrue(p["rare"])
        self.assertNotIn("security", p)


class AvatarTests(unittest.TestCase):
    def test_parse_avatar_accepts_text_faces(self):
        from cyberdeck.petdata import Face, parse_avatar

        face = parse_avatar({"style": "face", "mood": "happy", "frame": 0, "frames": ["(^_^)", "(^o^)"],
                             "interval_ms": 450, "costume": None})
        self.assertIsInstance(face, Face)
        self.assertTrue(face)
        self.assertIsNone(parse_avatar({"style": "face", "frames": []}))

    def test_the_companions_own_face_payload_parses(self):
        script = companion_script().parent
        if not (script / "link" / "handler.py").exists():
            self.skipTest("no companion checkout")
        code = ("import json,sys; sys.path.insert(0,'.'); from link.handler import encode_avatar; "
                "from pet.creature import Creature; print(json.dumps(encode_avatar(Creature(), frame=0, mood='playful')))")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(script), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        from cyberdeck.petdata import parse_avatar

        self.assertTrue(parse_avatar(json.loads(out.stdout)))


if __name__ == "__main__":
    unittest.main()
