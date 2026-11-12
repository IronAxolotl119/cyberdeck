"""The hardware-facing interface for noticing nearby signals.

Nothing in this module talks to a radio. Scanner is an abstract base class
so the rest of the game can ask "what is nearby?" without caring whether the
answer came from a seeded fake list or, later, from a Wi-Fi / Bluetooth
driver on the Pi. When you add real hardware, implement Scanner.scan() and
leave Signal alone — effects.py already knows how to read these fields.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# The only two kinds the rest of the game currently understands.
SIGNAL_KINDS = frozenset({"wifi", "bt"})


@dataclass(frozen=True)
class Signal:
    """One network or device spotted during a scan.

    kind:  'wifi' or 'bt'
    id:    a stable identifier (use this to remember "already seen")
    name:  a human-readable label for logs
    rssi:  signal strength in dBm. Closer to 0 is stronger; -30 is loud,
           -90 is a whisper. This is the same unit real radios report.
    """

    kind: str
    id: str
    name: str
    rssi: int

    def __post_init__(self) -> None:
        if self.kind not in SIGNAL_KINDS:
            raise ValueError(f"kind must be 'wifi' or 'bt', got {self.kind!r}")


class Scanner(ABC):
    """Something that can return a list of nearby Signals.

    SimulatedScanner (in simulated.py) is the only implementation in this
    repo. A future Pi driver should subclass Scanner and must not be imported
    by the core simulation.
    """

    @abstractmethod
    def scan(self) -> list[Signal]:
        """Return whatever is visible right now. May be empty."""
        raise NotImplementedError
