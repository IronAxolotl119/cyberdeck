"""sensors.py — temperature, battery, wifi, and other board health data.

The status bar at the top of the screen needs numbers like CPU temp
and battery percent. Those numbers come from a SystemSensors object.

There are two implementations:
  * SimSensors — fake values that drift slowly, for use on any laptop.
  * PiSensors  — a starter for the real Raspberry Pi, reading common
                 Linux files when they exist, otherwise returning None.

Commands and the UI only call read(). They do not care which
implementation is plugged in. Swap them with --mode sim or --mode real.
"""

from __future__ import annotations

import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


# Keep a number inside a min/max range. Used by the sim walks.
def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


# One snapshot of board health, taken at a single moment. Fields are
# optional (None) when a real sensor is not wired up yet, so the UI
# can print "--" instead of crashing.
@dataclass(frozen=True, slots=True)
class SensorReading:
    """A point-in-time snapshot of main-board health."""

    hostname: str
    uptime_s: float
    cpu_temp_c: float | None
    cpu_percent: float | None
    mem_percent: float | None
    battery_percent: float | None
    charging: bool | None
    wifi_ssid: str | None
    wifi_dbm: int | None

    def wifi_label(self) -> str:
        """Short wifi string for the status bar."""
        if not self.wifi_ssid:
            return "WIFI --"
        if self.wifi_dbm is None:
            return f"WIFI {self.wifi_ssid}"
        return f"{self.wifi_ssid} {self.wifi_dbm}dBm"

    def battery_label(self) -> str:
        """Short battery string; a trailing + means it is charging."""
        if self.battery_percent is None:
            return "BAT --"
        mark = "+" if self.charging else ""
        return f"BAT {self.battery_percent:.0f}%{mark}"

    def temp_label(self) -> str:
        """Short temperature string for the status bar."""
        if self.cpu_temp_c is None:
            return "TEMP --"
        return f"{self.cpu_temp_c:.1f}C"


# The interface every sensor backend must implement. Add a new backend
# (for example a custom UPS hat) by subclassing this and writing read().
class SystemSensors(ABC):
    """Hardware-abstracted system telemetry for the status bar."""

    @abstractmethod
    def read(self) -> SensorReading:
        """Return a fresh (or freshly ticked) snapshot."""


# Fake sensors used in --mode sim. Values wander like a small
# battery-powered board: temperature and wifi jitter, battery slowly
# drains or charges, CPU load occasionally spikes.
class SimSensors(SystemSensors):
    """In-process sensors that wander like a small battery-powered board."""

    def __init__(
        self,
        seed: int | None = None,
        clock: Callable[[], float] | None = None,
        hostname: str = "deck-main",
    ) -> None:
        # seed and clock are injectable so tests can be deterministic.
        self._rng = random.Random(seed)
        self._clock = clock or time.monotonic
        self._t0 = self._clock()
        self._last = self._t0
        self._hostname = hostname
        self._cpu_temp = 46.0 + self._rng.uniform(-2.0, 2.0)
        self._cpu_pct = 16.0 + self._rng.uniform(-4.0, 8.0)
        self._mem_pct = 33.0 + self._rng.uniform(-3.0, 5.0)
        self._battery = 78.0 + self._rng.uniform(-8.0, 12.0)
        self._charging = self._rng.random() < 0.25
        self._wifi_ssid = "DECK-NET"
        self._wifi_dbm = -52.0 + self._rng.uniform(-6.0, 6.0)
        self._charge_flip_in = self._rng.uniform(90.0, 240.0)

    def _tick(self) -> float:
        """Advance the fake values by however long has passed since last read."""
        now = self._clock()
        dt = max(0.0, now - self._last)
        self._last = now
        if dt == 0:
            return now
        r = self._rng

        # Mean-reverting random walks, scaled by elapsed time, so a
        # longer pause between reads produces a bigger (but still
        # bounded) change.
        load_pull = (self._cpu_pct - 20.0) * 0.04
        self._cpu_temp += dt * (r.uniform(-0.35, 0.35) + (47.5 + load_pull - self._cpu_temp) * 0.05)
        self._cpu_temp = _clamp(self._cpu_temp, 38.0, 74.0)

        self._cpu_pct += dt * (r.uniform(-3.0, 3.0) + (18.0 - self._cpu_pct) * 0.08)
        # Occasional brief spikes, like a background job kicking in.
        if r.random() < min(0.04 * dt, 0.2):
            self._cpu_pct += r.uniform(8.0, 25.0)
        self._cpu_pct = _clamp(self._cpu_pct, 3.0, 92.0)

        self._mem_pct += dt * (r.uniform(-0.4, 0.4) + (34.0 - self._mem_pct) * 0.03)
        self._mem_pct = _clamp(self._mem_pct, 22.0, 78.0)

        self._charge_flip_in -= dt
        if self._charge_flip_in <= 0:
            self._charging = not self._charging
            self._charge_flip_in = r.uniform(80.0, 280.0) if self._charging else r.uniform(120.0, 420.0)

        if self._charging:
            self._battery += dt * (0.8 / 60.0)  # ~0.8%/min while charging
        else:
            self._battery -= dt * (1.0 / 360.0)  # ~1% per 6 minutes at idle
        self._battery = _clamp(self._battery, 4.0, 100.0)
        if self._battery >= 100.0:
            self._charging = False

        self._wifi_dbm += dt * (r.uniform(-1.2, 1.2) + (-54.0 - self._wifi_dbm) * 0.05)
        self._wifi_dbm = _clamp(self._wifi_dbm, -82.0, -38.0)
        return now

    def read(self) -> SensorReading:
        """Tick the simulation forward and return the current values."""
        now = self._tick()
        return SensorReading(
            hostname=self._hostname,
            uptime_s=max(0.0, now - self._t0),
            cpu_temp_c=round(self._cpu_temp, 2),
            cpu_percent=round(self._cpu_pct, 1),
            mem_percent=round(self._mem_pct, 1),
            battery_percent=round(self._battery, 1),
            charging=self._charging,
            wifi_ssid=self._wifi_ssid,
            wifi_dbm=int(round(self._wifi_dbm)),
        )


# Starter for a real Pi. Safe to construct on a laptop: missing sysfs
# paths just yield None. Fill in the TODOs when you have the hardware.
class PiSensors(SystemSensors):
    """Placeholder for real Raspberry Pi Zero 2 W telemetry.

    Tries a few portable Linux sources so the class is instantiable on
    a development box. Pi-specific paths (battery gauge, wifi) return
    None until they are wired up.
    """

    THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
    # Typical USB / PiJuice / UPS hat paths; not assumed present.
    BATTERY_PATHS = (
        "/sys/class/power_supply/BAT0/capacity",
        "/sys/class/power_supply/battery/capacity",
    )

    def __init__(self, hostname: str | None = None) -> None:
        self._hostname = hostname or os.uname().nodename
        self._t0 = time.monotonic()

    def read(self) -> SensorReading:
        """Read whatever real Linux files are available right now."""
        return SensorReading(
            hostname=self._hostname,
            uptime_s=self._uptime(),
            cpu_temp_c=self._cpu_temp(),
            cpu_percent=None,  # TODO: sample /proc/stat deltas
            mem_percent=self._mem_percent(),
            battery_percent=self._battery_percent(),
            charging=None,
            wifi_ssid=None,  # TODO: parse `iw dev` or /proc/net/wireless
            wifi_dbm=None,
        )

    def _uptime(self) -> float:
        """Seconds since boot, from /proc/uptime when available."""
        try:
            with open("/proc/uptime", encoding="ascii") as fh:
                return float(fh.read().split()[0])
        except (OSError, ValueError, IndexError):
            return max(0.0, time.monotonic() - self._t0)

    def _cpu_temp(self) -> float | None:
        """CPU temperature in C, or None if the thermal file is missing."""
        try:
            with open(self.THERMAL_PATH, encoding="ascii") as fh:
                milli = int(fh.read().strip())
            return milli / 1000.0
        except (OSError, ValueError):
            return None

    def _mem_percent(self) -> float | None:
        """Used memory percent from /proc/meminfo, or None on failure."""
        try:
            info: dict[str, int] = {}
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    key, value, *_ = line.split()
                    info[key.rstrip(":")] = int(value)
            total = info.get("MemTotal")
            available = info.get("MemAvailable")
            if not total or available is None:
                return None
            return round(100.0 * (1.0 - available / total), 1)
        except (OSError, ValueError):
            return None

    def _battery_percent(self) -> float | None:
        """Battery percent from a common power-supply path, if one exists."""
        for path in self.BATTERY_PATHS:
            try:
                with open(path, encoding="ascii") as fh:
                    return float(fh.read().strip())
            except (OSError, ValueError):
                continue
        return None
