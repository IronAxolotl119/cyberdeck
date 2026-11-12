"""The ONE interface: the Rich dashboard with the main deck attached.

These drive the combined interface the way a user does, with key names
and a fake clock, and no terminal: the console bridge (run a command,
Tab completion, history), the Notes & Todo, Dungeon and Fun & Tools tabs,
keys.ini hotkeys, the single shared companion link, and the entry points
(`python3 -m cyberdeck`, --classic, --headless/--script, and the
`python3 -m cyberdeck_dash` alias).
Skipped if Rich is not installed (pip install -r requirements-dash.txt).
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from rich.console import Console
    HAVE_RICH = True
except ImportError:  # pragma: no cover
    HAVE_RICH = False

REPO = Path(__file__).resolve().parents[2]
T0 = 1_800_000_000


def make_unified(seed: int = 7):
    """A Dashboard + DeckBridge on the in-process sim pet, temp data folder."""
    from cyberdeck.commands import Context, build_registry
    from cyberdeck.companion import SimCompanionLink
    from cyberdeck.sensors import SimSensors
    from cyberdeck_dash.unified import build_dashboard

    link = SimCompanionLink()
    link.connect()
    ctx = Context(sensors=SimSensors(seed=1), companion=link, mode="sim",
                  data_dir=Path(tempfile.mkdtemp(prefix="cyberdeck-unified-")))
    args = SimpleNamespace(dash_config=None, dash_mode="sim", seed=seed, theme=None, tab=None)
    dash = build_dashboard(args, ctx, build_registry(include_plugins=True), threaded=False, journal_path=False,
                           start_services=False)
    dash.step(T0)
    return dash


def render(dash, width=100, height=30) -> str:
    from cyberdeck_dash.ui.screen import build_screen

    dash.screen_width = width
    console = Console(record=True, width=width, height=height, force_terminal=True, file=io.StringIO())
    console.print(build_screen(dash, dash.now), height=height)
    return console.export_text()


def press(dash, *keys, t=None):
    """Feed keys one per step (words are typed letter by letter)."""
    t = dash.now + 0.5 if t is None else t
    for k in keys:
        names = [k] if (len(k) == 1 or k in {"enter", "esc", "tab", "up", "down", "left", "right", "backspace",
                                             "space", "pgup", "pgdn", "delete", "f2", "ctrl+o", "ctrl+u"}) else list(k)
        for name in names:
            dash.step(t, [" " if name == "space" else name])
            t += 0.5


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class Tabs(unittest.TestCase):
    def test_deck_panel_keys_match_registry(self):
        from cyberdeck_dash.app import DECK_PANEL_KEYS
        from cyberdeck_dash.ui.deck_panels import DECK_PANELS

        self.assertEqual(set(DECK_PANELS), set(DECK_PANEL_KEYS))

    def test_all_tabs_present_and_render(self):
        dash = make_unified()
        titles = [t for t, _, _ in dash.tabs]
        self.assertEqual(titles, ["Awareness & Diagnostics", "Deck & PULSE", "Console", "Dungeon",
                                  "Notes & Todo", "Fun & Tools"])
        bar = render(dash).splitlines()[3]
        for short in ("Console", "Dungeon", "Notes", "Fun"):
            self.assertIn(short, bar)
        for _, keys, _ in dash.tabs:
            for k in keys:
                dash.go_to_panel(k)
                text = render(dash)
                self.assertNotIn("panel error", text, k)
                self.assertNotIn("error:", text, k)

    def test_without_deck_the_deck_tabs_are_hidden(self):
        from cyberdeck_dash.app import Dashboard
        from cyberdeck_dash.config import load_config

        dash = Dashboard(load_config(), mode="sim", seed=1, threaded=False, companion=False, journal_path=False)
        self.assertEqual([t for t, _, _ in dash.tabs], ["Awareness & Diagnostics", "Deck & PULSE"])

    def test_tab_switching_by_keys_and_ui_action(self):
        dash = make_unified()
        press(dash, "]", "]")
        self.assertEqual(dash.tab_title, "Console")
        dash.deck_ui_action(":tab dungeon")
        self.assertEqual(dash.tab_title, "Dungeon")
        dash.deck_ui_action(":tab sky")                # a panel name works too
        self.assertEqual(dash.tab_title, "Fun & Tools")
        dash.deck_ui_action(":tab notes")
        self.assertEqual(dash.tab_title, "Notes & Todo")
        press(dash, ":")                                # : jumps to the console
        self.assertTrue(dash.console_typing())


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class ConsoleBridge(unittest.TestCase):
    def setUp(self):
        self.dash = make_unified()
        self.dash.go_to_panel("console", zoom=False)
        self.b = self.dash.bridge

    def test_run_a_command_and_see_it(self):
        press(self.dash, "pet status", "enter")
        self.assertIn("> pet status", self.b.lines)
        self.assertTrue(any("PULSE" in ln for ln in self.b.lines))
        text = render(self.dash)
        self.assertIn("CONSOLE", text)
        self.assertIn("> pet status", text)

    def test_letters_type_instead_of_dashboard_keys(self):
        press(self.dash, "qsuf")
        self.assertTrue(self.dash.running)              # q did not quit
        self.assertFalse(self.dash.filter.field_mode)   # f did not toggle field mode
        self.assertEqual(self.b.input, "qsuf")
        press(self.dash, "esc")
        self.assertEqual(self.b.input, "")

    def test_tab_completion_and_history(self):
        press(self.dash, "typeh", "tab")
        self.assertEqual(self.b.input.strip(), "typehack")
        press(self.dash, "ctrl+u", "pet st", "tab")
        self.assertEqual(self.b.input.strip(), "pet status")
        press(self.dash, "ctrl+u", "help", "enter", "status", "enter", "up")
        self.assertEqual(self.b.input, "status")
        press(self.dash, "up")
        self.assertEqual(self.b.input, "help")

    def test_brackets_switch_tabs_only_on_an_empty_line(self):
        press(self.dash, "]")
        self.assertEqual(self.dash.tab_title, "Dungeon")
        self.dash.go_to_panel("console", zoom=False)
        press(self.dash, "x", "]")
        self.assertEqual(self.dash.tab_title, "Console")
        self.assertEqual(self.b.input, "x]")

    def test_quit_command_and_clear(self):
        press(self.dash, "clear", "enter")
        self.assertEqual(len(self.b.lines), 0)
        press(self.dash, "quit", "enter")
        self.assertFalse(self.dash.running)

    def test_background_job_result_lands_in_the_log(self):
        from cyberdeck.jobs import in_background

        lines = in_background(self.b.ctx, "unit", "unit job", lambda: ["job says hi"])
        self.b.append(lines)
        for _ in range(40):
            self.dash.step(self.dash.now + 0.05)
            if any("job says hi" in ln for ln in self.b.lines):
                break
        self.assertTrue(any("job says hi" in ln for ln in self.b.lines))

    def test_external_program_is_handed_to_the_runner(self):
        self.b.ctx.external = ["true"]
        self.b.after_command()
        self.assertEqual(self.b.external, ["true"])
        argv, self.b.external = self.b.external, None
        self.b.run_external(argv, runner=lambda a: 0)
        self.assertIn("[true exited: 0]", self.b.lines)


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class NotesTodo(unittest.TestCase):
    def test_add_and_delete_a_note(self):
        from cyberdeck.features.notes import notes

        dash = make_unified()
        dash.go_to_panel("notes", zoom=False)
        press(dash, "a", "buy solder", "enter")
        self.assertTrue(notes(dash.bridge.ctx).read()[-1].endswith("| buy solder"))
        self.assertIn("buy solder", render(dash))
        press(dash, "x")
        self.assertEqual(notes(dash.bridge.ctx).read(), [])

    def test_todo_add_toggle_clear(self):
        from cyberdeck.features.notes import todos

        dash = make_unified()
        dash.go_to_panel("todo", zoom=False)
        press(dash, "a", "print the case", "enter", "n", "flash the pi", "enter")
        self.assertEqual([t for _, t in todos(dash.bridge.ctx).items()], ["print the case", "flash the pi"])
        press(dash, "down", "space")
        self.assertEqual(todos(dash.bridge.ctx).items()[1], (True, "flash the pi"))
        press(dash, "c")
        self.assertEqual(todos(dash.bridge.ctx).items(), [(False, "print the case")])
        self.assertIn("1 open · 0 done", render(dash))


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class DungeonTab(unittest.TestCase):
    def test_start_a_run_with_pulse_and_leave(self):
        from cyberdeck.features.dungeon import DungeonActivity

        dash = make_unified()
        dash.go_to_panel("dungeon", zoom=False)
        press(dash, "enter")
        act = dash.bridge.activity
        self.assertIsInstance(act, DungeonActivity)
        self.assertTrue(act.game.ally.online)            # PULSE joined over the (sim) link
        text = render(dash)
        self.assertIn("DUNGEON", text)
        self.assertIn("PULSE HP", text)
        press(dash, "right", "left", "q")                 # every key goes to the game; q leaves
        self.assertIsNone(dash.bridge.activity)
        self.assertTrue(dash.running)
        self.assertTrue(any("depth" in ln for ln in dash.dungeon_last))

    def test_combat_sheet_and_loot_gift(self):
        dash = make_unified()
        dash.bridge.run_line("loot add power cell", remember=False)
        dash.go_to_panel("loot", zoom=False)
        dash.step(dash.now + 1)
        self.assertTrue(dash.pet.combat.get("moves"))      # pet.combat.stats over the shared link
        self.assertIn("PULSE COMBAT", render(dash))
        press(dash, "g")
        self.assertTrue(any("power cell" in ln for ln in dash.bridge.lines))
        from cyberdeck.features.dungeon import loot_bag

        self.assertEqual(loot_bag(dash.bridge.ctx).items(), [])


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class FunAndHotkeys(unittest.TestCase):
    def test_launcher_runs_an_entry(self):
        dash = make_unified()
        dash.go_to_panel("launcher", zoom=False)
        first = dash.launcher_items()[0]
        press(dash, "enter")
        if first.action.startswith(":") or first.action.endswith("..."):
            self.skipTest("first launcher entry is not a plain command")
        self.assertIn(f"> {first.action}", dash.bridge.last_output[0])
        self.assertEqual(dash.tab_title, "Fun & Tools")   # stays put; the OUTPUT tile shows it

    def test_f2_opens_the_launcher_overlay(self):
        from cyberdeck.activity import MenuActivity

        dash = make_unified()
        press(dash, "f2")
        self.assertIsInstance(dash.bridge.activity, MenuActivity)
        self.assertIn("LAUNCHER", render(dash))
        press(dash, "esc")
        self.assertIsNone(dash.bridge.activity)

    def test_typehack_runs_inside_the_interface(self):
        dash = make_unified()
        dash.go_to_panel("console", zoom=False)
        press(dash, "typehack", "enter")
        self.assertIsNotNone(dash.bridge.activity)
        self.assertNotIn("error", render(dash).lower().split("typehack")[0][-50:])
        press(dash, "esc")
        self.assertIsNone(dash.bridge.activity)


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class OneLink(unittest.TestCase):
    def test_discoveries_go_to_the_deck_link(self):
        dash = make_unified()
        for i in range(12):
            dash.step(T0 + 1 + i)
        inner = dash.bridge.link.inner
        self.assertIs(dash.link.link, dash.bridge.ctx.companion)   # one link, shared
        self.assertTrue(inner.pet.seen)                               # the sim PULSE logged finds
        self.assertTrue(dash.pet.discovery_supported)
        self.assertIn("LINK ◆ SIM", render(dash))

    def test_pushed_events_reach_deck_and_dashboard(self):
        from cyberdeck.protocol import Message

        dash = make_unified()
        tee = dash.bridge.link
        tee._for_deck.clear()
        tee._for_dash.clear()
        tee._link.drain_events = lambda: [Message("pet.event", "e1", {"event": "hungry", "detail": {}})]
        tee.pump()
        tee._link.drain_events = lambda: []
        self.assertEqual([m.payload["event"] for m in tee.drain_events()], ["hungry"])
        self.assertEqual([m.payload["event"] for m in tee.drain_dash_events()], ["hungry"])


class EntryPoints(unittest.TestCase):
    def run_deck(self, *args, module="cyberdeck"):
        env = dict(os.environ, CYBERDECK_LINK="sim")
        return subprocess.run([sys.executable, "-m", module, *args], cwd=REPO, env=env, capture_output=True,
                              text=True, timeout=120)

    def test_script_mode_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_deck("--data-dir", tmp, "--script", "todo add from a script", "--script", "todo list")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("from a script", res.stdout)

    def test_classic_and_rich_flags_parse(self):
        from cyberdeck.app import build_parser

        args = build_parser().parse_args(["--classic", "--theme", "amber", "--tab", "dungeon", "--link", "auto"])
        self.assertTrue(args.classic)
        self.assertEqual((args.theme, args.tab, args.link), ("amber", "dungeon", "auto"))

    @unittest.skipUnless(HAVE_RICH, "rich not installed")
    def test_snapshot_through_the_old_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_deck("--snapshot", f"{tmp}/s", "--frames", "5", "--tab", "dungeon", "--keys", "enter",
                                module="cyberdeck_dash")
            self.assertEqual(res.returncode, 0, res.stderr)
            text = Path(f"{tmp}/s.txt").read_text(encoding="utf-8")
            self.assertTrue(Path(f"{tmp}/s.svg").exists())
        self.assertIn("CK//MAIN", text)                   # (the logo glitches a letter now and then)
        self.assertIn("▶ DUNGEON", text)


class KeyParsing(unittest.TestCase):
    def test_function_and_control_keys(self):
        from cyberdeck_dash.inputs import parse_keys

        self.assertEqual(parse_keys("\x1bOP\x1b[15~\x1b[5~\x1b[6~\x1b[H\x1b[3~\x0f\x1b"),
                         ["f1", "f5", "pgup", "pgdn", "home", "delete", "ctrl+o", "esc"])
        self.assertEqual(parse_keys("a\x1b[Ab\r\t\x7f"), ["a", "up", "b", "enter", "tab", "backspace"])


if __name__ == "__main__":
    unittest.main()
