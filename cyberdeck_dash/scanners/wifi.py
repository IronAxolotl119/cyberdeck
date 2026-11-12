"""Wi-Fi scanners: a simulator plus nmcli and iw backends.

In plain English: these list the Wi-Fi networks the Pi's built-in radio
can hear. They only LISTEN (the same list your phone shows); they never
join, probe, or disturb anyone's network.

- SimWifiScanner  invents a believable neighbourhood: your home network,
                  neighbours, a phone hotspot that comes and goes, and now
                  and then a "Free Public WiFi" or an open look-alike of
                  your network so the anomaly rules have something to flag.
- NmcliWifiScanner  `nmcli dev wifi list` (works as a normal user on Pi OS).
- IwWifiScanner     `iw dev wlan0 scan` (needs root).
"""

from __future__ import annotations

import random
import re
import subprocess

from .base import AccessPoint, ScanResult, Scanner


# --- Simulation ---------------------------------------------------------------
class SimWifiScanner(Scanner):
    name = "wifi"

    # (ssid, bssid, base rssi, security, channel, chance it is visible per scan)
    WORLD = [
        ("AXOLOTL_NET", "aa:bb:cc:00:00:01", -42, "WPA2", 6, 1.0),
        ("AXOLOTL_NET", "aa:bb:cc:00:00:02", -61, "WPA2", 36, 1.0),
        ("HomeLab-IoT", "aa:bb:cc:00:00:09", -55, "WPA2", 11, 1.0),
        ("NETGEAR42", "3c:37:86:12:4f:a0", -71, "WPA2", 1, 0.95),
        ("SunsetVilla_5G", "f4:92:bf:7a:11:3e", -78, "WPA3", 149, 0.9),
        ("DIRECT-7F-HP Laser", "02:9b:cd:51:7f:00", -80, "WPA2", 6, 0.7),
        ("", "5e:aa:02:13:88:01", -83, "WPA2", 11, 0.6),
        ("Pixel_8842", "da:a1:19:42:0c:77", -66, "WPA2", 6, 0.35),
        ("Free Public WiFi", "be:ef:00:66:01:01", -74, "open", 1, 0.15),
        ("AXOLOTL_NET", "de:ad:be:ef:00:01", -58, "open", 6, 0.08),
    ]

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.offsets = {b: 0.0 for _, b, *_ in self.WORLD}

    def scan(self) -> ScanResult:
        aps = []
        for ssid, bssid, base, sec, ch, chance in self.WORLD:
            if self.rng.random() > chance:
                continue
            # Slow random walk, with a rare sudden jump (someone walked by
            # with the hotspot, a door closed...).
            off = self.offsets[bssid] * 0.7 + self.rng.uniform(-3, 3)
            if self.rng.random() < 0.04:
                off += self.rng.choice((-1, 1)) * self.rng.uniform(16, 24)
            self.offsets[bssid] = off
            aps.append(AccessPoint(ssid, bssid, int(max(-95, min(-25, base + off))), sec, ch))
        return ScanResult(self.name, aps, simulated=True)


# --- Real: nmcli ---------------------------------------------------------------
_UNESCAPED_COLON = re.compile(r"(?<!\\):")


def parse_nmcli(text: str) -> list[AccessPoint]:
    """Parse `nmcli -t -f SSID,BSSID,SIGNAL,SECURITY,CHAN dev wifi list`.

    Terse mode escapes ':' inside values as '\\:'. SIGNAL is 0-100 %, which
    we turn into an approximate dBm (100 % ~ -50 dBm, 0 % ~ -100 dBm).
    """
    aps = []
    for line in text.splitlines():
        parts = [p.replace("\\:", ":") for p in _UNESCAPED_COLON.split(line)]
        if len(parts) < 5:
            continue
        ssid, bssid, signal, security, chan = parts[:5]
        try:
            rssi = int(int(signal) / 2 - 100)
        except ValueError:
            continue
        aps.append(AccessPoint(ssid, bssid.lower(), rssi, security or "open",
                               int(chan) if chan.isdigit() else 0))
    return aps


class NmcliWifiScanner(Scanner):
    name = "wifi"
    simulated = False

    def __init__(self, interface: str = "wlan0") -> None:
        self.interface = interface

    def scan(self) -> ScanResult:
        cmd = ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,SECURITY,CHAN", "dev", "wifi", "list",
               "ifname", self.interface, "--rescan", "auto"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            return ScanResult(self.name, error=f"nmcli: {exc}", simulated=False)
        return ScanResult(self.name, parse_nmcli(out), simulated=False)

    def describe(self) -> str:
        return "nmcli"


# --- Real: iw ------------------------------------------------------------------
def parse_iw(text: str) -> list[AccessPoint]:
    """Parse `iw dev wlan0 scan` output into AccessPoints."""
    aps: list[AccessPoint] = []
    cur: dict | None = None

    def flush() -> None:
        if cur:
            sec = cur.get("sec") or ("WEP" if cur.get("privacy") else "open")
            aps.append(AccessPoint(cur.get("ssid", ""), cur["bssid"], int(cur.get("rssi", -100)), sec,
                                   cur.get("chan", 0)))

    for line in text.splitlines():
        m = re.match(r"^BSS ([0-9a-fA-F:]{17})", line)
        if m:
            flush()
            cur = {"bssid": m.group(1).lower()}
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("signal:"):
            cur["rssi"] = float(s.split()[1])
        elif s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif s.startswith("DS Parameter set: channel") or s.startswith("* primary channel:"):
            cur["chan"] = int(s.split()[-1])
        elif s.startswith("RSN:"):
            cur["sec"] = "WPA2"
        elif s.startswith("WPA:") and "sec" not in cur:
            cur["sec"] = "WPA"
        elif s.startswith("capability:") and "Privacy" in s:
            cur["privacy"] = True
    flush()
    return aps


class IwWifiScanner(Scanner):
    name = "wifi"
    simulated = False

    def __init__(self, interface: str = "wlan0") -> None:
        self.interface = interface

    def scan(self) -> ScanResult:
        try:
            proc = subprocess.run(["iw", "dev", self.interface, "scan"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            return ScanResult(self.name, error=f"iw: {exc}", simulated=False)
        if proc.returncode != 0:
            return ScanResult(self.name, error=(proc.stderr.strip() or "iw scan failed (root?)")[:60],
                              simulated=False)
        return ScanResult(self.name, parse_iw(proc.stdout), simulated=False)

    def describe(self) -> str:
        return "iw"
