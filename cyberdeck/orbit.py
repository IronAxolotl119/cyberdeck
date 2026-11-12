"""orbit.py — where is a satellite, and when will it pass overhead?

Input is a TLE ("two-line element set"), the standard text format
Celestrak publishes for every satellite, e.g. the ISS:

    ISS (ZARYA)
    1 25544U 98067A   26316.17460339  .00008107  00000+0  15334-3 0  9995
    2 25544  51.6324 287.7668 0007312  16.4277 343.6947 15.49327857594697

How the position is worked out (light enough for a Pi Zero):
  1. parse_tle()   -> the orbit's shape and tilt at the TLE's epoch
  2. Satellite.eci -> move it forward in time: Kepler orbit, plus the
                     slow wobble Earth's bulge (J2) adds, plus drag
                     (the TLE's n-dot term) — good to a few km over a
                     couple of days, i.e. pass times good to ~10 s
  3. astro.eci_to_ecef + look_angles -> elevation/azimuth from you
  4. find_passes() -> step through time and record each rise/peak/set

It is not full SGP4 — TLEs older than about a week drift, so the deck
warns you and re-downloads them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import astro

# WGS72 constants, which TLEs are made with.
EARTH_RADIUS_KM = 6378.135
KE = 0.0743669161          # sqrt(GM) in earth-radii^1.5 per minute
J2 = 1.082616e-3
TWO_PI = 2 * math.pi


class TleError(ValueError):
    """The text didn't look like a valid two-line element set."""


def tle_checksum(line: str) -> int:
    """Celestrak's checksum: sum of digits, minus signs count 1, mod 10."""
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def _implied_decimal(field: str) -> float:
    """TLE shorthand ' 15334-3' -> 0.15334e-3."""
    field = field.strip()
    if not field:
        return 0.0
    sign = -1.0 if field[0] == "-" else 1.0
    field = field.lstrip("+-")
    mantissa, exp = field[:-2], field[-2:]
    return sign * float("0." + mantissa) * 10 ** int(exp)


@dataclass(frozen=True)
class Tle:
    name: str
    catalog: int
    epoch: datetime
    ndot2: float        # half the first derivative of mean motion, rev/day^2
    bstar: float
    inclination: float  # radians
    raan: float
    eccentricity: float
    arg_perigee: float
    mean_anomaly: float
    mean_motion: float  # rev/day

    def age_days(self, now: datetime) -> float:
        return (now - self.epoch).total_seconds() / 86400


def parse_tle(text: str, check: bool = True) -> Tle:
    """First TLE in text (2 or 3 lines; a name line is optional)."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            l1, l2 = line, lines[i + 1]
            name = lines[i - 1].strip() if i > 0 and not lines[i - 1].startswith(("1 ", "2 ")) else l1[2:7].strip()
            break
    else:
        raise TleError("no TLE lines found")
    if len(l1) < 69 or len(l2) < 69:
        raise TleError("TLE lines are too short")
    if check and (tle_checksum(l1) != int(l1[68]) or tle_checksum(l2) != int(l2[68])):
        raise TleError("TLE checksum mismatch")
    try:
        yy = int(l1[18:20])
        year = 2000 + yy if yy < 57 else 1900 + yy
        epoch = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=float(l1[20:32]) - 1)
        return Tle(
            name=name, catalog=int(l1[2:7]), epoch=epoch,
            ndot2=float(l1[33:43]), bstar=_implied_decimal(l1[53:61]),
            inclination=math.radians(float(l2[8:16])), raan=math.radians(float(l2[17:25])),
            eccentricity=float("0." + l2[26:33].strip()), arg_perigee=math.radians(float(l2[34:42])),
            mean_anomaly=math.radians(float(l2[43:51])), mean_motion=float(l2[52:63]),
        )
    except ValueError as exc:
        raise TleError(f"bad TLE field: {exc}") from exc


class Satellite:
    """A TLE plus the pre-computed rates used to move it through time."""

    def __init__(self, tle: Tle):
        self.tle = tle
        e, i = tle.eccentricity, tle.inclination
        n0 = tle.mean_motion * TWO_PI / 1440.0          # rad/min
        cosi = math.cos(i)
        beta2 = 1 - e * e
        # Recover the "un-Kozai'd" mean motion and semi-major axis (SGP4 step 1).
        k2 = 0.5 * J2
        a1 = (KE / n0) ** (2 / 3)
        temp = 1.5 * k2 * (3 * cosi * cosi - 1) / beta2 ** 1.5
        d1 = temp / (a1 * a1)
        a0 = a1 * (1 - d1 * (1 / 3 + d1 * (1 + 134 / 81 * d1)))
        d0 = temp / (a0 * a0)
        self.n = n0 / (1 + d0)
        self.a_km = a0 / (1 - d0) * EARTH_RADIUS_KM
        # Secular drift caused by Earth's equatorial bulge.
        p = (self.a_km / EARTH_RADIUS_KM) * beta2
        factor = J2 / (p * p) * self.n
        self.raan_dot = -1.5 * factor * cosi
        self.argp_dot = 0.75 * factor * (5 * cosi * cosi - 1)
        self.m_dot = self.n + 0.75 * factor * math.sqrt(beta2) * (3 * cosi * cosi - 1)

    def eci(self, when: datetime) -> tuple[float, float, float]:
        """Position (km) in the Earth-centred inertial frame."""
        t = self.tle
        minutes = (when - t.epoch).total_seconds() / 60.0
        days = minutes / 1440.0
        mean = t.mean_anomaly + self.m_dot * minutes + TWO_PI * t.ndot2 * days * days
        raan = t.raan + self.raan_dot * minutes
        argp = t.arg_perigee + self.argp_dot * minutes
        # The orbit shrinks a little as drag speeds it up.
        n_now = self.n + TWO_PI * 2 * t.ndot2 * days / 1440.0
        a = self.a_km * (self.n / n_now) ** (2 / 3)
        e = t.eccentricity
        ecc = mean % TWO_PI
        for _ in range(10):  # Kepler's equation by Newton's method
            step = (ecc - e * math.sin(ecc) - (mean % TWO_PI)) / (1 - e * math.cos(ecc))
            ecc -= step
            if abs(step) < 1e-10:
                break
        px = a * (math.cos(ecc) - e)
        py = a * math.sqrt(1 - e * e) * math.sin(ecc)
        co, so = math.cos(argp), math.sin(argp)
        cr, sr = math.cos(raan), math.sin(raan)
        ci, si = math.cos(t.inclination), math.sin(t.inclination)
        x1, y1 = px * co - py * so, px * so + py * co
        return (x1 * cr - y1 * ci * sr, x1 * sr + y1 * ci * cr, y1 * si)

    def ecef(self, when: datetime) -> tuple[float, float, float]:
        return astro.eci_to_ecef(self.eci(when), when)

    def subpoint(self, when: datetime) -> tuple[float, float, float]:
        """(latitude, longitude, altitude km) of the point directly below."""
        x, y, z = self.ecef(when)
        a, f = 6378.137, 1 / 298.257223563
        e2 = f * (2 - f)
        lon = math.atan2(y, x)
        r = math.hypot(x, y)
        lat = math.atan2(z, r)
        for _ in range(5):
            n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
            lat = math.atan2(z + n * e2 * math.sin(lat), r)
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        alt = r / math.cos(lat) - n
        return math.degrees(lat), (math.degrees(lon) + 180) % 360 - 180, alt

    def sunlit(self, when: datetime) -> bool:
        """False while the satellite is inside Earth's shadow (cylinder model)."""
        r = self.eci(when)
        s = astro.sun_direction(when)
        along = r[0] * s[0] + r[1] * s[1] + r[2] * s[2]
        if along > 0:
            return True
        perp = math.sqrt(max(0.0, r[0] ** 2 + r[1] ** 2 + r[2] ** 2 - along * along))
        return perp > EARTH_RADIUS_KM


# --- passes -------------------------------------------------------------------

@dataclass
class Pass:
    rise: datetime
    peak: datetime
    set: datetime
    max_elevation: float
    rise_azimuth: float
    set_azimuth: float
    visible: bool          # sunlit satellite + dark sky = you can see it

    @property
    def duration(self) -> timedelta:
        return self.set - self.rise


def find_passes(sat: Satellite, lat: float, lon: float, start: datetime, hours: float = 48,
                min_elevation: float = 10.0, step_s: float = 30.0, limit: int = 20) -> list[Pass]:
    """Passes above min_elevation degrees in the next `hours`."""
    obs = astro.observer_ecef(lat, lon)

    def elev(when: datetime) -> float:
        return astro.look_angles(sat.ecef(when), lat, lon, obs)[0]

    def crossing(before: datetime, after: datetime, rising: bool) -> datetime:
        for _ in range(12):  # bisection to ~0.01 s
            mid = before + (after - before) / 2
            if (elev(mid) >= min_elevation) == rising:
                after = mid
            else:
                before = mid
        return after if rising else before

    passes: list[Pass] = []
    step = timedelta(seconds=step_s)
    t = start
    end = start + timedelta(hours=hours)
    prev = elev(t)
    rise = start if prev >= min_elevation else None
    while t < end and len(passes) < limit:
        nxt = t + step
        cur = elev(nxt)
        if rise is None and prev < min_elevation <= cur:
            rise = crossing(t, nxt, True)
        elif rise is not None and cur < min_elevation <= prev:
            setting = crossing(t, nxt, False)
            passes.append(_describe(sat, lat, lon, obs, rise, setting))
            rise = None
        prev, t = cur, nxt
    return passes


def _describe(sat: Satellite, lat: float, lon: float, obs, rise: datetime, setting: datetime) -> Pass:
    """Fill in the peak, directions, and visibility for one pass."""
    samples = max(2, int((setting - rise).total_seconds() // 10))
    best_t, best_el, visible = rise, -90.0, False
    for k in range(samples + 1):
        when = rise + (setting - rise) * (k / samples)
        el = astro.look_angles(sat.ecef(when), lat, lon, obs)[0]
        if el > best_el:
            best_t, best_el = when, el
        if not visible and astro.sun_elevation(when, lat, lon) < -6 and sat.sunlit(when):
            visible = True
    return Pass(rise=rise, peak=best_t, set=setting, max_elevation=best_el,
                rise_azimuth=astro.look_angles(sat.ecef(rise), lat, lon, obs)[1],
                set_azimuth=astro.look_angles(sat.ecef(setting), lat, lon, obs)[1],
                visible=visible)
