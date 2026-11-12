"""The outside world the pet can sense: CPU temperature and (optional) weather.

Both are injectable so tests never touch /sys or the network.

* CPU temperature: read from /sys/class/thermal/thermal_zone0/temp when it
  exists (millidegrees C on a Pi); otherwise a gentle simulated value.
* Weather: off unless a latitude/longitude is configured. Uses Open-Meteo's
  free current-weather endpoint over plain urllib with a short timeout, in a
  background thread. Any failure (offline, DNS, bad JSON) just leaves the
  weather unknown, and the pet behaves as if there were no weather at all.
"""

from __future__ import annotations

import json
import math
import threading
import urllib.request
from pathlib import Path
from typing import Callable

THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
HOT_C = 70.0
COLD_C = 30.0
WEATHER_REFRESH_S = 1800.0


def read_cpu_temp(path: Path = THERMAL_PATH) -> float | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        value = float(raw)
    except (OSError, ValueError):
        return None
    return value / 1000.0 if value > 200 else value


def simulated_temp(now: float, busy: float = 0.0) -> float:
    """A believable idle CPU temperature that drifts slowly."""
    return round(46.0 + 5.0 * math.sin(now / 900.0) + 10.0 * max(0.0, min(1.0, busy)), 1)


def weather_condition(code: int) -> str:
    """WMO weather code -> sunny/cloudy/fog/rain/snow/storm."""
    if code in (0, 1, 2):
        return "sunny"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloudy"


def fetch_open_meteo(lat: float, lon: float, timeout: float = 4.0) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}&current_weather=true"
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        data = json.loads(resp.read().decode("utf-8"))
    current = data.get("current_weather") or {}
    return {"weathercode": int(current.get("weathercode", 3)), "temperature": current.get("temperature")}


class World:
    """Sensor facade used by the life engine."""

    def __init__(
        self,
        temp_reader: Callable[[], float | None] | None = None,
        lat: float | None = None,
        lon: float | None = None,
        weather_fetch: Callable[[float, float], dict] | None = None,
        threaded: bool = True,
    ) -> None:
        self.temp_reader = temp_reader if temp_reader is not None else read_cpu_temp
        self.lat = lat
        self.lon = lon
        self.weather_fetch = weather_fetch or fetch_open_meteo
        self.threaded = threaded
        self.weather: dict | None = None
        self._last_try = -1e18
        self._busy = False

    @property
    def weather_enabled(self) -> bool:
        return self.lat is not None and self.lon is not None

    def cpu_temp(self, now: float, busy: float = 0.0) -> tuple[float, str]:
        value = None
        try:
            value = self.temp_reader()
        except Exception:  # a broken sensor must never crash the pet
            value = None
        if value is None:
            return simulated_temp(now, busy), "sim"
        return round(float(value), 1), "sensor"

    def _fetch(self, now: float) -> None:
        try:
            raw = self.weather_fetch(float(self.lat), float(self.lon))
            code = int(raw.get("weathercode", 3))
            self.weather = {
                "condition": weather_condition(code),
                "code": code,
                "temp_c": raw.get("temperature"),
                "at": now,
            }
        except Exception:
            # Offline or blocked: keep the last good reading (or none).
            pass
        finally:
            self._busy = False

    def refresh_weather(self, now: float) -> dict | None:
        """Kick off a refresh when due. Returns the current reading (may be None)."""
        if not self.weather_enabled:
            return None
        if not self._busy and now - self._last_try >= WEATHER_REFRESH_S:
            self._last_try = now
            self._busy = True
            if self.threaded:
                threading.Thread(target=self._fetch, args=(now,), daemon=True).start()
            else:
                self._fetch(now)
        if self.weather and now - float(self.weather.get("at", 0)) > WEATHER_REFRESH_S * 4:
            return None  # stale
        return self.weather
