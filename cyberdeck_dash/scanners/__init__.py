"""Scanner registry: picks Sim or Real backends from config/dash.ini.

In plain English: build_scanners() reads each section's `backend` key and
creates the matching scanner. In sim mode (the default) every scanner is
simulated. In real mode a missing tool or library makes that one
scanner show an error in its panel instead of crashing the dashboard.
"""

from __future__ import annotations

import random

from ..config import DashConfig
from .base import AccessPoint, BtDevice, LanHost, ScanResult, Scanner, TagRead
from .bluetooth import BleakBtScanner, BluetoothctlScanner, SimBtScanner
from .lan import ProcLanScanner, SimLanScanner
from .rfid import Mfrc522Scanner, SimRfidScanner
from .wifi import IwWifiScanner, NmcliWifiScanner, SimWifiScanner

__all__ = ["AccessPoint", "BtDevice", "LanHost", "ScanResult", "Scanner", "TagRead", "build_scanners"]


def build_scanners(cfg: DashConfig, mode: str = "sim", rng: random.Random | None = None) -> dict[str, Scanner]:
    """Return {"wifi": ..., "bt": ..., "lan": ..., "rfid": ...}."""
    rng = rng or random.Random()
    sim = mode == "sim"

    def backend(section: str) -> str:
        return "sim" if sim else cfg.get(section, "backend").lower()

    wifi_b = backend("wifi")
    if wifi_b == "nmcli":
        wifi: Scanner = NmcliWifiScanner(cfg.get("wifi", "interface"))
    elif wifi_b == "iw":
        wifi = IwWifiScanner(cfg.get("wifi", "interface"))
    else:
        wifi = SimWifiScanner(random.Random(rng.random()))

    bt_b = backend("bluetooth")
    if bt_b == "bluetoothctl":
        bt: Scanner = BluetoothctlScanner(cfg.int("bluetooth", "listen"))
    elif bt_b == "bleak":
        bt = BleakBtScanner(cfg.int("bluetooth", "listen"))
    else:
        bt = SimBtScanner(random.Random(rng.random()))

    if backend("lan") in ("proc", "arp"):
        lan: Scanner = ProcLanScanner(cfg.bool("lan", "ping_sweep"), cfg.get("lan", "subnet"),
                                      cfg.float("lan", "pings_per_second"), cfg.float("lan", "sweep_cooldown"))
    else:
        lan = SimLanScanner(random.Random(rng.random()))

    rfid: Scanner = Mfrc522Scanner() if backend("rfid") == "mfrc522" else SimRfidScanner(random.Random(rng.random()))
    return {"wifi": wifi, "bt": bt, "lan": lan, "rfid": rfid}
