"""What every scanner returns, and the interface each one follows.

In plain English: there is one Scanner per radio/tool (Wi-Fi, Bluetooth,
LAN, RFID). Each comes in two flavours: a Sim version that invents
realistic data so the dashboard works on any computer, and a Real
version that talks to the Pi's hardware or system tools. Both return the
same small record types defined here, so the rest of the code never
cares which one is running.

To add a new scanner: subclass Scanner, set `name`, implement scan(),
and register it in scanners/__init__.py (see docs/DASHBOARD.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- Records returned by scans ---------------------------------------------
@dataclass
class AccessPoint:
    ssid: str            # network name ("" for hidden)
    bssid: str           # radio MAC, lowercase aa:bb:cc:dd:ee:ff
    rssi: int            # signal in dBm (-30 strong ... -90 weak)
    security: str        # "open", "WPA2", "WPA3", "WEP", ...
    channel: int = 0

    @property
    def is_open(self) -> bool:
        return self.security.strip().lower() in ("", "open", "none", "--")


@dataclass
class BtDevice:
    mac: str
    name: str = ""
    rssi: int | None = None


@dataclass
class LanHost:
    ip: str
    mac: str
    iface: str = ""
    alive: bool = True   # True if it answered a ping or is in the ARP table


@dataclass
class TagRead:
    uid: str             # hex UID, e.g. "04:a2:19:7f"


@dataclass
class ScanResult:
    """What one scan() call hands back. `items` holds the records above."""

    scanner: str
    items: list = field(default_factory=list)
    error: str = ""      # non-empty if the backend failed (shown in panel)
    simulated: bool = True


class Scanner:
    """Base class. `name` is the key used by config, panels and moves."""

    name = "base"
    simulated = True

    def scan(self) -> ScanResult:  # pragma: no cover - overridden
        raise NotImplementedError

    def describe(self) -> str:
        """Short backend label for the panel title, e.g. 'SIM' or 'nmcli'."""
        return "SIM" if self.simulated else type(self).__name__.replace("Scanner", "")
