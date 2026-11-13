"""Moves the creature can learn, and the shared library they come from.

A move is just data: a name, a little power number, and a short description.
Creatures do not invent moves; they look them up here by id. Some moves
unlock automatically when the pet reaches a certain level. Others have no
learn_level and can only show up through a discovery event (for example, a
nearby Bluetooth device "teaching" a handshake).

accuracy, energy_cost, and effect are used by the dungeon combat API
(docs/DUNGEON_INTEGRATION.md). They are safe to ignore in the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Move:
    """One technique the pet can use.

    frozen=True makes instances immutable, so a shared library entry cannot
    be accidentally edited by one creature and change it for everyone else.
    """

    id: str
    name: str
    power: int
    kind: str  # flavor tag: "signal", "strike", "guard", "burst"
    description: str
    # If set, the move is offered automatically when the pet reaches this level.
    # If None, the move is only learnable through a discovery event.
    learn_level: int | None = None
    # Combat extras (0–100 chance to hit, energy spent, optional after-effect).
    accuracy: int = 90
    energy_cost: int = 8
    effect: str | None = None
    # Battle type (pet/battle.py TYPES): NORM, VOLT, DATA, NET, FIRE, ICE, VOID.
    type: str = "NORM"


# A small, original library. Keep this list short so it is easy to read and
# easy to extend later. Keys are the move ids used everywhere else.
MOVE_LIBRARY: dict[str, Move] = {
    "chirp": Move(
        id="chirp",
        name="Chirp",
        power=8,
        kind="signal",
        description="A tiny radio peep. Every Chirplet knows this one.",
        learn_level=1,
        accuracy=95,
        energy_cost=4,
    ),
    "static_snap": Move(
        id="static_snap",
        name="Static Snap",
        power=14,
        kind="strike",
        description="A sharp crackle of built-up static.",
        learn_level=3,
        accuracy=90,
        energy_cost=8,
    ),
    "echo_pulse": Move(
        id="echo_pulse",
        name="Echo Pulse",
        power=18,
        kind="signal",
        description="A bouncing wave that comes back a little louder.",
        learn_level=5,
        accuracy=90,
        energy_cost=10,
    ),
    "bright_arc": Move(
        id="bright_arc",
        name="Bright Arc",
        power=24,
        kind="burst",
        description="A clean, high arc of light between two points.",
        learn_level=8,
        accuracy=80,
        energy_cost=14,
    ),
    "stray_handshake": Move(
        id="stray_handshake",
        name="Stray Handshake",
        power=16,
        kind="signal",
        description="A borrowed greeting packet, picked up from a nearby device.",
        learn_level=None,
        accuracy=85,
        energy_cost=8,
    ),
    "packet_burst": Move(
        id="packet_burst",
        name="Packet Burst",
        power=22,
        kind="burst",
        description="A rushed spray of data borrowed from a chatty beacon.",
        learn_level=None,
        accuracy=80,
        energy_cost=12,
    ),
    "noise_veil": Move(
        id="noise_veil",
        name="Noise Veil",
        power=0,
        kind="guard",
        description="A curtain of hiss that makes the user harder to pin down.",
        learn_level=None,
        accuracy=100,
        energy_cost=6,
        effect="guard",
    ),
    "signal_sniff": Move(
        id="signal_sniff",
        name="Signal Sniff",
        power=12,
        kind="signal",
        description="A curious sniff at a new Wi-Fi beacon.",
        learn_level=None,
        accuracy=90,
        energy_cost=6,
    ),
    "beacon_burst": Move(
        id="beacon_burst",
        name="Beacon Burst",
        power=18,
        kind="burst",
        description="A short, bright pop copied from a loud access point.",
        learn_level=None,
        accuracy=85,
        energy_cost=10,
    ),
    "channel_hop": Move(
        id="channel_hop",
        name="Channel Hop",
        power=15,
        kind="signal",
        description="A quick skip across nearby Wi-Fi channels.",
        learn_level=None,
        accuracy=90,
        energy_cost=8,
    ),
    "blue_pulse": Move(
        id="blue_pulse",
        name="Blue Pulse",
        power=13,
        kind="signal",
        description="A soft pulse picked up from a Bluetooth advert.",
        learn_level=None,
        accuracy=90,
        energy_cost=6,
    ),
    "tooth_echo": Move(
        id="tooth_echo",
        name="Tooth Echo",
        power=16,
        kind="signal",
        description="An echo of a nearby Bluetooth handshake.",
        learn_level=None,
        accuracy=85,
        energy_cost=8,
    ),
    "ping_echo": Move(
        id="ping_echo",
        name="Ping Echo",
        power=14,
        kind="signal",
        description="A bounced ping learned from a LAN host.",
        learn_level=None,
        accuracy=95,
        energy_cost=6,
    ),
    "arp_whisper": Move(
        id="arp_whisper",
        name="ARP Whisper",
        power=17,
        kind="signal",
        description="A quiet address-resolution murmur from the local net.",
        learn_level=None,
        accuracy=90,
        energy_cost=8,
    ),
    "tag_bite": Move(
        id="tag_bite",
        name="Tag Bite",
        power=20,
        kind="strike",
        description="A sharp nibble inspired by a close-range RFID tag.",
        learn_level=None,
        accuracy=90,
        energy_cost=10,
    ),
    "near_field": Move(
        id="near_field",
        name="Near Field",
        power=16,
        kind="signal",
        description="A tight, close-range field learned from a badge or tag.",
        learn_level=None,
        accuracy=95,
        energy_cost=8,
    ),
}

# Built-in last-resort dungeon action (not a learnable library move).
# Used when the pet is too tired even for Chirp: costs nothing, deals no
# damage, and halves the next incoming hit.
GUARD_MOVE = Move(
    id="guard",
    name="Guard",
    power=0,
    kind="guard",
    description="A last-resort brace that costs no energy.",
    learn_level=None,
    accuracy=100,
    energy_cost=0,
    effect="guard",
)


# Battle types for every move (see pet/battle.py for the chart).
MOVE_TYPES: dict[str, str] = {
    "chirp": "NORM",
    "static_snap": "VOLT",
    "echo_pulse": "NET",
    "bright_arc": "FIRE",
    "stray_handshake": "NET",
    "packet_burst": "DATA",
    "noise_veil": "VOID",
    "signal_sniff": "DATA",
    "beacon_burst": "FIRE",
    "channel_hop": "NET",
    "blue_pulse": "ICE",
    "tooth_echo": "ICE",
    "ping_echo": "DATA",
    "arp_whisper": "VOID",
    "tag_bite": "VOLT",
    "near_field": "VOLT",
    "guard": "NORM",
}
MOVE_LIBRARY = {key: replace(move, type=MOVE_TYPES.get(key, "NORM")) for key, move in MOVE_LIBRARY.items()}
GUARD_MOVE = replace(GUARD_MOVE, type="NORM")


def get_move(move_id: str) -> Move:
    """Look up a move by id, or raise KeyError if it is not in the library."""
    if move_id not in MOVE_LIBRARY:
        raise KeyError(f"Unknown move id: {move_id!r}")
    return MOVE_LIBRARY[move_id]


def find_move(name_or_id: str) -> Move | None:
    """Find a library move by id or display name (case-insensitive)."""
    key = name_or_id.strip().lower()
    if key in MOVE_LIBRARY:
        return MOVE_LIBRARY[key]
    for move in MOVE_LIBRARY.values():
        if move.name.lower() == key:
            return move
    if key in {"guard", "brace", "defend"}:
        return GUARD_MOVE
    return None


def moves_for_level(level: int) -> list[Move]:
    """Return every library move that is learnable at or below this level.

    Discovery-only moves (learn_level is None) are never included.
    """
    learned = [
        move
        for move in MOVE_LIBRARY.values()
        if move.learn_level is not None and move.learn_level <= level
    ]
    return sorted(learned, key=lambda move: (move.learn_level or 0, move.id))
