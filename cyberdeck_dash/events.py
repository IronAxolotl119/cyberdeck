"""The event bus and the Finding record every scanner produces.

In plain English: scanners, the pet, field mode, and the journal never
call each other directly. Instead, one part *publishes* a message on a
named topic ("finding", "alert", "pet.event", ...) and any part that
cares *subscribes* to that topic. That keeps each piece small and lets
you add a new panel or rule without rewiring the others.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

# --- Interest levels --------------------------------------------------------
# Every finding gets one. Field mode only alerts on HIGH by default.
LOW, MEDIUM, HIGH = 1, 2, 3
INTEREST_NAMES = {LOW: "low", MEDIUM: "med", HIGH: "HIGH"}

# --- Topics used across the dashboard ---------------------------------------
TOPIC_FINDING = "finding"        # Finding from any rule tracker
TOPIC_ALERT = "alert"            # Finding that passed the alert filter
TOPIC_PET_EVENT = "pet.event"    # dict pushed by the companion (or sim pet)
TOPIC_PET_REACTION = "pet.reaction"  # dict: result of a discovery / move
TOPIC_SCAN_REQUEST = "scan.request"  # str: scanner name or "all"
TOPIC_LOG = "log"                # str: plain status line for the ticker


@dataclass
class Finding:
    """One thing worth noting, e.g. 'new Wi-Fi network' or 'unknown tag'.

    kind      which radio/tool: wifi, bt, lan, rfid, pet, system
    event     what happened: new, open_network, rssi_jump, evil_twin, ...
    id        stable id of the thing (BSSID, MAC, IP, tag UID)
    label     short human name (SSID, device name, ...)
    interest  LOW / MEDIUM / HIGH
    detail    extra facts, saved to the journal as-is
    first     True for a first sighting (earns pet XP)
    """

    kind: str
    event: str
    id: str
    label: str = ""
    interest: int = LOW
    detail: dict[str, Any] = field(default_factory=dict)
    first: bool = False
    ts: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        """Identity used by cooldowns: same kind+event+id = same alert."""
        return f"{self.kind}:{self.event}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    """Tiny synchronous publish/subscribe hub.

    Handlers run immediately, in the order they subscribed. A handler
    that raises is reported on the "log" topic instead of crashing the UI.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> None:
        self._subs[topic].append(handler)

    def publish(self, topic: str, payload: Any = None) -> None:
        for handler in list(self._subs.get(topic, ())):
            try:
                handler(payload)
            except Exception as exc:  # keep the dashboard alive
                if topic != TOPIC_LOG:
                    self.publish(TOPIC_LOG, f"handler error on {topic}: {exc}")
