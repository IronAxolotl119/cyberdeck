"""iss.py — when the International Space Station flies over you.

  iss [passes]   the next passes above your horizon (alias: `iss`)
  iss visible    only the passes you can actually see (sunlit station,
                 dark sky — the bright moving "star" kind)
  iss now        where it is right now: over which lat/lon, how high,
                 and where to look from your location
  iss tle        show the orbit data being used and how old it is
  iss refresh    re-download the orbit data now

How it works: the deck downloads the ISS's TLE (orbit description) from
Celestrak and works out the passes itself (cyberdeck/orbit.py) — no
tracking API needed. The TLE is cached for `refresh_hours`; offline, the
cached one is used, and on a brand-new deck with no network, the copy in
text/iss_fallback.tle (accurate only for a week or two after it was
saved). The deck warns when the orbit data is getting old.

While the TUI runs it also puts a notice on the ticker a few minutes
before a visible pass (`alert_minutes` in config/world.ini).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from cyberdeck import astro, netcache
from cyberdeck.config import Settings, repo_root
from cyberdeck.orbit import Pass, Satellite, Tle, TleError, find_passes, parse_tle
from cyberdeck.world import cache_dir, clock, duration, fetch, get_refresher, load_world, local_zone

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

FALLBACK_FILE = "text/iss_fallback.tle"


# --- getting the orbit data -------------------------------------------------------------

@dataclass
class OrbitData:
    tle: Tle | None
    source: str      # what to tell the user about where it came from
    warning: str = ""


def get_tle(ctx: "Context", force: bool = False, network: bool = True) -> OrbitData:
    """Celestrak (cached) -> stale cache -> the bundled fallback file."""
    ws = load_world(ctx)
    if network:
        got = fetch(ctx, ws.tle_url, ws.tle_hours * 3600, force=force)
    else:
        cached = netcache.read_cache(cache_dir(ctx), ws.tle_url)
        got = netcache.Fetched(cached[0], "cached", cached[1]) if cached else netcache.Fetched(None, "offline")
    tle, source = None, ""
    if got.ok:
        try:
            tle, source = parse_tle(got.text or ""), f"Celestrak ({got.age_label()})"
        except TleError as exc:
            source = f"downloaded data unusable: {exc}"
    if tle is None:
        try:
            text = (repo_root() / FALLBACK_FILE).read_text(encoding="utf-8")
            why = f"download failed: {got.error}" if got.error else "no download yet"
            tle, source = parse_tle(text), f"bundled {FALLBACK_FILE} ({why})"
        except (OSError, TleError):
            return OrbitData(None, source or "no orbit data — connect once and run: iss refresh")
    age = tle.age_days(datetime.now(timezone.utc))
    warning = ""
    if age > ws.tle_warn_days:
        warning = f"orbit data is {age:.0f} days old — times may be off by minutes; run: iss refresh"
    return OrbitData(tle, source, warning)


def upcoming(ctx: "Context", now: datetime, network: bool = False) -> tuple[list[Pass], OrbitData]:
    """Passes from now on, memoised for 10 minutes (the scan costs ~0.3 s on a Pi Zero)."""
    ws = load_world(ctx)
    orbit = get_tle(ctx, network=network)
    if orbit.tle is None:
        return [], orbit
    key = (orbit.tle.epoch, ws.latitude, ws.longitude, ws.iss_min_elevation, ws.iss_hours)
    memo = ctx.services.get("iss_memo")
    if memo and memo[0] == key and (now - memo[1]).total_seconds() < 600:
        passes = memo[2]
    else:
        passes = find_passes(Satellite(orbit.tle), ws.latitude, ws.longitude, now - timedelta(minutes=15),
                             hours=ws.iss_hours, min_elevation=ws.iss_min_elevation)
        ctx.services["iss_memo"] = (key, now, passes)
    return [p for p in passes if p.set > now], orbit


# --- formatting ----------------------------------------------------------------------------

def pass_line(p: Pass, zone, now: datetime) -> str:
    when = clock(p.rise, zone, True)
    soon = ""
    if p.rise <= now <= p.set:
        soon = "  << OVERHEAD NOW"
    elif p.rise - now < timedelta(hours=1):
        soon = f"  in {duration((p.rise - now).total_seconds())}"
    star = "*" if p.visible else " "
    path = f"{astro.compass(p.rise_azimuth)}>{astro.compass(p.set_azimuth)}"
    return (f"  {when} {duration(p.duration.total_seconds()):>4} {p.max_elevation:>3.0f}°  "
            f"{path:<7} {star}{soon}")


def passes_lines(ctx: "Context", only_visible: bool = False, now: datetime | None = None) -> list[str]:
    ws = load_world(ctx)
    now = now or datetime.now(timezone.utc)
    zone = local_zone(ws.timezone)
    passes, orbit = upcoming(ctx, now, network=True)
    head = (f"ISS PASSES · {ws.place}" + (" · visible only" if only_visible else ""))
    if orbit.tle is None:
        return [head, "  " + orbit.source]
    shown = [p for p in passes if p.visible or not only_visible][:ws.iss_count]
    lines = [head, f"  next {ws.iss_hours}h, higher than {ws.iss_min_elevation:.0f}°",
             "  start      len peak  path", ]
    lines += [pass_line(p, zone, now) for p in shown] or ["  (no passes in that window)"]
    lines += ["", "  * = visible: the station is sunlit and your",
              "  sky is dark. Look for a bright, steady,",
              "  fast-moving star.",
              f"  orbit: {orbit.source}"]
    if orbit.warning:
        lines.append("  ! " + orbit.warning)
    return lines


def now_lines(ctx: "Context", now: datetime | None = None) -> list[str]:
    ws = load_world(ctx)
    now = now or datetime.now(timezone.utc)
    orbit = get_tle(ctx)
    if orbit.tle is None:
        return ["ISS NOW", "  " + orbit.source]
    sat = Satellite(orbit.tle)
    lat, lon, alt = sat.subpoint(now)
    el, az, rng = astro.look_angles(sat.ecef(now), ws.latitude, ws.longitude)
    where = "above your horizon — look " + astro.compass(az) if el > 0 else "below your horizon"
    lines = [
        "ISS NOW",
        f"  over      {abs(lat):.1f}°{'N' if lat >= 0 else 'S'}  {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}",
        f"  altitude  {alt:.0f} km   ({'in sunlight' if sat.sunlit(now) else 'in Earth shadow'})",
        f"  from {ws.place}: {el:.0f}° elevation, {az:.0f}° ({astro.compass(az)}), {rng:.0f} km away",
        f"  {where}",
        f"  orbit: {orbit.source}",
    ]
    if orbit.warning:
        lines.append("  ! " + orbit.warning)
    return lines


def tle_lines(ctx: "Context") -> list[str]:
    orbit = get_tle(ctx, network=False)
    if orbit.tle is None:
        return [orbit.source]
    t = orbit.tle
    age = t.age_days(datetime.now(timezone.utc))
    return [f"{t.name} (catalog {t.catalog})",
            f"  epoch {t.epoch:%Y-%m-%d %H:%M} UTC ({age:.1f} days old)",
            f"  {t.mean_motion:.4f} orbits/day, inclination {t.inclination * 57.29578:.2f}°",
            f"  source: {orbit.source}"] + (["  ! " + orbit.warning] if orbit.warning else [])


def next_pass_line(ctx: "Context", now: datetime) -> tuple[str, str]:
    """One line for the sky panel; uses cached data only."""
    passes, orbit = upcoming(ctx, now, network=False)
    if orbit.tle is None:
        return "ISS: no orbit data", "dim"
    zone = local_zone(load_world(ctx).timezone)
    visible = [p for p in passes if p.visible]
    p = visible[0] if visible else (passes[0] if passes else None)
    if p is None:
        return "ISS: no pass soon", "dim"
    if p.rise <= now <= p.set:
        return f"ISS OVERHEAD {astro.compass(p.rise_azimuth)}->{astro.compass(p.set_azimuth)}", "alert"
    star = "*" if p.visible else ""
    return (f"ISS {clock(p.rise, zone, True)} {p.max_elevation:.0f}°{star}",
            "ok" if p.visible else "dim")


# --- ticker alert before a visible pass -------------------------------------------------------

class IssWatcher:
    """Puts 'ISS visible in 5m' on the ticker while the TUI runs."""

    def __init__(self, alert_minutes: float) -> None:
        self.alert_minutes = alert_minutes
        self.announced: set[datetime] = set()
        self._next_check = 0.0

    def due(self, passes: list[Pass], now: datetime) -> list[Pass]:
        window = timedelta(minutes=self.alert_minutes)
        return [p for p in passes if p.visible and p.rise not in self.announced and now <= p.rise <= now + window]

    def ui_tick(self, ctx: "Context", now_ts: float) -> None:
        if self.alert_minutes <= 0 or now_ts < self._next_check:
            return
        self._next_check = now_ts + 20
        now = datetime.now(timezone.utc)
        passes, _ = upcoming(ctx, now, network=False)
        for p in self.due(passes, now):
            self.announced.add(p.rise)
            zone = local_zone(load_world(ctx).timezone)
            ctx.notify(f"ISS visible at {clock(p.rise, zone)}: look {astro.compass(p.rise_azimuth)}, "
                       f"up to {p.max_elevation:.0f}°", "warn")


def start_iss(ctx: "Context"):
    """TUI startup: TLE refresh job + the pass alert."""
    ws = load_world(ctx)
    get_refresher(ctx).add("iss_tle", ws.tle_hours * 3600, lambda c: get_tle(c))
    minutes = Settings.load("world.ini", ctx.config_dir).get_float("iss", "alert_minutes", 5)
    return ctx.service("iss_watcher", lambda: IssWatcher(minutes))


def register(reg: "Registry") -> None:
    from cyberdeck.features import UI_STARTUP

    if start_iss not in UI_STARTUP:
        UI_STARTUP.append(start_iss)

    @reg.command("iss", "ISS passes over you, where it is now, orbit data.",
                 usage="iss [passes|visible|now|tle|refresh]",
                 subcommands=("passes", "visible", "now", "tle", "refresh"))
    def cmd_iss(ctx: "Context", args: list[str]) -> list[str]:
        action = args[0].lower() if args else "passes"
        if action == "now":
            return now_lines(ctx)
        if action == "tle":
            return tle_lines(ctx)
        if action == "refresh":
            orbit = get_tle(ctx, force=True)
            ctx.services.pop("iss_memo", None)
            return [f"orbit data: {orbit.source}"] + ([orbit.warning] if orbit.warning else [])
        if action in {"passes", "visible"}:
            return passes_lines(ctx, only_visible=action == "visible")
        return ["usage: iss [passes|visible|now|tle|refresh]"]
