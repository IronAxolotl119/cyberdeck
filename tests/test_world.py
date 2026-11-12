"""tests/test_world.py — weather, sun, moon, ISS, and the network cache.

Nothing here touches the internet: downloads are faked or the deck is
put in offline mode. The reference numbers were checked by hand on
2026-11-12 against Open-Meteo (sunrise/sunset), wheretheiss.at (ISS
position), and Heavens-Above (ISS passes over Phoenix).
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cyberdeck import astro, netcache, orbit
from cyberdeck.commands import build_registry
from cyberdeck.config import Settings
from cyberdeck.features import iss, weather
from cyberdeck.world import SkyRefresher, WorldSettings, duration, local_zone, parse_world

from helpers import make_ctx, write_config

MST = timezone(timedelta(hours=-7))
PHX = (33.4484, -112.0740)
ISS_TLE = """ISS (ZARYA)
1 25544U 98067A   26316.17460339  .00008107  00000+0  15334-3 0  9995
2 25544  51.6324 287.7668 0007312  16.4277 343.6947 15.49327857594697"""

SAMPLE = {
    "utc_offset_seconds": -25200,
    "current_units": {"temperature_2m": "°F", "wind_speed_10m": "mp/h"},
    "current": {"time": "2026-11-12T11:00", "temperature_2m": 79.5, "relative_humidity_2m": 27,
                "apparent_temperature": 76.3, "weather_code": 0, "wind_speed_10m": 4.5,
                "wind_direction_10m": 114, "is_day": 1},
    "daily": {"time": ["2026-11-12", "2026-11-13"], "weather_code": [3, 63],
              "temperature_2m_max": [85.6, 80.3], "temperature_2m_min": [57.7, 57.9],
              "precipitation_probability_max": [1, 46],
              "sunrise": ["2026-11-12T06:57", "2026-11-13T06:58"],
              "sunset": ["2026-11-12T17:27", "2026-11-13T17:26"]},
}

OFFLINE = "[network]\noffline = yes\n"


def near(a: datetime, b: datetime, seconds: float) -> bool:
    return abs((a - b).total_seconds()) <= seconds


# --- network cache ------------------------------------------------------------------

class NetCacheTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.calls = 0

    def ok(self, url, timeout):
        self.calls += 1
        return "fresh body"

    @staticmethod
    def down(url, timeout):
        raise OSError("no route to host")

    def test_live_then_cached(self):
        first = netcache.fetch("u", self.dir, 60, now=1000, downloader=self.ok)
        self.assertEqual((first.status, first.text), ("live", "fresh body"))
        again = netcache.fetch("u", self.dir, 60, now=1030, downloader=self.ok)
        self.assertEqual(again.status, "cached")
        self.assertEqual(self.calls, 1)

    def test_old_cache_is_refreshed(self):
        netcache.fetch("u", self.dir, 60, now=1000, downloader=self.ok)
        self.assertEqual(netcache.fetch("u", self.dir, 60, now=2000, downloader=self.ok).status, "live")
        self.assertEqual(self.calls, 2)

    def test_offline_uses_stale_copy(self):
        netcache.fetch("u", self.dir, 60, now=1000, downloader=self.ok)
        got = netcache.fetch("u", self.dir, 60, now=1000 + 7200, downloader=self.down)
        self.assertEqual((got.status, got.text), ("stale", "fresh body"))
        self.assertIn("offline", got.age_label(now=1000 + 7200))
        self.assertIn("2.0h", got.age_label(now=1000 + 7200))

    def test_offline_with_nothing_cached(self):
        got = netcache.fetch("u", self.dir, 60, downloader=self.down)
        self.assertFalse(got.ok)
        self.assertEqual(got.status, "offline")
        self.assertIn("no route", got.error)

    def test_force_skips_fresh_cache(self):
        netcache.fetch("u", self.dir, 60, now=1000, downloader=self.ok)
        netcache.fetch("u", self.dir, 60, now=1001, force=True, downloader=self.ok)
        self.assertEqual(self.calls, 2)

    def test_corrupt_cache_is_ignored(self):
        netcache.write_cache(self.dir, "u", "x", 1)
        next(self.dir.glob("*.json")).write_text("{not json")
        self.assertIsNone(netcache.read_cache(self.dir, "u"))


# --- sun and moon ----------------------------------------------------------------------

class SunMoonTests(unittest.TestCase):
    def test_phoenix_sunrise_sunset_match_open_meteo(self):
        rise, noon, sets = astro.sun_times(date(2026, 11, 12), *PHX)
        self.assertTrue(near(rise, datetime(2026, 11, 12, 6, 57, tzinfo=MST), 120))
        self.assertTrue(near(sets, datetime(2026, 11, 12, 17, 27, tzinfo=MST), 120))
        self.assertTrue(rise < noon < sets)

    def test_summer_days_are_longer(self):
        r1, _, s1 = astro.sun_times(date(2026, 6, 21), *PHX)
        r2, _, s2 = astro.sun_times(date(2026, 12, 21), *PHX)
        self.assertGreater((s1 - r1).total_seconds(), (s2 - r2).total_seconds() + 3 * 3600)

    def test_polar_night_has_no_sunrise(self):
        rise, _, sets = astro.sun_times(date(2026, 12, 21), 80.0, 15.0)
        self.assertIsNone(rise)
        self.assertIsNone(sets)

    def test_sun_elevation_day_and_night(self):
        self.assertGreater(astro.sun_elevation(datetime(2026, 11, 12, 12, 12, tzinfo=MST), *PHX), 35)
        self.assertLess(astro.sun_elevation(datetime(2026, 11, 12, 0, 0, tzinfo=MST), *PHX), -30)

    def test_moon_phase_at_known_moons(self):
        # New moon 2000-01-06 18:14 UTC; full moon 2026-11-24 14:53 UTC.
        new = astro.moon_phase(datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc))
        self.assertLess(min(new, 1 - new), 0.01)   # 0.999 and 0.001 are both "new"
        full = astro.moon_phase(datetime(2026, 11, 24, 14, 53, tzinfo=timezone.utc))
        self.assertAlmostEqual(full, 0.5, delta=0.01)
        self.assertGreater(astro.moon_illumination(full), 0.99)
        self.assertEqual(astro.moon_name(full), "full moon")

    def test_next_full_moon_within_a_few_hours(self):
        start = datetime(2026, 11, 12, 18, tzinfo=timezone.utc)
        self.assertTrue(near(astro.next_phase(start, 0.5), datetime(2026, 11, 24, 14, 53, tzinfo=timezone.utc), 3 * 3600))

    def test_compass(self):
        self.assertEqual([astro.compass(a) for a in (0, 22.5, 90, 200, 359)], ["N", "NNE", "E", "SSW", "N"])

    def test_moon_art_lights_the_right_side(self):
        new, full = weather.moon_art(0.0), weather.moon_art(0.5)
        self.assertNotIn("@", "".join(new))
        self.assertNotIn(".", "".join(full))
        waxing = weather.moon_art(0.25)
        middle = waxing[len(waxing) // 2]
        self.assertTrue(middle.strip().startswith(".") and middle.strip().endswith("@"))
        waning = weather.moon_art(0.75)[len(waxing) // 2].strip()
        self.assertTrue(waning.startswith("@") and waning.endswith("."))
        self.assertEqual(weather.moon_art(0.25, southern=True)[3].strip()[0], "@")


# --- ISS orbit ----------------------------------------------------------------------------

class OrbitTests(unittest.TestCase):
    def setUp(self):
        self.tle = orbit.parse_tle(ISS_TLE)
        self.sat = orbit.Satellite(self.tle)

    def test_parse_fields(self):
        t = self.tle
        self.assertEqual((t.name, t.catalog), ("ISS (ZARYA)", 25544))
        self.assertTrue(near(t.epoch, datetime(2026, 11, 12, 4, 11, 26, tzinfo=timezone.utc), 1))
        self.assertAlmostEqual(math.degrees(t.inclination), 51.6324, places=4)
        self.assertAlmostEqual(t.eccentricity, 0.0007312)
        self.assertAlmostEqual(t.bstar, 0.15334e-3)
        self.assertAlmostEqual(orbit._implied_decimal("-11606-4"), -0.11606e-4)

    def test_checksum_is_enforced(self):
        broken = ISS_TLE.replace("51.6324", "51.6325")
        with self.assertRaises(orbit.TleError):
            orbit.parse_tle(broken)
        orbit.parse_tle(broken, check=False)
        with self.assertRaises(orbit.TleError):
            orbit.parse_tle("<html>rate limited</html>")

    def test_position_matches_live_tracker(self):
        # wheretheiss.at at 1794506780: -3.1634, -41.0657, 415.3 km
        lat, lon, alt = self.sat.subpoint(datetime.fromtimestamp(1794506780, timezone.utc))
        self.assertAlmostEqual(lat, -3.1634, delta=0.3)
        self.assertAlmostEqual(lon, -41.0657, delta=0.3)
        self.assertAlmostEqual(alt, 415.3, delta=5)

    def test_passes_match_heavens_above(self):
        start = datetime(2026, 11, 12, 18, 6, tzinfo=timezone.utc)
        passes = orbit.find_passes(self.sat, *PHX, start, hours=48, min_elevation=10)
        visible = [p for p in passes if p.visible]
        # Heavens-Above: 19:03:39 MST max 19°, and Nov 13 18:15:13 max 30°
        self.assertTrue(near(visible[0].rise, datetime(2026, 11, 12, 19, 3, 39, tzinfo=MST), 30))
        self.assertAlmostEqual(visible[0].max_elevation, 19, delta=2)
        self.assertTrue(near(visible[1].rise, datetime(2026, 11, 13, 18, 15, 13, tzinfo=MST), 30))
        self.assertAlmostEqual(visible[1].max_elevation, 30, delta=2)
        self.assertEqual(astro.compass(visible[0].rise_azimuth), "W")
        for p in passes:
            self.assertTrue(p.rise < p.peak < p.set)
            self.assertGreaterEqual(p.max_elevation, 10)
        # Daytime passes are never marked visible.
        self.assertFalse(any(p.visible for p in passes if 8 <= p.rise.astimezone(MST).hour < 16))

    def test_shadow(self):
        # Opposite the sun and low: in Earth's shadow.
        when = datetime(2026, 11, 12, 18, tzinfo=timezone.utc)
        results = {self.sat.sunlit(when + timedelta(minutes=m)) for m in range(0, 95, 5)}
        self.assertEqual(results, {True, False})


# --- weather parsing and display ---------------------------------------------------------

class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.wx = weather.parse_open_meteo(json.dumps(SAMPLE))
        self.ws = WorldSettings()

    def test_parse(self):
        w = self.wx
        self.assertEqual((w.temperature, w.code, w.label, w.wind_unit), (79.5, 0, "clear", "mph"))
        self.assertEqual(len(w.days), 2)
        self.assertEqual(w.days[1].rain_chance, 46)
        self.assertEqual(w.days[0].sunrise, datetime(2026, 11, 12, 6, 57, tzinfo=MST))

    def test_error_reply(self):
        with self.assertRaises(ValueError):
            weather.parse_open_meteo('{"error": true, "reason": "Latitude must be in range"}')

    def test_codes_and_icons(self):
        self.assertEqual(weather.describe_code(63), ("rain", "rain"))
        self.assertEqual(weather.describe_code(95)[1], "storm")
        self.assertEqual(weather.describe_code(1234)[0], "unknown")
        self.assertEqual(weather.icon_for(0, is_day=False), weather.ICONS["moon"])
        for art in weather.ICONS.values():
            self.assertEqual(len(art), 5)
            self.assertTrue(all(ch.isascii() for line in art for ch in line))

    def test_url_uses_settings(self):
        ws = WorldSettings(latitude=40.0, longitude=-105.25, units="celsius", wind_units="kmh")
        url = weather.weather_url(ws)
        self.assertIn("latitude=40.0000", url)
        self.assertIn("temperature_unit=celsius", url)
        self.assertIn("wind_speed_unit=kmh", url)

    def test_report_with_and_without_data(self):
        now = datetime(2026, 11, 12, 11, 10, tzinfo=MST)
        lines = weather.weather_lines(self.wx, netcache.Fetched("x", "live", 0), self.ws, now)
        text = "\n".join(lines)
        self.assertIn("80°F", text)
        self.assertIn("today", text)
        self.assertIn("46% rain", text)
        self.assertIn("06:57 [", text)
        offline = "\n".join(weather.weather_lines(None, netcache.Fetched(None, "offline"), self.ws, now))
        self.assertIn("offline", offline)
        self.assertIn("moon", offline)          # sun and moon still work offline
        self.assertIn("06:5", offline)

    def test_sun_clock_marker(self):
        zone = local_zone("America/Phoenix")
        sd = weather.sun_day(date(2026, 11, 12), self.ws, self.wx)
        self.assertEqual(sd.source, "forecast")
        noonish = weather.sun_clock(sd, datetime(2026, 11, 12, 12, 12, tzinfo=MST), zone, 40)
        track = noonish[noonish.index("[") + 1:noonish.index("]")]
        self.assertAlmostEqual(track.index("o") / len(track), 0.5, delta=0.06)
        night = weather.sun_clock(sd, datetime(2026, 11, 12, 22, 0, tzinfo=MST), zone, 40)
        self.assertNotIn("o", night[6:-6])
        tomorrow = weather.sun_day(date(2026, 11, 13), self.ws, self.wx)
        self.assertIn("sunrise in", weather.sun_status(sd, tomorrow, datetime(2026, 11, 12, 22, 0, tzinfo=MST)))
        self.assertEqual(weather.sun_day(date(2027, 1, 1), self.ws, self.wx).source, "calculated")


# --- settings and helpers ---------------------------------------------------------------------

class WorldSettingsTests(unittest.TestCase):
    def test_defaults_are_phoenix(self):
        ws = parse_world(Settings.from_text(""))
        self.assertEqual(ws.place, "Phoenix, AZ")
        self.assertAlmostEqual(ws.latitude, 33.4484)

    def test_shipped_world_ini_parses(self):
        ws = parse_world(Settings.load("world.ini"))
        self.assertEqual(ws.timezone, "America/Phoenix")
        self.assertEqual(ws.iss_min_elevation, 10)

    def test_overrides_and_limits(self):
        ws = parse_world(Settings.from_text(
            "[location]\nname = Boulder\nlatitude = 40.01\n[weather]\nunits = celsius\nforecast_days = 30\n"))
        self.assertEqual((ws.place, ws.degree, ws.forecast_days), ("Boulder", "C", 7))

    def test_zone_fallback_and_duration(self):
        self.assertIsNotNone(local_zone("Not/AZone"))
        self.assertEqual(duration(5400), "1h30m")
        self.assertEqual(duration(300), "5m")

    def test_refresher_only_runs_due_jobs(self):
        r = SkyRefresher()
        r.add("a", 60, lambda c: None)
        r.add("a", 60, lambda c: None)
        self.assertEqual(len(r.jobs), 1)
        r.last_run["a"] = 1000
        self.assertEqual(r.due(1030), [])
        self.assertEqual(len(r.due(1061)), 1)


# --- commands, offline ------------------------------------------------------------------------

class OfflineCommandTests(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx(write_config({"world.ini": OFFLINE}))
        self.reg = build_registry(include_plugins=False)

    def test_weather_offline_without_cache(self):
        text = "\n".join(self.reg.dispatch("weather", self.ctx))
        self.assertIn("offline", text)
        self.assertIn("moon", text)

    def test_weather_offline_uses_saved_copy(self):
        url = weather.weather_url(parse_world(Settings.load("world.ini", self.ctx.config_dir)))
        netcache.write_cache(self.ctx.data_path("cache"), url, json.dumps(SAMPLE), 1.0)
        text = "\n".join(self.reg.dispatch("wx", self.ctx))
        self.assertIn("clear", text)
        self.assertIn("offline — data from", text)

    def test_iss_falls_back_to_bundled_tle(self):
        data = iss.get_tle(self.ctx)
        self.assertIsNotNone(data.tle)
        self.assertIn("bundled", data.source)
        lines = self.reg.dispatch("iss tle", self.ctx)
        self.assertIn("25544", lines[0])

    def test_iss_commands_run(self):
        self.assertIn("ISS PASSES", self.reg.dispatch("iss", self.ctx)[0])
        self.assertIn("visible only", self.reg.dispatch("iss visible", self.ctx)[0])
        self.assertTrue(any("altitude" in l for l in self.reg.dispatch("iss now", self.ctx)))
        self.assertIn("usage", self.reg.dispatch("iss sideways", self.ctx)[0])

    def test_sun_and_moon_commands(self):
        self.assertTrue(any("sunrise" in l for l in self.reg.dispatch("sun", self.ctx)))
        self.assertTrue(any("sunrise" in l for l in self.reg.dispatch("sun tomorrow", self.ctx)))
        self.assertTrue(any("MOON" in l for l in self.reg.dispatch("moon", self.ctx)))

    def test_sky_pane_never_needs_network(self):
        rows = weather.sky_pane(self.ctx, 30, 8)
        text = "\n".join("".join(seg for seg, _ in row) for row in rows)
        self.assertIn("not fetched", text)
        self.assertIn("moon", text)
        self.assertIn("ISS", text)

    def test_stale_tle_warning(self):
        ws = WorldSettings(tle_warn_days=0.0, offline=True)   # never touch the network in tests
        self.ctx.services["world_settings"] = ws
        self.assertIn("days old", iss.get_tle(self.ctx).warning)


class IssWatcherTests(unittest.TestCase):
    def test_alert_once_for_visible_pass_soon(self):
        sat = orbit.Satellite(orbit.parse_tle(ISS_TLE))
        start = datetime(2026, 11, 12, 18, 6, tzinfo=timezone.utc)
        passes = orbit.find_passes(sat, *PHX, start, hours=30)
        target = next(p for p in passes if p.visible)
        w = iss.IssWatcher(alert_minutes=5)
        self.assertEqual(w.due(passes, target.rise - timedelta(minutes=10)), [])
        due = w.due(passes, target.rise - timedelta(minutes=3))
        self.assertEqual(due, [target])
        w.announced.add(target.rise)
        self.assertEqual(w.due(passes, target.rise - timedelta(minutes=2)), [])


if __name__ == "__main__":
    unittest.main()
