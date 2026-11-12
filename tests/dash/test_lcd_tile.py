"""The 16x2 LCD inside the Rich interface: wiring and the LCD tile.

Awareness alerts, pet.discovery reactions and field mode must reach the
deck's shared LcdService, and the Deck & PULSE tab must draw the tile.
Skipped if Rich is not installed.
"""

from __future__ import annotations

import io
import unittest

try:
    from rich.console import Console
    HAVE_RICH = True
except ImportError:  # pragma: no cover
    HAVE_RICH = False

from cyberdeck.lcd import ALERT
from cyberdeck_dash.events import HIGH, MEDIUM, TOPIC_ALERT, TOPIC_PET_REACTION, Finding

T0 = 1_800_000_000


def render(dash, width=110, height=34) -> str:
    from cyberdeck_dash.ui.screen import build_screen

    console = Console(record=True, width=width, height=height, force_terminal=True, file=io.StringIO())
    console.print(build_screen(dash, dash.now), height=height)
    return console.export_text()


@unittest.skipUnless(HAVE_RICH, "rich not installed")
class LcdInTheRichInterface(unittest.TestCase):
    def setUp(self):
        from tests.dash.test_unified import make_unified

        self.dash = make_unified()
        self.lcd = self.dash.lcd

    def test_shared_with_the_deck_and_on_the_fake_clock(self):
        self.assertIsNotNone(self.lcd)
        self.assertIs(self.lcd, self.dash.bridge.ctx.services["lcd"])
        self.assertFalse(self.lcd.threaded)

    def test_awareness_alert_shows_on_the_lcd(self):
        f = Finding("wifi", "evil_twin", "aa:bb:cc:dd:ee:ff", "AXOLOTL_NET", HIGH, ts=T0 + 1)
        self.dash.bus.publish(TOPIC_ALERT, f)
        self.dash.step(T0 + 1.5)
        lines = self.lcd.screen.lines
        self.assertTrue(lines[0].startswith("\x02EVIL TWIN?"), lines)
        self.assertEqual(self.lcd.current.priority, ALERT)

    def test_field_mode_reaches_the_lcd(self):
        self.dash.toggle_field()
        self.dash.step(T0 + 1)
        self.assertTrue(self.lcd.queue.field_mode)
        f = Finding("wifi", "open_network", "11:22", "CafeWiFi", MEDIUM, ts=T0 + 1)
        self.dash.bus.publish(TOPIC_ALERT, f)
        self.assertEqual(self.lcd.queue.history[0][1], "field")

    def test_discovery_reaction_becomes_a_blurb_and_news(self):
        self.dash.bus.publish(TOPIC_PET_REACTION, {"reaction": "PULSE logged HomeNet (+7 xp)", "xp": 7,
                                                   "learned_move": "Signal Sniff", "level": 3})
        self.assertIn("PULSE logged HomeNet (+7 xp)", [t for _, t in self.lcd.blurbs.reactions])
        self.assertIn("{diamond}NEW MOVE!", [m.title for m in self.lcd.queue.items] +
                      [self.lcd.current.title if self.lcd.current else ""])

    def test_deck_tab_draws_the_tile(self):
        self.dash.deck_ui_action(":tab lcd")
        self.dash.lcd.say("hello tile", now=self.dash.now)
        self.dash.step(self.dash.now + 0.5)
        text = render(self.dash)
        self.assertIn("LCD 16x2", text)
        self.assertIn("hello tile", text)
        self.assertIn("BL o", text)              # the backlight lamp (on / off while flashing)

    def test_tile_says_when_switched_off(self):
        from cyberdeck_dash.ui.lcd_panel import render_lcd

        self.lcd.enabled = False
        out = Console(file=io.StringIO(), width=60, record=True)
        out.print(render_lcd(self.dash, False))
        self.assertIn("switched off", out.export_text())


if __name__ == "__main__":
    unittest.main()
