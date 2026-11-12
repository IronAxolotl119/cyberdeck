"""weather.py — weather, sunrise/sunset, and the moon, plus a "sky" panel.

  weather [refresh]   current conditions with an ASCII icon, a short
                      forecast, the sun clock, and the moon (alias: wx)
  sun [tomorrow]      sunrise, solar noon, sunset, day length, and a
                      bar showing where the sun is in today's arc
  moon                the moon's phase drawn in ASCII, how lit it is,
                      and the next full and new moons

Weather comes from Open-Meteo (free, no API key) for the place in
config/world.ini — Phoenix by default. Results are cached in
data/cache/ for `refresh_minutes`; offline, the last download is used
and marked as old. Sun and moon are calculated on the deck itself
(cyberdeck/astro.py), so they work with no network at all.

The "sky" panel (add `sky` to [panes] side or tabs in layout.ini)
shows a compact version and never waits for the network: it reads the
cache, which a background refresher keeps fresh while the deck runs.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from cyberdeck import astro, netcache
from cyberdeck.activity import Row
from cyberdeck.panes import register_pane
from cyberdeck.world import (WorldSettings, cache_dir, clock, duration, fetch, get_refresher,
                             load_world, local_zone)

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

API = "https://api.open-meteo.com/v1/forecast"


# --- WMO weather codes -> words and icons ----------------------------------------

def describe_code(code: int | None) -> tuple[str, str]:
    """Open-Meteo's WMO code -> (label, icon name)."""
    table = [
        ((0,), "clear", "sun"), ((1,), "mostly clear", "partly"), ((2,), "partly cloudy", "partly"),
        ((3,), "overcast", "cloud"), ((45, 48), "fog", "fog"),
        ((51, 53, 55), "drizzle", "rain"), ((56, 57), "freezing drizzle", "rain"),
        ((61,), "light rain", "rain"), ((63,), "rain", "rain"), ((65,), "heavy rain", "rain"),
        ((66, 67), "freezing rain", "rain"), ((71, 73, 75, 77), "snow", "snow"),
        ((80, 81), "showers", "rain"), ((82,), "violent showers", "rain"), ((85, 86), "snow showers", "snow"),
        ((95,), "thunderstorm", "storm"), ((96, 99), "thunderstorm + hail", "storm"),
    ]
    for codes, label, icon in table:
        if code in codes:
            return label, icon
    return "unknown", "unknown"


# Plain ASCII so they draw on the Pi's console font. 5 rows x 13 columns.
ICONS: dict[str, list[str]] = {
    "sun":     ["    \\   /    ", "     .-.     ", "  - (   ) -  ", "     `-'     ", "    /   \\    "],
    "moon":    ["     _..     ", "   .' .'     ", "  |  (       ", "   '. '.     ", "     `''     "],
    "partly":  ["   \\  /      ", " _ /\"\".-.    ", "   \\_(   ).  ", "   /(___(__) ", "             "],
    "cloud":   ["             ", "     .--.    ", "  .-(    ).  ", " (___.__)__) ", "             "],
    "fog":     ["             ", " _ - _ - _ - ", "  _ - _ - _  ", " _ - _ - _ - ", "             "],
    "rain":    ["     .-.     ", "    (   ).   ", "   (___(__)  ", "    ' ' ' '  ", "   ' ' ' '   "],
    "snow":    ["     .-.     ", "    (   ).   ", "   (___(__)  ", "    *  *  *  ", "   *  *  *   "],
    "storm":   ["     .-.     ", "    (   ).   ", "   (___(__)  ", "    /_ /_    ", "     /  /    "],
    "unknown": ["    .-.      ", "     __)     ", "    (        ", "     `-'     ", "      *      "],
}


def icon_for(code: int | None, is_day: bool = True) -> list[str]:
    name = describe_code(code)[1]
    if name == "sun" and not is_day:
        name = "moon"
    return ICONS[name]


# --- parsing the API reply ------------------------------------------------------

@dataclass
class DayForecast:
    day: date
    code: int | None
    high: float | None
    low: float | None
    rain_chance: float | None
    sunrise: datetime | None = None
    sunset: datetime | None = None


@dataclass
class Weather:
    temperature: float | None
    feels_like: float | None
    humidity: float | None
    wind: float | None
    wind_direction: float | None
    code: int | None
    is_day: bool
    observed: datetime | None
    days: list[DayForecast] = field(default_factory=list)
    temp_unit: str = "°F"
    wind_unit: str = "mph"

    @property
    def label(self) -> str:
        return describe_code(self.code)[0]


def weather_url(ws: WorldSettings) -> str:
    query = {
        "latitude": f"{ws.latitude:.4f}", "longitude": f"{ws.longitude:.4f}",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
                   "wind_speed_10m,wind_direction_10m,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "timezone": "auto", "forecast_days": ws.forecast_days,
        "temperature_unit": "celsius" if ws.degree == "C" else "fahrenheit",
        "wind_speed_unit": "kmh" if ws.wind_units.startswith("k") else "mph",
    }
    return API + "?" + urlencode(query)


def _local_time(text: str | None, offset: timezone) -> datetime | None:
    """'2026-11-12T06:57' (the place's local time) -> aware datetime."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=offset)
    except ValueError:
        return None


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def parse_open_meteo(text: str) -> Weather:
    """Turn Open-Meteo's JSON into a Weather. Raises ValueError if unusable."""
    data = json.loads(text)
    if not isinstance(data, dict) or "current" not in data:
        raise ValueError(data.get("reason", "no current weather in reply") if isinstance(data, dict) else "bad reply")
    offset = timezone(timedelta(seconds=int(data.get("utc_offset_seconds", 0))))
    cur, units = data["current"], data.get("current_units", {})
    daily = data.get("daily", {})

    def col(name: str) -> list:
        return list(daily.get(name, []))

    days = []
    for i, d in enumerate(col("time")):
        def pick(name: str):
            values = col(name)
            return values[i] if i < len(values) else None
        days.append(DayForecast(
            day=date.fromisoformat(d), code=pick("weather_code"),
            high=_num(pick("temperature_2m_max")), low=_num(pick("temperature_2m_min")),
            rain_chance=_num(pick("precipitation_probability_max")),
            sunrise=_local_time(pick("sunrise"), offset), sunset=_local_time(pick("sunset"), offset)))
    return Weather(
        temperature=_num(cur.get("temperature_2m")), feels_like=_num(cur.get("apparent_temperature")),
        humidity=_num(cur.get("relative_humidity_2m")), wind=_num(cur.get("wind_speed_10m")),
        wind_direction=_num(cur.get("wind_direction_10m")), code=cur.get("weather_code"),
        is_day=bool(cur.get("is_day", 1)), observed=_local_time(cur.get("time"), offset), days=days,
        temp_unit=units.get("temperature_2m", "°F"),
        wind_unit=units.get("wind_speed_10m", "mph").replace("mp/h", "mph"))


def get_weather(ctx: "Context", force: bool = False, network: bool = True
                ) -> tuple[Weather | None, netcache.Fetched]:
    """(weather or None, how we got it). network=False reads only the cache."""
    ws = load_world(ctx)
    url = weather_url(ws)
    if network:
        got = fetch(ctx, url, ws.weather_minutes * 60, force=force)
    else:
        cached = netcache.read_cache(cache_dir(ctx), url)
        got = netcache.Fetched(cached[0], "cached", cached[1]) if cached else netcache.Fetched(None, "offline")
    if not got.ok:
        return None, got
    memo = ctx.services.get("weather_memo")
    if memo and memo[0] == (url, got.fetched_at):
        return memo[1], got
    try:
        weather = parse_open_meteo(got.text or "")
    except (ValueError, KeyError, TypeError) as exc:
        return None, netcache.Fetched(None, "error", got.fetched_at, f"bad weather data: {exc}")
    ctx.services["weather_memo"] = ((url, got.fetched_at), weather)
    return weather, got


# --- sun -------------------------------------------------------------------------

@dataclass
class SunDay:
    sunrise: datetime | None
    noon: datetime
    sunset: datetime | None
    source: str               # "forecast" or "calculated"

    @property
    def length(self) -> float:
        return (self.sunset - self.sunrise).total_seconds() if self.sunrise and self.sunset else 0.0


def sun_day(day: date, ws: WorldSettings, weather: Weather | None = None) -> SunDay:
    """Forecast sunrise/sunset when we have them, otherwise calculated."""
    rise, noon, sets = astro.sun_times(day, ws.latitude, ws.longitude)
    for f in (weather.days if weather else []):
        if f.day == day and f.sunrise and f.sunset:
            return SunDay(f.sunrise, noon, f.sunset, "forecast")
    return SunDay(rise, noon, sets, "calculated")


def sun_clock(sd: SunDay, now: datetime, zone, width: int = 30) -> str:
    """'06:57 [=====o--------] 17:27' with o where the sun is right now."""
    if not sd.sunrise or not sd.sunset:
        return "no sunrise/sunset today"
    inner = max(4, width - 14)
    frac = (now - sd.sunrise).total_seconds() / max(1.0, sd.length)
    if 0 <= frac <= 1:
        pos = min(inner - 1, int(frac * inner))
        track = "=" * pos + "o" + "-" * (inner - pos - 1)
    else:
        track = "." * inner   # night
    return f"{clock(sd.sunrise, zone)} [{track}] {clock(sd.sunset, zone)}"


def sun_status(sd: SunDay, tomorrow: SunDay, now: datetime) -> str:
    if sd.sunrise and now < sd.sunrise:
        return f"sunrise in {duration((sd.sunrise - now).total_seconds())}"
    if sd.sunset and now < sd.sunset:
        return f"daylight left {duration((sd.sunset - now).total_seconds())}"
    if tomorrow.sunrise:
        return f"night — sunrise in {duration((tomorrow.sunrise - now).total_seconds())}"
    return "night"


def sun_lines(ws: WorldSettings, now: datetime, weather: Weather | None, which: str = "today") -> list[str]:
    zone = local_zone(ws.timezone)
    today = now.astimezone(zone).date()
    day = today + timedelta(days=1) if which == "tomorrow" else today
    sd, prev, nxt = sun_day(day, ws, weather), sun_day(day - timedelta(days=1), ws), sun_day(day + timedelta(days=1), ws, weather)
    change = sun_day(day, ws).length - prev.length
    lines = [f"SUN · {ws.place} · {day:%a %b %d}"]
    if not sd.sunrise or not sd.sunset:
        return lines + ["  no sunrise/sunset today (polar day or night)"]
    lines += [
        f"  sunrise    {clock(sd.sunrise, zone)}",
        f"  solar noon {clock(sd.noon, zone)}   (sun at {90 - abs(ws.latitude - _declination(day)):.0f}° up)",
        f"  sunset     {clock(sd.sunset, zone)}",
        f"  daylight   {duration(sd.length)}  ({'+' if change >= 0 else '-'}{abs(change) / 60:.1f} min vs the day before)",
    ]
    if day == today:
        lines += ["", "  " + sun_clock(sd, now, zone, 40), "  " + sun_status(sd, nxt, now)]
    lines.append(f"  ({sd.source}; times in {getattr(zone, 'key', 'device time')})")
    return lines


def _declination(day: date) -> float:
    """Sun's declination in degrees at noon UTC (for the noon altitude)."""
    x, y, z = astro.sun_direction(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc))
    return math.degrees(math.asin(z))


# --- moon -------------------------------------------------------------------------

def moon_art(phase: float, rows: int = 7, southern: bool = False) -> list[str]:
    """The moon as an ASCII disc: '@' lit, '.' dark (northern-hemisphere view)."""
    cols = rows * 2 + 1
    k = math.cos(2 * math.pi * phase)
    out = []
    for r in range(rows):
        y = (r + 0.5) / rows * 2 - 1
        half = math.sqrt(max(0.0, 1 - y * y))
        line = ""
        for c in range(cols):
            x = (c + 0.5) / cols * 2 - 1
            if abs(x) > half:
                line += " "
                continue
            lit = x > half * k if phase < 0.5 else x < -half * k
            line += "@" if lit else "."
        out.append(line)
    if southern:  # seen upside-down from the southern hemisphere
        out = [l[::-1] for l in out]
    return out


def moon_lines(now: datetime, ws: WorldSettings) -> list[str]:
    zone = local_zone(ws.timezone)
    phase = astro.moon_phase(now)
    art = moon_art(phase, southern=ws.latitude < 0)
    info = [
        f"MOON · {astro.moon_name(phase)}",
        f"{astro.moon_illumination(phase) * 100:.0f}% lit · {phase * astro.SYNODIC_MONTH:.1f} days old",
        "",
        f"next full  {astro.next_phase(now, 0.5).astimezone(zone):%a %b %d %H:%M}",
        f"next new   {astro.next_phase(now, 0.0).astimezone(zone):%a %b %d %H:%M}",
        "(calculated here, ±2 h)",
    ]
    return side_by_side(art, info)


def moon_glyph(phase: float) -> str:
    """One-character-ish moon for the panel: ( ) C D O."""
    return ["( )", " )", " D", "(D", "(O)", "C)", "C ", "( "][int(phase * 8 + 0.5) % 8]


def side_by_side(left: list[str], right: list[str], gap: int = 2) -> list[str]:
    width = max((len(l) for l in left), default=0)
    out = []
    for i in range(max(len(left), len(right))):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        out.append((l.ljust(width) + " " * gap + r).rstrip())
    return out


# --- the weather report ------------------------------------------------------------

def weather_lines(weather: Weather | None, got: netcache.Fetched, ws: WorldSettings, now: datetime) -> list[str]:
    zone = local_zone(ws.timezone)
    lines: list[str] = []
    if weather:
        details = [
            f"{ws.place} · {weather.label}",
            f"{_fmt(weather.temperature)}{weather.temp_unit}  (feels {_fmt(weather.feels_like)}{weather.temp_unit})",
            f"humidity {_fmt(weather.humidity)}%",
            f"wind {_fmt(weather.wind)} {weather.wind_unit} from {astro.compass(weather.wind_direction or 0)}"
            if weather.wind is not None else "wind —",
            f"[{got.age_label()}]",
        ]
        lines += side_by_side(icon_for(weather.code, weather.is_day), details)
        if weather.days:
            lines.append("")
            for f in weather.days:
                name = "today" if f.day == now.astimezone(zone).date() else f"{f.day:%a}"
                rain = f"  {f.rain_chance:>3.0f}% rain" if f.rain_chance is not None else ""
                hi_lo = f"{_fmt(f.high)}/{_fmt(f.low)}"
                lines.append(f"  {name:<5} {describe_code(f.code)[0]:<14.14} {hi_lo:>7}{rain}")
    else:
        why = got.error or got.status
        lines += [f"WEATHER · {ws.place}", f"  offline — no weather saved yet ({why})",
                  "  connect to wifi and run: weather refresh"]
    today = now.astimezone(zone).date()
    sd = sun_day(today, ws, weather)
    lines += ["", "  " + sun_clock(sd, now, zone, 36),
              "  " + sun_status(sd, sun_day(today + timedelta(days=1), ws, weather), now)]
    phase = astro.moon_phase(now)
    lines.append(f"  moon {moon_glyph(phase)}  {astro.moon_name(phase)}, {astro.moon_illumination(phase) * 100:.0f}% lit")
    return lines


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


# --- the "sky" panel -----------------------------------------------------------------

def sky_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    """Compact weather/sun/moon/ISS; reads caches only, never the network."""
    ws = load_world(ctx)
    now = datetime.now(timezone.utc)
    zone = local_zone(ws.timezone)
    weather, got = get_weather(ctx, network=False)
    rows: list[Row] = []
    if weather:
        rows.append([(f"{_fmt(weather.temperature)}{weather.temp_unit} ", "title"), (weather.label, "text")])
        if weather.days:
            f = weather.days[0]
            rain = f" rain {f.rain_chance:.0f}%" if f.rain_chance else ""
            rows.append([(f"H{_fmt(f.high)} L{_fmt(f.low)}{rain}", "dim")])
        if got.fetched_at and time.time() - got.fetched_at > ws.weather_minutes * 120:
            rows.append([(got.age_label(), "warn")])
    else:
        rows.append([("weather: not fetched yet", "dim")])
    today = now.astimezone(zone).date()
    sd = sun_day(today, ws, weather)
    rows.append([(sun_clock(sd, now, zone, width - 1), "warn" if sd.sunrise and sd.sunrise <= now <= (sd.sunset or now) else "dim")])
    phase = astro.moon_phase(now)
    rows.append([("moon ", "dim"), (f"{moon_glyph(phase).strip()} {astro.moon_illumination(phase) * 100:.0f}% {astro.moon_name(phase)}", "text")])
    try:
        from cyberdeck.features.iss import next_pass_line
        line, role = next_pass_line(ctx, now)
        rows.append([(line, role)])
    except Exception:
        pass
    return rows[:height]


# --- commands ----------------------------------------------------------------------------

def weather_is_fresh(ctx: "Context") -> bool:
    """True if the cached weather is young enough to use without downloading."""
    ws = load_world(ctx)
    cached = netcache.read_cache(cache_dir(ctx), weather_url(ws))
    return bool(cached) and time.time() - cached[1] < ws.weather_minutes * 60


def refresh_weather(ctx: "Context") -> None:
    get_weather(ctx)


def start_weather_refresh(ctx: "Context"):
    """TUI startup: keep the weather cache fresh in the background."""
    refresher = get_refresher(ctx)
    refresher.add("weather", load_world(ctx).weather_minutes * 60, refresh_weather)
    return refresher


def register(reg: "Registry") -> None:
    from cyberdeck.features import UI_STARTUP

    if start_weather_refresh not in UI_STARTUP:
        UI_STARTUP.append(start_weather_refresh)

    @reg.command("weather", "Weather with an ASCII icon, forecast, sun and moon.", usage="weather [refresh]",
                 aliases=("wx",), subcommands=("refresh",))
    def cmd_weather(ctx: "Context", args: list[str]) -> list[str]:
        force = bool(args) and args[0].lower() == "refresh"

        def work() -> list[str]:
            weather, got = get_weather(ctx, force=force)
            return weather_lines(weather, got, load_world(ctx), datetime.now(timezone.utc))

        if not force and weather_is_fresh(ctx):
            return work()   # cached and fresh: no network, answer right away
        from cyberdeck.jobs import in_background

        return in_background(ctx, "weather", "weather", work)

    @reg.command("sun", "Sunrise, sunset, day length, and the sun clock.", usage="sun [tomorrow]",
                 subcommands=("today", "tomorrow"))
    def cmd_sun(ctx: "Context", args: list[str]) -> list[str]:
        weather, _ = get_weather(ctx, network=False)
        which = args[0].lower() if args else "today"
        return sun_lines(load_world(ctx), datetime.now(timezone.utc), weather, which)

    @reg.command("moon", "Moon phase (calculated on the deck), next full and new moon.", usage="moon")
    def cmd_moon(ctx: "Context", args: list[str]) -> list[str]:
        return moon_lines(datetime.now(timezone.utc), load_world(ctx))

    register_pane("sky", "SKY", sky_pane)
