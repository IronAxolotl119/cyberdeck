"""Tests for the 16x2 LCD (cyberdeck/lcd and cyberdeck/features/lcd.py).

Everything runs on a fake clock with threaded=False, and the real-LCD
paths (RPLCD and the raw smbus2 driver) are tried against small fake
modules, so no Pi, no I2C bus and no extra installs are needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path

from cyberdeck.lcd import ALERT, INFO, MANUAL, NOTICE, LcdMessage, LcdService, LcdSettings, NotifyQueue
from cyberdeck.lcd.backends import I2cLcd, RawPcf8574, SimScreen
from cyberdeck.lcd.messages import in_quiet_hours, parse_hhmm
from cyberdeck.lcd.pages import BlurbPool, clock_page, fill, layout_message, lines_at, load_blurbs, pulse_page
from cyberdeck.lcd.settings import load_lcd_settings, parse_address
from cyberdeck.lcd.text import ASCII_LOOK, G, GLYPHS, fit, marquee, marquee_steps, sim_view, to_lcd, wrap2
from tests.helpers import make_ctx, write_config

REPO = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0
PET = {"name": "PULSE", "level": 7, "mood": "happy", "hunger": 35, "energy": 82, "happiness": 76}


def service(**overrides) -> LcdService:
    s = replace(LcdSettings(), **overrides)
    lcd = LcdService(s, threaded=False, blurbs={"any": ["{name} hi"], "happy": ["{heart} yay"]})
    lcd.set_pet(PET)
    return lcd


def run(lcd: LcdService, start: float, seconds: float) -> list[str]:
    """Tick at the LCD's fps from start for `seconds`; returns the last frame."""
    t, lines = start, []
    while t <= start + seconds:
        lines = lcd.tick(t)
        t += 1.0 / lcd.s.fps
    return lines


# ---------------------------------------------------------------------------
class TextMapping(unittest.TestCase):
    def test_eight_glyphs_in_eight_slots(self):
        slots = sorted(slot for slot, _ in GLYPHS.values())
        self.assertEqual(slots, list(range(8)))
        for slot, rows in GLYPHS.values():
            self.assertEqual(len(rows), 8)
            self.assertTrue(all(0 <= r < 32 for r in rows))
        self.assertEqual(GLYPHS["heart"][1][1], 0b01010)

    def test_symbols_become_glyphs(self):
        self.assertEqual(to_lcd("◆ LINK ♥"), f"{G['diamond']} LINK {G['heart']}")
        self.assertEqual(to_lcd("⚡🔔📶"), G["bolt"] + G["bell"] + G["wifi"])
        self.assertEqual(to_lcd("{food} time"), G["food"] + " time")

    def test_accents_dashes_and_emoji(self):
        self.assertEqual(to_lcd("Café — naïve… → Ü"), "Cafe - naive... > U")
        self.assertEqual(to_lcd("hi 🦎 there"), "hi  there")       # unknown emoji just vanish
        self.assertEqual(to_lcd("Straße"), "Strasse")
        self.assertEqual(to_lcd("a\\b~c"), "a/b-c")              # A00 ROM shows \\ as Yen, ~ as arrow
        self.assertTrue(all(ord(c) < 127 for c in to_lcd("日本 ñ ✓ ° ±")))

    def test_ascii_fallback_for_glyphs(self):
        self.assertEqual(to_lcd("♥ ◆", glyphs=False), "< *")
        self.assertEqual(sim_view(G["heart"] + "x"), "♥x")
        self.assertEqual(sim_view(G["heart"], ascii_only=True), ASCII_LOOK[G["heart"]])


class WrapAndMarquee(unittest.TestCase):
    def test_wrap_when_it_fits(self):
        self.assertEqual(wrap2("PULSE learned Tag Bite"), ["PULSE learned", "Tag Bite"])
        self.assertEqual(wrap2("short"), ["short", ""])

    def test_no_wrap_when_too_long(self):
        self.assertIsNone(wrap2("this sentence is definitely longer than two lines of sixteen"))
        self.assertIsNone(wrap2("supercalifragilisticexpialidocious"))   # one word wider than the screen

    def test_marquee_window_and_loop(self):
        text = "ABCDEFGHIJKLMNOPQRST"                     # 20 chars
        self.assertEqual(marquee(text, 16, 0), "ABCDEFGHIJKLMNOP")
        self.assertEqual(marquee(text, 16, 4), "EFGHIJKLMNOPQRST")
        self.assertEqual(marquee(text, 16, 6), "GHIJKLMNOPQRST  ")
        self.assertEqual(marquee(text, 16, 24), marquee(text, 16, 0))     # loops after text + 4 spaces
        self.assertEqual(marquee_steps(text, 16), 24)
        self.assertEqual(marquee("short", 16, 99), fit("short"))
        self.assertEqual(marquee_steps("short", 16), 0)


# ---------------------------------------------------------------------------
class Queue(unittest.TestCase):
    def test_priority_then_age(self):
        q = NotifyQueue(max_per_minute=60, burst=10)
        q.push(LcdMessage("a", priority=INFO), T0)
        q.push(LcdMessage("b", priority=ALERT), T0)
        q.push(LcdMessage("c", priority=ALERT), T0)
        self.assertEqual([q.pop().text for _ in range(3)], ["b", "c", "a"])

    def test_dedupe(self):
        q = NotifyQueue(dedupe_seconds=60, max_per_minute=60, burst=10)
        self.assertEqual(q.push(LcdMessage("x", key="k"), T0), "ok")
        self.assertEqual(q.push(LcdMessage("x again", key="k"), T0 + 30), "dup")
        self.assertEqual(q.push(LcdMessage("x later", key="k"), T0 + 61), "ok")
        self.assertEqual(q.counts["dup"], 1)

    def test_rate_limit_never_drops_alerts(self):
        q = NotifyQueue(dedupe_seconds=0, max_per_minute=6, burst=2, max_queue=20)
        res = [q.push(LcdMessage(f"n{i}", priority=NOTICE), T0) for i in range(3)]
        self.assertEqual(res, ["ok", "ok", "rate"])
        for i in range(4):                                     # a burst of real alerts all get through
            self.assertEqual(q.push(LcdMessage(f"alert{i}", priority=ALERT), T0), "ok")
        self.assertEqual(q.push(LcdMessage("notice", priority=NOTICE), T0 + 10), "rate")   # alerts used the tokens
        self.assertEqual(q.push(LcdMessage("later", priority=NOTICE), T0 + 60), "ok")      # refilled

    def test_manual_bypasses_everything(self):
        q = NotifyQueue(max_per_minute=1, burst=1)
        q.field_mode = True
        for i in range(5):
            self.assertEqual(q.push(LcdMessage("say", priority=MANUAL), T0), "ok")

    def test_field_mode_passes_only_alerts(self):
        q = NotifyQueue()
        q.field_mode = True
        self.assertEqual(q.push(LcdMessage("level up", priority=NOTICE), T0), "field")
        self.assertEqual(q.push(LcdMessage("evil twin", priority=ALERT), T0), "ok")

    def test_quiet_hours(self):
        self.assertEqual(parse_hhmm("22:30"), 1350)
        self.assertIsNone(parse_hhmm("25:00"))
        lt = time.localtime(T0)
        now_min = lt.tm_hour * 60 + lt.tm_min
        hhmm = lambda m: f"{(m // 60) % 24:02d}:{m % 60:02d}"
        self.assertTrue(in_quiet_hours(T0, hhmm(now_min - 30), hhmm(now_min + 30)))
        self.assertFalse(in_quiet_hours(T0, hhmm(now_min + 30), hhmm(now_min + 90)))
        # Crossing midnight: start after end.
        self.assertTrue(in_quiet_hours(T0, hhmm(now_min - 10 + 1440), hhmm(now_min - 20 + 1440)))
        q = NotifyQueue(quiet_enabled=True, quiet_start=hhmm(now_min - 30), quiet_end=hhmm(now_min + 30))
        self.assertEqual(q.push(LcdMessage("notice", priority=NOTICE), T0), "quiet")
        self.assertEqual(q.push(LcdMessage("alert", priority=ALERT), T0), "ok")

    def test_full_queue_drops_least_important(self):
        q = NotifyQueue(dedupe_seconds=0, max_per_minute=600, burst=100, max_queue=2)
        q.push(LcdMessage("info", priority=INFO), T0)
        q.push(LcdMessage("alert", priority=ALERT), T0)
        q.push(LcdMessage("notice", priority=NOTICE), T0)
        self.assertEqual(sorted(m.text for m in q.items), ["alert", "notice"])
        self.assertEqual(q.counts.get("full"), 1)


# ---------------------------------------------------------------------------
class Pages(unittest.TestCase):
    def test_message_with_title_and_scrolling_body(self):
        scr = layout_message(LcdMessage("de:ad:be:ef:00:11:22 near the door", "{bell}UNKNOWN TAG", ALERT))
        self.assertEqual(scr.lines[0], G["bell"] + "UNKNOWN TAG    ")
        first = lines_at(scr, 0, 16, 2, 4, 1.0)
        later = lines_at(scr, 3.0, 16, 2, 4, 1.0)          # 2 s of scrolling = 8 steps
        self.assertEqual(first[1], "de:ad:be:ef:00:1")
        self.assertEqual(later[1], marquee(scr.lines[1], 16, 8))
        self.assertEqual(later[1], ":ef:00:11:22 nea")
        self.assertEqual(first[0], later[0])                # the title stays put

    def test_message_without_title_wraps(self):
        scr = layout_message(LcdMessage("PULSE learned Tag Bite!", priority=NOTICE))
        self.assertEqual(scr.lines, ["PULSE learned   ", "Tag Bite!       "])

    def test_long_untitled_message_gets_header(self):
        scr = layout_message(LcdMessage("x " * 30, priority=ALERT))
        self.assertTrue(scr.lines[0].startswith(G["bell"] + " ALERT"))

    def test_pulse_and_clock_pages(self):
        p = pulse_page(PET)
        self.assertEqual(p.lines[0], G["face"] + "PULSE L7  happy")
        self.assertIn(G["food"] + "35", p.lines[1])
        self.assertIn(G["bolt"] + "82", p.lines[1])
        self.assertTrue(pulse_page(dict(PET, asleep=True)).lines[0].rstrip().endswith("zZ"))
        c = clock_page(T0, "LINK UP", 87, "%H:%M")
        self.assertEqual(c.lines[0].strip(), time.strftime("%H:%M", time.localtime(T0)))
        self.assertTrue(c.lines[1].endswith(G["bolt"] + "87%"))
        self.assertEqual(len(c.lines[1]), 16)

    def test_blurb_file_and_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.txt"
            path.write_text("# comment\nhello {name}\n[hungry]\n{food} feed me\n[sleeping]\nzZz\n", encoding="utf-8")
            groups = load_blurbs(path)
        self.assertEqual(groups, {"any": ["hello {name}"], "hungry": ["{food} feed me"], "sleeping": ["zZz"]})
        self.assertEqual(fill("{name} L{level} {unknown}", PET), "PULSE L7 {unknown}")
        pool = BlurbPool(groups, rng=__import__("random").Random(1))
        seen = {pool.next({"name": "P", "mood": "hungry"}, T0) for _ in range(20)}
        self.assertEqual(seen, {"hello P", "{food} feed me"})
        self.assertEqual({pool.next({"asleep": True}, T0) for _ in range(10)} - {"hello PULSE"}, {"zZz"})

    def test_reactions_come_first_then_join_rotation(self):
        pool = BlurbPool({"any": ["flavor"]}, reaction_minutes=1)
        pool.add_reaction("PULSE logged HomeNet (+7 xp)", T0)
        self.assertEqual(pool.next(PET, T0), "PULSE logged HomeNet (+7 xp)")
        later = {pool.next(PET, T0 + 600) for _ in range(10)}
        self.assertEqual(later, {"flavor"})                   # expired after reaction_minutes

    def test_shipped_blurb_file_loads(self):
        groups = load_blurbs(REPO / "text" / "lcd_blurbs.txt")
        for mood in ("any", "happy", "hungry", "tired", "sad", "sleeping", "excited", "content"):
            self.assertIn(mood, groups)


# ---------------------------------------------------------------------------
class FakeHw:
    def __init__(self):
        self.lines, self.light, self.writes, self._started = {}, None, 0, True

    def write_line(self, row, text):
        self.lines[row] = text
        self.writes += 1

    def set_backlight(self, on):
        self.light = on

    def describe(self):
        return "fake"

    def close(self):
        pass


class Service(unittest.TestCase):
    def test_notification_then_back_to_idle(self):
        lcd = service(hold_seconds=4, flash=False)
        run(lcd, T0, 1)
        self.assertEqual(lcd.notify("hello", "{bell}TEST", NOTICE, now=T0 + 1), "ok")
        lines = run(lcd, T0 + 1, 1)
        self.assertEqual(lines[0].strip(), G["bell"] + "TEST")
        lines = run(lcd, T0 + 2, 6)
        self.assertIsNone(lcd.current)
        self.assertNotIn("TEST", lines[0])

    def test_alert_interrupts_and_cut_message_comes_back(self):
        lcd = service(hold_seconds=6, alert_hold_seconds=4, flash=False)
        lcd.notify("news", "NEWS", NOTICE, now=T0)
        run(lcd, T0, 0.5)
        lcd.notify("evil twin", "TWIN", ALERT, now=T0 + 0.6)
        lines = run(lcd, T0 + 0.6, 0.5)
        self.assertEqual(lines[0].strip(), "TWIN")
        lines = run(lcd, T0 + 1.2, 4)
        self.assertEqual(lines[0].strip(), "NEWS")          # requeued after the alert

    def test_flash_blinks_backlight(self):
        lcd = service(flash=True, flash_times=2)
        run(lcd, T0, 0.5)
        lcd.notify("x", "X", NOTICE, now=T0 + 0.5)
        states = set()
        t = T0 + 0.5
        for _ in range(6):
            lcd.tick(t)
            states.add(lcd.screen.backlight)
            t += 0.125
        self.assertEqual(states, {True, False})
        lcd.tick(T0 + 3)
        self.assertTrue(lcd.screen.backlight)

    def test_backlight_override(self):
        lcd = service()
        lcd.set_backlight("off")
        lcd.tick(T0)
        self.assertFalse(lcd.screen.backlight)
        lcd.set_backlight("auto")
        lcd.tick(T0 + 1)
        self.assertTrue(lcd.screen.backlight)

    def test_link_and_battery_notifications(self):
        lcd = service()
        lcd.set_link(True, now=T0)                       # first reading: no pop-up
        self.assertEqual(len(lcd.queue), 0)
        lcd.set_link(False, now=T0 + 1)
        self.assertEqual(lcd.queue.peek().priority, ALERT)
        lcd.set_battery(50, now=T0)
        lcd.set_battery(12, now=T0 + 2)
        lcd.set_battery(11, now=T0 + 40)                 # only once until it recovers
        self.assertEqual(sum(1 for m in lcd.queue.items if m.kind == "battery"), 1)

    def test_pet_events_and_reactions_dedupe(self):
        lcd = service()
        payload = {"event": "learned_move", "detail": {"move": "Tag Bite"}, "pet": dict(PET, level=8)}
        self.assertEqual(lcd.pet_event("learned_move", "PULSE learned Tag Bite!", payload, now=T0), "ok")
        self.assertEqual(lcd.pet["level"], 8)
        lcd.discovery_reaction({"reaction": "PULSE logged a tag (+8 xp)", "learned_move": "Tag Bite"}, now=T0 + 1)
        self.assertEqual(len(lcd.queue), 1)               # same move: not twice
        self.assertIn("PULSE logged a tag (+8 xp)", [t for _, t in lcd.blurbs.reactions])
        self.assertEqual(lcd.pet_event("mood", "mood changed", now=T0), "skip")
        strong = lcd.pet_event("move_strengthened", "x", {"detail": {"move": "Zap", "rank": 3, "bonus": 6}}, now=T0)
        self.assertEqual(strong, "ok")
        self.assertIn("rank 3 (+6 power)", lcd.queue.items[-1].text)
        lcd.discovery_reaction({"reaction": "already logged", "xp": 0}, now=T0 + 2)
        self.assertNotIn("already logged", [t for _, t in lcd.blurbs.reactions])

    def test_companion_say_lines(self):
        lcd = service()
        # A life event with `say` pops up (sick) and its line is what shows.
        res = lcd.pet_event("sick", "PULSE: sick", {"detail": {"kind": "cold", "say": "achoo... I feel fuzzy"}}, now=T0)
        self.assertEqual(res, "ok")
        self.assertEqual(lcd.queue.items[-1].text, "achoo... I feel fuzzy")
        # Chatter (speech) only becomes a blurb.
        self.assertEqual(lcd.pet_event("speech", "x", {"detail": {"say": "beep boop, operator"}}, now=T0), "skip")
        self.assertEqual(lcd.blurbs.next(PET, T0 + 1), "achoo... I feel fuzzy")
        self.assertEqual(lcd.blurbs.next(PET, T0 + 2), "beep boop, operator")

    def test_awareness_alerts_and_field_mode(self):
        lcd = service()
        lcd.set_field_mode(True)
        self.assertEqual(lcd.awareness_alert("wifi", "open_network", "CoffeeShop", "aa:bb", 2, now=T0), "field")
        self.assertEqual(lcd.awareness_alert("rfid", "unknown_tag", "unknown tag", "de:ad", 3, now=T0), "ok")
        msg = lcd.queue.peek()
        self.assertEqual((msg.title, msg.text, msg.priority), ("{bell}UNKNOWN TAG", "de:ad", ALERT))

    def test_chirp_hook_for_alerts_only(self):
        chirps = []
        lcd = service(chirp=True, chirp_priority=ALERT)
        lcd.chirp = lambda: chirps.append(1)
        lcd.notify("n", "N", NOTICE, now=T0)
        run(lcd, T0, 0.5)
        lcd.notify("a", "A", ALERT, now=T0 + 1)
        run(lcd, T0 + 1, 0.5)
        self.assertEqual(chirps, [1])

    def test_only_changed_lines_hit_the_hardware(self):
        hw = FakeHw()
        lcd = LcdService(LcdSettings(rotate_seconds=60), threaded=False, hardware=hw, blurbs={"any": ["hi"]})
        lcd.set_pet(PET)
        lcd.tick(T0)
        first = hw.writes
        for i in range(10):
            lcd.tick(T0 + 0.25 * i)
        self.assertEqual(hw.writes, first)                 # static page: no rewrites
        self.assertTrue(hw.light)
        self.assertEqual(hw.lines[0], lcd.screen.lines[0])

    def test_broken_i2c_falls_back_to_sim(self):
        class Broken(FakeHw):
            def __init__(self):
                super().__init__()
                self._started = False

            def start(self):
                raise OSError("no /dev/i2c-1")

        lcd = LcdService(LcdSettings(backend="i2c"), threaded=False, hardware=Broken(), blurbs={})
        lcd._open_hw()
        self.assertIsNone(lcd.hw)
        self.assertIn("no /dev/i2c-1", lcd.backend_text())
        lcd.tick(T0)                                       # still runs on the sim copy
        self.assertTrue(any(lcd.screen.lines))

    def test_threaded_start_stop(self):
        lcd = LcdService(LcdSettings(fps=10), threaded=True, blurbs={"any": ["hi"]})
        lcd.start()
        lcd.say("threaded hello")
        deadline = time.time() + 3
        while time.time() < deadline and "threaded" not in lcd.screen.lines[0]:
            time.sleep(0.05)
        lcd.stop()
        self.assertIn("threaded", lcd.screen.lines[0])

    def test_none_backend_is_off(self):
        lcd = LcdService(LcdSettings(backend="none"), threaded=False, blurbs={})
        self.assertEqual(lcd.notify("x"), "off")
        self.assertIn("none", lcd.backend_text())


class SimRender(unittest.TestCase):
    def test_box_and_backlight_indicator(self):
        sc = SimScreen()
        sc.write_line(0, G["heart"] + "hello")
        sc.write_line(1, "world")
        box = sc.render()
        self.assertEqual(box[0], "┌" + "─" * 16 + "┐ ● on")
        self.assertEqual(box[1], "│♥hello          │")
        sc.set_backlight(False)
        ascii_box = sc.render(ascii_only=True)
        self.assertTrue(ascii_box[0].endswith("BL:off"))
        self.assertEqual(ascii_box[1], "|<hello          |")

    def test_mirror_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lcd.txt"
            sc = SimScreen(mirror=path)
            sc.write_line(0, "mirror me")
            self.assertIn("|mirror me       |", path.read_text())


# ---------------------------------------------------------------------------
class RealDriversWithFakes(unittest.TestCase):
    """The i2c code paths, against fake RPLCD / smbus2 modules."""

    def setUp(self):
        self.saved = {k: sys.modules.get(k) for k in ("RPLCD", "RPLCD.i2c", "smbus2")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def fake_smbus(self, present=(0x27,)):
        log = []

        class SMBus:
            def __init__(self, port):
                self.port = port

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read_byte(self, addr):
                if addr not in present:
                    raise OSError("nack")
                return 0

            def write_byte(self, addr, value):
                log.append((addr, value))

            def close(self):
                pass

        sys.modules["smbus2"] = types.SimpleNamespace(SMBus=SMBus)
        sys.modules.pop("RPLCD", None)
        sys.modules["RPLCD.i2c"] = None          # "not installed"
        return log

    def test_raw_smbus2_driver(self):
        log = self.fake_smbus(present=(0x3F,))
        hw = I2cLcd(address=None, candidates=[0x27, 0x3F], driver="auto")
        hw.start()
        self.assertEqual((hw.kind, hw.address), ("smbus2", 0x3F))
        self.assertTrue(all(addr == 0x3F for addr, _ in log))
        self.assertTrue(all(v & RawPcf8574.BL for _, v in log))    # backlight bit stays on
        log.clear()
        hw.write_line(1, "Hi")
        # 3 writes per nibble, 2 nibbles per byte: cursor command + 16 characters.
        self.assertEqual(len(log), 3 * 2 * 17)
        hw.set_backlight(False)
        self.assertEqual(log[-1], (0x3F, 0))

    def test_no_backpack_found(self):
        self.fake_smbus(present=())
        from cyberdeck.lcd.backends import LcdHardwareError

        with self.assertRaises(LcdHardwareError) as err:
            I2cLcd(address=None).start()
        self.assertIn("i2cdetect -y 1", str(err.exception))

    def test_rplcd_driver(self):
        self.fake_smbus()
        made = {}

        class CharLCD:
            def __init__(self, **kw):
                made.update(kw)
                self.chars, self.written, self.cursor_pos, self.backlight_enabled = {}, [], None, True

            def create_char(self, slot, rows):
                self.chars[slot] = rows

            def write_string(self, text):
                self.written.append((self.cursor_pos, text))

            def close(self, clear=False):
                self.closed = clear

        sys.modules["RPLCD"] = types.ModuleType("RPLCD")
        sys.modules["RPLCD.i2c"] = types.SimpleNamespace(CharLCD=CharLCD)
        hw = I2cLcd(address=0x27)
        hw.start()
        self.assertEqual(hw.kind, "rplcd")
        self.assertEqual((made["i2c_expander"], made["address"], made["cols"], made["rows"]), ("PCF8574", 0x27, 16, 2))
        self.assertEqual(sorted(hw.dev.chars), list(range(8)))
        hw.write_line(0, "hello")
        self.assertEqual(hw.dev.written[-1], ((0, 0), "hello           "))
        hw.set_backlight(False)
        self.assertFalse(hw.dev.backlight_enabled)

    def test_parse_address_and_settings(self):
        self.assertEqual(parse_address("0x27"), 0x27)
        self.assertEqual(parse_address("63"), 63)
        self.assertIsNone(parse_address("auto"))
        cfg = write_config({"lcd.ini": "[lcd]\nbackend = i2c\nfps = 99\n[i2c]\naddress = 0x3F\n[quiet]\nenabled = yes\n"})
        from cyberdeck.config import Settings

        s = load_lcd_settings(Settings.load("lcd.ini", cfg))
        self.assertEqual((s.backend, s.fps, s.address, s.quiet_enabled), ("i2c", 10.0, "0x3F", True))
        shipped = load_lcd_settings(Settings.load("lcd.ini", REPO / "config"))
        self.assertEqual((shipped.backend, shipped.cols, shipped.rows, shipped.quiet_enabled), ("sim", 16, 2, False))


# ---------------------------------------------------------------------------
class ConsoleCommands(unittest.TestCase):
    def setUp(self):
        from cyberdeck.commands import build_registry

        self.ctx = make_ctx()
        self.ctx.services["lcd_options"] = {"threaded": False}
        self.reg = build_registry(include_plugins=False)

    def run_line(self, line):
        return self.reg.dispatch(line, self.ctx)

    def test_status(self):
        out = "\n".join(self.run_line("lcd status"))
        self.assertIn("lcd: sim", out)
        self.assertIn("┌────────────────┐", out)
        self.assertEqual(self.run_line("lcd")[0], self.run_line("lcd status")[0])

    def test_say_and_test(self):
        self.assertEqual(self.run_line("lcd say hi operator"), ["lcd: hi operator"])
        lcd = self.ctx.services["lcd"]
        lcd.tick(time.time())
        self.assertEqual(lcd.screen.lines[0].strip(), "hi operator")
        self.run_line("lcd test")                                   # replaces the say right away
        lcd.tick(time.time() + 0.01)
        self.assertTrue(lcd.screen.lines[0].startswith("".join(chr(i) for i in range(8))))
        self.assertEqual(self.run_line("lcd say"), ["usage: lcd say <text>"])

    def test_backlight_next_clear_reload(self):
        self.assertEqual(self.run_line("lcd backlight off"), ["lcd backlight off"])
        lcd = self.ctx.services["lcd"]
        lcd.tick(time.time())
        self.assertFalse(lcd.screen.backlight)
        self.assertIn("usage", self.run_line("lcd backlight dim")[0])
        self.assertEqual(self.run_line("lcd next"), ["lcd: next page"])
        lcd.notify("x", "X", NOTICE)
        self.assertEqual(self.run_line("lcd clear"), ["lcd: dropped 1 waiting notification(s)"])
        self.assertIn("blurbs loaded", self.run_line("lcd reload")[0])
        self.assertIn("usage", self.run_line("lcd dance")[0])

    def test_tab_completion(self):
        from cyberdeck.completion import complete_line

        self.assertEqual(complete_line("lcd te", self.reg, self.ctx)[0], "lcd test ")
        self.assertEqual(complete_line("lcd backlight of", self.reg, self.ctx)[0], "lcd backlight off ")

    def test_pet_watcher_events_reach_the_lcd(self):
        from cyberdeck.features.petwatch import PetEvent, get_watcher

        self.run_line("lcd status")                                  # builds the LCD
        payload = {"event": "evolve_choice", "detail": {"choices": []}, "pet": dict(PET)}
        get_watcher(self.ctx).handle(self.ctx, [PetEvent("evolve_choice", "alert", "PULSE is ready to evolve!",
                                                         payload)], time.time())
        texts = [m.title for m in self.ctx.services["lcd"].queue.items]
        self.assertIn("{bell}PICK EVOLUTION", texts)
        # The watcher's own polled check (no payload) works too: evolve -> evolved.
        get_watcher(self.ctx).handle(self.ctx, [PetEvent("evolve", "ok", "PULSE evolved!")], time.time())
        self.assertIn("{diamond}EVOLVED!", [m.title for m in self.ctx.services["lcd"].queue.items])

    def test_pane_renders(self):
        from cyberdeck.panes import PANES

        self.run_line("lcd status")
        rows = PANES["lcd"].render(self.ctx, 30, 8)
        self.assertTrue(rows[0][0][0].startswith("┌"))


class EntryPoints(unittest.TestCase):
    def run_deck(self, *args, module="cyberdeck"):
        env = dict(os.environ, CYBERDECK_LINK="sim")
        return subprocess.run([sys.executable, "-m", module, *args], cwd=REPO, env=env, capture_output=True,
                              text=True, timeout=120)

    def test_headless_lcd_sim_prints_the_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_deck("--data-dir", tmp, "--lcd-sim", "--script", "lcd say hello from a script")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[lcd] |hello from a    |", res.stdout)
        self.assertIn("[lcd] |script          |", res.stdout)

    def test_lcd_none_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_deck("--data-dir", tmp, "--lcd", "none", "--script", "lcd say hi")
        self.assertIn("switched off", res.stdout)

    def test_standalone_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "lcd.txt"
            res = self.run_deck("--snapshot", str(out), module="cyberdeck.lcd")
            self.assertEqual(res.returncode, 0, res.stderr)
            text = out.read_text(encoding="utf-8")
        for bit in ("notification (alert)", "UNKNOWN TAG", "idle page: pulse", "idle page: blurb", "idle page: clock"):
            self.assertIn(bit, text)


if __name__ == "__main__":
    unittest.main()
