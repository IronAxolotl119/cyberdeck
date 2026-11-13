"""PULSE adapter: the companion pet over TCP, with a local stand-in.

In plain English: if the companion program (companion/companion.py) is
running, the dashboard talks to it with the same newline-JSON protocol
the main deck uses (docs/PROTOCOL.md, reusing cyberdeck/protocol.py). It
asks for pet.status every few seconds, shows pet.event pushes as alerts,
and reports each first-time discovery with `pet.discovery` (specified in
companion/docs/DISCOVERY.md). The companion's reply is the source of
truth for XP, reactions, learned or strengthened moves, and level-ups.
If the companion is unlinked, or answers unknown_type, a small local pet
applies the same XP rules so the Digimon tie-ins still work.

Threading: all socket work happens on one background thread. The UI only
calls non-blocking methods and drains results each frame (drain()).
"""

from __future__ import annotations

import queue
import random
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from cyberdeck.protocol import Message, ProtocolError, decode_buffer

from .events import Finding
from .rules import XpRules

TYPE_DISCOVERY = "pet.discovery"        # companion/docs/DISCOVERY.md
TYPE_DISCOVERY_OK = "pet.discovery.ok"
TYPE_COMBAT_STATS = "pet.combat.stats"
TYPE_COMBAT_STATS_OK = "pet.combat.stats.ok"
MAX_MOVE_RANK = 5                       # matches the companion's cap


# ===========================================================================
# Local stand-in pet (used offline, and for XP when the companion lacks
# pet.discovery)
# ===========================================================================
@dataclass
class LocalPet:
    name: str = "PULSE"
    species: str = "Chirplet"
    stage: str = "baby"
    level: int = 1
    xp: int = 0
    energy: int = 80
    moves: dict[str, int] = field(default_factory=lambda: {"Signal Sniff": 1})  # name -> strength

    def xp_to_next(self) -> int:
        return 20 + 15 * (self.level - 1)

    def gain(self, xp: int) -> bool:
        """Add XP; return True on level-up."""
        self.xp += xp
        leveled = False
        while self.xp >= self.xp_to_next():
            self.xp -= self.xp_to_next()
            self.level += 1
            leveled = True
        return leveled

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "species": self.species, "stage": self.stage, "level": self.level,
                "xp": self.xp, "xp_next": self.xp_to_next(), "energy": self.energy,
                "moves": list(self.moves), "move_strength": dict(self.moves)}


# Scanner stand-ins for "no name" (not real names, so not dungeon names).
PLACEHOLDER_LABELS = {"<hidden>", "unnamed", "unknown", "(hidden)", "hidden"}


def discovery_payload(f: Finding) -> dict[str, Any]:
    """The pet.discovery request for a first sighting.

    Network dungeons use the optional extras: rssi (dBm), security (wifi:
    "open", "wpa2"...) and rare:true. The id goes to the companion only,
    which hashes it; the deck never shows or logs it in dungeon notices.
    """
    payload: dict[str, Any] = {"kind": f.kind, "id": f.id, "interest": f.interest, "event": f.event,
                               "label": f.label}
    if str(f.label or "").strip().lower() in PLACEHOLDER_LABELS:
        payload["label"] = ""        # a hidden SSID / nameless device: the companion names it hidden-<id>
    detail = f.detail if isinstance(f.detail, dict) else {}
    rssi = detail.get("rssi")
    if isinstance(rssi, (int, float)) and not isinstance(rssi, bool):
        payload["rssi"] = int(rssi)
    security = detail.get("security")
    if isinstance(security, str) and security.strip():
        payload["security"] = security.strip().lower()
    if detail.get("secured") is True:
        payload["secured"] = True
    if detail.get("rare") is True:
        payload["rare"] = True
    return payload


def apply_discovery(pet: LocalPet, f: Finding, rules: XpRules, rng: random.Random,
                    xp_mult: float = 1.0) -> dict[str, Any] | None:
    """Local fallback version of a pet.discovery.ok body. None if no XP."""
    xp = int(round(rules.xp_for(f) * xp_mult))
    if xp <= 0:
        return None
    leveled = pet.gain(xp)
    result: dict[str, Any] = {"xp": xp, "level": pet.level, "learned_move": None,
                              "strengthened_move": None, "level_up": leveled}
    roll = rules.roll_move(f, pet.moves, rng)
    label = f.label or f.id
    if roll and roll[0] == "learned":
        pet.moves[roll[1]] = 1
        result["learned_move"] = roll[1]
        result["reaction"] = f"{pet.name} learned {roll[1]} from {label}!"
    elif roll:
        pet.moves[roll[1]] = min(MAX_MOVE_RANK, pet.moves.get(roll[1], 1) + 1)
        result["strengthened_move"] = roll[1]
        result["reaction"] = f"{pet.name}'s {roll[1]} grew stronger (lv {pet.moves[roll[1]]})"
    else:
        result["reaction"] = f"{pet.name} logged {label} (+{xp} XP)"
    if leveled:
        result["reaction"] += f"  LEVEL {pet.level}!"
    return result


def env_mood_from_buff(buff: str) -> str:
    """Map the companion's environment.buff label to a short mood word.

    The PULSE panel uses this for the face when the companion is linked.
    """
    low = (buff or "").lower()
    if low.startswith("busy"):
        return "buzzing"
    if low.startswith("curious"):
        return "curious"
    if low.startswith("bored"):
        return "bored"
    if low.startswith("alert"):
        return "alert"
    return "calm"


# ===========================================================================
# TCP link to companion/companion.py
# ===========================================================================
class CompanionLink(threading.Thread):
    """Background client. Reconnects with backoff; never blocks the UI."""

    def __init__(self, host: str, port: int, timeout: float = 1.5, poll: float = 3.0) -> None:
        super().__init__(daemon=True, name="companion-link")
        self.host, self.port, self.timeout, self.poll = host, port, timeout, poll
        self.online = False
        self.pet: dict[str, Any] = {}
        self.events: "queue.Queue[dict]" = queue.Queue()      # pet.event payloads
        self.results: "queue.Queue[tuple]" = queue.Queue()    # (callback, Message)
        self._outbox: "queue.Queue[tuple]" = queue.Queue()
        self._pending: dict[str, tuple[Callable | None, float]] = {}
        self._halt = threading.Event()
        self.status = "connecting"

    # --- called from the UI thread ------------------------------------------
    def request(self, typ: str, payload: dict | None = None, callback: Callable | None = None) -> None:
        self._outbox.put((Message.request(typ, payload or {}), callback))

    def stop(self) -> None:
        self._halt.set()

    # --- background thread ----------------------------------------------------
    def run(self) -> None:
        backoff = 0.5
        while not self._halt.is_set():
            try:
                self._session()
                backoff = 0.5
            except OSError as exc:
                self.status = f"offline ({exc.__class__.__name__})"
            self.online = False
            self._halt.wait(backoff)
            backoff = min(backoff * 2, 10.0)

    def _session(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(0.2)
            buf = ""
            hello = Message.request("hello", {"role": "main", "proto": 1, "events": True, "client": "dash"})
            sock.sendall(hello.encode_bytes())
            self._pending[hello.id] = (None, time.monotonic())
            last_poll = 0.0
            while not self._halt.is_set():
                now = time.monotonic()
                if self.online and now - last_poll >= self.poll:
                    self._outbox.put((Message.request("pet.status", {}), None))
                    last_poll = now
                while not self._outbox.empty():
                    msg, cb = self._outbox.get_nowait()
                    sock.sendall(msg.encode_bytes())
                    self._pending[msg.id] = (cb, now)
                # Expire stale requests; a lost status reply means the link is down.
                for mid, (cb, sent) in list(self._pending.items()):
                    if now - sent > self.timeout * 3:
                        del self._pending[mid]
                        if cb:
                            self.results.put((cb, Message("error", mid, {"code": "timeout", "message": "no reply"})))
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionResetError("companion closed the link")
                try:
                    msgs, buf = decode_buffer(buf + chunk.decode("utf-8", "replace"))
                except ProtocolError:
                    buf = ""
                    continue
                for msg in msgs:
                    self._on_message(msg)

    def _on_message(self, msg: Message) -> None:
        if msg.type == "pet.event":
            if isinstance(msg.payload.get("pet"), dict):
                self.pet = msg.payload["pet"]
            self.events.put(msg.payload)
            return
        cb, _ = self._pending.pop(msg.id, (None, 0))
        if msg.type == "hello.ok":
            self.online = True
            self.status = f"linked {self.host}:{self.port}"
            self._outbox.put((Message.request("pet.status", {}), None))
        elif msg.type == "pet.status.ok" and isinstance(msg.payload.get("pet"), dict):
            self.pet = msg.payload["pet"]
        if cb:
            self.results.put((cb, msg))


# ===========================================================================
# The adapter the dashboard talks to
# ===========================================================================
class PetAdapter:
    """One face for the pet panel, whichever pet is actually answering."""

    def __init__(self, rules: XpRules, link: CompanionLink | None = None, rng: random.Random | None = None) -> None:
        self.rules = rules
        self.link = link
        self.rng = rng or random.Random()
        self.local = LocalPet()
        self.discovery_supported: bool | None = None   # None = not asked yet
        self.combat_supported: bool | None = None
        self.combat: dict[str, Any] = {}               # last pet.combat.stats.ok (ranks)
        self.recent_moves: deque[str] = deque(maxlen=6)  # learned/strengthened, newest first
        self.alerts: deque[dict] = deque(maxlen=20)      # companion pushes + reactions
        self.last_reaction: dict[str, Any] | None = None
        self.mood, self.buff = "calm", ""
        self.xp_mult = 1.0
        self.move_index = 0
        self.on_reaction: Callable[[dict], None] | None = None
        self.on_event: Callable[[dict], None] | None = None

    @property
    def online(self) -> bool:
        return bool(self.link and self.link.online)

    # --- what the panel shows -------------------------------------------------
    def view(self) -> dict[str, Any]:
        base = self.local.as_dict()
        if self.online and self.link and self.link.pet:
            # The companion's pet object is authoritative while the link is up.
            remote = dict(self.link.pet)
            remote.setdefault("moves", [])
            remote["source"] = "LINK"
            # Drop local-only fields that would disagree with the companion.
            base = {**base, **remote}
            if "xp_next" not in self.link.pet:
                base.pop("xp_next", None)
            if "move_strength" not in self.link.pet:
                base.pop("move_strength", None)
        else:
            base["source"] = "LOCAL"
        env = base.get("environment") if isinstance(base.get("environment"), dict) else None
        if env and base.get("source") == "LINK":
            buff = str(env.get("buff") or "")
            base["env_busy"] = env.get("busy")
            base["env_mood"] = env_mood_from_buff(buff)
            base["buff"] = buff
        else:
            base["env_mood"], base["buff"] = self.mood, self.buff
            base["env_busy"] = None
        if self.combat:
            base["combat"] = self.combat
        if self.last_reaction:
            base["last_reaction"] = self.last_reaction
        return base

    def moves(self) -> list[str]:
        v = self.view()
        names = list(v.get("moves") or [])
        for m in self.local.moves:  # discovery-learned moves always usable
            if m not in names:
                names.append(m)
        return names

    def selected_move(self) -> str | None:
        names = self.moves()
        return names[self.move_index % len(names)] if names else None

    def next_move(self) -> str | None:
        self.move_index += 1
        return self.selected_move()

    # --- inputs -----------------------------------------------------------------
    def set_environment(self, mood: str, buff: str) -> None:
        self.mood, self.buff = mood, buff
        self.xp_mult = 1.25 if mood == "buzzing" else 1.0

    def on_finding(self, f: Finding) -> None:
        """Called for every Finding; first sightings earn XP."""
        if not f.first:
            return
        if self.online and self.discovery_supported is not False:
            payload = discovery_payload(f)
            self.link.request(TYPE_DISCOVERY, payload, lambda msg, f=f: self._discovery_reply(f, msg))
            return
        self._local_discovery(f)

    def _local_discovery(self, f: Finding) -> None:
        res = apply_discovery(self.local, f, self.rules, self.rng, self.xp_mult)
        if res:
            self._react(res, "local")

    def _discovery_reply(self, f: Finding, msg: Message) -> None:
        """Apply a pet.discovery reply. Local XP is only a fallback.

        The companion is the source of truth when it answers
        pet.discovery.ok. We fall back to local XP rules only when it
        answers unknown_type (an older companion). Other errors
        (timeout, bad_payload, …) do not invent local XP, so a late
        or lost reply cannot be double-counted.
        """
        if msg.type == TYPE_DISCOVERY_OK:
            self.discovery_supported = True
            payload = dict(msg.payload)
            if isinstance(payload.get("reaction"), str):     # never show a raw MAC / BSSID
                from cyberdeck.netdungeons import scrub_ids

                payload["reaction"] = scrub_ids(payload["reaction"])
            pet = payload.get("pet")
            if self.link and isinstance(pet, dict):
                self.link.pet = pet
            self._react(payload, "companion")
            self._request_combat_stats()
            return
        if msg.payload.get("code") == "unknown_type":
            self.discovery_supported = False   # stop asking; use local rules
            self._local_discovery(f)

    def _react(self, res: dict, source: str) -> None:
        if res.get("learned_move"):
            self.recent_moves.appendleft(f"+ {res['learned_move']}")
        if res.get("strengthened_move"):
            self.recent_moves.appendleft(f"^ {res['strengthened_move']}")
        res["source"] = source
        self.last_reaction = res
        if self.on_reaction:
            self.on_reaction(res)

    def use_move(self, move: str) -> str:
        """Spend a little energy; the app turns the move into a fresh scan."""
        self.local.energy = max(0, self.local.energy - 4)
        return f"{self.view().get('name', 'PULSE')} uses {move}!"

    # --- per-frame housekeeping ----------------------------------------------
    def _request_combat_stats(self) -> None:
        """Ask for move power/rank/bonus so the PULSE panel can show ranks."""
        if not self.online or self.combat_supported is False or not self.link:
            return
        self.link.request(TYPE_COMBAT_STATS, {}, self._combat_reply)

    def _combat_reply(self, msg: Message) -> None:
        if msg.type == TYPE_COMBAT_STATS_OK:
            self.combat_supported = True
            self.combat = dict(msg.payload)
        elif msg.payload.get("code") == "unknown_type":
            self.combat_supported = False

    def drain(self) -> None:
        """Run reply callbacks and forward pushed events (UI thread only)."""
        if not self.link:
            return
        while not self.link.results.empty():
            cb, msg = self.link.results.get_nowait()
            cb(msg)
        while not self.link.events.empty():
            ev = self.link.events.get_nowait()
            if isinstance(ev.get("pet"), dict):
                self.link.pet = ev["pet"]
            self.alerts.appendleft(ev)
            detail = ev.get("detail") or {}
            name = detail.get("move") or detail.get("name")
            kind = ev.get("event")
            if kind == "learned_move" and name:
                note = f"+ {name}"
                if note not in self.recent_moves:
                    self.recent_moves.appendleft(note)
            elif kind == "move_strengthened" and name:
                extra = ""
                if detail.get("rank") is not None:
                    extra += f" r{detail['rank']}"
                if detail.get("bonus") is not None:
                    extra += f" +{detail['bonus']}"
                note = f"^ {name}{extra}"
                if note not in self.recent_moves:
                    self.recent_moves.appendleft(note)
                self._request_combat_stats()
            if self.on_event:
                self.on_event(ev)

    def tick(self) -> None:
        """Slow local energy regen when calm (called about once a second)."""
        if self.mood == "calm" and self.rng.random() < 0.2:
            self.local.energy = min(100, self.local.energy + 1)
