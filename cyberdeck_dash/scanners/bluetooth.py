"""Bluetooth scanners: a simulator plus bluetoothctl and bleak backends.

In plain English: these list Bluetooth devices that are advertising
nearby (headphones, watches, phones in pairing mode, trackers). Listening
to adverts is passive; nothing here pairs with or connects to a device.

- SimBtScanner           invents devices that come, go, and sometimes linger.
- BluetoothctlScanner    runs `bluetoothctl --timeout N scan on` (BlueZ, Pi OS).
- BleakScanner           uses the optional `bleak` library (imported lazily).
"""

from __future__ import annotations

import random
import re
import subprocess

from .base import BtDevice, ScanResult, Scanner


# --- Simulation ---------------------------------------------------------------
class SimBtScanner(Scanner):
    name = "bt"

    # (mac, name, base rssi, chance visible per scan)
    WORLD = [
        ("11:22:33:44:55:66", "Chessin's Phone", -48, 1.0),
        ("c0:ff:ee:00:12:34", "CYBERDECK-PET", -40, 1.0),
        ("7c:d1:c3:9a:01:22", "JBL Flip 5", -63, 0.8),
        ("e8:9f:6d:44:aa:10", "Galaxy Watch4", -77, 0.5),
        ("f0:5c:77:10:20:30", "", -85, 0.4),
        ("d4:3a:2c:88:19:07", "Tile", -70, 0.0),   # the lingerer, appears later
        ("6a:11:9e:02:4b:c3", "", -88, 0.3),
    ]

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.scans = 0
        self.extra: list[BtDevice] = []

    def scan(self) -> ScanResult:
        self.scans += 1
        devs = []
        for mac, name, base, chance in self.WORLD:
            if name == "Tile":
                chance = 1.0 if self.scans >= 3 else 0.0   # shows up and stays
            if self.rng.random() <= chance:
                devs.append(BtDevice(mac, name, int(base + self.rng.uniform(-5, 5))))
        # Passers-by with random (privacy) MACs.
        if self.rng.random() < 0.3:
            mac = "%02x:%02x:%02x:%02x:%02x:%02x" % tuple([0x5a] + [self.rng.randrange(256) for _ in range(5)])
            devs.append(BtDevice(mac, self.rng.choice(["", "", "AirPods", "LE-Bose", "Fitbit"]),
                                 self.rng.randint(-92, -70)))
        return ScanResult(self.name, devs, simulated=True)


# --- Real: bluetoothctl -----------------------------------------------------
_DEV = re.compile(r"Device ([0-9A-Fa-f:]{17})(?: (.*))?$")
_RSSI = re.compile(r"Device ([0-9A-Fa-f:]{17}) RSSI: (?:0x[0-9a-f]+ \()?(-?\d+)")


def parse_bluetoothctl(text: str) -> list[BtDevice]:
    """Parse lines like '[NEW] Device AA:BB:.. Name' and '[CHG] Device AA:.. RSSI: -60'."""
    devs: dict[str, BtDevice] = {}
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text)
    for line in clean.splitlines():
        m = _RSSI.search(line)
        if m:
            d = devs.setdefault(m.group(1).lower(), BtDevice(m.group(1).lower()))
            d.rssi = int(m.group(2))
            continue
        m = _DEV.search(line)
        if m and ":" not in (m.group(2) or ""):
            d = devs.setdefault(m.group(1).lower(), BtDevice(m.group(1).lower()))
            name = (m.group(2) or "").strip()
            # bluetoothctl uses the MAC with dashes as the name when unknown
            if name and not re.fullmatch(r"[0-9A-Fa-f-]{17}", name):
                d.name = name
    return list(devs.values())


class BluetoothctlScanner(Scanner):
    name = "bt"
    simulated = False

    def __init__(self, listen: int = 5) -> None:
        self.listen = listen

    def scan(self) -> ScanResult:
        try:
            out = subprocess.run(["bluetoothctl", "--timeout", str(self.listen), "scan", "on"],
                                 capture_output=True, text=True, timeout=self.listen + 10).stdout
            out += subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            return ScanResult(self.name, error=f"bluetoothctl: {exc}", simulated=False)
        return ScanResult(self.name, parse_bluetoothctl(out), simulated=False)

    def describe(self) -> str:
        return "bluetoothctl"


# --- Real: bleak (optional library) -------------------------------------------
class BleakBtScanner(Scanner):
    name = "bt"
    simulated = False

    def __init__(self, listen: int = 5) -> None:
        self.listen = listen

    def scan(self) -> ScanResult:
        try:
            import asyncio

            from bleak import BleakScanner  # lazy: optional dependency
        except ImportError:
            return ScanResult(self.name, error="bleak not installed (pip install bleak)", simulated=False)
        try:
            found = asyncio.run(BleakScanner.discover(timeout=self.listen, return_adv=True))
        except Exception as exc:  # adapter off, permissions, ...
            return ScanResult(self.name, error=f"bleak: {exc}"[:60], simulated=False)
        devs = [BtDevice(addr.lower(), dev.name or adv.local_name or "", adv.rssi)
                for addr, (dev, adv) in found.items()]
        return ScanResult(self.name, devs, simulated=False)

    def describe(self) -> str:
        return "bleak"
