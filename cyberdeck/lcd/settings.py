"""settings.py — read config/lcd.ini into one LcdSettings object.

In plain English: every knob of the little screen (which backend, its
I2C address, how long notifications stay, quiet hours, which PULSE
events show up…) lives in config/lcd.ini. This file reads it once and
fills in a sensible default for anything you leave out, so a typo or a
missing line never stops the deck from starting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cyberdeck.config import Settings

# PULSE event names (pushed pet.event, plus the deck's own need alerts)
# that pop up on the LCD by default. config/lcd.ini [notify] pet_events.
DEFAULT_PET_EVENTS = ["hungry", "tired", "bored", "sad", "evolve_soon", "evolve_choice", "evolving", "evolved",
                      "level_up", "learned_move", "move_strengthened", "woke", "signal_found"]


@dataclass
class LcdSettings:
    # [lcd] — the screen itself
    backend: str = "sim"            # sim | i2c | none
    cols: int = 16
    rows: int = 2
    fps: float = 4.0                # screen updates per second (marquee speed)
    scroll_pause: float = 1.2       # seconds a ticker waits at its start
    rotate_seconds: float = 6.0     # each idle page stays this long (longer if it scrolls)
    hold_seconds: float = 6.0       # a notification stays this long…
    alert_hold_seconds: float = 10.0  # …and an ALERT this long
    flash: bool = True              # blink the backlight when a notification arrives
    flash_times: int = 3
    chirp: bool = False             # buzzer chirp hook (config/hardware.ini [pins] buzzer)
    chirp_priority: int = 3         # chirp from this priority up (3 = alerts only)
    backlight: str = "auto"         # auto | on | off
    dim_after_minutes: float = 0.0  # auto: backlight off after this idle time (0 = never)
    sim_mirror: str = ""            # also write the sim screen to this text file

    # [i2c] — the real LCD1602 + PCF8574 backpack
    address: str = "auto"          # auto, 0x27, 0x3F …
    port: int = 1                  # /dev/i2c-1 on every modern Pi
    driver: str = "auto"           # auto | rplcd | smbus2
    charmap: str = "A00"           # A00 (most common) or A02 ROM
    expander: str = "PCF8574"
    candidates: list[str] = field(default_factory=lambda: ["0x27", "0x3F"])

    # [filter] — keep the screen calm
    dedupe_seconds: float = 90.0    # same message again within this = dropped
    max_per_minute: int = 6         # info/notice pop-ups per minute (ALERTs are never rate-dropped)
    burst: int = 3
    max_queue: int = 8
    field_min_priority: int = 3     # field mode: only ALERTs interrupt

    # [quiet] — quiet hours (local time on the deck)
    quiet_enabled: bool = False
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    quiet_allow_priority: int = 3   # these still show (without flash/chirp)

    # [pages] — the idle rotation
    pages: list[str] = field(default_factory=lambda: ["pulse", "blurb", "clock", "blurb"])
    blurb_file: str = "text/lcd_blurbs.txt"
    reaction_minutes: float = 30.0  # PULSE's recent reactions stay in the rotation this long
    clock_format: str = "%a %d %b %H:%M"

    # [notify] — what interrupts the idle pages
    pet_events: list[str] = field(default_factory=lambda: list(DEFAULT_PET_EVENTS))
    awareness_alerts: bool = True
    reactions: bool = True          # pet.discovery.ok with a learned/strengthened move or level-up
    link_changes: bool = True
    battery_low: int = 15           # percent; 0 = never


def load_lcd_settings(settings: Settings | None = None, config_dir: str | Path | None = None) -> LcdSettings:
    """config/lcd.ini -> LcdSettings (defaults for anything missing)."""
    s = settings or Settings.load("lcd.ini", config_dir)
    d = LcdSettings()
    backend = s.get_str("lcd", "backend", d.backend).lower()
    return LcdSettings(
        backend=backend if backend in {"sim", "i2c", "none"} else d.backend,
        cols=max(8, s.get_int("lcd", "cols", d.cols)),
        rows=max(1, min(4, s.get_int("lcd", "rows", d.rows))),
        fps=max(1.0, min(10.0, s.get_float("lcd", "fps", d.fps))),
        scroll_pause=max(0.0, s.get_float("lcd", "scroll_pause", d.scroll_pause)),
        rotate_seconds=max(1.0, s.get_float("lcd", "rotate_seconds", d.rotate_seconds)),
        hold_seconds=max(1.0, s.get_float("lcd", "hold_seconds", d.hold_seconds)),
        alert_hold_seconds=max(1.0, s.get_float("lcd", "alert_hold_seconds", d.alert_hold_seconds)),
        flash=s.get_bool("lcd", "flash", d.flash),
        flash_times=max(0, s.get_int("lcd", "flash_times", d.flash_times)),
        chirp=s.get_bool("lcd", "chirp", d.chirp),
        chirp_priority=s.get_int("lcd", "chirp_priority", d.chirp_priority),
        backlight=s.get_str("lcd", "backlight", d.backlight).lower(),
        dim_after_minutes=max(0.0, s.get_float("lcd", "dim_after_minutes", d.dim_after_minutes)),
        sim_mirror=s.get_str("lcd", "sim_mirror", d.sim_mirror),
        address=s.get_str("i2c", "address", d.address),
        port=s.get_int("i2c", "port", d.port),
        driver=s.get_str("i2c", "driver", d.driver).lower(),
        charmap=s.get_str("i2c", "charmap", d.charmap),
        expander=s.get_str("i2c", "expander", d.expander),
        candidates=s.get_list("i2c", "candidates", d.candidates),
        dedupe_seconds=max(0.0, s.get_float("filter", "dedupe_seconds", d.dedupe_seconds)),
        max_per_minute=max(1, s.get_int("filter", "max_per_minute", d.max_per_minute)),
        burst=max(1, s.get_int("filter", "burst", d.burst)),
        max_queue=max(1, s.get_int("filter", "max_queue", d.max_queue)),
        field_min_priority=s.get_int("filter", "field_min_priority", d.field_min_priority),
        quiet_enabled=s.get_bool("quiet", "enabled", d.quiet_enabled),
        quiet_start=s.get_str("quiet", "start", d.quiet_start),
        quiet_end=s.get_str("quiet", "end", d.quiet_end),
        quiet_allow_priority=s.get_int("quiet", "allow_priority", d.quiet_allow_priority),
        pages=[p.lower() for p in s.get_list("pages", "order", d.pages)],
        blurb_file=s.get_str("pages", "blurb_file", d.blurb_file),
        reaction_minutes=max(0.0, s.get_float("pages", "reaction_minutes", d.reaction_minutes)),
        clock_format=s.get_str("pages", "clock_format", d.clock_format),
        pet_events=[e.lower() for e in s.get_list("notify", "pet_events", d.pet_events)],
        awareness_alerts=s.get_bool("notify", "awareness_alerts", d.awareness_alerts),
        reactions=s.get_bool("notify", "reactions", d.reactions),
        link_changes=s.get_bool("notify", "link_changes", d.link_changes),
        battery_low=max(0, s.get_int("notify", "battery_low", d.battery_low)),
    )


def parse_address(text: str) -> int | None:
    """'0x27' / '39' -> 39; 'auto' or junk -> None."""
    text = (text or "").strip().lower()
    if not text or text == "auto":
        return None
    try:
        return int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return None
