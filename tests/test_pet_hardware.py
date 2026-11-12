"""tests/test_pet_hardware.py — pet alerts/log, GPIO backends, and music.

No real hardware is touched: GPIO runs on SimGpio (with sleeping
turned off so songs finish instantly) or on a fake /sys/class/gpio
folder made of ordinary files.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cyberdeck.commands import build_registry
from cyberdeck.config import Settings
from cyberdeck.features.hardware import Hardware, get_hardware
from cyberdeck.features.music import MusicPlayer, SongError, note_freq, parse_song, piano_roll
from cyberdeck.features.petwatch import AlertSettings, PetLog, PetWatcher, load_alert_settings
from cyberdeck.gpio import GpioError, SimGpio, SysfsGpio, open_gpio

from helpers import make_ctx


def _pet(**over):
    base = {"name": "PULSE", "stage": "rookie", "mood": "content", "hunger": 40, "happiness": 70, "energy": 80}
    base.update(over)
    return base


class PetWatcherTests(unittest.TestCase):
    def setUp(self):
        self.log = PetLog(Path(tempfile.mkdtemp()) / "pet_log.txt")
        self.w = PetWatcher(AlertSettings(hungry_at=75, bored_at=35, tired_at=15, repeat_minutes=10), self.log)

    def kinds(self, events):
        return [e.kind for e in events]

    def test_first_check_of_a_fine_pet_is_quiet(self):
        self.assertEqual(self.w.check(_pet(), 0), [])

    def test_hunger_alert_once_then_repeat(self):
        self.w.check(_pet(), 0)
        self.assertEqual(self.kinds(self.w.check(_pet(hunger=80), 10)), ["hungry"])
        self.assertEqual(self.w.check(_pet(hunger=82), 20), [])          # still hungry, no spam
        self.assertEqual(self.kinds(self.w.check(_pet(hunger=85), 10 + 600)), ["hungry"])  # reminder
        self.assertEqual(self.w.status_badge(), ("! HUNGRY", "alert"))

    def test_recovery(self):
        self.w.check(_pet(hunger=90), 0)
        events = self.w.check(_pet(hunger=20), 5)
        self.assertEqual(self.kinds(events), ["recovered"])
        self.assertIsNone(self.w.status_badge())

    def test_bored_and_tired_are_warnings(self):
        events = self.w.check(_pet(happiness=30, energy=10), 0)
        self.assertEqual(sorted(self.kinds(events)), ["bored", "tired"])
        self.assertTrue(all(e.level == "warn" for e in events))

    def test_evolution_and_mood_change(self):
        self.w.check(_pet(), 0)
        events = self.w.check(_pet(stage="champion", mood="joyful"), 1)
        self.assertEqual(self.kinds(events), ["evolve", "mood"])
        self.assertIn("rookie → champion", events[0].text)
        self.assertEqual(events[1].level, "quiet")

    def test_handle_logs_and_notifies(self):
        ctx = make_ctx()
        self.w.settings = AlertSettings(sound="none")
        self.w.handle(ctx, self.w.check(_pet(hunger=99), 0), 0)
        self.assertEqual(ctx.notices[0][0], "alert")
        self.assertIn("hungry", self.log.read()[0])

    def test_ui_tick_polls_on_schedule(self):
        ctx = make_ctx()
        ctx.companion.pet.hunger = 99
        self.w.settings = AlertSettings(sound="none", poll_seconds=10)
        self.w.ui_tick(ctx, 100.0)
        self.assertIn("hungry", self.w.active)
        ctx.companion.pet.hunger = 10
        self.w.ui_tick(ctx, 105.0)  # too soon: no poll
        self.assertIn("hungry", self.w.active)
        self.w.ui_tick(ctx, 111.0)
        self.assertNotIn("hungry", self.w.active)

    def test_settings_from_ini(self):
        s = load_alert_settings(Settings.from_text("[alerts]\nhungry_at = 60\nsound = none\nenabled = no\n"))
        self.assertEqual((s.hungry_at, s.sound, s.enabled), (60, "none", False))


class PetCommandTests(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry(include_plugins=False)
        self.ctx = make_ctx()

    def test_feed_and_play_are_logged(self):
        self.reg.dispatch("pet feed", self.ctx)
        self.reg.dispatch("pet play", self.ctx)
        log = "\n".join(self.reg.dispatch("pet log", self.ctx))
        self.assertIn("fed ration", log)
        self.assertIn("played spar", log)
        self.reg.dispatch("pet log clear", self.ctx)
        self.assertIn("empty", self.reg.dispatch("pet log", self.ctx)[0])

    def test_sim_and_alerts(self):
        self.reg.dispatch("pet sim hunger 90", self.ctx)
        text = "\n".join(self.reg.dispatch("pet alerts", self.ctx))
        self.assertIn("hungry", text)
        self.assertIn("active: hungry", text)

    def test_sim_rejects_bad_input(self):
        self.assertIn("usage", self.reg.dispatch("pet sim mood 3", self.ctx)[0])


class GpioTests(unittest.TestCase):
    def test_sim_outputs_inputs_and_tones(self):
        g = SimGpio(sleep=False)
        g.set_output(17, True)
        self.assertTrue(g.outputs[17])
        self.assertFalse(g.read_input(27))
        g.press(27)
        self.assertTrue(g.read_input(27))
        g.tone(18, 440.0, 0.1)
        g.tone(18, 0.0, 0.1)  # a rest makes no sound
        self.assertEqual([t[2] for t in g.tones], [440.0])

    def test_bad_pin(self):
        with self.assertRaises(GpioError):
            SimGpio().set_output(40, True)

    def test_auto_backend_in_sim_mode(self):
        gpio, note = open_gpio("sim")
        self.assertEqual((gpio.name, note), ("sim", ""))

    def test_real_mode_falls_back_to_sim_with_a_note(self):
        gpio, note = open_gpio("real", "sysfs")
        if Path("/sys/class/gpio/export").exists():
            self.skipTest("this machine has real sysfs GPIO")
        self.assertEqual(gpio.name, "sim")
        self.assertIn("sysfs", note)

    def test_sysfs_backend_with_fake_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "export").write_text("")
        for pin in (17, 27):
            (root / f"gpio{pin}").mkdir()
            (root / f"gpio{pin}" / "direction").write_text("in")
            (root / f"gpio{pin}" / "value").write_text("0")
        g = SysfsGpio(root=root)
        g.set_output(17, True)
        self.assertEqual((root / "gpio17" / "value").read_text(), "1")
        self.assertEqual((root / "gpio17" / "direction").read_text(), "out")
        (root / "gpio27" / "value").write_text("1\n")
        self.assertTrue(g.read_input(27))

    def test_named_pins_and_busy_buzzer(self):
        hw = Hardware(SimGpio(sleep=False), {"led": 17, "buzzer": 18})
        self.assertEqual(hw.pin("led", "led"), 17)
        self.assertEqual(hw.pin("22", "led"), 22)
        with self.assertRaises(GpioError):
            hw.pin("fan", "led")
        self.assertTrue(hw.beep())
        with hw.buzzer_lock:
            self.assertFalse(hw.beep())

    def test_gpio_command(self):
        reg = build_registry(include_plugins=False)
        ctx = make_ctx()
        get_hardware(ctx).gpio.sleep = False
        self.assertIn("ON", reg.dispatch("gpio led on", ctx)[0])
        self.assertIn("off", reg.dispatch("gpio led toggle", ctx)[0])
        self.assertIn("low", reg.dispatch("gpio read", ctx)[0])
        reg.dispatch("gpio sim press button", ctx)
        self.assertIn("HIGH", reg.dispatch("gpio read button", ctx)[0])
        self.assertIn("beep 440", reg.dispatch("gpio beep 440 10", ctx)[0])
        self.assertIn("freq", reg.dispatch("gpio beep 5 10", ctx)[0])
        self.assertIn("no pin named", reg.dispatch("gpio led on fan", ctx)[0])


class MusicTests(unittest.TestCase):
    def test_note_frequencies(self):
        self.assertAlmostEqual(note_freq("A", "", 4), 440.0)
        self.assertAlmostEqual(note_freq("C", "", 4), 261.63, places=1)
        self.assertAlmostEqual(note_freq("C", "#", 4), note_freq("D", "b", 4))

    def test_parse_lengths_headers_and_rests(self):
        song = parse_song("title: T\ntempo: 60\n# comment\nC4 | D4/8 E4/2. R/4 F#5\n")
        self.assertEqual(song.title, "T")
        self.assertEqual([n.name for n in song.notes], ["C4", "D4", "E4", "R", "F#5"])
        self.assertEqual([n.seconds for n in song.notes], [1.0, 0.5, 3.0, 1.0, 1.0])
        self.assertEqual(song.notes[3].freq, 0.0)

    def test_default_octave_and_length(self):
        song = parse_song("octave: 5\nlength: 8\ntempo: 120\nA B/4\n")
        self.assertAlmostEqual(song.notes[0].freq, 880.0)
        self.assertEqual([n.seconds for n in song.notes], [0.25, 0.5])

    def test_bad_note(self):
        with self.assertRaises(SongError):
            parse_song("C4 H4")
        with self.assertRaises(SongError):
            parse_song("C4/3")

    def test_bundled_songs_parse(self):
        folder = Path(__file__).resolve().parent.parent / "songs"
        names = sorted(p.stem for p in folder.glob("*.txt"))
        self.assertIn("ode_to_joy", names)
        for path in folder.glob("*.txt"):
            self.assertTrue(parse_song(path.read_text()).notes, path.name)

    def test_player_plays_every_note_on_the_buzzer(self):
        gpio = SimGpio(sleep=False)
        hw = Hardware(gpio, {"buzzer": 18})
        player = MusicPlayer(hw, gap=0.0)
        song = parse_song("tempo: 400\nC4/32 R/32 E4/32 G4/32")
        self.assertTrue(player.play(song, block=True))
        self.assertEqual(len(gpio.tones), 3)
        self.assertFalse(player.playing)
        self.assertFalse(hw.buzzer_lock.locked())

    def test_player_stop(self):
        hw = Hardware(SimGpio(sleep=True), {"buzzer": 18})
        player = MusicPlayer(hw)
        player.play(parse_song("tempo: 20\nC4/1 C4/1"))
        self.assertTrue(player.playing)
        player.stop()
        self.assertFalse(player.playing)
        self.assertFalse(hw.buzzer_lock.locked())

    def test_piano_roll(self):
        roll = piano_roll(parse_song("C4 E4 G4 R"), rows=3)
        self.assertEqual(roll, ["  █ ", " █  ", "█  ·"])

    def test_music_command(self):
        reg = build_registry(include_plugins=False)
        ctx = make_ctx()
        get_hardware(ctx).gpio.sleep = False
        self.assertIn("ode_to_joy", "\n".join(reg.dispatch("music list", ctx)))
        out = reg.dispatch("music play alert", ctx)  # headless: plays to the end
        self.assertEqual(out[-1], "done.")
        self.assertIn("no song", reg.dispatch("music play nope", ctx)[0])
        self.assertIn("nothing", reg.dispatch("music stop", ctx)[0])


if __name__ == "__main__":
    unittest.main()
