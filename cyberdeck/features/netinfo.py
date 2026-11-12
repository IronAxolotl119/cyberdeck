"""netinfo.py — the network info screen and the live "net" panel.

  net info  (or just `net`)   IP addresses, gateway, DNS, and traffic rates

Where the numbers come from (all plain Linux files, no root, no installs):
  /proc/net/dev        bytes received/sent per interface (rx / tx)
  /proc/net/route      the default gateway (stored as little-endian hex)
  /etc/resolv.conf     DNS servers
  `ip -o -4 addr`      interface addresses, when the ip tool exists;
                       otherwise a UDP-socket trick finds the main IP
                       (no packet is actually sent)

Rates are worked out by comparing two byte counts taken a moment
apart (RateMeter). The "net" panel keeps one RateMeter alive so its
up/down numbers update live; `net info` takes two samples 0.5 s apart.

The parse_* functions take file text, so they are unit-tested.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cyberdeck.activity import Row
from cyberdeck.config import Settings
from cyberdeck.panes import register_pane

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

PROC_DEV = Path("/proc/net/dev")
PROC_ROUTE = Path("/proc/net/route")
RESOLV = Path("/etc/resolv.conf")


# --- parsers ----------------------------------------------------------------

def parse_proc_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """/proc/net/dev -> {interface: (rx_bytes, tx_bytes)}."""
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 9:
            out[name.strip()] = (int(fields[0]), int(fields[8]))
    return out


def hex_to_ip(value: str) -> str:
    """'0101A8C0' (little-endian) -> '192.168.1.1'."""
    raw = int(value, 16)
    return ".".join(str((raw >> shift) & 0xFF) for shift in (0, 8, 16, 24))


def parse_default_routes(text: str) -> dict[str, str]:
    """/proc/net/route -> {interface: gateway IP} for default routes."""
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000":
            out[fields[0]] = hex_to_ip(fields[2])
    return out


def parse_ip_addr(text: str) -> dict[str, list[str]]:
    """`ip -o -4 addr show` -> {interface: ['192.168.1.5/24', …]}."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[2] == "inet":
            out.setdefault(fields[1], []).append(fields[3])
    return out


def parse_resolv(text: str) -> list[str]:
    """DNS servers from resolv.conf."""
    return [line.split()[1] for line in text.splitlines()
            if line.strip().startswith("nameserver") and len(line.split()) > 1]


def human_rate(bytes_per_s: float | None) -> str:
    """1536.0 -> '1.5 KB/s'."""
    if bytes_per_s is None:
        return "--"
    value = float(bytes_per_s)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return "--"


def human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "--"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return ""


# --- live rates ----------------------------------------------------------------

@dataclass
class RateMeter:
    """Turns two byte-counter samples into rx/tx bytes per second."""

    min_gap: float = 0.8  # seconds between samples used for a rate
    _last: tuple[float, dict[str, tuple[int, int]]] | None = None
    rates: dict[str, tuple[float, float]] = field(default_factory=dict)

    def feed(self, now: float, counters: dict[str, tuple[int, int]]) -> dict[str, tuple[float, float]]:
        """Give the meter a sample; returns the latest rates per interface."""
        if self._last is None:
            self._last = (now, counters)
            return self.rates
        then, old = self._last
        gap = now - then
        if gap < self.min_gap:
            return self.rates
        for name, (rx, tx) in counters.items():
            if name in old:
                orx, otx = old[name]
                # Counters can reset (interface restart): never show a negative rate.
                self.rates[name] = (max(0, rx - orx) / gap, max(0, tx - otx) / gap)
        self._last = (now, counters)
        return self.rates

    def sample(self, now: float | None = None) -> dict[str, tuple[float, float]]:
        return self.feed(time.monotonic() if now is None else now, parse_proc_net_dev(_read(PROC_DEV)))


# --- gathering info ---------------------------------------------------------------

@dataclass
class NetInfo:
    hostname: str
    addresses: dict[str, list[str]]
    gateways: dict[str, str]
    dns: list[str]
    counters: dict[str, tuple[int, int]]
    main_ip: str | None


def primary_ip() -> str | None:
    """The address used to reach the outside world (no packets sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET address; UDP connect sends nothing
            return sock.getsockname()[0]
    except OSError:
        return None


def gather() -> NetInfo:
    """Read everything for the info screen."""
    addresses: dict[str, list[str]] = {}
    if shutil.which("ip"):
        try:
            out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=3).stdout
            addresses = parse_ip_addr(out)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return NetInfo(
        hostname=socket.gethostname(),
        addresses=addresses,
        gateways=parse_default_routes(_read(PROC_ROUTE)),
        dns=parse_resolv(_read(RESOLV)),
        counters=parse_proc_net_dev(_read(PROC_DEV)),
        main_ip=primary_ip(),
    )


def main_interface(info: NetInfo, preferred: str = "auto") -> str | None:
    """The interface to feature: the configured one, or the default route's."""
    if preferred and preferred != "auto":
        return preferred
    if info.gateways:
        return next(iter(info.gateways))
    others = [name for name in info.counters if name != "lo"]
    return others[0] if others else None


def info_lines(ctx: "Context") -> list[str]:
    """The `net info` screen."""
    meter = RateMeter(min_gap=0.4)
    meter.sample(time.monotonic())
    time.sleep(0.5)
    rates = meter.sample(time.monotonic())
    info = gather()
    preferred = Settings.load("network.ini", ctx.config_dir).get_str("net", "interface", "auto")
    main = main_interface(info, preferred)
    lines = [
        f"host      {info.hostname}",
        f"main ip   {info.main_ip or 'offline'}" + (f"  ({main})" if main else ""),
        f"gateway   {', '.join(f'{gw} via {i}' for i, gw in info.gateways.items()) or 'none (offline?)'}",
        f"dns       {', '.join(info.dns) or '--'}",
        "interfaces:",
    ]
    for name, (rx, tx) in info.counters.items():
        addr = ", ".join(info.addresses.get(name, [])) or "-"
        rx_rate, tx_rate = rates.get(name, (None, None))
        mark = "*" if name == main else " "
        lines.append(f" {mark}{name:<10} {addr:<18} ↓{human_rate(rx_rate):>10} ↑{human_rate(tx_rate):>10}"
                     f"   total ↓{human_bytes(rx)} ↑{human_bytes(tx)}")
    if not info.counters:
        lines.append("  (no /proc/net/dev — not a Linux system?)")
    return lines


# --- the live panel ----------------------------------------------------------------

def net_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    """IP, gateway, and live up/down rates for the main interface."""
    state = ctx.services.setdefault("net_pane", {"meter": RateMeter(), "info": None, "at": 0.0})
    now = time.monotonic()
    rates = state["meter"].sample(now)
    if state["info"] is None or now - state["at"] > 15:  # addresses change rarely
        state["info"], state["at"] = gather(), now
    info: NetInfo = state["info"]
    main = main_interface(info, Settings.load("network.ini", ctx.config_dir).get_str("net", "interface", "auto"))
    rx, tx = rates.get(main or "", (None, None))
    rows: list[Row] = [
        [("ip   ", "dim"), (info.main_ip or "offline", "ok" if info.main_ip else "warn")],
        [("gw   ", "dim"), (info.gateways.get(main or "", "--"), "text")],
        [("if   ", "dim"), (main or "--", "text")],
        [("down ", "dim"), (human_rate(rx), "ok")],
        [("up   ", "dim"), (human_rate(tx), "prompt")],
    ]
    return rows[:height]


def register(reg: "Registry") -> None:
    """The `net info` command itself is part of `net` (netcheck.py); this adds the panel."""
    del reg
    register_pane("net", "NET", net_pane)
