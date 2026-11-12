"""Headless dashboard tests: a fake-clock session and a Rich snapshot.

Renders the whole screen with Console(record=True) at 100x30 (no
terminal needed) and checks the key parts are on it. Also drives the
dashboard with keys the way a user (or a GPIO button) would.
Skipped if Rich is not installed (pip install -r requirements-dash.txt).
"""

import io
import tempfile
import unittest
from pathlib import Path

try:
    from rich.console import Console
    HAVE_RICH = True
except ImportError:  # pragma: no cover
    HAVE_RICH = False


def make_dash(**kw):
    from cyberdeck_dash.app import Dashboard
    from cyberdeck_dash.config import load_config
    return Dashboard(load_config(), mode="sim", seed=7, threaded=False, companion=False,
                     journal_path=kw.pop("journal_path", False), **kw)


def render(dash, width=100, height=30):
    from cyberdeck_dash.ui.screen import build_screen
    dash.screen_width = width
    console = Console(record=True, width=width, height=height, force_terminal=True, file=io.StringIO())
    console.print(build_screen(dash, dash.now), height=height)
    return console.export_text()


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class RenderSnapshot(unittest.TestCase):
    def setUp(self):
        self.dash = make_dash()
        for i in range(30):
            self.dash.step(1_800_000_000 + i)

    def test_awareness_tab_snapshot(self):
        text = render(self.dash)
        lines = text.splitlines()
        self.assertEqual(len(lines), 30)
        self.assertTrue(all(len(line) <= 100 for line in lines))
        for needle in ("CYBERDECK//DASH", "AWARENESS & DIAGNOSTICS", "WI-FI", "BLUETOOTH", "LAN MAP",
                       "RFID/NFC", "JOURNAL", "DIAGNOSTICS", "CHECKLIST", "AXOLOTL_NET", "SIM"):
            self.assertIn(needle, text)

    def test_deck_tab_and_zoom_and_menu(self):
        self.dash.step(1_800_000_031, ["]"])
        text = render(self.dash)
        for needle in ("FIELD MODE", "PULSE", "SYSTEM"):
            self.assertIn(needle, text)
        self.dash.step(1_800_000_032, ["2", "enter"])          # zoom PULSE
        self.assertIn("all moves:", render(self.dash))
        self.dash.step(1_800_000_033, ["esc", "m"])
        menu = render(self.dash)
        self.assertIn("MAIN MENU", menu)
        self.assertIn("── AWARENESS & DIAGNOSTICS ──", menu)

    def test_every_panel_zoomed_renders(self):
        for ti, (_, keys, _) in enumerate(self.dash.tabs):
            for k in keys:
                self.dash.go_to_panel(k)
                self.assertNotIn("panel error", render(self.dash), k)

    def test_small_screen_does_not_crash(self):
        self.assertIn("CYBERDECK", render(self.dash, 60, 20))


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class Interaction(unittest.TestCase):
    def test_field_mode_move_and_rfid_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash = make_dash(journal_path=Path(tmp) / "j.jsonl")
            dash.tags_path = Path(tmp) / "tags.json"
            t = 1_800_000_000
            dash.step(t)
            dash.step(t + 1, ["f"])
            self.assertTrue(dash.filter.field_mode)
            self.assertEqual(dash.interval("wifi"), 4)
            dash.step(t + 2, ["u"])                            # move -> fresh scan
            self.assertTrue(any("uses" in line for _, _, line in dash.ticker))
            dash.step(t + 3, ["r"])                            # sim tag tap
            dash.step(t + 4)
            self.assertIsNotNone(dash.rfid.last_uid)
            dash.step(t + 5, ["n", *"x" * 0, "backspace"] + list("my note") + ["enter"])
            self.assertEqual(dash.rfid.known[dash.rfid.last_uid].endswith("my note"), True)
            self.assertTrue(dash.tags_path.exists())
            lines = (Path(tmp) / "j.jsonl").read_text().splitlines()
            self.assertGreater(len(lines), 5)                  # shared JSONL journal written

    def test_diagnostics_sim(self):
        dash = make_dash()
        res = dash.diag.run_now("ping", "192.168.1.1")
        self.assertTrue(res.ok)
        self.assertTrue(dash.diag.run_now("dns", "github.com").ok)
        self.assertIn("not a host", dash.diag.start("ping", "1.1.1.1; reboot"))
        dash.diag.refresh_ifaces(0)
        dash.diag.refresh_ifaces(1)
        self.assertGreater(dash.diag.ifaces["wlan0"]["rx_rate"], 0)


if __name__ == "__main__":
    unittest.main()
