"""LAN mapper: which devices are on YOUR local network.

In plain English: every computer keeps an "ARP table" of the local
devices it has talked to recently. Reading /proc/net/arp is free and
silent. An optional ping sweep (off by default) pings each address on
your own subnet once, gently and rate-limited, so more devices show up
in that table. The sweep refuses anything that is not a private subnet
of /24 or smaller, so it can only ever touch your own network.

- SimLanScanner   a pretend homelab.
- ProcLanScanner  reads /proc/net/arp (+ optional ping sweep).
"""

from __future__ import annotations

import ipaddress
import random
import subprocess
import threading
import time
from pathlib import Path

from ..ratelimit import Cooldown, RateLimiter
from .base import LanHost, ScanResult, Scanner


# --- Simulation ---------------------------------------------------------------
class SimLanScanner(Scanner):
    name = "lan"

    WORLD = [
        ("192.168.1.1", "aa:bb:cc:00:00:01", "router"),
        ("192.168.1.10", "dc:a6:32:01:02:03", "pi-hole"),
        ("192.168.1.20", "b8:27:eb:44:55:66", "homelab-nas"),
        ("192.168.1.21", "52:54:00:12:34:56", "proxmox"),
        ("192.168.1.42", "c0:ff:ee:00:12:34", "cyberdeck-pet"),
        ("192.168.1.77", "3c:22:fb:9a:bc:de", "laptop"),
    ]
    VISITORS = [("192.168.1.115", "a4:83:e7:11:22:33"), ("192.168.1.130", "f2:18:98:aa:00:51")]

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.names = {ip: name for ip, _, name in self.WORLD}

    def scan(self) -> ScanResult:
        hosts = [LanHost(ip, mac, "wlan0") for ip, mac, _ in self.WORLD if self.rng.random() < 0.97]
        for ip, mac in self.VISITORS:
            if self.rng.random() < 0.25:
                hosts.append(LanHost(ip, mac, "wlan0"))
        return ScanResult(self.name, hosts, simulated=True)


# --- Real: /proc/net/arp ------------------------------------------------------
def parse_proc_arp(text: str) -> list[LanHost]:
    """Parse /proc/net/arp. Incomplete entries (flags 0x0) are skipped."""
    hosts = []
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 6:
            continue
        ip, _hw, flags, mac, _mask, dev = cols[:6]
        if flags == "0x0" or mac == "00:00:00:00:00:00":
            continue
        hosts.append(LanHost(ip, mac.lower(), dev))
    return hosts


def guess_subnet() -> str | None:
    """First non-loopback IPv4 network of this machine, via `ip -o -4 addr`."""
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if "inet" in parts and parts[1] != "lo":
            cidr = parts[parts.index("inet") + 1]
            return str(ipaddress.ip_interface(cidr).network)
    return None


def sweep_targets(subnet: str) -> list[str]:
    """Addresses a sweep may ping. Own private subnets only, /24 or smaller."""
    net = ipaddress.ip_network(subnet, strict=False)
    if not net.is_private or net.version != 4 or net.prefixlen < 24:
        raise ValueError(f"refusing to sweep {subnet}: only private /24 (or smaller) subnets")
    return [str(h) for h in net.hosts()]


class PingSweeper:
    """Background, rate-limited, one-ping-per-address sweep of your subnet."""

    def __init__(self, subnet: str, per_second: float = 10, cooldown: float = 600) -> None:
        self.subnet = subnet
        self.limiter = RateLimiter(per_second, per=1.0, burst=max(1, int(per_second)))
        self.cooldown = Cooldown(cooldown)
        self.running = False
        self.done = 0
        self.total = 0
        self.status = "idle"

    def start(self) -> bool:
        if self.running or not self.cooldown.ready("sweep"):
            return False
        try:
            targets = sweep_targets(self.subnet)
        except ValueError as exc:
            self.status = str(exc)
            return False
        self.running, self.done, self.total = True, 0, len(targets)
        threading.Thread(target=self._run, args=(targets,), daemon=True).start()
        return True

    def _run(self, targets: list[str]) -> None:
        procs = []
        self.status = "sweeping"
        for ip in targets:
            while not self.limiter.allow():
                time.sleep(0.05)
            try:
                procs.append(subprocess.Popen(["ping", "-c", "1", "-W", "1", ip],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            except OSError:
                self.status = "ping not available"
                break
            self.done += 1
            procs = [p for p in procs if p.poll() is None]
        for p in procs:
            p.wait()
        self.running = False
        self.status = f"swept {self.done} hosts"


class ProcLanScanner(Scanner):
    name = "lan"
    simulated = False

    def __init__(self, ping_sweep: bool = False, subnet: str = "auto", per_second: float = 10,
                 cooldown: float = 600, arp_path: str = "/proc/net/arp") -> None:
        self.arp_path = Path(arp_path)
        self.sweeper = None
        if ping_sweep:
            net = guess_subnet() if subnet == "auto" else subnet
            if net:
                self.sweeper = PingSweeper(net, per_second, cooldown)

    def scan(self) -> ScanResult:
        if self.sweeper:
            self.sweeper.start()   # no-op while cooling down or already running
        try:
            text = self.arp_path.read_text()
        except OSError as exc:
            return ScanResult(self.name, error=f"arp: {exc}", simulated=False)
        return ScanResult(self.name, parse_proc_arp(text), simulated=False)

    def describe(self) -> str:
        return "arp+ping" if self.sweeper else "arp"
