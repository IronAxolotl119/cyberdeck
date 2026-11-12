"""tests/test_core_ux.py — history, Tab completion, hotkeys, launcher, panes.

These are the pieces behind the full-screen UI that do not need
curses: they are plain classes and functions, tested directly.
"""

from __future__ import annotations

import curses
import tempfile
import unittest
from pathlib import Path

from cyberdeck.activity import MenuActivity, MenuItem, rows_to_text
from cyberdeck.commands import build_registry
from cyberdeck.completion import complete_line
from cyberdeck.config import Settings
from cyberdeck.headless import handle_ui_requests
from cyberdeck.history import History
from cyberdeck.keys import DEFAULT_HOTKEYS, key_name, load_hotkeys, load_launcher, normalize_key_spec
from cyberdeck.panes import PaneSettings, compute_layout, pet_pane, system_pane

from helpers import make_ctx, write_config


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "history.txt"

    def test_up_down_walks_entries_and_restores_draft(self):
        h = History(self.path)
        for line in ("one", "two", "three"):
            h.add(line)
        self.assertEqual(h.previous("dra"), "three")
        self.assertEqual(h.previous("three"), "two")
        self.assertEqual(h.previous("two"), "one")
        self.assertEqual(h.previous("one"), "one")  # stops at the oldest
        self.assertEqual(h.next("one"), "two")
        self.assertEqual(h.next("two"), "three")
        self.assertEqual(h.next("three"), "dra")   # back to the draft

    def test_persists_and_reloads(self):
        h = History(self.path)
        h.add("pet feed ")
        h.add("status")
        again = History(self.path)
        self.assertEqual(again.entries, ["pet feed", "status"])

    def test_skips_blanks_duplicates_and_space_prefixed(self):
        h = History(self.path)
        for line in ("ls", "ls", "   ", " secret", "ls"):
            h.add(line)
        self.assertEqual(h.entries, ["ls"])

    def test_limit_keeps_newest(self):
        h = History(self.path, limit=3)
        for i in range(6):
            h.add(f"cmd{i}")
        self.assertEqual(h.entries, ["cmd3", "cmd4", "cmd5"])
        self.assertEqual(History(self.path, limit=3).entries, ["cmd3", "cmd4", "cmd5"])


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry(include_plugins=False)
        self.ctx = make_ctx()

    def test_unique_command_is_completed_with_space(self):
        line, choices = complete_line("hist", self.reg, self.ctx)
        self.assertEqual(line, "history ")
        self.assertEqual(choices, ["history"])

    def test_ambiguous_lists_choices(self):
        line, choices = complete_line("p", self.reg, self.ctx)
        self.assertIn("pet", choices)
        self.assertIn("ping", choices)
        self.assertEqual(line, "p")

    def test_common_prefix_is_filled(self):
        line, choices = complete_line("pe", self.reg, self.ctx)
        self.assertEqual(line, "pet ")

    def test_subcommands(self):
        self.assertEqual(complete_line("pet f", self.reg, self.ctx)[0], "pet feed ")
        _, choices = complete_line("pet ", self.reg, self.ctx)
        self.assertEqual(set(choices), {"status", "feed", "play"})

    def test_dynamic_completer(self):
        line, _ = complete_line("help st", self.reg, self.ctx)
        self.assertEqual(line, "help status ")

    def test_no_match_leaves_line(self):
        self.assertEqual(complete_line("zzz", self.reg, self.ctx), ("zzz", []))


class KeyTests(unittest.TestCase):
    def test_key_names(self):
        self.assertEqual(key_name(curses.KEY_F0 + 5), "f5")
        self.assertEqual(key_name(curses.KEY_UP), "up")
        self.assertEqual(key_name("\x10"), "ctrl+p")
        self.assertEqual(key_name("\n"), "enter")
        self.assertEqual(key_name("\t"), "tab")
        self.assertEqual(key_name("\x7f"), "backspace")
        self.assertEqual(key_name("\x1b"), "esc")
        self.assertEqual(key_name("x"), "x")

    def test_spec_spellings(self):
        for spec in ("ctrl+p", "Ctrl-P", "C-p", "^P"):
            self.assertEqual(normalize_key_spec(spec), "ctrl+p")
        self.assertEqual(normalize_key_spec("F12"), "f12")
        self.assertIsNone(normalize_key_spec("F13"))
        self.assertIsNone(normalize_key_spec("banana"))

    def test_load_hotkeys_with_warnings(self):
        s = Settings.from_text("[hotkeys]\nF9 = wifi scan\nctrl+c = quit\nnope = x\n")
        mapping, warnings = load_hotkeys(s)
        self.assertEqual(mapping, {"f9": "wifi scan"})
        self.assertEqual(len(warnings), 2)

    def test_defaults_when_missing(self):
        mapping, warnings = load_hotkeys(Settings(None))
        self.assertEqual(mapping, DEFAULT_HOTKEYS)
        self.assertEqual(warnings, [])

    def test_launcher_labels_can_contain_colons(self):
        s = Settings.from_text("[launcher]\nPet: feed = pet feed\nPet: play = pet play\n")
        self.assertEqual(load_launcher(s), [("Pet: feed", "pet feed"), ("Pet: play", "pet play")])


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.menu = MenuActivity([MenuItem("Status", "status"), MenuItem("Pet feed", "pet feed"),
                                  MenuItem("Add note", "note add ...")])

    def test_arrows_and_enter(self):
        self.menu.on_key("down")
        self.menu.on_key("enter")
        self.assertTrue(self.menu.done)
        self.assertEqual(self.menu.result_command, "pet feed")

    def test_filter(self):
        for ch in "note":
            self.menu.on_key(ch)
        self.assertEqual([i.label for i in self.menu.visible()], ["Add note"])
        self.menu.on_key("enter")
        self.assertIsNone(self.menu.result_command)
        self.assertEqual(self.menu.result_prompt, "note add ")

    def test_escape_closes_without_action(self):
        self.menu.on_key("esc")
        self.assertTrue(self.menu.done)
        self.assertIsNone(self.menu.result_command)

    def test_render_marks_selection(self):
        text = rows_to_text(self.menu.render(30, 6))
        self.assertTrue(any(line.startswith("▶ Status") for line in text))


class PaneLayoutTests(unittest.TestCase):
    def test_split_at_80x24(self):
        geo = compute_layout(24, 80, PaneSettings())
        self.assertEqual(geo.mode, "split")
        self.assertEqual([name for name, _, _ in geo.side], ["pet", "system"])
        self.assertEqual(geo.side[0][1], geo.top_divider)  # first title on the divider
        last = geo.side[-1][2]
        self.assertEqual(last.top + last.height, geo.bottom_divider)
        self.assertLess(geo.output.left + geo.output.width, geo.divider_col)

    def test_narrow_falls_back_to_tabs(self):
        geo = compute_layout(24, 60, PaneSettings())
        self.assertEqual(geo.mode, "tabs")
        self.assertIsNone(geo.divider_col)

    def test_no_ticker_moves_body_up(self):
        geo = compute_layout(24, 80, PaneSettings(ticker=False))
        self.assertIsNone(geo.ticker_row)
        self.assertEqual(geo.top_divider, 2)

    def test_settings_from_ini(self):
        from cyberdeck.panes import load_pane_settings

        s = Settings.from_text("[panes]\nmode = tabs\nside = system\nside_width = 5\nticker = no\n")
        ps = load_pane_settings(s)
        self.assertEqual((ps.mode, ps.side, ps.side_width, ps.ticker), ("tabs", ["system"], 16, False))

    def test_builtin_panes_render(self):
        ctx = make_ctx()
        pet = rows_to_text(pet_pane(ctx, 28, 8))
        self.assertIn("PULSE", pet[0])
        sysrows = rows_to_text(system_pane(ctx, 28, 8))
        self.assertTrue(sysrows[0].startswith("temp"))


class CoreUxCommandTests(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry(include_plugins=False)
        self.ctx = make_ctx()

    def test_history_command_uses_shared_history(self):
        from cyberdeck.features.core_ux import get_history

        get_history(self.ctx).add("status")
        self.assertIn("status", "\n".join(self.reg.dispatch("history", self.ctx)))
        self.reg.dispatch("history clear", self.ctx)
        self.assertEqual(get_history(self.ctx).entries, [])

    def test_menu_headless_lists_entries(self):
        text = "\n".join(self.reg.dispatch("menu", self.ctx))
        self.assertIn("launcher entries", text)

    def test_menu_interactive_requests_launcher(self):
        self.ctx.interactive = True
        self.reg.dispatch("menu", self.ctx)
        self.assertEqual(self.ctx.ui_action, ":launcher")

    def test_view_and_layout_set_ui_actions(self):
        self.ctx.interactive = True
        self.reg.dispatch("view pet", self.ctx)
        self.assertEqual(self.ctx.ui_action, ":tab pet")
        self.reg.dispatch("layout tabs", self.ctx)
        self.assertEqual(self.ctx.ui_action, ":layout tabs")
        self.assertIn("no such view", self.reg.dispatch("view nowhere", self.ctx)[0])

    def test_keys_reads_config_dir(self):
        cfg = write_config({"keys.ini": "[hotkeys]\nF7 = sys\n"})
        ctx = make_ctx(cfg)
        text = "\n".join(self.reg.dispatch("keys", ctx))
        self.assertIn("f7", text)

    def test_headless_clears_ui_requests(self):
        self.ctx.ui_action = ":launcher"
        self.ctx.should_clear = True
        handle_ui_requests(self.ctx)
        self.assertIsNone(self.ctx.ui_action)
        self.assertFalse(self.ctx.should_clear)


if __name__ == "__main__":
    unittest.main()
