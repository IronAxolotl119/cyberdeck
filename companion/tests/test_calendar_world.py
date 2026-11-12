"""Real-date helpers (holidays, birthdays, time of day) and sensors."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from pet import calendar as cal
from pet.world import World, read_cpu_temp, simulated_temp, weather_condition


class CalendarTests(unittest.TestCase):
    def test_holidays(self) -> None:
        self.assertEqual(cal.holiday_for(date(2026, 10, 31)), "halloween")
        self.assertEqual(cal.holiday_for(date(2026, 12, 25)), "christmas")
        self.assertEqual(cal.holiday_for(date(2027, 1, 1)), "new_year")
        self.assertEqual(cal.holiday_for(date(2026, 2, 14)), "valentines")
        self.assertEqual(cal.holiday_for(date(2026, 3, 14)), "pi_day")
        self.assertEqual(cal.holiday_for(date(2026, 11, 26)), "thanksgiving")
        self.assertEqual(cal.holiday_for(date(2026, 9, 13)), "programmers_day")
        self.assertEqual(cal.holiday_for(date(2028, 2, 29)), "leap_day")
        self.assertEqual(cal.holiday_for(date(2026, 2, 13)), "valentines")
        self.assertIsNone(cal.holiday_for(date(2026, 11, 12)))
        self.assertEqual(cal.holiday_for(date(2026, 3, 13)), "friday_13")

    def test_thanksgiving_is_fourth_thursday(self) -> None:
        self.assertEqual(cal.thanksgiving(2026), date(2026, 11, 26))
        self.assertEqual(cal.thanksgiving(2027), date(2027, 11, 25))

    def test_birthday_and_hatchday(self) -> None:
        self.assertTrue(cal.is_birthday(date(2025, 6, 1), date(2026, 6, 1)))
        self.assertFalse(cal.is_birthday(date(2026, 6, 1), date(2026, 6, 1)))
        self.assertTrue(cal.is_birthday(date(2024, 2, 29), date(2025, 2, 28)))
        self.assertTrue(cal.is_hatchday(date(2026, 1, 15), date(2026, 3, 15)))
        self.assertFalse(cal.is_hatchday(date(2025, 1, 15), date(2026, 1, 15)))  # that is the birthday
        self.assertTrue(cal.is_hatchday(date(2026, 1, 31), date(2026, 2, 28)))

    def test_time_of_day(self) -> None:
        self.assertEqual(cal.time_of_day(datetime(2026, 1, 1, 7)), "morning")
        self.assertEqual(cal.time_of_day(datetime(2026, 1, 1, 15)), "afternoon")
        self.assertEqual(cal.time_of_day(datetime(2026, 1, 1, 23)), "night")
        self.assertTrue(cal.is_late_night(datetime(2026, 1, 1, 2)))

    def test_costumes(self) -> None:
        self.assertEqual(cal.costume_text("halloween"), "🎃")
        self.assertEqual(cal.costume_text("halloween", ascii_mode=True), "{o}")
        self.assertEqual(cal.costume_text(None), "")
        for key in cal.COSTUMES:
            self.assertTrue(cal.costume_text(key, ascii_mode=True).isascii(), key)


class WorldTests(unittest.TestCase):
    def test_weather_codes(self) -> None:
        self.assertEqual(weather_condition(0), "sunny")
        self.assertEqual(weather_condition(3), "cloudy")
        self.assertEqual(weather_condition(45), "fog")
        self.assertEqual(weather_condition(63), "rain")
        self.assertEqual(weather_condition(81), "rain")
        self.assertEqual(weather_condition(73), "snow")
        self.assertEqual(weather_condition(95), "storm")

    def test_weather_off_without_location(self) -> None:
        world = World(weather_fetch=lambda lat, lon: {"weathercode": 0}, threaded=False)
        self.assertIsNone(world.refresh_weather(1000.0))

    def test_weather_fetch_and_offline_fallback(self) -> None:
        world = World(lat=1.0, lon=2.0, weather_fetch=lambda lat, lon: {"weathercode": 61, "temperature": 9}, threaded=False)
        reading = world.refresh_weather(1000.0)
        self.assertEqual(reading["condition"], "rain")

        def boom(lat, lon):
            raise OSError("offline")

        offline = World(lat=1.0, lon=2.0, weather_fetch=boom, threaded=False)
        self.assertIsNone(offline.refresh_weather(1000.0))

    def test_cpu_temp_sensor_and_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "temp"
            path.write_text("71234\n")
            self.assertAlmostEqual(read_cpu_temp(path), 71.234)
            self.assertIsNone(read_cpu_temp(Path(tmp) / "missing"))
        sensor = World(temp_reader=lambda: 72.0)
        self.assertEqual(sensor.cpu_temp(0.0), (72.0, "sensor"))
        sim = World(temp_reader=lambda: None)
        value, source = sim.cpu_temp(0.0)
        self.assertEqual(source, "sim")
        self.assertAlmostEqual(value, simulated_temp(0.0))

    def test_broken_sensor_never_raises(self) -> None:
        def bad():
            raise RuntimeError("i2c bus fell off")

        self.assertEqual(World(temp_reader=bad).cpu_temp(0.0)[1], "sim")


if __name__ == "__main__":
    unittest.main()
