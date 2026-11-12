"""netcheck.py — is my homelab up? Ping hosts and check ports.

  net hosts                     list the hosts in config/hosts.ini
  net ping <name|address>       one ping (also: `ping <name>`)
  net port <name|address> <port> [port…]   TCP connect test
  net check [name]              ping + port test every host (or one)

config/hosts.ini [hosts] lists your machines:

    nas    = 192.168.1.20   22, 80, 445
    router = 192.168.1.1    53, 80

How the checks work (no root needed, stdlib only):
  * ping uses the system `ping -c 1` when it exists. If it is missing
    or blocked, it falls back to a "TCP ping": timing a connection to
    one of the host's ports (or 22/80/443).
  * port checks open a TCP connection and close it straight away.
    open = something answered, closed = refused, filtered = no answer
    before the timeout.
  * `net check` runs the checks in parallel threads so a dead host
    does not hold everything up.

Everything returns plain result objects, so the logic is tested with a
local test server on 127.0.0.1 — no network needed.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cyberdeck.config import Settings
from cyberdeck.jobs import in_background

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

# Ports tried by the TCP-ping fallback when a host has none configured.
FALLBACK_PORTS = (22, 80, 443)


@dataclass(frozen=True)
class HostEntry:
    """One machine from hosts.ini."""

    name: str
    address: str
    ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class PingResult:
    host: str
    ok: bool
    ms: float | None
    method: str          # "icmp" or "tcp:<port>"
    error: str = ""


@dataclass(frozen=True)
class PortResult:
    host: str
    port: int
    state: str           # open | closed | filtered | error
    ms: float | None = None
    error: str = ""


# --- config ---------------------------------------------------------------

def parse_host_line(name: str, value: str) -> HostEntry | None:
    """'192.168.1.20  22, 80' -> HostEntry. Returns None if empty."""
    parts = value.replace(",", " ").split()
    if not parts:
        return None
    ports = tuple(int(p) for p in parts[1:] if p.isdigit() and 0 < int(p) < 65536)
    return HostEntry(name, parts[0], ports)


def load_hosts(settings: Settings | None = None) -> dict[str, HostEntry]:
    """Every host in hosts.ini [hosts], by name (in file order)."""
    s = settings or Settings.load("hosts.ini")
    out: dict[str, HostEntry] = {}
    for name, value in s.section("hosts").items():
        entry = parse_host_line(name, value)
        if entry:
            out[name] = entry
    return out


@dataclass(frozen=True)
class NetSettings:
    ping_timeout: float = 1.0
    port_timeout: float = 0.8
    workers: int = 8


def load_net_settings(settings: Settings | None = None) -> NetSettings:
    s = settings or Settings.load("network.ini")
    return NetSettings(
        ping_timeout=s.get_float("net", "ping_timeout", 1.0),
        port_timeout=s.get_float("net", "port_timeout", 0.8),
        workers=max(1, s.get_int("net", "workers", 8)),
    )


def resolve(target: str, hosts: dict[str, HostEntry]) -> HostEntry:
    """A name from hosts.ini, or a raw address used as-is."""
    return hosts.get(target) or HostEntry(target, target)


# --- checks -----------------------------------------------------------------

def check_port(host: str, port: int, timeout: float = 0.8) -> PortResult:
    """Try a TCP connection to host:port."""
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return PortResult(host, port, "open", (time.perf_counter() - started) * 1000)
    except ConnectionRefusedError:
        return PortResult(host, port, "closed", (time.perf_counter() - started) * 1000)
    except (socket.timeout, TimeoutError):
        return PortResult(host, port, "filtered", None, "no answer")
    except OSError as exc:
        return PortResult(host, port, "error", None, exc.strerror or str(exc))


_PING_TIME = re.compile(r"time[=<]\s*([\d.]+)\s*ms")


def icmp_ping(host: str, timeout: float = 1.0) -> PingResult | None:
    """Use the system ping tool. None if it is not installed or not allowed."""
    binary = shutil.which("ping")
    if not binary:
        return None
    wait = str(max(1, int(round(timeout))))
    try:
        proc = subprocess.run([binary, "-c", "1", "-W", wait, host], capture_output=True, text=True,
                              timeout=timeout + 2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = proc.stdout + proc.stderr
    if proc.returncode == 0:
        match = _PING_TIME.search(text)
        return PingResult(host, True, float(match.group(1)) if match else None, "icmp")
    if "operation not permitted" in text.lower() or "permission" in text.lower():
        return None  # sandboxed: let the TCP fallback try
    reason = "unknown host" if ("unknown host" in text.lower() or "name or service" in text.lower()) else "no reply"
    return PingResult(host, False, None, "icmp", reason)


def tcp_ping(host: str, ports: tuple[int, ...] = (), timeout: float = 0.8) -> PingResult:
    """Time a TCP handshake. A refused port still proves the host is up."""
    last_error = "no answer"
    for port in ports or FALLBACK_PORTS:
        result = check_port(host, port, timeout)
        if result.state in {"open", "closed"}:
            return PingResult(host, True, result.ms, f"tcp:{port}")
        last_error = result.error or result.state
        if result.state == "error":
            break  # e.g. unknown host: other ports will not help
    return PingResult(host, False, None, "tcp", last_error)


def ping(entry: HostEntry, ns: NetSettings) -> PingResult:
    """ICMP when possible, otherwise the TCP fallback."""
    result = icmp_ping(entry.address, ns.ping_timeout)
    if result is not None and (result.ok or result.error == "unknown host"):
        return result
    tcp = tcp_ping(entry.address, entry.ports, ns.port_timeout)
    return tcp if tcp.ok or result is None else result


def check_host(entry: HostEntry, ns: NetSettings) -> tuple[PingResult, list[PortResult]]:
    """Ping plus every configured port for one host."""
    return ping(entry, ns), [check_port(entry.address, p, ns.port_timeout) for p in entry.ports]


def check_many(entries: list[HostEntry], ns: NetSettings) -> list[tuple[HostEntry, PingResult, list[PortResult]]]:
    """check_host for several hosts at once (threads), results in input order."""
    with ThreadPoolExecutor(max_workers=min(ns.workers, max(1, len(entries)))) as pool:
        results = list(pool.map(lambda e: check_host(e, ns), entries))
    return [(entry, p, ports) for entry, (p, ports) in zip(entries, results)]


# --- formatting -----------------------------------------------------------------

def format_ping(entry: HostEntry, result: PingResult) -> str:
    label = entry.name if entry.name == entry.address else f"{entry.name} ({entry.address})"
    if result.ok:
        ms = f"{result.ms:.1f}ms" if result.ms is not None else "?ms"
        return f"UP    {label}  {ms}  via {result.method}"
    return f"DOWN  {label}  {result.error or 'no reply'}"


def format_port(result: PortResult) -> str:
    ms = f"  {result.ms:.1f}ms" if result.ms is not None and result.state == "open" else ""
    extra = f"  ({result.error})" if result.error and result.state == "error" else ""
    return f"  {result.port:>5}/tcp  {result.state}{ms}{extra}"


def ping_lines(ctx: "Context", target: str) -> list[str]:
    """Used by both `net ping` and `ping <host>`."""
    hosts = load_hosts(Settings.load("hosts.ini", ctx.config_dir))
    ns = load_net_settings(Settings.load("network.ini", ctx.config_dir))
    entry = resolve(target, hosts)
    return [format_ping(entry, ping(entry, ns))]


def host_names(ctx: "Context", args: list[str] | None = None) -> list[str]:
    """Tab completion: names from hosts.ini."""
    return list(load_hosts(Settings.load("hosts.ini", getattr(ctx, "config_dir", None))))


# --- commands --------------------------------------------------------------------

def register(reg: "Registry") -> None:
    """Add the `net` host-checking subcommands (net info lives in netinfo.py)."""

    def net_complete(ctx: "Context", args: list[str]) -> list[str]:
        if args and args[0] in {"ping", "port", "check"} and len(args) == 1:
            return host_names(ctx)
        return []

    @reg.command("net", "Network: info, hosts, ping, port, check.",
                 usage="net [info|hosts|ping|port|check] ...",
                 subcommands=("info", "hosts", "ping", "port", "check"), completer=net_complete)
    def cmd_net(ctx: "Context", args: list[str]) -> list[str]:
        hosts = load_hosts(Settings.load("hosts.ini", ctx.config_dir))
        ns = load_net_settings(Settings.load("network.ini", ctx.config_dir))
        action = args[0].lower() if args else "info"
        if action == "info":
            from cyberdeck.features.netinfo import info_lines

            return info_lines(ctx)
        if action == "hosts":
            if not hosts:
                return ["no hosts yet — add some to config/hosts.ini [hosts]"]
            return [f"  {h.name:<12} {h.address:<18} {', '.join(map(str, h.ports)) or '-'}" for h in hosts.values()]
        if action == "ping":
            if len(args) < 2:
                return ["usage: net ping <name|address>"]
            return in_background(ctx, f"ping {args[1]}", f"ping {args[1]}", lambda: ping_lines(ctx, args[1]))
        if action == "port":
            if len(args) < 3:
                return ["usage: net port <name|address> <port> [port…]"]
            entry = resolve(args[1], hosts)
            ports = [int(p) for p in " ".join(args[2:]).replace(",", " ").split() if p.isdigit()]

            def scan() -> list[str]:
                with ThreadPoolExecutor(max_workers=min(ns.workers, max(1, len(ports)))) as pool:
                    results = list(pool.map(lambda p: check_port(entry.address, p, ns.port_timeout), ports))
                return [f"{entry.name} ({entry.address})"] + [format_port(r) for r in results]

            return in_background(ctx, f"port {entry.name}", f"net port {entry.name}", scan)
        if action == "check":
            targets = [resolve(name, hosts) for name in args[1:]] or list(hosts.values())
            if not targets:
                return ["no hosts to check — add some to config/hosts.ini [hosts]"]

            def check() -> list[str]:
                lines: list[str] = []
                up = 0
                for entry, result, ports in check_many(targets, ns):
                    up += result.ok
                    lines.append(format_ping(entry, result))
                    lines.extend(format_port(r) for r in ports)
                return lines + [f"{up}/{len(targets)} hosts up"]

            # Slow hosts can take a whole timeout, so the TUI runs this on a thread.
            return in_background(ctx, "net check", "net check", check)
        return ["usage: net [info|hosts|ping|port|check] ..."]
