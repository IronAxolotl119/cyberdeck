"""The rule logic: turns raw scans into Findings worth noting.

In plain English: scanners only report "here is what I see right now".
These trackers remember what they saw before and decide what is new or
odd. All of it is plain Python with no hardware, so it is easy to test
(see tests/dash/test_rules.py) and easy to tweak.

Awareness only: these rules flag things for you to look at. A flag is a
hint, not proof of anything malicious, and none of this detects
skimmers.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field

from .events import HIGH, LOW, MEDIUM, Finding
from .scanners.base import AccessPoint, BtDevice, LanHost


# ===========================================================================
# Wi-Fi: anomaly logger + suspicious AP highlighter + RSSI history
# ===========================================================================
@dataclass
class ApState:
    ap: AccessPoint
    first_seen: float
    last_seen: float
    missed: int = 0                      # scans in a row it was absent
    gone: bool = False
    history: deque = field(default_factory=lambda: deque(maxlen=40))
    flags: set = field(default_factory=set)   # "open", "suspicious", "twin"


class WifiTracker:
    """Remembers every BSSID and flags changes between scans.

    Flags raised (Finding.event):
      new             a BSSID never seen before (first=True)
      new_ssid        ...and its network name is new too
      open_network    no encryption (once per BSSID)
      suspicious_name SSID matches a pattern from [wifi] suspicious_patterns
      evil_twin       a trusted SSID broadcast from an unlisted BSSID, or an
                      open AP copying the name of an encrypted one
      rssi_jump       signal changed by >= rssi_jump_db in one scan
      gone/back       missing for gone_after_scans scans / returned
    """

    def __init__(self, rssi_jump_db: int = 15, gone_after: int = 3, history_len: int = 40,
                 patterns: list[str] | None = None, trusted: dict[str, set[str]] | None = None) -> None:
        self.rssi_jump_db = rssi_jump_db
        self.gone_after = gone_after
        self.history_len = history_len
        self.patterns = [p.lower() for p in (patterns or [])]
        self.trusted = {k: {b.lower() for b in v} for k, v in (trusted or {}).items()}
        self.aps: dict[str, ApState] = {}
        self.ssids_seen: set[str] = set()
        self.scans = 0

    # --- single-network checks (also handy on their own) --------------------
    def suspicious_name(self, ssid: str) -> str | None:
        """Return the matching pattern if the SSID looks like bait."""
        low = ssid.lower()
        for pat in self.patterns:
            if pat and pat in low:
                return pat
        return None

    def twin_reason(self, ap: AccessPoint, current: list[AccessPoint]) -> str | None:
        """Why this AP might be an evil twin, or None."""
        if not ap.ssid:
            return None
        allowed = self.trusted.get(ap.ssid)
        if allowed:
            # Your own network: only an unlisted radio is suspicious.
            return None if ap.bssid.lower() in allowed else "trusted name from an unknown radio"
        # Someone else's network: an OPEN copy of an encrypted name is the
        # classic look-alike (the encrypted original is not flagged).
        if ap.is_open:
            for other in current:
                if other.ssid == ap.ssid and other.bssid != ap.bssid and not other.is_open:
                    return f"open copy of a {other.security} network"
        return None

    # --- main entry ----------------------------------------------------------
    def update(self, aps: list[AccessPoint], now: float | None = None) -> list[Finding]:
        now = time.time() if now is None else now
        self.scans += 1
        out: list[Finding] = []
        seen = set()
        for ap in aps:
            bssid = ap.bssid.lower()
            seen.add(bssid)
            label = ap.ssid or "<hidden>"
            st = self.aps.get(bssid)
            if st is None:
                st = ApState(ap=ap, first_seen=now, last_seen=now, history=deque(maxlen=self.history_len))
                self.aps[bssid] = st
                new_name = bool(ap.ssid) and ap.ssid not in self.ssids_seen
                out.append(Finding("wifi", "new_ssid" if new_name else "new", bssid, label, LOW,
                                   {"rssi": ap.rssi, "security": ap.security, "channel": ap.channel},
                                   first=True, ts=now))
            else:
                if st.gone:
                    out.append(Finding("wifi", "back", bssid, label, LOW, {"rssi": ap.rssi}, ts=now))
                prev = st.history[-1] if st.history else st.ap.rssi
                if abs(ap.rssi - prev) >= self.rssi_jump_db:
                    out.append(Finding("wifi", "rssi_jump", bssid, label, MEDIUM,
                                       {"from": prev, "to": ap.rssi}, ts=now))
                st.ap = ap
            st.last_seen, st.missed, st.gone = now, 0, False
            st.history.append(ap.rssi)
            if ap.ssid:
                self.ssids_seen.add(ap.ssid)
            # One-time flags per BSSID.
            if ap.is_open and "open" not in st.flags:
                st.flags.add("open")
                out.append(Finding("wifi", "open_network", bssid, label, MEDIUM, {"rssi": ap.rssi}, ts=now))
            pat = self.suspicious_name(ap.ssid)
            if pat and "suspicious" not in st.flags:
                st.flags.add("suspicious")
                out.append(Finding("wifi", "suspicious_name", bssid, label, MEDIUM, {"pattern": pat}, ts=now))
            reason = self.twin_reason(ap, aps)
            if reason and "twin" not in st.flags:
                st.flags.add("twin")
                out.append(Finding("wifi", "evil_twin", bssid, label, HIGH,
                                   {"reason": reason, "security": ap.security}, ts=now))
        # Anything not in this scan counts a miss; enough misses = gone.
        for bssid, st in self.aps.items():
            if bssid in seen or st.gone:
                continue
            st.missed += 1
            if st.missed >= self.gone_after:
                st.gone = True
                out.append(Finding("wifi", "gone", bssid, st.ap.ssid or "<hidden>", LOW,
                                   {"seen_for_s": int(st.last_seen - st.first_seen)}, ts=now))
        return out

    def visible(self) -> list[ApState]:
        """Current networks, strongest first."""
        return sorted((s for s in self.aps.values() if not s.gone), key=lambda s: -s.ap.rssi)


# ===========================================================================
# Bluetooth: unique devices + "new and lingering" dwell alerts
# ===========================================================================
@dataclass
class BtState:
    dev: BtDevice
    first_seen: float
    last_seen: float
    dwell_alerted: bool = False


class BtTracker:
    """Tracks MACs; alerts when a NEW device stays at least dwell_s seconds.

    Note: many phones randomise their Bluetooth MAC, so one person can look
    like several devices. Treat dwell alerts as "worth a glance", nothing more.
    """

    def __init__(self, dwell_s: float = 600, forget_after: float = 300, known: list[str] | None = None) -> None:
        self.dwell_s = dwell_s
        self.forget_after = forget_after
        self.known = {m.lower() for m in (known or [])}
        self.devices: dict[str, BtState] = {}
        self.unique_total = 0

    def update(self, devs: list[BtDevice], now: float | None = None) -> list[Finding]:
        now = time.time() if now is None else now
        out: list[Finding] = []
        # Forget devices that left long enough ago (a later return is "new" again).
        for mac in [m for m, s in self.devices.items() if now - s.last_seen > self.forget_after]:
            del self.devices[mac]
        for dev in devs:
            mac = dev.mac.lower()
            st = self.devices.get(mac)
            if st is None:
                st = BtState(dev=dev, first_seen=now, last_seen=now)
                self.devices[mac] = st
                self.unique_total += 1
                if mac not in self.known:
                    out.append(Finding("bt", "new", mac, dev.name or "unnamed", LOW,
                                       {"rssi": dev.rssi}, first=True, ts=now))
            else:
                if dev.name:
                    st.dev.name = dev.name
                if dev.rssi is not None:
                    st.dev.rssi = dev.rssi
            st.last_seen = now
            if (mac not in self.known and not st.dwell_alerted
                    and now - st.first_seen >= self.dwell_s):
                st.dwell_alerted = True
                out.append(Finding("bt", "long_stay", mac, st.dev.name or "unnamed", HIGH,
                                   {"minutes": round((now - st.first_seen) / 60, 1), "rssi": st.dev.rssi},
                                   ts=now))
        return out

    def visible(self, now: float | None = None, window: float = 60) -> list[BtState]:
        now = time.time() if now is None else now
        return sorted((s for s in self.devices.values() if now - s.last_seen <= window),
                      key=lambda s: -(s.dev.rssi or -100))


# ===========================================================================
# LAN: hosts on your own subnet (ARP table + optional ping sweep)
# ===========================================================================
class LanTracker:
    """Flags new hosts and an IP whose MAC changed (worth checking)."""

    def __init__(self) -> None:
        self.hosts: dict[str, LanHost] = {}
        self.first_seen: dict[str, float] = {}

    def update(self, hosts: list[LanHost], now: float | None = None) -> list[Finding]:
        now = time.time() if now is None else now
        out: list[Finding] = []
        for h in hosts:
            old = self.hosts.get(h.ip)
            if old is None:
                self.first_seen[h.ip] = now
                out.append(Finding("lan", "new_host", h.ip, h.mac, MEDIUM, {"mac": h.mac, "iface": h.iface},
                                   first=True, ts=now))
            elif old.mac.lower() != h.mac.lower():
                out.append(Finding("lan", "mac_changed", h.ip, h.mac, HIGH,
                                   {"old_mac": old.mac, "new_mac": h.mac}, ts=now))
            self.hosts[h.ip] = h
        return out


# ===========================================================================
# RFID: inventory, unknown-tag alert, presence sessions
# ===========================================================================
@dataclass
class Presence:
    uid: str
    start: float
    last: float
    reads: int = 1


class RfidTracker:
    """Known tags have notes. Unknown tags alert. Presence = a read session."""

    def __init__(self, known: dict[str, str] | None = None, absent_after: float = 4) -> None:
        self.known: dict[str, str] = {k.lower(): v for k, v in (known or {}).items()}
        self.absent_after = absent_after
        self.present: dict[str, Presence] = {}
        self.log: deque = deque(maxlen=50)          # finished sessions
        self.last_uid: str | None = None
        self.alerted: set[str] = set()

    def note(self, uid: str, text: str) -> None:
        self.known[uid.lower()] = text

    def update(self, uids: list[str], now: float | None = None) -> list[Finding]:
        now = time.time() if now is None else now
        out: list[Finding] = []
        for raw in uids:
            uid = raw.lower()
            self.last_uid = uid
            pres = self.present.get(uid)
            if pres is None:
                self.present[uid] = Presence(uid, now, now)
                if uid in self.known:
                    out.append(Finding("rfid", "tag_seen", uid, self.known[uid], LOW, ts=now))
                else:
                    first = uid not in self.alerted
                    self.alerted.add(uid)
                    out.append(Finding("rfid", "unknown_tag", uid, "unknown tag", HIGH, first=first, ts=now))
            else:
                pres.last, pres.reads = now, pres.reads + 1
        # Close sessions for tags no longer on the reader.
        for uid in [u for u, p in self.present.items() if now - p.last > self.absent_after]:
            p = self.present.pop(uid)
            self.log.appendleft(p)
            out.append(Finding("rfid", "tag_left", uid, self.known.get(uid, "unknown tag"), LOW,
                               {"seconds": round(p.last - p.start, 1), "reads": p.reads}, ts=now))
        return out


# ===========================================================================
# Digimon tie-in: XP + move learning from first sightings
# ===========================================================================
# Moves the local sim pet can learn, by which scanner they belong to.
# Using a move runs a fresh scan of that scanner ("all" = every scanner).
MOVE_POOL: dict[str, list[str]] = {
    "wifi": ["Signal Sniff", "Beacon Burst", "Channel Hop"],
    "bt": ["Blue Pulse", "Tooth Echo"],
    "lan": ["Ping Echo", "ARP Whisper"],
    "rfid": ["Tag Bite", "Near Field"],
}


def move_scanner(move: str) -> str:
    """Which scanner a move name triggers (works for companion moves too)."""
    for kind, names in MOVE_POOL.items():
        if move in names:
            return kind
    low = move.lower()
    for kind, words in (("wifi", ("signal", "wave", "beacon", "static", "chirp")),
                        ("bt", ("blue", "tooth")), ("lan", ("ping", "arp", "net")),
                        ("rfid", ("tag", "field", "bite"))):
        if any(w in low for w in words):
            return kind
    return "all"


@dataclass
class XpRules:
    base: dict[str, int] = field(default_factory=lambda: {"wifi": 4, "bt": 3, "lan": 3, "rfid": 8})
    interest_bonus: int = 3
    learn_chance: float = 0.12

    def xp_for(self, f: Finding) -> int:
        """First sightings earn base XP for their kind, plus a bonus per interest above LOW."""
        if not f.first:
            return 0
        return self.base.get(f.kind, 2) + self.interest_bonus * (max(LOW, f.interest) - LOW)

    def roll_move(self, f: Finding, known_moves: dict[str, int], rng: random.Random) -> tuple[str, str] | None:
        """Maybe learn a new move or strengthen a known one.

        Returns ("learned", name), ("strengthened", name) or None.
        Chance is learn_chance * interest (capped at 1).
        """
        if not f.first or f.kind not in MOVE_POOL:
            return None
        if rng.random() >= min(1.0, self.learn_chance * max(LOW, f.interest)):
            return None
        pool = MOVE_POOL[f.kind]
        new = [m for m in pool if m not in known_moves]
        if new:
            return ("learned", rng.choice(new))
        return ("strengthened", rng.choice(pool))


def activity_mood(recent_finds: int, recent_high: int, busy_at: int = 8) -> tuple[str, str]:
    """Environment activity -> (mood word, buff text) for the pet panel."""
    if recent_high >= 2:
        return "alert", "focus +2: high-interest signals nearby"
    if recent_finds >= busy_at:
        return "buzzing", "XP x1.25: busy airwaves"
    if recent_finds >= max(1, busy_at // 3):
        return "curious", "sniffing around"
    return "calm", "energy regen: quiet surroundings"
