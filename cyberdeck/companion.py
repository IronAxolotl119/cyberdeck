"""companion.py — the pet board's side of the conversation, simulated.

A second Pi Zero 2 W runs a Digimon-style pet ("PULSE"). This file has:

  * PetState           — a believable fake pet: needs that drift over
                         time, levels and moves, an evolution ladder,
                         gifts, a 16x16 avatar, and pushed events
  * CompanionLink      — the "plug" the rest of the deck talks through
  * SimCompanionLink   — PetState in this process, speaking the real
                         protocol (the default, and what tests use)

The real connections (TCP to a companion program on the same computer,
or UART between two Pis) live in cyberdeck/link.py. tools/fake_companion.py
serves this same SimCompanionLink over TCP or serial, so you can test
the deck against a separate process before the real pet program is ready.
"""

from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from cyberdeck.protocol import (
    STAGES as _STAGES,
    TYPE_HELLO,
    TYPE_HELLO_OK,
    TYPE_PET_AVATAR,
    TYPE_PET_AVATAR_OK,
    TYPE_PET_EVENT,
    TYPE_PET_FEED,
    TYPE_PET_FEED_OK,
    TYPE_PET_GIFT,
    TYPE_PET_GIFT_OK,
    TYPE_PET_PLAY,
    TYPE_PET_PLAY_OK,
    TYPE_PET_STATUS,
    TYPE_PET_STATUS_OK,
    TYPE_PING,
    TYPE_PONG,
    Message,
    PROTO_VERSION,
    new_id,
)
from cyberdeck.sensors import PiSensors, SimSensors, SystemSensors


# Raised when the link is down or a request cannot be completed.
# Command handlers catch this and show a short error in the UI.
class CompanionError(RuntimeError):
    """The companion could not be reached or did not answer in time."""


def _clamp(value: float, lo: float, hi: float) -> float:
    """Keep value between lo and hi."""
    return max(lo, min(hi, value))


# The evolution ladder (shared with protocol.py).
STAGES = list(_STAGES)

# What the sim pet is called at each stage. A real companion sends its
# own names; the deck shows whatever it gets.
SIM_FORMS = {"egg": "Egg", "baby": "Chirplet", "in-training": "Wavling", "rookie": "Aethling",
             "champion": "Aethlance", "ultimate": "Aethgeist", "mega": "Aethcrown"}

# Item kinds the pet understands in a pet.gift message (see PROTOCOL.md).
GIFT_KINDS = {"food", "toy", "battery", "evolve", "trinket"}

# Moves the sim pet learns as it levels up.
MOVE_LADDER = {2: "static zap", 3: "wave dash", 5: "signal burst", 7: "nova flare"}
XP_PER_LEVEL = 40

# When the sim pet complains (pushed as pet.event): event -> test.
NEED_EVENTS = (
    ("hungry", lambda p: p.hunger >= 80),
    ("tired", lambda p: p.energy <= 15),
    ("sad", lambda p: p.happiness <= 25),
)


# The fake pet. Needs are 0-100: hunger 100 = starving, happiness and
# energy 100 = great. mood() is a word derived from those numbers.
@dataclass(slots=True)
class PetState:
    """Simulated companion pet."""

    name: str = "PULSE"
    stage: str = "rookie"
    hunger: float = 38.0
    happiness: float = 72.0
    energy: float = 80.0
    age_hours: float = 11.5
    level: int = 3
    xp: int = 95
    hp: int = 28
    max_hp: int = 28
    attack: int = 7
    defense: int = 6
    speed: int = 8
    moves: list[str] = field(default_factory=lambda: ["peck", "static zap", "wave dash"])
    care_mistakes: int = 0
    frame: int = 0
    # Gifts PULSE has received (names, newest last). Kept short.
    inventory: list[str] = field(default_factory=list)
    # Events waiting to be pushed: (event name, detail dict).
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _flags: set[str] = field(default_factory=set)

    @property
    def species(self) -> str:
        return SIM_FORMS.get(self.stage, self.stage.title())

    def mood(self) -> str:
        """Pick a short mood word from the current needs."""
        if self.hunger >= 82:
            return "hungry"
        if self.energy <= 18:
            return "tired"
        if self.happiness <= 28:
            return "grumpy"
        if self.happiness >= 85 and self.hunger <= 45:
            return "joyful"
        if self.happiness >= 70:
            return "playful"
        return "content"

    def as_payload(self) -> dict[str, Any]:
        """Shape used in protocol replies (see the Pet object in PROTOCOL.md)."""
        return {
            "name": self.name,
            "species": self.species,
            "stage": self.stage,
            "hunger": int(round(self.hunger)),
            "happiness": int(round(self.happiness)),
            "energy": int(round(self.energy)),
            "mood": self.mood(),
            "age_hours": round(self.age_hours, 2),
            "gifts": len(self.inventory),
            "level": self.level,
            "xp": self.xp,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "attack": self.attack,
            "defense": self.defense,
            "speed": self.speed,
            "moves": list(self.moves),
            "care_mistakes": self.care_mistakes,
            "avatar": {"form": self.species.lower(), "mood": self.mood(), "frame": self.frame},
        }

    def tick(self, dt: float) -> None:
        """Advance needs. dt is elapsed seconds of companion time."""
        if dt <= 0:
            return
        minutes = dt / 60.0
        # Hunger climbs to full in roughly 40 minutes of neglect.
        self.hunger = _clamp(self.hunger + minutes * 2.5, 0.0, 100.0)
        # Happiness fades; faster when hungry.
        fade = 0.8 + (0.04 * max(0.0, self.hunger - 50.0))
        self.happiness = _clamp(self.happiness - minutes * fade, 0.0, 100.0)
        # Energy recovers slowly at rest.
        self.energy = _clamp(self.energy + minutes * 0.9, 0.0, 100.0)
        self.age_hours += dt / 3600.0
        self.frame = 1 - self.frame
        self._maybe_evolve()
        self._check_needs()

    def feed(self, item: str = "ration") -> dict[str, Any]:
        """Lower hunger and bump happiness, then return a protocol payload."""
        before = self.hunger
        relief = 28.0 if item in {"ration", "kibble", "food"} else 22.0
        self.hunger = _clamp(self.hunger - relief, 0.0, 100.0)
        self.happiness = _clamp(self.happiness + 8.0, 0.0, 100.0)
        self._check_needs()
        return {
            "item": item,
            "hunger_delta": int(round(self.hunger - before)),
            "pet": self.as_payload(),
        }

    def play(self, game: str = "spar") -> dict[str, Any]:
        """Play if there is enough energy; otherwise explain why not."""
        if self.energy < 12:
            return {
                "game": game,
                "played": False,
                "reason": "too tired",
                "pet": self.as_payload(),
            }
        before_h, before_e = self.happiness, self.energy
        self.happiness = _clamp(self.happiness + 16.0, 0.0, 100.0)
        self.energy = _clamp(self.energy - 18.0, 0.0, 100.0)
        self.hunger = _clamp(self.hunger + 6.0, 0.0, 100.0)
        self.gain_xp(10)
        self._check_needs()
        return {
            "game": game,
            "played": True,
            "happiness_delta": int(round(self.happiness - before_h)),
            "energy_delta": int(round(self.energy - before_e)),
            "pet": self.as_payload(),
        }

    def gift(self, item: dict[str, Any]) -> dict[str, Any]:
        """Receive an item from the main deck (dungeon loot). See PROTOCOL.md."""
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "trinket").lower()
        if kind not in GIFT_KINDS:
            kind = "trinket"
        try:
            power = max(0.0, min(100.0, float(item.get("power", 10))))
        except (TypeError, ValueError):
            power = 10.0
        clean = {"name": name, "kind": kind, "power": int(power)}
        before = (self.hunger, self.happiness, self.energy, self.stage)
        if kind == "evolve" and self.stage == STAGES[-1]:
            # Already at the last form: refuse, and the deck keeps the item.
            return {
                "accepted": False,
                "item": clean,
                "reaction": f"{self.name} is already {self.stage} — it sniffs the {name} and hands it back.",
                "effects": {"hunger": 0, "happiness": 0, "energy": 0, "stage": None},
                "pet": self.as_payload(),
            }
        if kind == "food":
            self.hunger = _clamp(self.hunger - power, 0.0, 100.0)
            self.happiness = _clamp(self.happiness + 5.0, 0.0, 100.0)
            reaction = f"{self.name} munches the {name}. nom."
        elif kind == "toy":
            self.happiness = _clamp(self.happiness + power, 0.0, 100.0)
            self.energy = _clamp(self.energy - 5.0, 0.0, 100.0)
            reaction = f"{self.name} bats the {name} around happily!"
        elif kind == "battery":
            self.energy = _clamp(self.energy + power, 0.0, 100.0)
            reaction = f"{self.name} plugs into the {name}. charged up!"
        elif kind == "evolve":
            index = STAGES.index(self.stage) if self.stage in STAGES else STAGES.index("rookie")
            self._evolve_to(STAGES[index + 1])
            reaction = f"the {name} glows... {self.name} evolved into {self.species} ({self.stage})!"
        else:
            self.happiness = _clamp(self.happiness + 10.0, 0.0, 100.0)
            reaction = f"{self.name} adds the {name} to its hoard."
        self.inventory = (self.inventory + [name])[-20:]
        self._check_needs()
        return {
            "accepted": True,
            "item": clean,
            "reaction": reaction,
            "effects": {
                "hunger": int(round(self.hunger - before[0])),
                "happiness": int(round(self.happiness - before[1])),
                "energy": int(round(self.energy - before[2])),
                "stage": self.stage if self.stage != before[3] else None,
            },
            "pet": self.as_payload(),
        }

    def gain_xp(self, amount: int) -> None:
        """Add xp; level up (and maybe learn a move) every XP_PER_LEVEL."""
        self.xp += amount
        while self.xp >= self.level * XP_PER_LEVEL:
            self.level += 1
            self.max_hp += 4
            self.hp = self.max_hp
            self.attack += 1
            self.defense += 1
            self.speed += 1
            self.events.append(("level_up", {"level": self.level}))
            move = MOVE_LADDER.get(self.level)
            if move and move not in self.moves:
                self.moves.append(move)
                self.events.append(("learned_move", {"move": move}))

    def avatar(self) -> dict[str, Any]:
        """A 16x16 picture as palette indexes (pet.avatar.ok payload)."""
        return sim_avatar(self.stage, self.mood(), self.frame, self.species.lower())

    def _evolve_to(self, stage: str) -> None:
        old_stage, old_form = self.stage, self.species
        self.stage = stage
        self.events.append(("evolved", {"from": old_form, "to": self.species, "stage": stage,
                                        "from_stage": old_stage}))

    def _check_needs(self) -> None:
        """Queue hungry/tired/sad once each time a need crosses its line."""
        for event, test in NEED_EVENTS:
            if test(self):
                if event not in self._flags:
                    self._flags.add(event)
                    self.events.append((event, {}))
            else:
                self._flags.discard(event)
        # Letting it starve counts as a care mistake (once per time).
        if self.hunger >= 100 and "starving" not in self._flags:
            self._flags.add("starving")
            self.care_mistakes += 1
        elif self.hunger < 90:
            self._flags.discard("starving")

    def _maybe_evolve(self) -> None:
        """A light stage ladder so the sim can grow over a long run."""
        if self.stage == "egg" and self.age_hours >= 0.2:
            self._evolve_to("baby")
        elif self.stage == "baby" and self.age_hours >= 2:
            self._evolve_to("in-training")
        elif self.stage == "in-training" and self.age_hours >= 4 and self.happiness >= 40:
            self._evolve_to("rookie")
        elif self.stage == "rookie" and self.age_hours >= 36 and self.happiness >= 60:
            self._evolve_to("champion")


# --- the sim avatar ---------------------------------------------------------------

# Body colours by stage, so evolving visibly changes the picture.
_STAGE_COLOURS = {"egg": "#f0e6c8", "baby": "#ffd440", "in-training": "#24bab8", "rookie": "#d6ba56",
                  "champion": "#9646d2", "ultimate": "#ff50c8", "mega": "#ff4650"}


def sim_avatar(stage: str, mood: str, frame: int, form: str = "") -> dict[str, Any]:
    """Draw a round critter: body, eyes, a mouth that follows the mood."""
    palette = [None, "#121018", _STAGE_COLOURS.get(stage, "#d6ba56"), "#fff0aa",
               "#ffffff", "#140f1c", "#ff8496", "#78aadc"]
    rows = [[0] * 16 for _ in range(16)]
    for y in range(16):
        for x in range(16):
            d = ((x - 7.5) / 6.5) ** 2 + ((y - 9.0) / 6.0) ** 2
            if d <= 1.0:
                rows[y][x] = 1 if d > 0.78 else 2       # outline, then body
    rows[6][5] = rows[5][6] = 3                          # shine
    for ex in (5, 10):                                   # eyes
        rows[8][ex] = 4
        rows[9][ex] = 1 if mood == "tired" else 5
    rows[10][3] = rows[10][12] = 6                       # blush
    mouth = {"hungry": [(6, 12), (7, 11), (8, 11), (9, 12)], "grumpy": [(6, 12), (7, 11), (8, 11), (9, 12)],
             "tired": [(7, 12), (8, 12)]}.get(mood, [(6, 11), (7, 12), (8, 12), (9, 11)])
    for x, y in mouth:
        rows[y][x] = 1
    if mood == "grumpy":
        rows[10][10] = 7                                 # a tear
    # Antenna wiggles between the two frames.
    tip = 6 if frame else 9
    rows[2][7] = rows[1][7] = 1
    rows[0][tip] = 3
    return {"form": form or stage, "mood": mood, "frame": frame, "width": 16, "height": 16,
            "palette": palette, "rows": rows}


# The interface the rest of the app uses. Commands call request();
# the UI calls is_linked() / link_label() for the status bar.
class CompanionLink(ABC):
    """Transport-agnostic request/response link to the companion board."""

    @abstractmethod
    def connect(self) -> None:
        """Open the link and optionally run a hello handshake."""

    @abstractmethod
    def close(self) -> None:
        """Release the transport. Safe to call more than once."""

    @abstractmethod
    def is_linked(self) -> bool:
        """True when the companion is believed reachable."""

    @abstractmethod
    def request(self, typ: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> Message:
        """Send a request and wait for the correlated reply."""

    def link_label(self) -> str:
        """Short status-bar text for the current link state."""
        return "LINK UP" if self.is_linked() else "LINK DOWN"

    def drain_events(self) -> list[Message]:
        """pet.event messages pushed since the last call (oldest first)."""
        return []

    def pet_snapshot(self) -> dict[str, Any] | None:
        """The newest pet object seen without asking (None = ask instead)."""
        return None


# In-process companion used by --mode sim. It encodes and decodes real
# protocol messages, then updates PetState, so the rest of the app
# looks just like it will on a real serial link.
@dataclass
class SimCompanionLink(CompanionLink):
    """In-process companion that speaks the real protocol end-to-end."""

    pet: PetState = field(default_factory=PetState)
    clock: Callable[[], float] = field(default=time.monotonic)
    source: str = "sim"
    _linked: bool = field(default=False, init=False)
    _last_tick: float | None = field(default=None, init=False)
    _hello: dict[str, Any] | None = field(default=None, init=False)
    # True once a hello asked for pushed events (and we agreed).
    events_enabled: bool = field(default=False, init=False)
    _lock: Any = field(default_factory=threading.RLock, init=False, repr=False)

    def connect(self) -> None:
        """Mark the link up and run the hello handshake (asking for events)."""
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
        else:
            self._tick()
        self._linked = True
        reply = self.request(TYPE_HELLO, {"role": "main", "proto": PROTO_VERSION, "events": True})
        self._hello = reply.payload

    def close(self) -> None:
        """Mark the simulated link down."""
        self._linked = False

    def is_linked(self) -> bool:
        return self._linked

    def link_label(self) -> str:
        return "LINK UP · SIM" if self._linked else "LINK DOWN · SIM"

    def request(self, typ: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> Message:
        """Build a protocol request, tick the pet, and return the reply."""
        del timeout  # Instant in-process; signature matches the real link.
        if not self._linked and typ != TYPE_HELLO:
            raise CompanionError("companion link is down")
        with self._lock:
            self._tick()
            incoming = Message.request(typ, payload)
            return self.handle(incoming)

    def drain_events(self) -> list[Message]:
        """Turn the pet's queued events into pet.event messages."""
        with self._lock:
            self._tick()
            return self.take_events()

    def take_events(self) -> list[Message]:
        """Pop queued events as messages (empty unless events were asked for)."""
        queued, self.pet.events = self.pet.events, []
        if not self.events_enabled:
            return []
        snapshot = self.pet.as_payload()
        return [Message(TYPE_PET_EVENT, new_id(), {"event": event, "detail": detail, "pet": snapshot})
                for event, detail in queued]

    def handle(self, message: Message) -> Message:
        """Dispatch one already-decoded request (used by the fake server too)."""
        typ = message.type
        if typ == TYPE_HELLO:
            hello = {
                "role": "companion",
                "proto": PROTO_VERSION,
                "source": self.source,
                "pet": self.pet.name,
            }
            # Push events only when the main deck asked for them.
            self.events_enabled = (message.payload or {}).get("events") is True
            if self.events_enabled:
                hello["events"] = True
            return message.reply(TYPE_HELLO_OK, hello)
        if typ == TYPE_PING:
            return message.reply(TYPE_PONG, {"ok": True, "source": self.source})
        if typ == TYPE_PET_STATUS:
            return message.reply(TYPE_PET_STATUS_OK, {"pet": self.pet.as_payload()})
        if typ == TYPE_PET_FEED:
            item = str((message.payload or {}).get("item") or "ration")
            return message.reply(TYPE_PET_FEED_OK, self.pet.feed(item))
        if typ == TYPE_PET_PLAY:
            game = str((message.payload or {}).get("game") or "spar")
            return message.reply(TYPE_PET_PLAY_OK, self.pet.play(game))
        if typ == TYPE_PET_GIFT:
            item = (message.payload or {}).get("item")
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                return message.error("bad_item", "pet.gift needs payload.item with a name")
            return message.reply(TYPE_PET_GIFT_OK, self.pet.gift(item))
        if typ == TYPE_PET_AVATAR:
            return message.reply(TYPE_PET_AVATAR_OK, self.pet.avatar())
        return message.error("unknown_type", f"unsupported message type: {typ}")

    def _tick(self) -> None:
        """Apply elapsed time to the pet so needs change between commands."""
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
            return
        self.pet.tick(max(0.0, now - self._last_tick))
        self._last_tick = now


def os_serial_default() -> str:
    """Serial device path: CYBERDECK_SERIAL, else /dev/serial0 (the Pi's UART alias)."""
    return os.environ.get("CYBERDECK_SERIAL", "/dev/serial0")


def open_sensors(mode: str, seed: int | None = None) -> SystemSensors:
    """Sensor pack for --mode sim (fake numbers) or real (Pi sysfs)."""
    normalized = (mode or "sim").strip().lower()
    if normalized == "sim":
        return SimSensors(seed=seed)
    if normalized == "real":
        return PiSensors()
    raise ValueError(f"unknown hardware mode {mode!r}; expected 'sim' or 'real'")
