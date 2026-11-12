"""companion.py — how the main deck talks to the pet board.

A second Pi Zero 2 W is meant to run a Digimon-style pet. This file
defines the "plug" used to reach it (CompanionLink) and two plugs:

  * SimCompanionLink    — a fake pet that lives in this process. It
                          speaks the real protocol, so commands and
                          tests exercise the same message types.
  * SerialCompanionLink — a placeholder for a later UART connection.
                          It is safe to construct, but requests raise
                          a clear "not implemented yet" error.

open_hardware() is the factory that picks a sensor pack plus a
companion link from --mode / CYBERDECK_MODE.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from cyberdeck.protocol import (
    TYPE_HELLO,
    TYPE_HELLO_OK,
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
)
from cyberdeck.sensors import PiSensors, SimSensors, SystemSensors


# Raised when the link is down or a request cannot be completed.
# Command handlers catch this and show a short error in the UI.
class CompanionError(RuntimeError):
    """Link or pet-request failure."""


# Keep a number inside a min/max range.
def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


# Growth stages in order. Gifting an "evolve" item moves one step up.
STAGES = ["egg", "baby", "rookie", "champion", "ultimate", "mega"]

# Item kinds the pet understands in a pet.gift message (see PROTOCOL.md).
GIFT_KINDS = {"food", "toy", "battery", "evolve", "trinket"}


# The fake (and later, reported) pet. Hunger, happiness, and energy
# are 0-100. mood() is derived from those numbers, not stored itself.
@dataclass(slots=True)
class PetState:
    """Simulated (or reported) companion pet."""

    name: str = "PULSE"
    species: str = "Bitling"
    stage: str = "rookie"
    hunger: float = 38.0
    happiness: float = 72.0
    energy: float = 80.0
    age_hours: float = 11.5
    # Gifts PULSE has received (names, newest last). Kept short.
    inventory: list[str] = field(default_factory=list)

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
        """Shape used in protocol replies (and shown by the pet command)."""
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
        self._maybe_evolve()

    def feed(self, item: str = "ration") -> dict[str, Any]:
        """Lower hunger and bump happiness, then return a protocol payload."""
        before = self.hunger
        relief = 28.0 if item in {"ration", "kibble", "food"} else 22.0
        self.hunger = _clamp(self.hunger - relief, 0.0, 100.0)
        self.happiness = _clamp(self.happiness + 8.0, 0.0, 100.0)
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
        before = (self.hunger, self.happiness, self.energy, self.stage)
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
            index = STAGES.index(self.stage) if self.stage in STAGES else 2
            if index < len(STAGES) - 1:
                self.stage = STAGES[index + 1]
                reaction = f"the {name} glows... {self.name} evolved into {self.stage}!"
            else:
                reaction = f"{self.name} is already at max stage. it keeps the {name} as a trophy."
        else:
            self.happiness = _clamp(self.happiness + 10.0, 0.0, 100.0)
            reaction = f"{self.name} adds the {name} to its hoard."
        self.inventory = (self.inventory + [name])[-20:]
        return {
            "accepted": True,
            "item": {"name": name, "kind": kind, "power": int(power)},
            "reaction": reaction,
            "effects": {
                "hunger": int(round(self.hunger - before[0])),
                "happiness": int(round(self.happiness - before[1])),
                "energy": int(round(self.energy - before[2])),
                "stage": self.stage if self.stage != before[3] else None,
            },
            "pet": self.as_payload(),
        }

    def _maybe_evolve(self) -> None:
        """A light stage ladder so the sim can grow over a long run."""
        if self.stage == "egg" and self.age_hours >= 0.2:
            self.stage = "baby"
        elif self.stage == "baby" and self.age_hours >= 4 and self.happiness >= 40:
            self.stage = "rookie"
        elif self.stage == "rookie" and self.age_hours >= 36 and self.happiness >= 60:
            self.stage = "champion"


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

    def connect(self) -> None:
        """Mark the link up and run the hello handshake."""
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
        else:
            self._tick()
        self._linked = True
        reply = self.request(TYPE_HELLO, {"role": "main", "proto": PROTO_VERSION})
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
        self._tick()
        incoming = Message.request(typ, payload)
        return self.handle(incoming)

    def handle(self, message: Message) -> Message:
        """Dispatch one already-decoded request (useful for tests)."""
        typ = message.type
        if typ == TYPE_HELLO:
            return message.reply(
                TYPE_HELLO_OK,
                {
                    "role": "companion",
                    "proto": PROTO_VERSION,
                    "source": self.source,
                    "pet": self.pet.name,
                },
            )
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
        return message.error("unknown_type", f"unsupported message type: {typ}")

    def _tick(self) -> None:
        """Apply elapsed time to the pet so needs change between commands."""
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
            return
        self.pet.tick(max(0.0, now - self._last_tick))
        self._last_tick = now


# Placeholder for the real UART link. connect() does not open a port
# in this scaffold; request() explains what is still missing.
class SerialCompanionLink(CompanionLink):
    """Stub for a UART / serial link to the real companion board.

    Planned later (see docs/PROTOCOL.md):
    - 115200 8N1 on /dev/ttyAMA0 (or CYBERDECK_SERIAL)
    - newline-delimited JSON frames, UTF-8
    - request/response correlated by id, with a read timeout
    """

    def __init__(self, port: str = "/dev/ttyAMA0", baud: int = 115200) -> None:
        self.port = port
        self.baud = baud
        self._linked = False

    def connect(self) -> None:
        # Intentionally not opening a serial device in this scaffold.
        self._linked = False

    def close(self) -> None:
        self._linked = False

    def is_linked(self) -> bool:
        return self._linked

    def link_label(self) -> str:
        return f"LINK DOWN · UART {self.port}"

    def request(self, typ: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> Message:
        raise CompanionError(
            f"UART companion link is not implemented yet (port={self.port}, "
            f"baud={self.baud}, type={typ}). Use --mode sim."
        )


def open_hardware(
    mode: str,
    serial_port: str | None = None,
    seed: int | None = None,
) -> tuple[SystemSensors, CompanionLink]:
    """Build the sensor + companion pair selected by mode or env."""
    normalized = (mode or "sim").strip().lower()
    if normalized == "sim":
        return SimSensors(seed=seed), SimCompanionLink()
    if normalized == "real":
        port = serial_port or os_serial_default()
        return PiSensors(), SerialCompanionLink(port=port)
    raise ValueError(f"unknown hardware mode {mode!r}; expected 'sim' or 'real'")


def os_serial_default() -> str:
    """Serial device path, overridable with the CYBERDECK_SERIAL env var."""
    return os.environ.get("CYBERDECK_SERIAL", "/dev/ttyAMA0")
