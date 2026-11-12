"""Real-date helpers: time of day, holidays, birthdays, costumes.

Everything takes a ``datetime`` (or an epoch ``now`` converted with
``local_dt``) so tests can pin any date. Times are the box's local time.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, timedelta


def local_dt(now: float) -> datetime:
    return datetime.fromtimestamp(float(now))


def time_of_day(dt: datetime) -> str:
    """morning 5-10, day 11-12, afternoon 13-17, evening 18-21, night 22-4."""
    h = dt.hour
    if 5 <= h <= 10:
        return "morning"
    if 11 <= h <= 12:
        return "day"
    if 13 <= h <= 17:
        return "afternoon"
    if 18 <= h <= 21:
        return "evening"
    return "night"


def is_late_night(dt: datetime) -> bool:
    return dt.hour >= 23 or dt.hour <= 4


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def thanksgiving(year: int) -> date:
    """US Thanksgiving: 4th Thursday of November."""
    return _nth_weekday(year, 11, 3, 4)


# key -> (label, unicode costume, ascii costume)
HOLIDAYS: dict[str, tuple[str, str, str]] = {
    "new_year": ("New Year", "🎉", "*<"),
    "valentines": ("Valentine's Day", "♥", "<3"),
    "pi_day": ("Pi Day", "π", "pi"),
    "st_patricks": ("St. Patrick's Day", "☘", "%"),
    "april_fools": ("April Fools' Day", "🃏", "?!"),
    "earth_day": ("Earth Day", "🌍", "(o)"),
    "star_wars": ("Star Wars Day", "⚔", "-=-"),
    "solstice": ("the summer solstice", "☀", "(*)"),
    "independence": ("the Fourth of July", "🎆", "*"),
    "programmers_day": ("Programmer's Day", "💾", "01"),
    "halloween": ("Halloween", "🎃", "{o}"),
    "thanksgiving": ("Thanksgiving", "🦃", "<v"),
    "christmas": ("Christmas", "🎄", "^*"),
    "leap_day": ("Leap Day", "🐸", "@"),
    "friday_13": ("Friday the 13th", "☠", "13"),
}

COSTUMES: dict[str, tuple[str, str]] = {
    "party": ("🥳", "^"),  # birthday / hatchday hat
}
COSTUMES.update({key: (uni, asc) for key, (_label, uni, asc) in HOLIDAYS.items()})


def holiday_for(d: date | datetime) -> str | None:
    """The holiday key for this date, or None."""
    if isinstance(d, datetime):
        d = d.date()
    m, day = d.month, d.day
    if (m == 12 and day == 31) or (m == 1 and day <= 2):
        return "new_year"
    if m == 2 and day in (13, 14):
        return "valentines"
    if m == 2 and day == 29:
        return "leap_day"
    if m == 3 and day == 14:
        return "pi_day"
    if m == 3 and day == 17:
        return "st_patricks"
    if m == 4 and day == 1:
        return "april_fools"
    if m == 4 and day == 22:
        return "earth_day"
    if m == 5 and day == 4:
        return "star_wars"
    if m == 6 and day in (20, 21):
        return "solstice"
    if m == 7 and day == 4:
        return "independence"
    if d.timetuple().tm_yday == 256:
        return "programmers_day"
    if m == 10 and 24 <= day <= 31:
        return "halloween"
    tg = thanksgiving(d.year)
    if m == 11 and tg <= d <= tg + timedelta(days=1):
        return "thanksgiving"
    if m == 12 and 20 <= day <= 26:
        return "christmas"
    if day == 13 and d.weekday() == 4:
        return "friday_13"
    return None


def holiday_label(key: str | None) -> str | None:
    if not key:
        return None
    return HOLIDAYS.get(key, (key, "", ""))[0]


def _clamped_day(year: int, month: int, day: int) -> int:
    return min(day, _cal.monthrange(year, month)[1])


def is_birthday(hatched: date, today: date) -> bool:
    """Same month/day in a later year (Feb 29 hatchlings party on Feb 28)."""
    if today.year <= hatched.year:
        return False
    if hatched.month != today.month:
        return False
    return today.day == _clamped_day(today.year, hatched.month, hatched.day)


def is_hatchday(hatched: date, today: date) -> bool:
    """Monthly anniversary (not the yearly birthday)."""
    months = months_between(hatched, today)
    if months <= 0 or months % 12 == 0:
        return False
    return today.day == _clamped_day(today.year, today.month, hatched.day)


def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def costume_text(key: str | None, ascii_mode: bool = False) -> str:
    if not key:
        return ""
    uni, asc = COSTUMES.get(key, ("", ""))
    return asc if ascii_mode else uni
