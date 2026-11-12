"""moods.py — the deck gets sassy: status-line quips for battery, heat, etc.

When nothing more important is on the ticker line, the deck shows a
"mood" line that depends on its own state. Low battery, a hot CPU, a
weak wifi signal, a busy processor, or a hungry PULSE each have their
own set of messages. Every message is in config/moods.ini, so you can
rewrite the deck's personality without touching code.

moods.ini format — one section per mood, checked top to bottom; the
FIRST section whose `when` is true wins:

    [battery_critical]
    when = battery < 8
    role = alert
    messages =
        {bat}% battery. this is my final form. plug. me. in.
        i can see the light… it's the low-battery LED.

`when` can compare battery, temp, cpu, mem, or wifi (dBm) with < <= > >=,
or be one of: charging, pet_alert, always.
`role` is a theme color (dim, text, ok, warn, alert, title).
Messages may use {bat} {temp} {cpu} {mem} {wifi} {ssid} {host}.
The line rotates to another message every [moods] rotate_seconds in fx.ini.

parse_rules() and pick() are pure, so they are unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cyberdeck.config import Settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry
    from cyberdeck.sensors import SensorReading

_WHEN = re.compile(r"^\s*(battery|temp|cpu|mem|wifi)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class MoodRule:
    name: str
    when: str
    role: str
    messages: tuple[str, ...]


def parse_rules(settings: Settings) -> list[MoodRule]:
    """Read every section of moods.ini into rules, in file order."""
    rules: list[MoodRule] = []
    parser = settings._parser  # noqa: SLF001 — need section order
    if parser is None:
        return rules
    for name in parser.sections():
        section = settings.section(name)
        messages = tuple(m.strip() for m in section.get("messages", "").splitlines() if m.strip())
        if messages:
            rules.append(MoodRule(name, section.get("when", "always").strip().lower(),
                                  section.get("role", "dim").strip().lower(), messages))
    return rules


def _value(metric: str, reading: "SensorReading") -> float | None:
    return {"battery": reading.battery_percent, "temp": reading.cpu_temp_c, "cpu": reading.cpu_percent,
            "mem": reading.mem_percent, "wifi": reading.wifi_dbm}[metric]


def matches(when: str, reading: "SensorReading", pet_alert: bool = False) -> bool:
    """Is a rule's `when` condition true right now?"""
    if when == "always":
        return True
    if when == "charging":
        return bool(reading.charging)
    if when == "pet_alert":
        return pet_alert
    m = _WHEN.match(when)
    if not m:
        return False
    value = _value(m.group(1), reading)
    if value is None:
        return False
    limit = float(m.group(3))
    return {"<": value < limit, "<=": value <= limit, ">": value > limit, ">=": value >= limit}[m.group(2)]


def fill(message: str, reading: "SensorReading") -> str:
    """Put live numbers into a message's {placeholders}."""
    values: dict[str, Any] = {
        "bat": f"{reading.battery_percent:.0f}" if reading.battery_percent is not None else "??",
        "temp": f"{reading.cpu_temp_c:.0f}" if reading.cpu_temp_c is not None else "??",
        "cpu": f"{reading.cpu_percent:.0f}" if reading.cpu_percent is not None else "??",
        "mem": f"{reading.mem_percent:.0f}" if reading.mem_percent is not None else "??",
        "wifi": reading.wifi_dbm if reading.wifi_dbm is not None else "??",
        "ssid": reading.wifi_ssid or "nothing",
        "host": reading.hostname,
    }
    try:
        return message.format(**values)
    except (KeyError, IndexError, ValueError):
        return message  # a typo in a placeholder just shows the raw text


def pick(rules: list[MoodRule], reading: "SensorReading", now: float, rotate: float = 20.0,
         pet_alert: bool = False) -> tuple[str, str] | None:
    """The (text, role) to show, or None if no rule matches."""
    for rule in rules:
        if matches(rule.when, reading, pet_alert):
            index = int(now // max(1.0, rotate)) % len(rule.messages)
            return fill(rule.messages[index], reading), rule.role
    return None


class Moods:
    """The service the TUI asks for a ticker line (see tui._ticker_text)."""

    def __init__(self, rules: list[MoodRule], rotate: float = 20.0, enabled: bool = True,
                 ctx: "Context | None" = None) -> None:
        self.rules = rules
        self.rotate = rotate
        self.enabled = enabled
        self.ctx = ctx

    def line(self, reading: "SensorReading", now: float) -> tuple[str, str] | None:
        if not self.enabled:
            return None
        watcher = self.ctx.services.get("petwatch") if self.ctx else None
        pet_alert = bool(watcher is not None and getattr(watcher, "active", None))
        return pick(self.rules, reading, now, self.rotate, pet_alert)


def get_moods(ctx: "Context") -> Moods:
    def build() -> Moods:
        fx = Settings.load("fx.ini", ctx.config_dir)
        rules = parse_rules(Settings.load(fx.get_str("moods", "file", "moods.ini"), ctx.config_dir))
        return Moods(rules, fx.get_float("moods", "rotate_seconds", 20.0), fx.get_bool("moods", "enabled", True), ctx)
    return ctx.service("moods", build)


def register(reg: "Registry") -> None:
    from cyberdeck.features import UI_STARTUP

    if get_moods not in UI_STARTUP:
        UI_STARTUP.append(get_moods)

    @reg.command("mood", "What is the deck feeling? (moods.ini)", usage="mood [on|off|reload]",
                 subcommands=("on", "off", "reload"))
    def cmd_mood(ctx: "Context", args: list[str]) -> list[str]:
        moods = get_moods(ctx)
        if args and args[0] == "reload":
            ctx.services.pop("moods", None)
            moods = get_moods(ctx)
            return [f"reloaded {len(moods.rules)} moods"]
        if args and args[0] in {"on", "off"}:
            moods.enabled = args[0] == "on"
            return [f"moods {args[0]}"]
        import time

        enabled = moods.enabled
        moods.enabled = True
        result = moods.line(ctx.sensors.read(), time.time())
        moods.enabled = enabled
        return [f"deck says: {result[0]}" if result else "deck is speechless (no mood matches)"]
