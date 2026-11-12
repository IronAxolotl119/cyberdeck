"""wifi.py — scan for wifi networks and watch signal strength live.

  wifi            the network you are on and its signal
  wifi scan       list nearby networks, strongest first, with signal bars
  wifi meter      full-screen live signal meter (any key to leave)

Where the data comes from (config/network.ini [wifi] backend):
  * nmcli  — NetworkManager's tool (Raspberry Pi OS Bookworm has it).
             Scans without root.
  * iw     — `iw dev wlan0 scan`. Usually needs root (sudo), so the deck
             falls back to `iw dev wlan0 link` for the current network.
  * sim    — made-up networks that drift, so this works on any laptop.
  * auto   — sim in --mode sim; in --mode real, nmcli, then iw.

The live meter reads /proc/net/wireless (cheap, no root) on a real
board, or the simulated sensors in sim mode.

The parse_* functions take the raw text a tool prints, so they are
unit-tested with captured examples instead of a real radio.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cyberdeck.activity import Activity, Row
from cyberdeck.config import Settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


@dataclass(frozen=True)
class Network:
    """One network seen in a scan."""

    ssid: str
    dbm: int
    channel: int | None = None
    security: str = ""
    in_use: bool = False

    @property
    def quality(self) -> int:
        return dbm_to_quality(self.dbm)


# --- signal maths -------------------------------------------------------

def dbm_to_quality(dbm: float) -> int:
    """Rough 0-100% quality from dBm (-100 dBm = 0%, -50 dBm = 100%)."""
    return int(max(0, min(100, 2 * (dbm + 100))))


def quality_to_dbm(quality: float) -> int:
    """Inverse of dbm_to_quality (nmcli reports percent)."""
    return int(round(quality / 2 - 100))


def signal_bars(dbm: float, width: int = 10, unicode: bool = True) -> str:
    """A bar like ▂▄▆█ scaled to quality, or #### in ASCII."""
    filled = int(round(dbm_to_quality(dbm) / 100 * width))
    if unicode:
        return "█" * filled + "░" * (width - filled)
    return "#" * filled + "." * (width - filled)


def signal_word(dbm: float) -> str:
    """Plain-English signal rating."""
    if dbm >= -55:
        return "excellent"
    if dbm >= -67:
        return "good"
    if dbm >= -75:
        return "fair"
    if dbm >= -85:
        return "weak"
    return "unusable"


# --- parsers for tool output -----------------------------------------------

def _split_terse(line: str) -> list[str]:
    """Split one `nmcli -t` line on ':' while honouring '\\:' escapes."""
    fields, current, escape = [], [], False
    for ch in line:
        if escape:
            current.append(ch)
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def parse_nmcli(text: str) -> list[Network]:
    """Parse `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY,CHAN dev wifi list`."""
    out: list[Network] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = _split_terse(line)
        if len(parts) < 5:
            continue
        in_use, ssid, signal, security, chan = parts[:5]
        try:
            dbm = quality_to_dbm(float(signal))
        except ValueError:
            continue
        out.append(Network(ssid or "(hidden)", dbm, int(chan) if chan.isdigit() else None,
                           security or "open", in_use.strip() == "*"))
    return out


def parse_iw_scan(text: str) -> list[Network]:
    """Parse `iw dev <iface> scan` output into networks."""
    out: list[Network] = []
    current: dict | None = None

    def flush() -> None:
        if current and current.get("dbm") is not None:
            out.append(Network(current.get("ssid") or "(hidden)", current["dbm"], current.get("chan"),
                               current.get("sec", "open"), current.get("use", False)))

    for raw in text.splitlines():
        line = raw.strip()
        if raw.startswith("BSS "):
            flush()
            current = {"use": "associated" in raw}
        elif current is None:
            continue
        elif line.startswith("signal:"):
            match = re.search(r"(-?\d+(?:\.\d+)?)", line)
            current["dbm"] = int(float(match.group(1))) if match else None
        elif line.startswith("SSID:"):
            current["ssid"] = line[5:].strip()
        elif "primary channel:" in line or line.startswith("DS Parameter set: channel"):
            match = re.search(r"(\d+)\s*$", line)
            if match:
                current["chan"] = int(match.group(1))
        elif line.startswith("RSN:") or line.startswith("WPA:"):
            current["sec"] = "WPA2" if line.startswith("RSN") else "WPA"
    flush()
    return out


def parse_proc_wireless(text: str) -> dict[str, int]:
    """Parse /proc/net/wireless -> {interface: signal dBm}."""
    out: dict[str, int] = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 3:
            try:
                out[name.strip()] = int(float(fields[2].rstrip(".")))
            except ValueError:
                continue
    return out


def dedupe(networks: list[Network]) -> list[Network]:
    """One row per SSID (strongest access point wins), strongest first."""
    best: dict[str, Network] = {}
    for net in networks:
        key = net.ssid
        if key not in best or net.dbm > best[key].dbm or net.in_use:
            best[key] = net
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.dbm))


# --- backends ----------------------------------------------------------------

SIM_NETWORKS = [("DECK-NET", 6, "WPA2"), ("homelab-5G", 36, "WPA2"), ("CoffeeShop", 11, "open"),
                ("NETGEAR42", 1, "WPA2"), ("PrettyFlyForAWiFi", 6, "WPA3"), ("DIRECT-printer", 11, "WPA2")]


class WifiScanner:
    """Picks a backend and returns scans / live signal readings."""

    def __init__(self, ctx: "Context", settings: Settings | None = None) -> None:
        s = settings or Settings.load("network.ini", ctx.config_dir)
        self.ctx = ctx
        self.interface = s.get_str("wifi", "interface", "wlan0")
        want = s.get_str("wifi", "backend", "auto").lower()
        if want == "auto":
            if ctx.mode == "sim":
                want = "sim"
            elif shutil.which("nmcli"):
                want = "nmcli"
            elif shutil.which("iw"):
                want = "iw"
            else:
                want = "none"
        self.backend = want
        self._rng = random.Random(42)

    def scan(self) -> tuple[list[Network], str]:
        """Return (networks, note). note explains fallbacks or errors."""
        if self.backend == "sim":
            return self._sim_scan(), "simulated scan"
        if self.backend == "nmcli":
            text, err = _run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN", "dev", "wifi", "list", "--rescan", "auto"])
            return (dedupe(parse_nmcli(text)), "nmcli") if text else ([], f"nmcli failed: {err}")
        if self.backend == "iw":
            text, err = _run(["iw", "dev", self.interface, "scan"])
            if text:
                return dedupe(parse_iw_scan(text)), "iw scan"
            # Scanning usually needs root; show at least the current link.
            link, _ = _run(["iw", "dev", self.interface, "link"])
            nets = parse_iw_scan("BSS (associated)\n" + link) if link else []
            return nets, f"iw scan needs root ({err.strip() or 'permission denied'}); showing current link only"
        return [], "no wifi tool found (install network-manager or iw), or set backend = sim"

    def signal(self) -> tuple[str | None, int | None]:
        """(ssid, dBm) of the current connection, for the live meter."""
        if self.backend == "sim":
            r = self.ctx.sensors.read()
            return r.wifi_ssid, r.wifi_dbm
        try:
            with open("/proc/net/wireless", encoding="ascii") as fh:
                levels = parse_proc_wireless(fh.read())
        except OSError:
            levels = {}
        dbm = levels.get(self.interface) or (next(iter(levels.values())) if levels else None)
        return None, dbm

    def _sim_scan(self) -> list[Network]:
        own = self.ctx.sensors.read().wifi_dbm or -55
        nets = [Network("DECK-NET", own, 6, "WPA2", True)]
        for i, (ssid, chan, sec) in enumerate(SIM_NETWORKS[1:]):
            dbm = int(-58 - i * 6 + self._rng.uniform(-4, 4))
            nets.append(Network(ssid, dbm, chan, sec))
        return dedupe(nets)


def _run(argv: list[str], timeout: float = 12.0) -> tuple[str, str]:
    """Run a tool and return (stdout, error text). Never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    return proc.stdout, ""


def scanner(ctx: "Context") -> WifiScanner:
    return ctx.service("wifi", lambda: WifiScanner(ctx))


# --- live meter ----------------------------------------------------------------

class SignalMeter(Activity):
    """Full-screen live signal meter with a scrolling history graph."""

    title = "WIFI METER"

    def __init__(self, scan: WifiScanner, interval: float = 0.5) -> None:
        super().__init__()
        self.scanner = scan
        self.tick = max(0.1, interval)
        self.history: deque[int] = deque(maxlen=200)
        self.ssid: str | None = None
        self._next = 0.0

    def on_key(self, key: str) -> None:
        self.done = True  # any key leaves

    def update(self, now: float) -> None:
        if now >= self._next:
            self._next = now + self.tick
            ssid, dbm = self.scanner.signal()
            self.ssid = ssid or self.ssid
            if dbm is not None:
                self.history.append(dbm)

    def render(self, width: int, height: int) -> list[Row]:
        rows: list[Row] = [[(f" WIFI SIGNAL METER  ·  {self.ssid or self.scanner.interface}", "title")], []]
        if not self.history:
            return rows + [[("  no signal reading (not connected, or no /proc/net/wireless)", "warn")],
                           [("  press any key to go back", "dim")]]
        dbm = self.history[-1]
        role = "ok" if dbm >= -67 else "warn" if dbm >= -80 else "alert"
        bar_w = max(10, width - 30)
        rows.append([(f"  {dbm:>4} dBm  ", role), (signal_bars(dbm, bar_w), role), (f"  {dbm_to_quality(dbm)}%", "text")])
        rows.append([(f"  {signal_word(dbm)}", role), (f"   min {min(self.history)}  max {max(self.history)}", "dim")])
        rows.append([])
        # A small scrolling graph of recent readings, newest on the right.
        graph_h = max(3, min(12, height - 8))
        samples = list(self.history)[-(width - 4):]
        for level in range(graph_h, 0, -1):
            line = "".join("█" if dbm_to_quality(s) >= level * 100 / graph_h else " " for s in samples)
            rows.append([("  ", "dim"), (line, "ok")])
        rows.append([])
        rows.append([("  any key to go back", "dim")])
        return rows[:height]

    def headless(self) -> list[str]:
        self.update(time.time())
        if not self.history:
            return ["wifi: no signal reading"]
        dbm = self.history[-1]
        return [f"signal {dbm} dBm  {signal_bars(dbm, 20, False)}  {dbm_to_quality(dbm)}%  {signal_word(dbm)}"]


# --- commands -----------------------------------------------------------------

def register(reg: "Registry") -> None:
    """Add the wifi command."""

    @reg.command("wifi", "Wifi status, scan nearby networks, or a live signal meter.",
                 usage="wifi [scan|meter]", subcommands=("scan", "meter"))
    def cmd_wifi(ctx: "Context", args: list[str]) -> list[str]:
        scan = scanner(ctx)
        action = args[0].lower() if args else "status"
        if action == "scan":
            nets, note = scan.scan()
            lines = [f"{'':2}{'SSID':<20} {'dBm':>4} {'signal':<12} {'ch':>3}  security   ({note})"]
            for n in nets:
                mark = "* " if n.in_use else "  "
                lines.append(f"{mark}{n.ssid[:20]:<20} {n.dbm:>4} {signal_bars(n.dbm, 10, False):<12} "
                             f"{n.channel or '':>3}  {n.security}")
            return lines if nets else [note]
        if action == "meter":
            interval = Settings.load("network.ini", ctx.config_dir).get_float("wifi", "meter_interval", 0.5)
            ctx.activity = SignalMeter(scan, interval)
            return []
        if action == "status":
            ssid, dbm = scan.signal()
            if dbm is None:
                return [f"wifi: not connected or no reading ({scan.backend} backend, {scan.interface})"]
            return [f"wifi: {ssid or scan.interface}  {dbm} dBm  {signal_bars(dbm, 12, False)}  {signal_word(dbm)}",
                    f"backend: {scan.backend}"]
        return ["usage: wifi [scan|meter]"]
