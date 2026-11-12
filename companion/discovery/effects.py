"""Turn scanned Signals into in-game events.

This is the only place that decides what a network or device *means* for the
pet. The rules are simple on purpose:

- A brand-new *strong* Wi-Fi network grants XP (the pet "studies" it).
- A brand-new weaker Wi-Fi network grants a little XP and a small mood bump.
- A brand-new Bluetooth device may teach a move (some ids are tutors) and
  always grants a bit of XP.
- A signal the pet has already seen is ignored, so standing next to the
  same router does not farm XP forever.

"Brand-new" is tracked on the creature itself (creature.discovered_ids),
which is part of the save file.
"""

from __future__ import annotations

from discovery.base import Signal
from pet.creature import Creature


# RSSI greater than or equal to this (e.g. -40 is stronger than -50) counts
# as a "strong" signal. Real-world dBm: -30 is excellent, -70 is fair.
STRONG_RSSI = -50

XP_STRONG_WIFI = 8
XP_WEAK_WIFI = 2
XP_BT = 4
MOOD_NEW_WIFI = 3.0

# Bluetooth device id -> move id. Only these tutors teach a technique;
# other BT devices still give XP, just no lesson.
BT_TUTORS: dict[str, str] = {
    "bt:mystery-beacon": "stray_handshake",
    "bt:arcade-cabinet": "packet_burst",
    "bt:quiet-fob": "noise_veil",
}


def describe_signal(signal: Signal) -> str:
    """A short, readable phrase for the log."""
    band = "Wi-Fi" if signal.kind == "wifi" else "Bluetooth"
    return f"{band} '{signal.name}' ({signal.id}, rssi {signal.rssi} dBm)"


def apply_signals(creature: Creature, signals: list[Signal]) -> list[str]:
    """Apply each unseen signal to the creature and return event log lines.

    Already-seen ids are left untouched. Newly seen ids are added to
    creature.discovered_ids even if they award nothing unusual, so they will
    not be treated as new on the next scan.
    """
    events: list[str] = []
    for signal in signals:
        if signal.id in creature.discovered_ids:
            continue
        creature.discovered_ids.add(signal.id)
        events.extend(_apply_new_signal(creature, signal))
    return events


def _apply_new_signal(creature: Creature, signal: Signal) -> list[str]:
    """Award XP / mood / a move for one first-time signal."""
    events = [f"New signal: {describe_signal(signal)}."]

    if signal.kind == "wifi":
        if signal.rssi >= STRONG_RSSI:
            events.append(f"{creature.name} studies the strong network and grows.")
            events.extend(creature.add_xp(XP_STRONG_WIFI))
        else:
            events.append(f"{creature.name} notes a faint network.")
            creature.stats.mood += MOOD_NEW_WIFI
            creature.stats.clamp()
            events.extend(creature.add_xp(XP_WEAK_WIFI))
        return events

    # Bluetooth
    events.extend(creature.add_xp(XP_BT))
    move_id = BT_TUTORS.get(signal.id)
    if move_id is not None:
        learned = creature.learn_move(move_id)
        if learned:
            events.append(f"The device teaches {creature.name} a new technique.")
            events.extend(learned)
        else:
            events.append("The device hums a technique already known.")
    else:
        events.append(f"{creature.name} pings the device and files it away.")
    return events
