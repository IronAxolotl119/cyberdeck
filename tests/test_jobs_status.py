"""tests/test_jobs_status.py — background commands and the status line.

  * jobs.in_background: placeholder in the TUI, direct in headless mode,
    one job per key, failures reported, results swapped into the log
  * weather / iss / net check use it (no network: offline mode, a
    host on a closed localhost port)
  * statusline.fit: shortens and drops low-priority readings so the
    companion link stays readable next to a badge at 80 columns
"""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest

from cyberdeck import netcache
from cyberdeck.commands import build_registry
from cyberdeck.config import Settings
from cyberdeck.features import weather
from cyberdeck.jobs import Job, get_jobs, in_background, place_results
from cyberdeck.sensors import SensorReading
from cyberdeck.statusline import Item, fit, status_items
from cyberdeck.world import parse_world

from helpers import make_ctx, write_config


def wait_jobs(ctx, count: int = 1, seconds: float = 10.0) -> list[Job]:
    done: list[Job] = []
    end = time.monotonic() + seconds
    while len(done) < count and time.monotonic() < end:
        done += get_jobs(ctx).finished()
        time.sleep(0.02)
    return done


class JobTests(unittest.TestCase):
    def test_headless_runs_inline(self):
        ctx = make_ctx()
        self.assertEqual(in_background(ctx, "k", "thing", lambda: ["done"]), ["done"])

    def test_tui_returns_placeholder_then_result(self):
        ctx = make_ctx()
        ctx.interactive = True
        gate = threading.Event()
        lines = in_background(ctx, "k", "thing", lambda: gate.wait(5) and ["result 1", "result 2"])
        self.assertTrue(lines[0].startswith("thing: fetching…"))
        self.assertIn("still fetching", in_background(ctx, "k", "thing", lambda: ["x"])[0])
        log = ["> thing", lines[0], "> other"]
        gate.set()
        job = wait_jobs(ctx)[0]
        self.assertTrue(place_results(log, job))
        self.assertEqual(log, ["> thing", "result 1", "result 2", "> other"])
        self.assertFalse(place_results(["cleared"], job))

    def test_failure_is_reported(self):
        ctx = make_ctx()
        ctx.interactive = True

        def boom():
            raise RuntimeError("no route")

        in_background(ctx, "b", "boom", boom)
        job = wait_jobs(ctx)[0]
        self.assertTrue(job.failed)
        self.assertIn("boom failed: no route", job.lines[0])


class BackgroundCommandTests(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx(write_config({"world.ini": "[network]\noffline = yes\n",
                                          "hosts.ini": f"[hosts]\nnothing = 127.0.0.1 {self._closed_port()}\n"}))
        self.ctx.interactive = True
        self.reg = build_registry(include_plugins=False)

    @staticmethod
    def _closed_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_weather_waits_off_thread_unless_fresh(self):
        first = self.reg.dispatch("weather", self.ctx)
        self.assertIn("fetching…", first[0])
        self.assertIn("offline", "\n".join(wait_jobs(self.ctx)[0].lines))
        # Fresh cache: answered immediately, no job.
        url = weather.weather_url(parse_world(Settings.load("world.ini", self.ctx.config_dir)))
        sample = {"utc_offset_seconds": 0, "current": {"temperature_2m": 70, "weather_code": 0}}
        netcache.write_cache(self.ctx.data_path("cache"), url, json.dumps(sample), time.time())
        self.ctx.services.pop("weather_memo", None)
        self.assertIn("clear", "\n".join(self.reg.dispatch("weather", self.ctx)))

    def test_iss_and_net_check_run_off_thread(self):
        for cmd, title in (("iss", "iss passes"), ("iss refresh", "iss refresh"), ("net check", "net check")):
            self.assertTrue(self.reg.dispatch(cmd, self.ctx)[0].startswith(f"{title}: fetching…"), cmd)
        jobs = {j.title: j for j in wait_jobs(self.ctx, 3)}
        self.assertIn("ISS PASSES", jobs["iss passes"].lines[0])
        self.assertIn("orbit data", jobs["iss refresh"].lines[0])
        self.assertIn("hosts up", jobs["net check"].lines[-1])
        self.assertEqual(self.reg.dispatch("iss tle", self.ctx)[0][:3], "ISS")   # cache only: instant


class StatusLineTests(unittest.TestCase):
    def reading(self, **kw):
        base = dict(hostname="deck", uptime_s=1, cpu_temp_c=47.7, cpu_percent=17, mem_percent=34,
                    battery_percent=72, charging=True, wifi_ssid="DECK-NET", wifi_dbm=-53)
        base.update(kw)
        return SensorReading(**base)

    def test_everything_fits_when_wide(self):
        line = fit(status_items(self.reading(), "LINK UP · TCP 7777"), 120)
        self.assertEqual(line, "47.7C  CPU 17%  MEM 34%  BAT 72%+  DECK-NET -53dBm  LINK UP · TCP 7777")

    def test_80_columns_with_a_badge_keeps_the_link(self):
        width = 78 - len(" ! HUNGRY ") - 2
        line = fit(status_items(self.reading(), "LINK UP · TCP 7777"), width)
        self.assertLessEqual(len(line), width)
        self.assertTrue(line.endswith("LINK UP · TCP 7777"))
        self.assertIn("BAT 72%+", line)
        self.assertIn("-53dBm", line)            # wifi shortened, not cut mid-word

    def test_very_narrow_drops_low_priority_items(self):
        line = fit(status_items(self.reading(), "LINK UP · TCP 7777"), 30)
        self.assertLessEqual(len(line), 30)
        self.assertIn("LINK UP", line)
        self.assertNotIn("M34%", line)            # memory goes first

    def test_fit_order_and_ties(self):
        items = [Item(1, ["aaaa", "a"]), Item(5, ["bbbb", "b"]), Item(1, ["cccc"])]
        self.assertEqual(fit(items, 100), "aaaa  bbbb  cccc")
        self.assertEqual(fit(items, 13), "a  bbbb  cccc")
        self.assertEqual(fit(items, 10), "a  b  cccc")   # shorten everything before dropping
        self.assertEqual(fit(items, 9), "a  b")          # on a tie the later item goes first
        self.assertEqual(fit([Item(1, ["toolongforit"])], 4), "tool")

    def test_missing_readings(self):
        r = self.reading(cpu_temp_c=None, cpu_percent=None, battery_percent=None, wifi_dbm=None, wifi_ssid=None)
        line = fit(status_items(r, "LINK DOWN · SIM"), 40)
        self.assertIn("LINK DOWN", line)


if __name__ == "__main__":
    unittest.main()
