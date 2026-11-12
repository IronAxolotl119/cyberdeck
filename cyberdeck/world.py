"""world.py — shared bits for the "outside world" features.

  WorldSettings / load_world()   config/world.ini (where you are, units,
                                 how often to refresh, ISS options)
  local_zone(), clock()          show times in your timezone
  SkyRefresher                   a tiny background service: while the
                                 TUI runs it re-downloads weather and the
                                 ISS orbit when the cache gets old, on a
                                 separate thread so the screen never waits

The actual features are cyberdeck/features/weather.py (weather, sun,
moon, the "sky" panel) and cyberdeck/features/iss.py.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from cyberdeck import netcache
from cyberdeck.config import Settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context


# --- settings -----------------------------------------------------------------

@dataclass
class WorldSettings:
    place: str = "Phoenix, AZ"
    latitude: float = 33.4484
    longitude: float = -112.0740
    timezone: str = "America/Phoenix"
    offline: bool = False            # never touch the network
    background: bool = True          # refresh caches while the TUI runs
    timeout: float = 12.0
    units: str = "fahrenheit"        # or celsius
    wind_units: str = "mph"          # or kmh
    weather_minutes: int = 30
    forecast_days: int = 3
    tle_url: str = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
    tle_hours: int = 12
    tle_warn_days: float = 7.0
    iss_hours: int = 48
    iss_min_elevation: float = 10.0
    iss_count: int = 6

    @property
    def degree(self) -> str:
        return "C" if self.units.startswith("c") else "F"


def parse_world(s: Settings) -> WorldSettings:
    d = WorldSettings()
    return WorldSettings(
        place=s.get_str("location", "name", d.place),
        latitude=s.get_float("location", "latitude", d.latitude),
        longitude=s.get_float("location", "longitude", d.longitude),
        timezone=s.get_str("location", "timezone", d.timezone),
        offline=s.get_bool("network", "offline", d.offline),
        background=s.get_bool("network", "background_refresh", d.background),
        timeout=s.get_float("network", "timeout", d.timeout),
        units=s.get_str("weather", "units", d.units).lower(),
        wind_units=s.get_str("weather", "wind_units", d.wind_units).lower(),
        weather_minutes=max(5, s.get_int("weather", "refresh_minutes", d.weather_minutes)),
        forecast_days=min(7, max(1, s.get_int("weather", "forecast_days", d.forecast_days))),
        tle_url=s.get_str("iss", "tle_url", d.tle_url),
        tle_hours=max(1, s.get_int("iss", "refresh_hours", d.tle_hours)),
        tle_warn_days=s.get_float("iss", "warn_after_days", d.tle_warn_days),
        iss_hours=min(240, max(1, s.get_int("iss", "search_hours", d.iss_hours))),
        iss_min_elevation=s.get_float("iss", "min_elevation", d.iss_min_elevation),
        iss_count=max(1, s.get_int("iss", "show", d.iss_count)),
    )


def load_world(ctx: "Context") -> WorldSettings:
    return ctx.service("world_settings", lambda: parse_world(Settings.load("world.ini", ctx.config_dir)))


def cache_dir(ctx: "Context") -> Path:
    return ctx.data_path("cache")


def fetch(ctx: "Context", url: str, max_age: float, force: bool = False) -> netcache.Fetched:
    """Cached download that honours `offline = yes`."""
    ws = load_world(ctx)
    if ws.offline:
        cached = netcache.read_cache(cache_dir(ctx), url)
        return (netcache.Fetched(cached[0], "stale", cached[1], "offline mode") if cached
                else netcache.Fetched(None, "offline", None, "offline mode"))
    return netcache.fetch(url, cache_dir(ctx), max_age, timeout=ws.timeout, force=force)


# --- time display ---------------------------------------------------------------

def local_zone(name: str) -> tzinfo:
    """The configured timezone, or the device's own if it can't be found."""
    if name and name.lower() not in {"local", "auto"}:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:  # missing tzdata or a typo
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def clock(when: datetime, zone: tzinfo, with_day: bool = False) -> str:
    local = when.astimezone(zone)
    return local.strftime("%a %H:%M" if with_day else "%H:%M")


def duration(seconds: float) -> str:
    """5400 -> '1h30m'; 300 -> '5m'."""
    seconds = max(0, int(seconds))
    h, m = divmod(seconds // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# --- background refresh --------------------------------------------------------

class SkyRefresher:
    """Runs registered refresh jobs on a thread when they are due.

    Jobs are (name, every_seconds, fn(ctx)). The TUI calls ui_tick every
    frame; we look at most once every `check_every` seconds and only
    one job runs at a time.
    """

    def __init__(self, check_every: float = 30.0) -> None:
        self.jobs: list[tuple[str, float, Callable[["Context"], object]]] = []
        self.last_run: dict[str, float] = {}
        self.check_every = check_every
        self._next_check = 0.0
        self._busy = threading.Lock()

    def add(self, name: str, every: float, fn: Callable[["Context"], object]) -> None:
        if all(job[0] != name for job in self.jobs):
            self.jobs.append((name, every, fn))

    def due(self, now: float) -> list[tuple[str, float, Callable]]:
        return [job for job in self.jobs if now - self.last_run.get(job[0], 0.0) >= job[1]]

    def ui_tick(self, ctx: "Context", now: float) -> None:
        if now < self._next_check or self._busy.locked():
            return
        self._next_check = now + self.check_every
        if load_world(ctx).offline or not load_world(ctx).background:
            return
        jobs = self.due(time.time())
        if not jobs:
            return

        def work() -> None:
            with self._busy:
                for name, _every, fn in jobs:
                    self.last_run[name] = time.time()
                    try:
                        fn(ctx)
                    except Exception:  # a refresh must never crash the deck
                        pass

        threading.Thread(target=work, name="sky-refresh", daemon=True).start()


def get_refresher(ctx: "Context") -> SkyRefresher:
    return ctx.service("sky_refresher", SkyRefresher)
