"""astro.py — sun and moon maths, done locally (no internet needed).

  sun_times(date, lat, lon)     sunrise, solar noon, sunset (UTC datetimes)
  sun_direction(when)           unit vector toward the sun (for ISS visibility)
  sun_elevation(when, lat, lon) degrees above the horizon
  moon_phase(when)              0..1 through the lunar month (0 = new, 0.5 = full)
  gmst(when)                    Greenwich sidereal angle (Earth's rotation)

These are the classic "good to a minute" formulas used by almanacs and
NOAA's calculator — plenty for a deck, and cheap enough for a Pi Zero.
All datetimes are timezone-aware UTC; callers convert for display.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

J2000 = 2451545.0
SYNODIC_MONTH = 29.530588853
# A known new moon: 2000-01-06 18:14 UTC.
NEW_MOON_JD = 2451550.26


def julian_day(when: datetime) -> float:
    """Julian day number (with fraction) for an aware datetime."""
    when = when.astimezone(timezone.utc)
    return when.timestamp() / 86400.0 + 2440587.5


def from_julian(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc)


def _sun_ecliptic(n: float) -> tuple[float, float]:
    """(ecliptic longitude, mean anomaly) in radians, n = days since J2000."""
    m = math.radians((357.5291 + 0.98560028 * n) % 360)
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    return lam, m


def sun_times(day: date, lat: float, lon: float) -> tuple[datetime | None, datetime, datetime | None]:
    """(sunrise, solar noon, sunset) in UTC for a calendar day at a place.

    lon is east-positive (Phoenix is -112.07). Sunrise/sunset are None
    during polar day or night.
    """
    jd_noon = julian_day(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc))
    n = round(jd_noon - J2000 + 0.0008)
    j_star = n - lon / 360.0
    lam, m = _sun_ecliptic(j_star)
    transit = J2000 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    decl = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    noon = from_julian(transit)
    if cos_w > 1 or cos_w < -1:
        return None, noon, None
    w = math.degrees(math.acos(cos_w))
    return from_julian(transit - w / 360), noon, from_julian(transit + w / 360)


def gmst(when: datetime) -> float:
    """Greenwich mean sidereal time in radians (IAU 1982)."""
    t = (julian_day(when) - J2000) / 36525.0
    seconds = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3
    return math.radians((seconds % 86400) / 240.0)


def sun_direction(when: datetime) -> tuple[float, float, float]:
    """Unit vector to the sun in the Earth-centred inertial frame."""
    n = julian_day(when) - J2000
    lam, _ = _sun_ecliptic(n)
    eps = math.radians(23.439 - 0.0000004 * n)
    return (math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam))


def observer_ecef(lat: float, lon: float, alt_km: float = 0.0) -> tuple[float, float, float]:
    """Observer position on the WGS84 ellipsoid, km, Earth-fixed."""
    a, f = 6378.137, 1 / 298.257223563
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return ((n + alt_km) * math.cos(phi) * math.cos(lam),
            (n + alt_km) * math.cos(phi) * math.sin(lam),
            (n * (1 - e2) + alt_km) * math.sin(phi))


def eci_to_ecef(vec: tuple[float, float, float], when: datetime) -> tuple[float, float, float]:
    """Rotate an inertial vector into the Earth-fixed frame."""
    th = gmst(when)
    x, y, z = vec
    return (x * math.cos(th) + y * math.sin(th), -x * math.sin(th) + y * math.cos(th), z)


def look_angles(target_ecef: tuple[float, float, float], lat: float, lon: float,
                obs: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    """(elevation deg, azimuth deg, range km) from an observer to a point."""
    ox, oy, oz = obs or observer_ecef(lat, lon)
    rx, ry, rz = target_ecef[0] - ox, target_ecef[1] - oy, target_ecef[2] - oz
    phi, lam = math.radians(lat), math.radians(lon)
    east = -math.sin(lam) * rx + math.cos(lam) * ry
    north = -math.sin(phi) * math.cos(lam) * rx - math.sin(phi) * math.sin(lam) * ry + math.cos(phi) * rz
    up = math.cos(phi) * math.cos(lam) * rx + math.cos(phi) * math.sin(lam) * ry + math.sin(phi) * rz
    rng = math.sqrt(rx * rx + ry * ry + rz * rz)
    return math.degrees(math.atan2(up, math.hypot(east, north))), math.degrees(math.atan2(east, north)) % 360, rng


def sun_elevation(when: datetime, lat: float, lon: float) -> float:
    """Degrees the sun is above (positive) or below the horizon."""
    au = 149597870.7
    sx, sy, sz = sun_direction(when)
    ecef = eci_to_ecef((sx * au, sy * au, sz * au), when)
    return look_angles(ecef, lat, lon)[0]


def compass(azimuth: float) -> str:
    """0..360 degrees -> N, NNE, NE, …"""
    points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return points[int((azimuth % 360) / 22.5 + 0.5) % 16]


# --- moon ---------------------------------------------------------------------

MOON_NAMES = ["new moon", "waxing crescent", "first quarter", "waxing gibbous",
              "full moon", "waning gibbous", "last quarter", "waning crescent"]


def moon_phase(when: datetime) -> float:
    """Fraction of the way through the lunar month (0 new, 0.5 full)."""
    return ((julian_day(when) - NEW_MOON_JD) / SYNODIC_MONTH) % 1.0


def moon_illumination(phase: float) -> float:
    """0..1 of the moon's face that is lit."""
    return (1 - math.cos(2 * math.pi * phase)) / 2


def moon_name(phase: float) -> str:
    return MOON_NAMES[int(phase * 8 + 0.5) % 8]


def next_phase(when: datetime, target: float) -> datetime:
    """When the phase next reaches target (0 = new, 0.5 = full)."""
    days = ((target - moon_phase(when)) % 1.0) * SYNODIC_MONTH
    return when + timedelta(days=days)
