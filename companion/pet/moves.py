"""Moves the creature can learn, and the shared library they come from.

A move is just data: a name, a little power number, and a short description.
Creatures do not invent moves; they look them up here by id. Some moves
unlock automatically when the pet reaches a certain level. Others have no
learn_level and can only show up through a discovery event (for example, a
nearby Bluetooth device "teaching" a handshake).
"""

from __future__ import annotations

from dataclasses import dataclass


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
    ),
    "static_snap": Move(
        id="static_snap",
        name="Static Snap",
        power=14,
        kind="strike",
        description="A sharp crackle of built-up static.",
        learn_level=3,
    ),
    "echo_pulse": Move(
        id="echo_pulse",
        name="Echo Pulse",
        power=18,
        kind="signal",
        description="A bouncing wave that comes back a little louder.",
        learn_level=5,
    ),
    "bright_arc": Move(
        id="bright_arc",
        name="Bright Arc",
        power=24,
        kind="burst",
        description="A clean, high arc of light between two points.",
        learn_level=8,
    ),
    "stray_handshake": Move(
        id="stray_handshake",
        name="Stray Handshake",
        power=16,
        kind="signal",
        description="A borrowed greeting packet, picked up from a nearby device.",
        learn_level=None,
    ),
    "packet_burst": Move(
        id="packet_burst",
        name="Packet Burst",
        power=22,
        kind="burst",
        description="A rushed spray of data borrowed from a chatty beacon.",
        learn_level=None,
    ),
    "noise_veil": Move(
        id="noise_veil",
        name="Noise Veil",
        power=10,
        kind="guard",
        description="A curtain of hiss that makes the user harder to pin down.",
        learn_level=None,
    ),
}


def get_move(move_id: str) -> Move:
    """Look up a move by id, or raise KeyError if it is not in the library."""
    if move_id not in MOVE_LIBRARY:
        raise KeyError(f"Unknown move id: {move_id!r}")
    return MOVE_LIBRARY[move_id]


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
