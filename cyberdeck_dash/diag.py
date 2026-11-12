"""Simple network diagnostics: ping a host, DNS lookup, interface stats.

In plain English: the same everyday checks you would type in a terminal,
as dashboard menu items:
- ping      `ping -c 3` one host you name (your router, a server, ...)
- dns       resolve a name with socket.getaddrinfo (what your system's
            resolver says)
- ifaces    traffic counters from /proc/net/dev plus each interface's IP
            addresses (from `ip -o addr`), with live rx/tx rates

Slow work (ping, DNS) runs on a short background thread so the screen
never freezes. In sim mode results are made up so it runs anywhere;
set [diag] backend = real (or --mode real) to run the real commands.
"""

from __future__ import annotations

import random
import re
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DiagResult:
    kind: str            # "ping" or "dns"
    target: str
    ok: bool
    summary: str         # one line for the panel
    lines: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)


# --- parsers (pure functions, tested) ---------------------------------------
def parse_proc_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """/proc/net/dev -> {iface: (rx_bytes, tx_bytes)}."""
    out = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        cols = rest.split()
        if len(cols) >= 9:
            out[name.strip()] = (int(cols[0]), int(cols[8]))
    return out


def parse_ip_addr(text: str) -> dict[str, list[str]]:
    """`ip -o addr show` -> {iface: ['192.168.1.5/24', 'fe80::1/64']}."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = line.split()
        for fam in ("inet", "inet6"):
            if fam in parts and len(parts) > parts.index(fam) + 1:
                out.setdefault(parts[1], []).append(parts[parts.index(fam) + 1])
    return out


def parse_ping(text: str) -> tuple[int, int, float | None]:
    """ping output -> (sent, received, avg_ms or None)."""
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", text)
    sent, recv = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    a = re.search(r"= [\d.]+/([\d.]+)/", text)
    return sent, recv, float(a.group(1)) if a else None


def valid_target(text: str) -> bool:
    """Hostnames / IPs only: letters, digits, dots, dashes, colons."""
    return bool(re.fullmatch(r"[A-Za-z0-9.:\-]{1,253}", text)) and not text.startswith("-")


class Diagnostics:
    def __init__(self, simulated: bool = True, rng: random.Random | None = None) -> None:
        self.simulated = simulated
        self.rng = rng or random.Random()
        self.results: deque[DiagResult] = deque(maxlen=12)
        self.busy: set[str] = set()
        self.ifaces: dict[str, dict] = {}
        self._prev: tuple[float, dict[str, tuple[int, int]]] | None = None
        self._sim_bytes = {"wlan0": [812_000_000, 96_000_000], "lo": [4_200_000, 4_200_000]}

    # --- ping / dns (background) --------------------------------------------
    def start(self, kind: str, target: str) -> str:
        target = target.strip()
        if not valid_target(target):
            return f"not a host name or IP: {target!r}"
        if kind in self.busy:
            return f"{kind} already running"
        self.busy.add(kind)
        threading.Thread(target=self._run, args=(kind, target), daemon=True).start()
        return f"{kind} {target} …"

    def run_now(self, kind: str, target: str) -> DiagResult:
        """Same as start() but blocking (tests and snapshots)."""
        res = (self._ping if kind == "ping" else self._dns)(target)
        self.results.appendleft(res)
        return res

    def _run(self, kind: str, target: str) -> None:
        try:
            fn = self._ping if kind == "ping" else self._dns
            self.results.appendleft(fn(target))
        except Exception as exc:
            self.results.appendleft(DiagResult(kind, target, False, f"error: {exc}"[:60]))
        finally:
            self.busy.discard(kind)

    def _ping(self, host: str) -> DiagResult:
        if self.simulated:
            time.sleep(0.3)
            if host.startswith("10.99"):
                return DiagResult("ping", host, False, "3 sent, 0 received (sim)")
            avg = self.rng.uniform(1.2, 30.0)
            return DiagResult("ping", host, True, f"3/3 replies · avg {avg:.1f} ms (sim)")
        proc = subprocess.run(["ping", "-c", "3", "-W", "1", host], capture_output=True, text=True, timeout=15)
        sent, recv, avg = parse_ping(proc.stdout)
        if not sent:
            return DiagResult("ping", host, False, (proc.stderr.strip() or "ping failed")[:60])
        avg_s = f" · avg {avg:.1f} ms" if avg is not None else ""
        return DiagResult("ping", host, recv > 0, f"{recv}/{sent} replies{avg_s}")

    def _dns(self, name: str) -> DiagResult:
        if self.simulated:
            time.sleep(0.2)
            ip = f"93.184.{self.rng.randrange(256)}.{self.rng.randrange(1, 255)}"
            return DiagResult("dns", name, True, f"{ip} (sim)", [ip])
        t0 = time.monotonic()
        try:
            infos = socket.getaddrinfo(name, None)
        except socket.gaierror as exc:
            return DiagResult("dns", name, False, f"no answer: {exc.strerror}")
        addrs = sorted({i[4][0] for i in infos})
        ms = (time.monotonic() - t0) * 1000
        return DiagResult("dns", name, True, f"{', '.join(addrs[:3])} · {ms:.0f} ms", addrs)

    # --- interface stats (cheap, called about once a second) ---------------------
    def refresh_ifaces(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.simulated:
            for v in self._sim_bytes.values():
                v[0] += self.rng.randint(2_000, 90_000)
                v[1] += self.rng.randint(500, 20_000)
            counters = {k: (v[0], v[1]) for k, v in self._sim_bytes.items()}
            addrs = {"wlan0": ["192.168.1.50/24", "fe80::ba27:ebff:fe12:3456/64"], "lo": ["127.0.0.1/8"]}
        else:
            try:
                counters = parse_proc_net_dev(Path("/proc/net/dev").read_text())
            except OSError:
                counters = {}
            try:
                addrs = parse_ip_addr(subprocess.run(["ip", "-o", "addr", "show"], capture_output=True,
                                                     text=True, timeout=3).stdout)
            except (OSError, subprocess.SubprocessError):
                addrs = {}
        prev = self._prev
        self._prev = (now, counters)
        out = {}
        for name, (rx, tx) in counters.items():
            rate = (0.0, 0.0)
            if prev and name in prev[1] and now > prev[0]:
                dt = now - prev[0]
                rate = ((rx - prev[1][name][0]) / dt, (tx - prev[1][name][1]) / dt)
            out[name] = {"rx": rx, "tx": tx, "rx_rate": rate[0], "tx_rate": rate[1], "addrs": addrs.get(name, [])}
        self.ifaces = out


def human_bytes(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"
