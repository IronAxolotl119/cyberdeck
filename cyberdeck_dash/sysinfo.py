"""System status readings for the System panel.

In plain English: CPU load, memory, temperature, uptime and disk, read
straight from Linux's /proc and /sys files (cheap, no extra libraries).
On a machine where a file is missing (or in sim mode for temperature on
non-Pi hardware) a plausible simulated value is shown and marked "~".
"""

from __future__ import annotations

import os
import random
import shutil
import time
from pathlib import Path


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


class SystemInfo:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._cpu_prev: tuple[int, int] | None = None
        self.started = time.time()

    def cpu_percent(self) -> float | None:
        text = _read("/proc/stat")
        if not text:
            return None
        vals = [int(v) for v in text.splitlines()[0].split()[1:]]
        idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if not prev or total == prev[1]:
            return None
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))

    def read(self) -> dict:
        info: dict = {}
        cpu = self.cpu_percent()
        info["cpu"] = cpu if cpu is not None else self.rng.uniform(8, 30)
        mem = _read("/proc/meminfo")
        if mem:
            kv = {line.split(":")[0]: int(line.split()[1]) for line in mem.splitlines() if len(line.split()) > 1}
            total, avail = kv.get("MemTotal", 1), kv.get("MemAvailable", 0)
            info["mem"] = 100.0 * (1 - avail / total)
            info["mem_total_mb"] = total // 1024
        temp = _read("/sys/class/thermal/thermal_zone0/temp")
        if temp and temp.strip().isdigit():
            info["temp"], info["temp_sim"] = int(temp) / 1000.0, False
        else:
            info["temp"], info["temp_sim"] = 46 + info["cpu"] / 5 + self.rng.uniform(-0.5, 0.5), True
        try:
            info["load"] = os.getloadavg()[0]
        except OSError:
            info["load"] = 0.0
        up = _read("/proc/uptime")
        info["uptime"] = float(up.split()[0]) if up else time.time() - self.started
        du = shutil.disk_usage("/")
        info["disk"] = 100.0 * du.used / du.total
        info["host"] = os.uname().nodename
        return info
